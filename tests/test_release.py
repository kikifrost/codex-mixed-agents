"""Release-checker regressions for the shareable beta (0.2.0).

Tests derive contracts from SHARE_SPEC.md and scripts/check_release.py.
They use a temporary tree and monkeypatch check_release.ROOT/PLUGIN so no
production credentials or Git history are scanned.
"""
from __future__ import annotations

import io
import json
import os
import secrets as _secrets
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/mixed-agents/bridge"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_release  # noqa: E402
from version import VERSION  # noqa: E402


def _make_secret() -> str:
    """Runtime-only token-shaped string; never committed literally."""
    return _secrets.token_urlsafe(32)


def _no_leak(obj, secret: str, msg="secret value leaked"):
    text = json.dumps(obj, default=str)
    assert secret not in text, msg


class ReleaseCheckerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name)
        self.plugin = self.tree / "plugins" / "mixed-agents"
        self.plugin.mkdir(parents=True)
        (self.tree / ".agents" / "plugins").mkdir(parents=True)
        # Redirect the release script away from the real repo.
        self._orig_root = check_release.ROOT
        self._orig_plugin = check_release.PLUGIN
        check_release.ROOT = self.tree
        check_release.PLUGIN = self.plugin
        self._orig_home = os.environ.get("KIMI_CODE_HOME")
        os.environ["KIMI_CODE_HOME"] = str(self.tree)
        self.addCleanup(self._restore)

    def _restore(self):
        check_release.ROOT = self._orig_root
        check_release.PLUGIN = self._orig_plugin
        if self._orig_home is None:
            os.environ.pop("KIMI_CODE_HOME", None)
        else:
            os.environ["KIMI_CODE_HOME"] = self._orig_home

    def _write(self, path: Path, content: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def _write_bytes(self, path: Path, content: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _write_valid_manifests(self):
        self._write(self.plugin / "plugin.json",
                    json.dumps({"name": "mixed-agents", "version": VERSION}))
        self._write(self.plugin / ".codex-plugin/plugin.json",
                    json.dumps({"name": "mixed-agents", "version": VERSION}))
        # Portable root MCP manifest: no native timeout keys, cwd must be "./".
        self._write(self.plugin / "mcp.json", json.dumps({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
            "mcpServers": {"mixed_agents": {
                "type": "stdio", "command": "python3",
                "args": ["./bridge/launch.py"],
                "cwd": "./"
            }}
        }))
        # Native .mcp.json keeps Codex-native timeouts and cwd ".".
        self._write(self.plugin / ".mcp.json", json.dumps({
            "mcpServers": {"mixed_agents": {
                "type": "stdio", "command": "python3",
                "args": ["./bridge/launch.py"],
                "cwd": ".",
                "startup_timeout_sec": 10, "tool_timeout_sec": 65
            }}
        }))

    def _write_required_release_files(self):
        self._write(self.tree / "LICENSE", "MIT")
        self._write(self.tree / "README.md", "# Mixed Agents")
        self._write(self.tree / ".agents" / "plugins" / "marketplace.json",
                    json.dumps({"name": "kai-mixed-agents", "plugins": []}))

    def _write_allowed_tree(self):
        self._write(self.plugin / "bridge" / "runtime.py", "# runtime")
        self._write(self.tree / "tests" / "test_runtime.py", "# tests")
        self._write(self.tree / "scripts" / "check_release.py", "# script")
        self._write(self.tree / "docs" / "SPEC.md", "# spec")
        self._write(self.tree / ".github" / "workflows" / "ci.yml", "# ci")
        self._write(self.tree / ".agents" / "skills" / "x" / "SKILL.md", "# skill")

    def _write_disallowed_tree(self):
        self._write(self.tree / ".state" / "config.json", _make_secret())
        self._write(self.tree / ".git" / "config", "[core]")
        self._write_bytes(self.tree / "__pycache__" / "x.pyc", b"pyc")
        self._write_bytes(self.tree / "dist" / "x.whl", b"whl")
        self._write(self.tree / ".validation" / "log", "log")
        # safe values so the release check itself stays focused on packaging.
        self._write(self.plugin / "auth.toml", 'api_key = "fake_example_key"')
        self._write(self.tree / "tests" / "credentials.env", 'TOKEN=fake_example_token')

    def test_secret_findings_are_value_free_and_detect_patterns(self):
        self._write_valid_manifests()
        self._write_required_release_files()
        (self.plugin / "leak.py").write_text(
            "sk_key = \"%s\"\nghp_pat = \"%s\"\n" % (
                "sk-" + _secrets.token_urlsafe(32),
                "ghp_" + _secrets.token_urlsafe(36),
            )
        )
        secret_a = _make_secret()
        (self.tree / "config.toml").write_text('[providers.x]\napi_key = "%s"\n' % secret_a)
        result = check_release.check(history=False)
        self.assertFalse(result["ok"])
        kinds = {f["kind"] for f in result["findings"]}
        self.assertTrue(kinds & {"credential_pattern", "literal_credential_assignment", "configured_credential"})
        _no_leak(result, secret_a)
        _no_leak(result, "sk-")
        _no_leak(result, "ghp_")

    def test_git_history_detects_secret_without_leaking_value(self):
        self._write_valid_manifests()
        self._write_required_release_files()
        secret = _make_secret()
        (self.plugin / "historic.py").write_text('api_key = "%s"\n' % secret)
        subprocess.run(["git", "init", "-q"], cwd=self.tree, check=True)
        subprocess.run(["git", "-C", str(self.tree), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.tree), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(self.tree), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.tree), "commit", "-q", "-m", "init"], check=True)
        # Remove secret from the working tree before scanning current files.
        (self.plugin / "historic.py").write_text("# clean\n")
        result = check_release.check(history=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["history"], "checked")
        revisions = {f.get("revision") for f in result["findings"] if "revision" in f}
        self.assertTrue(revisions)
        _no_leak(result, secret)

    def test_zip_whitelist_excludes_state_auth_and_unrelated(self):
        self._write_valid_manifests()
        self._write_required_release_files()
        self._write_allowed_tree()
        self._write_disallowed_tree()
        result = check_release.check(history=False)
        self.assertTrue(result["ok"], result["findings"])
        archive = self.tree / "release.zip"
        result.update(check_release.build(archive))
        self.assertEqual(result["archive"], str(archive))
        with zipfile.ZipFile(archive, "r") as zf:
            names = set(zf.namelist())
        self.assertIn("LICENSE", names)
        self.assertIn("README.md", names)
        self.assertIn(".agents/plugins/marketplace.json", names)
        self.assertIn("plugins/mixed-agents/bridge/runtime.py", names)
        self.assertIn("tests/test_runtime.py", names)
        self.assertNotIn(".state/config.json", names)
        self.assertNotIn(".git/config", names)
        self.assertNotIn("__pycache__/x.pyc", names)
        self.assertNotIn("dist/x.whl", names)
        self.assertNotIn(".validation/log", names)
        self.assertNotIn("plugins/mixed-agents/auth.toml", names)
        self.assertNotIn("tests/credentials.env", names)

    def test_symlink_blocks_packaging(self):
        self._write_valid_manifests()
        self._write_required_release_files()
        target = self.plugin / "bridge" / "runtime.py"
        self._write(target, "# runtime")
        link = self.plugin / "bridge" / "symlink.py"
        link.symlink_to(target)
        result = check_release.check(history=False)
        self.assertFalse(result["ok"])
        self.assertTrue(any(f["kind"] == "symlink_not_packaged" for f in result["findings"]))
        with self.assertRaises(ValueError):
            check_release.build(self.tree / "bad.zip")

    def test_portable_mcp_rejects_unknown_fields_and_bad_cwd(self):
        # Portable root mcp.json must not carry native timeout keys and cwd must be "./".
        self._write(self.plugin / "mcp.json", json.dumps({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
            "mcpServers": {"mixed_agents": {
                "type": "stdio", "command": "python3",
                "args": ["./bridge/launch.py"],
                "cwd": ".",
                "startup_timeout_sec": 10, "tool_timeout_sec": 65
            }}
        }))
        result = check_release.check(history=False)
        self.assertFalse(result["ok"])
        kinds = {f["kind"] for f in result["findings"]}
        self.assertIn("invalid_portable_mcp_fields", kinds)
        self.assertIn("nonportable_launcher", kinds)

        # Canonical portable manifest passes.
        self._write_valid_manifests()
        result = check_release.check(history=False)
        self.assertTrue(result["ok"], result["findings"])

    def test_version_mismatch_blocks_packaging_via_main(self):
        bad_version = "0.0.0-test"
        self._write(self.plugin / "plugin.json",
                    json.dumps({"name": "mixed-agents", "version": bad_version}))
        self._write(self.plugin / ".codex-plugin/plugin.json",
                    json.dumps({"name": "mixed-agents", "version": bad_version}))
        self._write(self.plugin / "mcp.json", json.dumps({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
            "mcpServers": {"mixed_agents": {
                "type": "stdio", "command": "python3",
                "args": ["./bridge/launch.py"],
                "cwd": "./"
            }}
        }))
        self._write(self.plugin / ".mcp.json", json.dumps({
            "mcpServers": {"mixed_agents": {
                "type": "stdio", "command": "python3",
                "args": ["./bridge/launch.py"],
                "cwd": ".",
                "startup_timeout_sec": 10, "tool_timeout_sec": 65
            }}
        }))
        archive = self.tree / "should_not_exist.zip"
        argv = ["check_release.py", "--output", str(archive)]
        stdout = io.StringIO()
        with patch("sys.argv", argv), patch("sys.stdout", stdout):
            rc = check_release.main()
        self.assertNotEqual(rc, 0)
        self.assertFalse(archive.exists())
        output = stdout.getvalue()
        self.assertIn("version_mismatch", output)
        self.assertIn('"ok": false', output)


if __name__ == "__main__":
    unittest.main()
