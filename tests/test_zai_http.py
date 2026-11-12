"""Offline transport/cache behavior: fake HTTPS only, synthetic fixture tokens."""
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/mixed-agents/bridge'))
import zai

def body(used=20):
    return json.dumps({'code':200,'success':True,'data':{'limits':[{'type':'CREDIT_LIMIT','unit':3,'number':5,'percentage':used}],'private':'RAW_PRIVATE_BODY'}}).encode()
class ZaiHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.state=self.root/'state';self.config=self.root/'config.toml'
        self.config.write_text('[providers.p]\nbase_url="https://api.z.ai/ignored/configured/path"\napi_key="SECRET_SENTINEL"\n[models.z]\nmodel="glm-5.3"\nprovider="p"\n')
        self.response=MagicMock(status=200);self.response.read.return_value=body()
        self.connection=MagicMock();self.connection.getresponse.return_value=self.response
        self.http=patch.object(zai.http.client,'HTTPSConnection',return_value=self.connection).start()
        self.net=patch('socket.socket.connect',side_effect=AssertionError('offline test')).start()
        self.addCleanup(patch.stopall);self.addCleanup(self.tmp.cleanup)
    def read(self,alias=None):return zai.read_usage(self.state,self.config,alias=alias)
    def persisted(self):return '\n'.join(p.read_text(errors='replace') for p in self.state.glob('*') if p.is_file())
    def test_fixed_https_get_bounded_timeout_body_and_safe_cache(self):
        value=self.read();self.assertEqual(value['remaining_percent'],80)
        args,kwargs=self.http.call_args
        self.assertEqual(args[0],'api.z.ai');self.assertGreater(kwargs['timeout'],0);self.assertLessEqual(kwargs['timeout'],30)
        args,kwargs=self.connection.request.call_args
        self.assertEqual(args[:2],('GET','/api/monitor/usage/quota/limit'))
        self.assertEqual(kwargs['headers']['Authorization'],'Bearer SECRET_SENTINEL')
        self.assertLessEqual(self.response.read.call_args.args[0],2**20+1)
        self.connection.close.assert_called_once()
        for forbidden in ('SECRET_SENTINEL','RAW_PRIVATE_BODY','Authorization'):
            self.assertNotIn(forbidden,json.dumps(value));self.assertNotIn(forbidden,self.persisted())
    def test_redirect_is_rejected_without_following_or_reading_body(self):
        self.response.status=302
        self.response.getheader.return_value='https://evil.invalid/steal'
        value=self.read()
        self.assertFalse(value['available']);self.assertEqual(value['error'],'redirect_rejected')
        self.assertEqual(self.http.call_count,1);self.response.read.assert_not_called()
    def test_errors_are_safe_and_unknown(self):
        for status in (401,429,500):
            with self.subTest(status=status):
                self.response.status=status
                value=zai.read_usage(None,self.config)
                self.assertFalse(value['available']);self.assertIsNone(value['remaining_percent'])
                self.assertNotIn('RAW_PRIVATE_BODY',json.dumps(value))
        self.connection.request.side_effect=OSError('SECRET_SENTINEL RAW_PRIVATE_BODY')
        value=zai.read_usage(None,self.config)
        self.assertFalse(value['available']);self.assertNotIn('SECRET_SENTINEL',json.dumps(value));self.assertNotIn('RAW_PRIVATE_BODY',json.dumps(value))
    def test_oversized_and_malformed_bodies_fail_closed(self):
        for raw in (b'x'*(2**20+1),b'not json',b'\xff'):
            self.response.read.return_value=raw
            self.assertFalse(zai.read_usage(None,self.config)['available'])
    def test_cache_reuses_recent_read_refreshes_stale_and_credentials(self):
        self.read();self.read();self.assertEqual(self.http.call_count,1)
        with patch.object(zai.time,'time',return_value=time.time()+31):self.read()
        self.assertEqual(self.http.call_count,2)
        self.config.write_text(self.config.read_text().replace('SECRET_SENTINEL','SECRET_B'))
        self.read();self.assertEqual(self.http.call_count,3)
        self.assertNotIn('SECRET_B',self.persisted())
    def test_multiple_accounts_conservative_and_alias_specific(self):
        self.config.write_text(self.config.read_text()+'[providers.q]\nbase_url="https://api.z.ai/api"\napi_key="SECRET_B"\n[models.other]\nmodel="glm-5.2"\nprovider="q"\n')
        self.response.read.side_effect=[body(20),body(95)]
        self.assertEqual(self.read()['remaining_percent'],5)
        self.assertEqual(self.read('z')['remaining_percent'],80)
        self.assertEqual(self.read('other')['remaining_percent'],5)
        self.assertEqual(self.http.call_count,2)
    def test_one_unavailable_account_makes_combined_usage_unknown(self):
        self.config.write_text(self.config.read_text()+'[providers.q]\nbase_url="https://api.z.ai/api"\napi_key="SECRET_B"\n[models.other]\nmodel="glm-5.2"\nprovider="q"\n')
        self.response.read.side_effect=[body(20),b'not json']
        self.assertFalse(self.read()['available'])
        self.assertEqual(self.read('z')['remaining_percent'],80)
    def test_invalid_or_future_cache_entries_are_refetched(self):
        self.read()
        path=self.state/'zai-usage-cache.json'
        original=json.loads(path.read_text());fingerprint=next(iter(original['accounts']))
        corruptions=[{'observed_at':time.time()+10000}, {'used_percent':None}, {'remaining_percent':101}, {'remaining_percent':'99'}, {'used_percent':-1}, {'used_percent':100,'remaining_percent':80}, {'reset_at':{'raw':'RAW_PRIVATE_BODY'}}, {'available':False,'error':'RAW_PRIVATE_BODY'}, {'available':True,'remaining_percent':90,'used_percent':10,'error':'RAW_PRIVATE_BODY'}]
        for corruption in corruptions:
            with self.subTest(corruption=corruption):
                data=json.loads(json.dumps(original));data['accounts'][fingerprint].update(corruption);path.write_text(json.dumps(data))
                before=self.http.call_count
                result=self.read()
                self.assertEqual(self.http.call_count,before+1,corruption)
                self.assertEqual(result['remaining_percent'],80)
    def test_cross_process_readers_share_single_flight(self):
        code="""import json,sys,time
from pathlib import Path
from unittest.mock import MagicMock,patch
sys.path.insert(0,sys.argv[1])
import zai
response=MagicMock(status=200)
response.read.return_value=json.dumps({'data':{'limits':[{'type':'CREDIT_LIMIT','unit':3,'number':5,'percentage':20}]}}).encode()
def connect(*args,**kwargs):
 with open(sys.argv[4],'a') as stream:stream.write('request\\n')
 time.sleep(.2)
 connection=MagicMock();connection.getresponse.return_value=response;return connection
with patch.object(zai.http.client,'HTTPSConnection',side_effect=connect),patch('socket.socket.connect',side_effect=AssertionError('offline')):
 print(json.dumps(zai.read_usage(sys.argv[2],sys.argv[3])))
"""
        log=self.root/'requests.txt'
        args=[sys.executable,'-c',code,str(Path(zai.__file__).parent),str(self.state),str(self.config),str(log)]
        procs=[subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(4)]
        results=[p.communicate(timeout=15) for p in procs]
        for p,(out,error) in zip(procs,results):
            self.assertEqual(p.returncode,0,error)
            self.assertEqual(json.loads(out)['remaining_percent'],80)
        self.assertEqual(log.read_text().splitlines(),['request'])
    def test_concurrent_readers_single_flight(self):
        def slow(*args):time.sleep(.1);return body()
        self.response.read.side_effect=slow
        results=[]
        threads=[threading.Thread(target=lambda:results.append(self.read())) for _ in range(5)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(5)
        self.assertEqual(len(results),5)
        self.assertEqual(self.http.call_count,1)
        self.assertTrue(all(r['remaining_percent']==80 for r in results))

if __name__=='__main__':unittest.main()
