"""GPT-authored Kimi reserve runtime acceptance, with fake CLI and no network."""
import importlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/mixed-agents/bridge'))
import runtime
from runtime import AgentManager, BridgeError
import server

FAKE = Path(__file__).with_name('fake_cli.py')


class KimiGuardTests(unittest.TestCase):
    def setUp(self):
        self.kimi = importlib.import_module('kimi_usage')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.config = self.root / 'config.toml'
        self.config.write_text('[providers.p]\nbase_url="https://api.kimi.com/coding/v1"\napi_key="SYNTHETIC_A"\n'
                               '[models.a]\nmodel="kimi-for-coding"\nprovider="p"\n'
                               '[models.alias]\nmodel="kimi-for-coding"\nprovider="p"\n'
                               '[providers.q]\nbase_url="https://api.kimi.com/coding/v1"\napi_key="SYNTHETIC_B"\n'
                               '[models.b]\nmodel="kimi-for-coding"\nprovider="q"\n')
        self.manager = AgentManager(state_dir=str(self.state), config_path=str(self.config),
                                    kimi_command=[sys.executable, str(FAKE)])
        self.remaining = 94
        self.by_alias = {}
        self.reads = []
        self.threads = []
        self.errors = []
        def read(state, config, alias=None, **kwargs):
            self.reads.append((str(config), alias, threading.current_thread().name))
            remaining = self.by_alias.get(alias, self.remaining)
            return {'available': remaining is not None, 'remaining_percent': remaining,
                    'used_percent': None if remaining is None else 100 - remaining,
                    'observed_at': time.time(), 'reset_at': None,
                    'error': 'no_data' if remaining is None else None}
        self.patches = [patch.object(self.kimi, 'read_usage', side_effect=read),
                        patch('socket.socket.connect', side_effect=AssertionError('offline test')),
                        patch.object(runtime.signal, 'signal')]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        def launch(agent_id, *fds):
            def run():
                try:
                    runtime.run_worker(str(self.state), agent_id, *fds)
                except BaseException as exc:
                    self.errors.append(exc)
            thread = threading.Thread(target=run, daemon=True)
            self.threads.append(thread)
            thread.start()
        self.launch = patch.object(self.manager, '_launch', side_effect=launch)
        self.launch.start()
        self.addCleanup(self.launch.stop)
        self.manager.set_kimi_policy(usage_guard=True, probe_on_unknown=False)

    def tearDown(self):
        for agent in self.manager.list_agents()['agents']:
            if agent['status'] not in runtime.TERMINAL:
                self.manager.interrupt_agent(agent['agent_id'])
        for thread in self.threads:
            thread.join(5)
        self.launch.stop()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def spawn(self, unit='guard', model='a', delay=.05):
        return self.manager.spawn_agent('kimi-guard', unit, 'exploration',
                                        json.dumps({'text': 'retained-output', 'delay': delay}),
                                        str(self.root), model)

    def wait(self, agent, predicate, timeout=35):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            status = self.manager.get_agent(agent['agent_id'])
            if predicate(status):
                return status
            if self.errors:
                raise self.errors[0]
            time.sleep(.03)
        self.fail('Missing expected transition: ' + str(status))

    def test_policy_persists_without_altering_zai_shared_or_unrelated_settings(self):
        self.manager.set_concurrency(4)
        self.manager.set_zai_policy(limit=2, stop_remaining_percent=12)
        path = self.state / 'config.json'
        data = json.loads(path.read_text())
        data['unrelated'] = {'keep': True}
        path.write_text(json.dumps(data))
        self.manager.set_kimi_policy(stop_remaining_percent=6, probe_on_unknown=True)
        data = json.loads(path.read_text())
        self.assertEqual(data['kimi_concurrency'], 4)
        self.assertEqual(data['unrelated'], {'keep': True})
        fresh = AgentManager(state_dir=str(self.state), config_path=str(self.config))
        view = fresh.model_catalog([])
        self.assertEqual(view['zai_policy']['cap'], 2)
        self.assertEqual(view['zai_policy']['stop_remaining_percent'], 12)
        self.assertEqual(view['kimi_policy']['stop_remaining_percent'], 6)
        self.assertTrue(view['kimi_policy']['usage_guard'])
        self.assertTrue(view['kimi_policy']['probe_on_unknown'])
        before = path.read_bytes()
        for arguments in ({'usage_guard': 'true'}, {'probe_on_unknown': 1},
                          {'stop_remaining_percent': -1}, {'stop_remaining_percent': '6'},
                          {'stop_remaining_percent': 100},
                          {'stop_remaining_percent': float('nan')}):
            with self.assertRaises(BridgeError):
                self.manager.set_kimi_policy(**arguments)
            self.assertEqual(path.read_bytes(), before)

    def test_reserve_boundary_blocks_and_unknown_disabled_probe_blocks(self):
        with patch.object(self.manager, 'probe_kimi_usage') as probe:
            for remaining, code in ((5, 'provider_quota_reserve'), (0, 'provider_quota_reserve'),
                                    (None, 'provider_usage_unavailable')):
                self.remaining = remaining
                with self.assertRaises(BridgeError) as caught:
                    self.spawn(str(remaining))
                self.assertEqual(caught.exception.code, code)
            probe.assert_not_called()
        self.remaining = 5.01
        agent = self.spawn('above-reserve')
        self.assertEqual(self.wait(agent, lambda a: a['status'] in runtime.TERMINAL)['status'], 'completed')
        self.manager.record_authorship('independence', 'unit', 'implementation', 'kimi', 'synthetic-author')
        self.manager.set_kimi_policy(probe_on_unknown=True)
        self.remaining = None
        with patch.object(self.manager, '_run_probe') as probe:
            with self.assertRaises(BridgeError) as caught:
                self.manager.spawn_agent('independence', 'unit', 'tests', 'must not run', str(self.root), 'a')
            self.assertEqual(caught.exception.code, 'family_conflict')
            probe.assert_not_called()

    def test_known_reserve_never_probes_even_when_enabled(self):
        self.manager.set_kimi_policy(probe_on_unknown=True)
        self.remaining = 5
        with patch.object(self.manager, 'probe_kimi_usage') as probe:
            with self.assertRaises(BridgeError):
                self.spawn()
            probe.assert_not_called()

    def test_readonly_catalog_selection_doctor_never_bootstrap(self):
        self.manager.set_kimi_policy(probe_on_unknown=True)
        self.remaining = None
        with patch.object(self.manager, 'probe_kimi_usage') as probe:
            self.manager.model_catalog(['gpt-6.1-sol'])
            chosen = self.manager.select_model('read-only', 'choose', 'exploration', ['gpt-6.1-sol'])
            self.assertNotIn(chosen['selected']['alias'], ('a', 'alias', 'b'))
            self.manager.doctor()
            probe.assert_not_called()

    def test_account_specific_routing_and_no_reads_when_guard_disabled(self):
        self.by_alias = {'a': None, 'alias': None, 'b': 94}
        chosen = self.manager.select_model('routing', 'healthy', 'exploration', [])
        self.assertEqual(chosen['selected']['alias'], 'b')
        agent = self.spawn(model='b')
        self.assertEqual(self.wait(agent, lambda a: a['status'] in runtime.TERMINAL)['status'], 'completed')
        self.manager.set_kimi_policy(usage_guard=False)
        self.reads.clear()
        self.manager.model_catalog([])
        self.manager.select_model('disabled', 'choice', 'exploration', [])
        agent = self.spawn('disabled')
        self.wait(agent, lambda a: a['status'] in runtime.TERMINAL)
        self.assertEqual(self.reads, [])

    def test_healthy_nondefault_same_profile_account_remains_routable(self):
        self.manager.set_kimi_policy(probe_on_unknown=True)
        view = self.manager.model_catalog([])
        aliases = {model['alias']: model for model in view['models']}
        self.assertTrue(aliases['a']['routing_default'])
        self.assertFalse(aliases['b']['routing_default'])
        self.assertEqual(aliases['a']['profile'], aliases['b']['profile'])
        with patch.object(self.manager, '_run_probe') as probe:
            for blocked in (5, None):
                self.by_alias = {'a': blocked, 'alias': blocked, 'b': 94}
                selected = self.manager.select_model('account-fallback', str(blocked), 'exploration', [])
                self.assertEqual(selected['selected']['alias'], 'b')
            probe.assert_not_called()

    def test_active_unknown_clears_pending_preserves_output_session_and_never_probes(self):
        agent = self.spawn()
        done = self.wait(agent, lambda a: a['status'] == 'completed')
        session = done['session_id']
        self.assertTrue(session)
        self.manager.followup_task(agent['agent_id'], json.dumps({'delay': 60}))
        self.wait(agent, lambda a: a['turn'] == 2 and a['status'] == 'running')
        self.manager.send_message(agent['agent_id'], 'queued-not-to-run')
        self.manager.set_kimi_policy(probe_on_unknown=True)
        with patch.object(self.manager, 'probe_kimi_usage') as probe:
            self.remaining = None
            stopped = self.wait(agent, lambda a: a['status'] == 'interrupted')
            probe.assert_not_called()
        self.assertEqual(stopped['session_id'], session)
        self.assertIn('retained-output', stopped['last_result'])
        self.assertEqual(stopped['queued_message_count'], 0)
        self.assertIn('unavailable', stopped['error'])

    def test_unknown_before_queued_turn_stops_without_launching_inference(self):
        with patch.object(self.manager, '_launch') as launch:
            agent = self.spawn('queued')
        _, fd = launch.call_args.args
        self.remaining = None
        self.manager.set_kimi_policy(probe_on_unknown=True)
        with patch.object(AgentManager, 'probe_kimi_usage') as probe:
            runtime.run_worker(str(self.state), agent['agent_id'], fd)
            probe.assert_not_called()
        stopped = self.manager.get_agent(agent['agent_id'])
        self.assertEqual(stopped['status'], 'interrupted')
        self.assertEqual(stopped['turn'], 0)
        self.assertEqual(stopped['queued_message_count'], 0)
        self.assertIn('unavailable', stopped['error'])

    def test_enabling_guard_stops_existing_worker_at_reserve(self):
        self.manager.set_kimi_policy(usage_guard=False)
        agent = self.spawn(delay=60)
        self.wait(agent, lambda a: bool(a['session_id']))
        self.remaining = 5
        self.manager.set_kimi_policy(usage_guard=True)
        stopped = self.wait(agent, lambda a: a['status'] == 'interrupted')
        self.assertIn('quota', stopped['error'])
        with self.assertRaises(BridgeError):
            self.manager.followup_task(agent['agent_id'], 'not admitted')

    def probe_fixture(self):
        log = self.root / 'probe-calls.jsonl'
        script = self.root / 'probe-cli.py'
        script.write_text("""import json,os,sys,time
from pathlib import Path
record={'args':sys.argv[1:],'cwd':os.getcwd(), 'env':{k:v for k,v in os.environ.items() if k.startswith('KIMI_')}}
with open(%r,'a') as stream: stream.write(json.dumps(record)+'\\n')
time.sleep(.1)
print(json.dumps({'role':'assistant','content':'OK'}))
""" % str(log))
        manager = AgentManager(state_dir=str(self.state), config_path=str(self.config),
                               kimi_command=[sys.executable, str(script)])
        return manager, log

    def attempt_probe(self, manager, model='a'):
        try:
            return manager.probe_kimi_usage(model)
        except BridgeError as error:
            return {'error': error.code}

    def test_unknown_probe_once_across_aliases_and_manager_restart(self):
        self.manager.set_kimi_policy(probe_on_unknown=True)
        self.remaining = None
        manager, log = self.probe_fixture()
        self.attempt_probe(manager)
        self.assertTrue(log.exists(), 'Unknown idle account should receive one bounded probe')
        self.attempt_probe(manager, 'alias')
        restarted = AgentManager(state_dir=str(self.state), config_path=str(self.config),
                                 kimi_command=manager.command)
        self.attempt_probe(restarted)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertNotEqual(Path(call['cwd']), self.root)
        self.assertNotIn('retained-output', json.dumps(call))
        self.assertEqual(call['env']['KIMI_LOOP_MAX_STEPS_PER_TURN'], '1')
        self.assertEqual(call['env']['KIMI_LOOP_MAX_ATTEMPTS_PER_STEP'], '1')
        self.assertLessEqual(int(call['env']['KIMI_MODEL_MAX_OUTPUT_SIZE']), 128)
        profile_index = call['args'].index('--agent-file') + 1
        profile = Path(call['args'][profile_index]).read_text()
        self.assertIn('tools: []', profile)
        self.assertIn('subagents: []', profile)
        self.assertLessEqual(runtime.PROBE_TIMEOUT_SECONDS, 30)
        self.assertTrue(any(alias == 'a' for _, alias, _ in self.reads))
        self.assertGreaterEqual(len(self.reads), 2, 'Probe success must re-read quota')
        self.attempt_probe(manager, 'b')
        self.assertEqual(len(log.read_text().splitlines()), 2, 'Separate account gets separate allowance')

    def test_concurrent_unknown_probe_callers_share_one_durable_allowance(self):
        self.manager.set_kimi_policy(probe_on_unknown=True)
        self.remaining = None
        manager, log = self.probe_fixture()
        barrier = threading.Barrier(4)
        errors = []
        def attempt(alias):
            try:
                barrier.wait(timeout=5)
                self.attempt_probe(manager, alias)
            except BaseException as error:
                errors.append(error)
        threads = [threading.Thread(target=attempt, args=(alias,)) for alias in ('a', 'alias', 'a', 'alias')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(log.read_text().splitlines()), 1)

    def test_oauth_token_refresh_does_not_reset_account_probe_allowance(self):
        self.config.write_text('[providers.p]\nbase_url="https://api.kimi.com/coding/v1"\napi_key=""\n'
                               'oauth={storage="file",key="oauth/kimi-code"}\n'
                               '[models.a]\nmodel="kimi-for-coding"\nprovider="p"\n')
        directory = self.root / 'credentials'
        directory.mkdir()
        path = directory / 'kimi-code.json'
        payload = {'access_token': 'SYNTHETIC_OAUTH', 'refresh_token': 'test_synthetic_refresh',
                   'expires_at': time.time() + 3600, 'token_type': 'Bearer'}
        path.write_text(json.dumps(payload))
        self.manager.set_kimi_policy(probe_on_unknown=True)
        self.remaining = None
        manager, log = self.probe_fixture()
        self.attempt_probe(manager)
        payload.update(access_token='ROTATED_ACCESS', refresh_token='ROTATED_REFRESH')
        path.write_text(json.dumps(payload))
        self.attempt_probe(manager)
        self.assertEqual(len(log.read_text().splitlines()), 1)

    def test_expired_oauth_allows_one_cli_refresh_probe_without_bridge_refresh(self):
        self.config.write_text('[providers.p]\nbase_url="https://api.kimi.com/coding/v1"\napi_key=""\n'
                               'oauth={storage="file",key="oauth/kimi-code"}\n'
                               '[models.a]\nmodel="kimi-for-coding"\nprovider="p"\n')
        directory = self.root / 'credentials'
        directory.mkdir()
        path = directory / 'kimi-code.json'
        path.write_text(json.dumps({'access_token': 'EXPIRED_ACCESS', 'refresh_token': 'test_synthetic_refresh',
                                    'expires_at': time.time() - 100, 'token_type': 'Bearer'}))
        before = path.read_bytes()
        self.manager.set_kimi_policy(probe_on_unknown=True)
        self.remaining = None
        manager, log = self.probe_fixture()
        self.attempt_probe(manager)
        self.assertTrue(log.exists(), 'Stable OAuth ref permits a bounded CLI bootstrap even with an expired token')
        self.assertEqual(path.read_bytes(), before, 'Bridge must not refresh or rewrite the OAuth credential')
        self.attempt_probe(manager)
        self.assertEqual(len(log.read_text().splitlines()), 1)

    def test_cross_process_probe_claim_survives_racing_managers(self):
        self.manager.set_kimi_policy(probe_on_unknown=True)
        self.remaining = None
        manager, log = self.probe_fixture()
        code = """import sys,time
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
import kimi_usage
from runtime import AgentManager,BridgeError
unknown={'available':False,'remaining_percent':None,'used_percent':None,'error':'no_data','observed_at':time.time(),'reset_at':None}
with patch.object(kimi_usage,'read_usage',return_value=unknown),patch('socket.socket.connect',side_effect=AssertionError('offline')):
 manager=AgentManager(state_dir=sys.argv[2],config_path=sys.argv[3],kimi_command=[sys.executable,sys.argv[4]])
 try: manager.probe_kimi_usage(sys.argv[5])
 except BridgeError: pass
"""
        args = [sys.executable, '-c', code, str(Path(runtime.__file__).parent),
                str(self.state), str(self.config), manager.command[1]]
        processes = [subprocess.Popen(args + [alias], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True) for alias in ('a', 'alias', 'a')]
        for process in processes:
            out, err = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, err)
        self.assertEqual(len(log.read_text().splitlines()), 1)

    def test_probe_respects_full_lowered_shared_cap_then_releases_slot(self):
        self.manager.set_kimi_policy(probe_on_unknown=True)
        self.manager.set_concurrency(2)
        active = self.spawn('slot-occupant', model='b', delay=60)
        self.wait(active, lambda a: bool(a['session_id']))
        self.manager.set_concurrency(1)
        self.remaining = None
        manager, log = self.probe_fixture()
        self.attempt_probe(manager)
        self.assertFalse(log.exists(), 'Lowered full shared cap must block the probe subprocess')
        self.manager.interrupt_agent(active['agent_id'])
        self.wait(active, lambda a: a['status'] in runtime.TERMINAL)
        self.attempt_probe(manager)
        self.assertTrue(log.exists(), 'Capacity denial must not spend the bootstrap allowance')
        self.assertEqual(len(log.read_text().splitlines()), 1)
        self.remaining = 94
        resumed = self.spawn('after-probe')
        self.assertEqual(self.wait(resumed, lambda a: a['status'] in runtime.TERMINAL)['status'], 'completed')

    def test_explicit_probe_at_known_reserve_does_not_launch_cli(self):
        self.manager.set_kimi_policy(probe_on_unknown=True)
        self.remaining = 5
        manager, log = self.probe_fixture()
        self.attempt_probe(manager)
        self.assertFalse(log.exists())

    def test_mcp_discovery_has_strict_fixed_probe_and_backward_compatible_usage(self):
        reply = server.handle(self.manager, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        tools = {tool['name']: tool for tool in reply['result']['tools']}
        self.assertIn('set_kimi_policy', tools)
        self.assertEqual(set(tools['probe_kimi_usage']['inputSchema']['properties']), {'model'})
        self.assertTrue(tools['probe_kimi_usage']['annotations']['openWorldHint'])
        self.assertTrue(tools['provider_usage']['annotations']['readOnlyHint'])
        with patch('zai.read_usage', return_value={'available': False}) as read:
            self.manager.provider_usage()
            read.assert_called_once()
        self.assertEqual(self.manager.provider_usage(provider='kimi', model='b')['remaining_percent'], 94)


if __name__ == '__main__':
    unittest.main()
