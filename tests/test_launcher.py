"""GPT-authored offline regression checks for the Kimi-authored launcher."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "mixed-agents"
LAUNCHER = PLUGIN / "bridge" / "launch.py"
sys.path.insert(0, str(PLUGIN / "bridge"))
from version import VERSION


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cwd = self.root / "unrelated"
        self.cwd.mkdir()
        config = self.root / "kimi"
        config.mkdir()
        (config / "config.toml").write_text("[models]\n", encoding="utf-8")
        self.env = dict(os.environ, KIMI_CODE_HOME=str(config),
                        MIXED_AGENTS_STATE_DIR=str(self.root / "state"))
        self.env.pop("PYTHONPATH", None)

    def run_python(self, args, input=None):
        return subprocess.run([sys.executable, *args], cwd=self.cwd, env=self.env,
                              input=input, capture_output=True, text=True, timeout=15)

    def assert_guard(self, override, expected):
        # Block runtime imports even if someone moves them ahead of the guard.
        code = f'''
import builtins, collections, runpy, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name in {{"server", "runtime", "fcntl"}} or name.endswith(".server"):
        raise AssertionError("runtime imported before dependency check")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
{override}
runpy.run_path({str(LAUNCHER)!r}, run_name="__main__")
'''
        result = self.run_python(["-c", code])
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(expected, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_old_python_fails_before_runtime_import(self):
        self.assert_guard(
            'sys.version_info = collections.namedtuple("Version", "major minor micro releaselevel serial")(3, 10, 9, "final", 0)',
            "Python 3.11+ required, found 3.10")

    def test_unsupported_os_fails_before_runtime_import(self):
        self.assert_guard('sys.platform = "win32"', "macOS or Linux required, found win32")

    def test_import_is_safe_without_starting_server(self):
        code = f'''
import importlib.util, sys
spec = importlib.util.spec_from_file_location("portable_launcher", {str(LAUNCHER)!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert callable(module.main)
assert "server" not in sys.modules and "runtime" not in sys.modules
'''
        result = self.run_python(["-c", code])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertFalse((self.root / "state").exists())

    def test_imported_main_starts_from_unrelated_directory(self):
        code = f'''
import importlib.util, sys
spec = importlib.util.spec_from_file_location("portable_launcher", {str(LAUNCHER)!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.argv = [{str(LAUNCHER)!r}, "call", "list_agents", "{{}}"]
raise SystemExit(module.main())
'''
        result = self.run_python(["-c", code])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["agents"], [])

    def test_script_starts_from_unrelated_directory(self):
        result = self.run_python([str(LAUNCHER), "call", "list_agents", "{}"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["agents"], [])

    def test_relocated_package_stdio_handshake(self):
        relocated = self.root / "relocated package"
        shutil.copytree(PLUGIN, relocated, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        result = self.run_python([str(relocated / "bridge" / "launch.py")],
                                 input=json.dumps(request) + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        info = json.loads(result.stdout)["result"]["serverInfo"]
        self.assertEqual(info, {"name": "mixed-agents", "version": VERSION})


if __name__ == "__main__":
    unittest.main()
