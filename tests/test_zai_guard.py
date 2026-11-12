"""Runtime guard acceptance with no HTTP, real fake CLI, and in-process workers."""
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/mixed-agents/bridge'))
import runtime
from runtime import AgentManager, BridgeError
FAKE=Path(__file__).with_name('fake_cli.py')

class ZaiGuardRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.config=self.root/'alternate'/'config.toml'
        self.config.parent.mkdir()
        self.config.write_text('[providers.p]\nbase_url="https://api.z.ai/api/coding/paas/v4"\napi_key="SECRET_SENTINEL"\n[models.z]\nmodel="glm-5.3"\nprovider="p"\n[models.k]\nmodel="kimi-for-coding"\nprovider="managed:kimi-code"\n')
        self.state=self.root/'state'
        self.mgr=AgentManager(state_dir=str(self.state),config_path=str(self.config),kimi_command=[sys.executable,str(FAKE)])
        self.remaining=80; self.alias_remaining={}; self.reads=[]; self.worker_reads=[]; self.threads=[]; self.errors=[]
        def read(state,config,alias=None):
            self.reads.append((str(config),alias))
            if threading.current_thread() is not threading.main_thread():self.worker_reads.append((str(config),alias))
            remaining=self.alias_remaining.get(alias,self.remaining)
            return {'available':remaining is not None,'remaining_percent':remaining,'used_percent':None if remaining is None else 100-remaining,'observed_at':time.time(),'reset_at':0,'error':None if remaining is not None else 'unavailable'}
        self.patches=[patch.object(importlib.import_module('zai'),'read_usage',side_effect=read),patch('socket.socket.connect',side_effect=AssertionError('No test networking')),patch.object(runtime.signal,'signal')]
        for p in self.patches:p.start()
        self.addCleanup(self.stop_patches)
        def launch(agent_id,*fds):
            def run():
                try: runtime.run_worker(str(self.state),agent_id,*fds)
                except BaseException as exc:self.errors.append(exc)
            thread=threading.Thread(target=run,daemon=True);self.threads.append(thread);thread.start()
        self.launch_patch=patch.object(self.mgr,'_launch',side_effect=launch); self.launch_patch.start()
        self.mgr.set_zai_policy(usage_guard=True)
    def stop_patches(self):
        self.launch_patch.stop()
        for p in reversed(self.patches):p.stop()
    def tearDown(self):
        for agent in self.mgr.list_agents()['agents']:
            if agent['status'] not in runtime.TERMINAL:self.mgr.interrupt_agent(agent['agent_id'])
        for t in self.threads:t.join(5)
        self.tmp.cleanup()
    def spawn(self,unit='guard',model='z',delay=.1):
        return self.mgr.spawn_agent('guard',unit,'exploration',json.dumps({'text':'retained-output','delay':delay}),str(self.root),model)
    def wait(self,a,predicate,timeout=35):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            status=self.mgr.get_agent(a['agent_id'])
            if predicate(status):return status
            if self.errors:raise self.errors[0]
            time.sleep(.05)
        self.fail('Expected guard transition, got '+str(status))
    def test_admission_reserve_and_unavailable_reasons_are_distinct(self):
        for remaining,code in ((10,'provider_quota_reserve'),(0,'provider_quota_reserve'),(None,'provider_usage_unavailable')):
            self.remaining=remaining
            with self.assertRaises(BridgeError) as caught:self.spawn(str(remaining))
            self.assertEqual(caught.exception.code,code)
        self.remaining=10.01
        agent=self.spawn('allowed')
        self.assertEqual(self.wait(agent,lambda a:a['status'] in runtime.TERMINAL)['status'],'completed')
    def test_active_quota_stop_retains_session_output_and_clears_queue(self):
        agent=self.spawn()
        done=self.wait(agent,lambda a:a['status']=='completed')
        session=done['session_id'];self.assertTrue(session)
        self.mgr.followup_task(agent['agent_id'],json.dumps({'delay':60,'text':'unfinished'}))
        self.wait(agent,lambda a:a['turn']==2 and a['status']=='running')
        self.mgr.send_message(agent['agent_id'],'queued-must-not-run')
        self.remaining=10
        stopped=self.wait(agent,lambda a:a['status']=='interrupted')
        self.assertEqual(stopped['session_id'],session)
        self.assertIn('retained-output',stopped['last_result'])
        self.assertEqual(stopped['queued_message_count'],0)
        self.assertIn('quota',stopped['error'].lower())
        observed=next(m for m in self.mgr.model_catalog([])['models'] if m['alias']=='z')['observation']
        self.assertEqual(observed['failures'],0)
        self.assertTrue(all(Path(config)==Path(self.mgr.config) for config,_ in self.reads),self.reads)
        self.assertTrue(self.worker_reads)
        self.assertTrue(all(alias=='z' for _,alias in self.worker_reads),self.worker_reads)
        with self.assertRaises(BridgeError) as caught:self.mgr.followup_task(agent['agent_id'],'resume')
        self.assertEqual(caught.exception.code,'provider_quota_reserve')
    def test_guard_unavailable_stops_active_turn(self):
        a=self.spawn(delay=60)
        self.wait(a,lambda x:bool(x['session_id']))
        self.remaining=None
        stopped=self.wait(a,lambda x:x['status']=='interrupted')
        self.assertIn('unavailable',stopped['error'])
    def test_disabled_guard_never_reads_usage_in_catalog_selection_or_worker(self):
        self.mgr.set_zai_policy(usage_guard=False)
        self.reads.clear()
        self.mgr.model_catalog([])
        self.mgr.select_model('disabled','unit','exploration',[])
        a=self.spawn('disabled')
        self.wait(a,lambda x:x['status']=='completed')
        self.assertEqual(self.reads,[])
    def test_selected_account_can_run_when_another_account_is_unknown(self):
        self.alias_remaining={None:None,'z':80}
        selected=self.mgr.select_model('account','unit','exploration',[],avoid_family='kimi')
        self.assertEqual(selected['selected']['alias'],'z')
        a=self.spawn('selected-account')
        self.assertEqual(self.wait(a,lambda x:x['status'] in runtime.TERMINAL)['status'],'completed')
        self.assertTrue(self.worker_reads)
        self.assertTrue(all(alias=='z' for _,alias in self.worker_reads),self.worker_reads)
    def test_enabling_guard_stops_already_running_zai_turn(self):
        self.mgr.set_zai_policy(usage_guard=False)
        self.reads.clear()
        a=self.spawn('live-enable',delay=60)
        self.wait(a,lambda x:bool(x['session_id']))
        self.assertEqual(self.reads,[])
        self.remaining=10
        self.mgr.set_zai_policy(usage_guard=True)
        stopped=self.wait(a,lambda x:x['status']=='interrupted')
        self.assertIn('quota',stopped['error'].lower())
        self.assertTrue(self.worker_reads)
        self.assertTrue(all(alias=='z' for _,alias in self.worker_reads),self.worker_reads)
    def test_other_family_available_when_zai_usage_unknown(self):
        self.remaining=None
        selected=self.mgr.select_model('blocked','unit','exploration',['gpt-6.1-sol'])
        self.assertNotEqual(selected['selected']['alias'],'z')
        a=self.spawn('kimi-still-works',model='k')
        self.assertEqual(self.wait(a,lambda x:x['status']=='completed')['status'],'completed')

if __name__=='__main__':unittest.main()
