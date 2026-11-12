"""Kimi K3.1 process/API acceptance tests using temporary CLI state."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

BRIDGE = Path(__file__).resolve().parents[1] / "plugins/mixed-agents/bridge"
sys.path.insert(0, str(BRIDGE))
import runtime  # noqa: E402
from runtime import AgentManager, BridgeError  # noqa: E402

FAKE_CLI = r'''import json
import os
from pathlib import Path
import sys
import tomllib

args = sys.argv[1:]
def option(name):
    return args[args.index(name) + 1] if name in args else None

def emit(value):
    print(json.dumps(value), flush=True)

marker = os.environ.get("K31_FAKE_MARKER")
if marker:
    Path(marker).write_text("launched", encoding="utf-8")
model = option("-m")
session = option("-S")
home = Path(os.environ["KIMI_CODE_HOME"])
with (home / "config.toml").open("rb") as stream:
    config = tomllib.load(stream)
entry = config.get("models", {}).get(model, {})
emit({"role": "meta", "type": "session.resume_hint",
      "session_id": session or "k31-fake-session"})
emit({"role": "assistant", "content": json.dumps({
    "model": model, "upstream": entry.get("model"),
    "thinking": config.get("thinking"), "session": session,
})})
'''


class Kimi31RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.config = self.root / "config.toml"
        self.config.write_text("", encoding="utf-8")
        self.fake = self.root / "fake_kimi31.py"
        self.fake.write_text(FAKE_CLI, encoding="utf-8")
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            for agent in manager.list_agents()["agents"]:
                if agent["status"] not in runtime.TERMINAL:
                    try:
                        manager.interrupt_agent(agent["agent_id"])
                    except Exception:
                        pass

    def manager(self, config=None):
        manager = AgentManager(
            state_dir=str(self.state), config_path=str(config or self.config),
            kimi_command=[sys.executable, str(self.fake)])
        self.managers.append(manager)
        return manager

    def wait(self, manager, agent_id):
        result = manager.wait_agent([agent_id], timeout_ms=15000)
        self.assertFalse(result["timed_out"], result)
        return manager.get_agent(agent_id)

    def allow_default(self, manager, alias):
        template = manager.get_template("default")
        payload = template["payload"]
        payload["agent_configs"] = [{
            "agent_type": "kimi-code", "enabled": True,
            "providers": [{"provider_id": "managed", "models": [alias]}],
        }]
        manager.save_template("default", payload,
                              expected_revision=template["revision"])

    def test_catalog_selection_exact_ids_and_unknown_suffix(self):
        upstreams = {
            "pro": "k3.1-pro",
            "pro-api": "kimi-k3.1-pro",
            "fast": "k3.1-pro-highspeed",
            "fast-api": "kimi-k3.1-pro-highspeed",
            "qualified": "kimi-code/k3.1-pro",
            "invented": "k3.1-pro-ultra",
        }
        text = '[providers.kimi]\napi_key = "fake"\nbase_url = "https://api.kimi.com/coding/v1"\n'
        for alias, upstream in upstreams.items():
            text += (f'\n[models."{alias}"]\nmodel = "{upstream}"\n'
                     'provider = "kimi"\nmax_context_size = 1048576\n')
        self.config.write_text(text, encoding="utf-8")
        manager = self.manager()
        catalog = manager.model_catalog([])
        models = {m["alias"]: m for m in catalog["models"] if m["family"] == "kimi"}
        self.assertEqual(set(models), set(upstreams) - {"invented"})
        self.assertEqual({m["model"] for m in models.values()},
                         set(upstreams.values()) - {"k3.1-pro-ultra"})
        self.assertEqual(models["pro"]["profile"], "k3.1-pro")
        self.assertEqual(models["pro-api"]["profile"], "k3.1-pro")
        self.assertEqual(models["fast"]["profile"], "k3.1-pro-highspeed")
        self.assertEqual(models["fast-api"]["profile"], "k3.1-pro-highspeed")
        selected = manager.select_model(
            "k31-catalog", "selection", "implementation", [],
            complexity="complex", context_tokens=1000000, latency_sensitive=True)
        self.assertIn(selected["selected"]["alias"], {"fast", "fast-api"})
        self.assertNotIn("invented", {m["alias"] for m in selected["candidates"]})

    def test_managed_create_allowlist_thinking_and_resume_dispatch(self):
        key = "TEST_K31_RUNTIME_KEY"
        os.environ[key] = "not-a-real-credential"
        self.addCleanup(os.environ.pop, key, None)
        manager = self.manager()
        manager.create_provider("managed", "Managed Kimi", "kimi",
                                "https://api.kimi.com/coding/v1", "openai",
                                "env:" + key)
        created = manager.create_model(
            "managed/pro", "managed", "k3.1-pro", "K3.1 Pro",
            1048576, ["tool_use"])
        self.assertEqual(created["upstream_model"], "k3.1-pro")
        with self.assertRaises(BridgeError) as denied:
            manager.spawn_agent("k31-managed", "allowlist", "implementation",
                                "first", str(self.root), "managed/pro")
        self.assertEqual(denied.exception.code, "model_not_allowed")
        self.allow_default(manager, "managed/pro")
        agent = manager.spawn_agent("k31-managed", "dispatch", "implementation",
                                    "first", str(self.root), "managed/pro")
        first = self.wait(manager, agent["agent_id"])
        self.assertEqual(first["status"], "completed", first)
        initial = json.loads(first["last_result"])
        self.assertEqual(initial["model"], "managed/pro")
        self.assertEqual(initial["upstream"], "k3.1-pro")
        self.assertIs(initial["thinking"]["enabled"], True)
        self.assertIsNone(initial["session"])
        manager.followup_task(agent["agent_id"], "second")
        second = self.wait(manager, agent["agent_id"])
        self.assertEqual(second["status"], "completed", second)
        resumed = json.loads(second["last_result"])
        self.assertEqual(resumed["model"], "managed/pro")
        self.assertEqual(resumed["upstream"], "k3.1-pro")
        self.assertIs(resumed["thinking"]["enabled"], True)
        self.assertEqual(resumed["session"], first["session_id"])

    def test_imported_disabled_thinking_rejected_before_cli(self):
        self.config.write_text(
            '[providers.kimi]\napi_key = "fake"\n'
            'base_url = "https://api.kimi.com/coding/v1"\n\n'
            '[models.disabled]\nmodel = "kimi-k3.1-pro"\nprovider = "kimi"\n'
            'capabilities = ["thinking", "tool_use"]\n\n'
            '[thinking]\nenabled = false\n', encoding="utf-8")
        marker = self.root / "cli-launched"
        os.environ["K31_FAKE_MARKER"] = str(marker)
        self.addCleanup(os.environ.pop, "K31_FAKE_MARKER", None)
        manager = self.manager()
        with self.assertRaises(BridgeError) as rejected:
            manager.spawn_agent("k31-imported", "disabled", "implementation",
                                "must not run", str(self.root), "disabled")
        self.assertEqual(rejected.exception.code, "invalid_state")
        self.assertIn("thinking", str(rejected.exception).lower())
        self.assertFalse(marker.exists())
        self.assertEqual(manager.list_agents()["agents"], [])


if __name__ == "__main__":
    unittest.main()
