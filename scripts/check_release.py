#!/usr/bin/env python3
"""Offline manifest/credential checks and a whitelisted marketplace ZIP.

Findings print locations and categories only, never matched credential values.
This is a bounded-pattern scan, not proof that a tree contains no sensitive data.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/mixed-agents"
sys.path.insert(0, str(PLUGIN / "bridge"))
from version import VERSION
from security import TOKEN_PATTERNS, configured_secrets
from catalog import default_config_path

TOP_LEVEL = {"README.md", "AGENTS.md", "LICENSE", "SECURITY.md", "CHANGELOG.md", ".gitignore"}
FOLDERS = {"plugins", "tests", "scripts", "docs", ".github", ".agents"}
SUFFIXES = {".py", ".md", ".json", ".yml", ".yaml", ".html"}
ASSIGNED = re.compile(r'''(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)\s*["']?\s*[:=]\s*["']([^"'\n]{16,})["']''', re.I)


def run_git(*args):
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, timeout=60)
    return result.stdout if result.returncode == 0 else None


def files():
    git_files = run_git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if git_files is not None:
        return sorted(set(Path(p.decode()) for p in git_files.split(b"\0") if p))
    return sorted(p.relative_to(ROOT) for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts)


def package_file(path):
    return (str(path) in TOP_LEVEL or path.parts[0] in FOLDERS and path.suffix in SUFFIXES) and not any(
        part in {"__pycache__", ".git", ".state", ".validation", "dist"} for part in path.parts)


def issues_in(data, secrets):
    text = data.decode("utf-8", "replace")
    hits = set()
    for number, line in enumerate(text.splitlines(), 1):
        if any(pattern.search(line) for pattern in TOKEN_PATTERNS):
            hits.add((number, "credential_pattern"))
        if any(secret in line for secret in secrets):
            hits.add((number, "configured_credential"))
        for match in ASSIGNED.finditer(line):
            value = match[1].lower()
            if not (value.startswith(("test", "fake", "example", "secret_sentinel", "__", "your_", "placeholder", "private_sentinel")) or
                    "${" in value or set(value) <= {"x", "*"}):
                hits.add((number, "literal_credential_assignment"))
    return sorted(hits)


def check(history=True):
    issues = []
    for name in ("plugin.json", ".codex-plugin/plugin.json", "mcp.json", ".mcp.json"):
        try:
            data = json.loads((PLUGIN / name).read_text())
            if name.endswith("plugin.json") and data.get("version") != VERSION:
                issues.append(dict(path=name, kind="version_mismatch"))
            if name.endswith("mcp.json"):
                entry = data["mcpServers"]["mixed_agents"]
                expected_cwd = "./" if name == "mcp.json" else "."
                if name == "mcp.json" and (data.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json" or entry.get("type") != "stdio" or set(entry) - {"type", "command", "args", "env", "cwd"}):
                    issues.append(dict(path=name, kind="invalid_portable_mcp_fields"))
                if entry.get("command") != "python3" or entry.get("args") != ["./bridge/launch.py"] or entry.get("cwd") != expected_cwd:
                    issues.append(dict(path=name, kind="nonportable_launcher"))
        except (OSError, ValueError, KeyError, TypeError):
            issues.append(dict(path=name, kind="invalid_manifest"))
    secrets = configured_secrets(default_config_path())
    current = files()
    for path in current:
        absolute = ROOT / path
        if absolute.is_symlink():
            issues.append(dict(path=str(path), kind="symlink_not_packaged"))
            continue
        if not absolute.is_file():
            continue
        for line, kind in issues_in(absolute.read_bytes(), secrets):
            issues.append(dict(path=str(path), line=line, kind=kind))
    refs = run_git("rev-list", "--all") if history else None
    if refs is not None:
        for revision in refs.decode().splitlines():
            names = run_git("ls-tree", "-r", "--name-only", "-z", revision)
            if names is None:
                issues.append(dict(revision=revision[:12], kind="history_read_failed"))
                continue
            for name in names.split(b"\0"):
                if not name:
                    continue
                path = name.decode()
                blob = run_git("show", revision + ":" + path)
                if blob is None:
                    issues.append(dict(path=path, revision=revision[:12], kind="history_read_failed"))
                    continue
                for line, kind in issues_in(blob, secrets):
                    issues.append(dict(path=path, revision=revision[:12], line=line, kind=kind))
    return {"ok": not issues, "version": VERSION, "findings": issues,
            "history": "checked" if refs is not None else "not_checked", "files_checked": len(current)}


def build(path):
    paths = [p for p in files() if package_file(p) and (ROOT / p).is_file()]
    # License and marketplace metadata must travel with the plugin.
    for required in ("LICENSE", "README.md", ".agents/plugins/marketplace.json"):
        if Path(required) not in paths:
            raise ValueError("Missing release file: " + required)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in paths:
            if (ROOT / source).is_symlink():
                raise ValueError("Symlinks cannot be included")
            entry = zipfile.ZipInfo(source.as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, (ROOT / source).read_bytes())
    return {"archive": str(path), "files": len(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-history", action="store_true", help="Tree-only check; reports history not checked")
    parser.add_argument("--output", help="Create a marketplace ZIP only after checks pass")
    args = parser.parse_args()
    result = check(history=not args.no_history)
    if result["ok"] and args.output:
        result.update(build(args.output))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
