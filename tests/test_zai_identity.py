"""Configured provider identity, not alias spelling, determines capacity group."""
import importlib
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/mixed-agents/bridge'))
class ZaiIdentityTests(unittest.TestCase):
    def test_only_recognized_glm_at_actual_https_origin(self):
        urls={'good':'https://api.z.ai/api/coding/paas/v4','case':'https://API.Z.AI/api','port':'https://api.z.ai:443/api','badport':'https://api.z.ai:8443/api','http':'http://api.z.ai/api','suffix':'https://api.z.ai.evil.invalid/api','user':'https://api.z.ai@evil.invalid/api','userinfo':'https://user:password@api.z.ai/api','ark':'https://ark.cn-beijing.volces.com/api/coding/v3'}
        text=''
        for name,url in urls.items():
            text+=f'[providers.{name}]\nbase_url="{url}"\napi_key="SECRET_SENTINEL"\n[models.{name}]\nmodel="glm-5.3"\nprovider="{name}"\n'
        text+='[models.kimi]\nmodel="kimi-for-coding"\nprovider="good"\n[models.unsupported]\nmodel="glm-unknown"\nprovider="good"\n'
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'kimi.toml';config.write_text(text)
            self.assertEqual(importlib.import_module('zai').aliases(config),{'good','case','port','userinfo'})
