"""Offline process-level acceptance of provider admission, independent GPT tests."""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

BRIDGE = Path(__file__).resolve().parents[1] / 'plugins/mixed-agents/bridge'
sys.path.insert(0, str(BRIDGE))
import runtime
from runtime import AgentManager, BridgeError
FAKE = Path(__file__).with_name('fake_cli.py')
CONFIG = '''
[providers.renamed_account]
base_url="https://api.z.ai/api/coding/paas/v4"
api_key="SECRET_SENTINEL"
[providers.second_account]
base_url="https://api.z.ai/api/paas/v4"
api_key="SECRET_B"
[providers.ark]
base_url="https://ark.cn-beijing.volces.com/api/coding/v3"
api_key="SECRET_SENTINEL"
[providers.spoof]
base_url="https://api.z.ai.evil.invalid/api"
api_key="SECRET_SENTINEL"
[models.z]
model="glm-5.3"
provider="renamed_account"
[models.z2]
model="glm-5.2"
provider="second_account"
[models.ark]
model="glm-5.3"
provider="ark"
[models.spoof]
model="glm-5.3"
provider="spoof"
[models.kimi]
model="kimi-for-coding"
provider="managed:kimi-code"
'''
class ZaiRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / 'config.toml'
        self.config.write_text(CONFIG)
        self.state = self.root / 'state'
        self.mgr = AgentManager(state_dir=str(self.state), config_path=str(self.config), kimi_command=[sys.executable, str(FAKE)])
    def tearDown(self):
        for agent in self.mgr.list_agents()['agents']:
            if agent['status'] not in runtime.TERMINAL:
                self.mgr.interrupt_agent(agent['agent_id'])
        self.tmp.cleanup()
    def spawn(self, unit, model='z', delay=20):
        return self.mgr.spawn_agent('zai-tests', unit, 'exploration', json.dumps({'delay':delay,'text':unit}), str(self.root), model)
    def wait(self, agent, predicate, timeout=15):
        end = time.monotonic()+timeout
        while time.monotonic()<end:
            value = self.mgr.get_agent(agent['agent_id'])
            if predicate(value): return value
            time.sleep(.05)
        self.fail('agent did not reach expected state: '+str(value))
    def test_default_three_provider_slots_and_shared_five(self):
        agents = [self.spawn(str(i), 'z' if i%2 else 'z2') for i in range(3)]
        with self.assertRaises(BridgeError) as caught: self.spawn('blocked')
        self.assertEqual(caught.exception.code,'provider_capacity_exceeded')
        self.spawn('ark','ark')
        self.spawn('kimi','kimi')
        self.assertEqual(self.mgr.model_catalog([])['kimi_running'],5)
        with self.assertRaises(BridgeError) as caught: self.spawn('shared-block','spoof')
        self.assertEqual(caught.exception.code,'capacity_exceeded')
        self.mgr.interrupt_agent(agents[0]['agent_id'])
        self.spawn('released')
    def test_origin_identity_ignores_alias_but_rejects_lookalike_hosts(self):
        self.mgr.set_zai_policy(limit=1)
        self.spawn('recognized')
        # An alias called spoof is GLM too, but its configured host is unrelated.
        self.spawn('lookalike','spoof')
        self.spawn('other-provider','ark')
        self.spawn('other-family','kimi')
        with self.assertRaises(BridgeError) as caught: self.spawn('second-account-alias','z2')
        self.assertEqual(caught.exception.code,'provider_capacity_exceeded')
    def test_lowering_does_not_cancel_and_unlimited_still_obeys_shared(self):
        self.mgr.set_zai_policy(limit='unlimited')
        agents=[self.spawn(str(i)) for i in range(4)]
        self.mgr.set_zai_policy(limit=1)
        for a in agents: self.assertIn(self.mgr.get_agent(a['agent_id'])['status'], {'starting','running'})
        with self.assertRaises(BridgeError) as caught: self.spawn('over-lowered')
        self.assertEqual(caught.exception.code,'provider_capacity_exceeded')
        self.mgr.set_zai_policy(limit='unlimited')
        self.spawn('fifth')
        with self.assertRaises(BridgeError) as caught: self.spawn('sixth')
        self.assertEqual(caught.exception.code,'capacity_exceeded')
    def test_idle_continuation_switch_into_provider_checks_current_cap(self):
        idle=self.spawn('idle','kimi',.05)
        self.wait(idle,lambda a:a['status']=='completed')
        self.mgr.set_zai_policy(limit=1)
        self.spawn('occupant')
        with self.assertRaises(BridgeError) as caught:
            self.mgr.followup_task(idle['agent_id'],'next',model='z')
        self.assertEqual(caught.exception.code,'provider_capacity_exceeded')
    def test_provider_blocked_selection_falls_back_preserving_family(self):
        self.mgr.set_zai_policy(limit=1)
        self.spawn('occupant')
        selected=self.mgr.select_model('s','u','exploration',['gpt-6.1-sol'])
        self.assertNotIn(selected['selected']['alias'],{'z','z2'})
        self.mgr.record_authorship('s','test-unit','implementation','gpt','parent')
        selected=self.mgr.select_model('s','test-unit','tests',['gpt-6.1-sol'])
        self.assertNotEqual(selected['selected']['family'],'gpt')
        self.assertNotIn(selected['selected']['alias'],{'z','z2'})
    def test_provider_overflow_keeps_legal_kimi_fallback_for_gpt_implementation(self):
        # Leave only Z.ai and Kimi CLI candidates so the legal fallback is clear.
        self.config.write_text(CONFIG.split('[models.ark]')[0] +
                               '[models.kimi]\nmodel="kimi-for-coding"\nprovider="managed:kimi-code"\n')
        for index in range(3):self.spawn('full-zai-'+str(index))
        self.assertEqual(self.mgr.model_catalog([])['kimi_available'],2)
        self.mgr.record_authorship('fallback','paired-unit','implementation','gpt','parent')
        with self.assertRaises(BridgeError) as caught:
            self.mgr.spawn_agent('fallback','paired-unit','tests','test the implementation',str(self.root),'z')
        error=caught.exception
        self.assertEqual(error.code,'provider_capacity_exceeded')
        self.assertNotEqual(error.details.get('fallback'),'gpt')
        self.assertFalse(error.details.get('diversity_blocked',False),error.details)
        selected=self.mgr.select_model('fallback','paired-unit','tests',['gpt-6.1-sol'])
        self.assertEqual(selected['selected']['family'],'kimi')
        self.assertEqual(selected['selected']['alias'],'kimi')
    def test_queue_reserves_provider_capacity(self):
        self.mgr.set_zai_policy(limit=1)
        a=self.spawn('queued',delay=.8)
        self.wait(a,lambda x:x['turn']==1 and bool(x['session_id']))
        self.mgr.followup_task(a['agent_id'],json.dumps({'delay':1,'text':'queued-turn'}))
        self.wait(a,lambda x:x['turn']>=2)
        with self.assertRaises(BridgeError) as caught: self.spawn('interloper')
        self.assertEqual(caught.exception.code,'provider_capacity_exceeded')
        self.wait(a,lambda x:x['status']=='completed')
        self.spawn('released',delay=.05)
    def test_failed_worker_launch_releases_both_reservations(self):
        self.mgr.set_zai_policy(limit=1)
        with patch.object(runtime.subprocess,'Popen',side_effect=OSError('synthetic failure')):
            with self.assertRaises((BridgeError, OSError)): self.spawn('failed-launch')
        self.spawn('after-failure')
    def test_live_orphan_retains_provider_reservation(self):
        self.mgr.set_zai_policy(limit=1)
        a=self.spawn('orphan')
        end=time.monotonic()+10
        while time.monotonic()<end:
            with sqlite3.connect(self.state/'agents.sqlite3') as db:
                db.row_factory=sqlite3.Row
                row=db.execute('SELECT * FROM agents WHERE id=?',(a['agent_id'],)).fetchone()
            if row['child_pid'] and row['session_id']: break
            time.sleep(.05)
        self.assertTrue(row['child_pid'])
        os.kill(row['worker_pid'],signal.SIGKILL)
        try:
            time.sleep(.3)
            with self.assertRaises(BridgeError) as caught: self.spawn('blocked-by-orphan')
            self.assertEqual(caught.exception.code,'provider_capacity_exceeded')
        finally:
            os.killpg(row['child_pid'],signal.SIGKILL)
        self.wait(a,lambda x:x['status'] in runtime.TERMINAL)
        self.spawn('recovered',delay=.05)
    def test_relative_config_is_canonical_for_detached_worker(self):
        code="""import json,sys
sys.path.insert(0,sys.argv[1])
from runtime import AgentManager
m=AgentManager(state_dir=sys.argv[2],config_path='config.toml',kimi_command=[sys.executable,sys.argv[3]])
a=m.spawn_agent('relative','unit','exploration','hello','.', 'z')
print(json.dumps(a))
"""
        child=subprocess.run([sys.executable,'-c',code,str(BRIDGE),str(self.state),str(FAKE)],cwd=self.root,capture_output=True,text=True,timeout=15)
        self.assertEqual(child.returncode,0,child.stderr)
        a=json.loads(child.stdout)
        self.assertEqual(self.wait(a,lambda x:x['status'] in runtime.TERMINAL)['status'],'completed')
        with sqlite3.connect(self.state/'agents.sqlite3') as db:
            config=db.execute('SELECT config FROM agents WHERE id=?',(a['agent_id'],)).fetchone()[0]
        self.assertEqual(Path(config),self.config.resolve())
    def test_simultaneous_initialization_migrates_legacy_database_once(self):
        with sqlite3.connect(self.state/'agents.sqlite3') as db:
            columns={r[1] for r in db.execute('PRAGMA table_info(agents)')}
            if 'provider' in columns:db.execute('ALTER TABLE agents DROP COLUMN provider')
        start=self.root/'start'
        code="""import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from runtime import AgentManager
while not Path(sys.argv[4]).exists():time.sleep(.005)
m=AgentManager(state_dir=sys.argv[2],config_path=sys.argv[3])
print(m.model_catalog([])['zai_policy']['cap'])
"""
        procs=[subprocess.Popen([sys.executable,'-c',code,str(BRIDGE),str(self.state),str(self.config),str(start)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(8)]
        start.touch()
        results=[p.communicate(timeout=15) for p in procs]
        for p,(out,error) in zip(procs,results):
            self.assertEqual(p.returncode,0,error)
            self.assertEqual(out.strip(),'3')
    def test_atomic_cross_process_admission(self):
        self.mgr.set_zai_policy(limit=2)
        code='''import json,sys
sys.path.insert(0,sys.argv[1])
from runtime import AgentManager,BridgeError
m=AgentManager(state_dir=sys.argv[2],config_path=sys.argv[3],kimi_command=[sys.executable,sys.argv[4]])
try:
 m.spawn_agent('race',sys.argv[5],'exploration',json.dumps({'delay':10}),sys.argv[2],'z')
 print('admitted')
except BridgeError as e: print(e.code)
'''
        procs=[subprocess.Popen([sys.executable,'-c',code,str(BRIDGE),str(self.state),str(self.config),str(FAKE),str(i)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i in range(5)]
        results=[p.communicate(timeout=30) for p in procs]
        for p,(_,error) in zip(procs,results): self.assertEqual(p.returncode,0,error)
        statuses=[out.strip() for out,_ in results]
        self.assertEqual(statuses.count('admitted'),2,statuses)
        self.assertEqual(statuses.count('provider_capacity_exceeded'),3,statuses)

if __name__=='__main__': unittest.main()
