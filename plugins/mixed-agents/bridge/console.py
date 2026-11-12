#!/usr/bin/env python3
"""Loopback-only policy console; stdlib, no credential storage or inference on load."""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socket
import webbrowser

try:
    from .runtime import AgentManager, BridgeError, kimi_usage, zai, catalog
    from . import server, security
    from .version import VERSION
except ImportError:
    from runtime import AgentManager, BridgeError, kimi_usage, zai, catalog
    import server, security
    from version import VERSION

MAX_BODY = 65536
ALLOWED = frozenset({"set_concurrency", "set_kimi_policy", "set_kimi_accounts",
                     "set_zai_policy", "set_cost_policy", "provider_usage", "probe_kimi_usage",
                     "configuration_resources", "list_templates", "get_template", "create_template",
                     "save_template", "archive_template", "register_project", "list_projects",
                     "bind_project_template", "resolve_project_config", "create_provider",
                     "save_provider", "create_model", "save_model", "get_provider_settings"})


def snapshot(manager):
    # Catalog admission views may read remote usage. Configuration loading only
    # inspects local state; quota reads and bootstrap probes have explicit buttons.
    aliases = catalog.load_kimi_models(manager.config, all_aliases=True)
    view = manager._capacity()
    view.update(kimi_policy=manager._kimi_policy_view(include_usage=False),
                zai_policy=manager._zai_policy_view(include_usage=False),
                cost_policy=manager._policy_view(manager._policy(), {m["alias"] for m in aliases}),
                kimi_accounts=manager._kimi_accounts_view(), models=aliases)
    # Do not return observations, saved config, paths, prompts or worker output.
    models = [{key: row.get(key) for key in ("alias", "family", "profile", "routing_default")}
              for row in view["models"]]
    value = {key: view[key] for key in ("kimi_cap", "kimi_running", "kimi_available",
                                       "kimi_unlimited", "kimi_policy", "zai_policy", "cost_policy")}
    supported = set(kimi_usage.aliases(manager.config)) | set(zai.aliases(manager.config))
    value.update(version=VERSION, models=models, kimi_accounts=view.get("kimi_accounts", {}),
                 usage_models=[row for row in models if row["alias"] in supported],
                 resources=manager.configuration_resources(),
                 templates=manager.list_templates()["templates"],
                 projects=manager.list_projects()["projects"],
                 runtime_configuration_enforced=True)
    return security.sanitize(value, security.configured_secrets(manager.config))


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, manager, port):
        self.manager = manager
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass  # Request bodies, tokens and account errors do not enter access logs.

    def reply(self, status, value, content_type="application/json; charset=utf-8"):
        payload = value.encode("utf-8") if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'nonce-" + self.server.token + "'; style-src 'nonce-" + self.server.token + "'; connect-src 'self'; frame-ancestors 'none'; form-action 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def authorized(self, write=False):
        expected = self.server.origin.removeprefix("http://")
        if self.headers.get_all("Host") != [expected]:
            self.reply(403, {"error": "invalid_host"})
            return False
        if write and self.headers.get_all("Origin") != [self.server.origin]:
            self.reply(403, {"error": "invalid_origin"})
            return False
        if self.path != "/" or write:
            if not secrets.compare_digest(self.headers.get("X-Console-Token", ""), self.server.token):
                self.reply(403, {"error": "invalid_token"})
                return False
        return True

    def do_GET(self):
        if not self.authorized():
            return
        if self.path == "/":
            page = Path(__file__).with_name("console.html").read_text(encoding="utf-8")
            self.reply(200, page.replace("__CONSOLE_TOKEN__", self.server.token), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            try:
                self.reply(200, snapshot(self.server.manager))
            except BridgeError as exc:
                value = exc.as_dict()
                value.pop("config_path", None)
                self.reply(400, security.sanitize(value, security.configured_secrets(self.server.manager.config)))
            except ValueError:
                self.reply(400, {"error": "invalid_config", "message": "配置无效，请检查本机配置。"})
        else:
            self.reply(404, {"error": "not_found"})

    def do_POST(self):
        if not self.authorized(write=True):
            return
        if self.path != "/api/call":
            self.reply(404, {"error": "not_found"})
            return
        if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
            self.reply(415, {"error": "json_required"})
            return
        try:
            length_headers = self.headers.get_all("Content-Length")
            if not length_headers or len(length_headers) != 1:
                raise ValueError("Missing body length")
            length = int(length_headers[0])
            if not 0 < length <= MAX_BODY:
                self.reply(413, {"error": "body_too_large"})
                return
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or set(body) != {"method", "arguments"}:
                raise ValueError("Expected method and arguments")
            if not isinstance(body["method"], str) or body["method"] not in ALLOWED:
                raise ValueError("Unsupported operation")
            value = server.invoke(self.server.manager, body["method"], body["arguments"])
            value.pop("config_path", None)
            self.reply(200, value)
        except (ValueError, TypeError, BridgeError) as exc:
            value = exc.as_dict() if isinstance(exc, BridgeError) else {"error": "invalid_argument", "message": str(exc)}
            value.pop("config_path", None)
            self.reply(400, security.sanitize(value, security.configured_secrets(self.server.manager.config)))
        except (OSError, socket.timeout):
            self.reply(400, {"error": "request_failed"})
        except Exception:
            self.reply(500, {"error": "internal_error"})


def make_server(manager=None, port=0):
    return ConsoleServer(manager or AgentManager(), port)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765, help="Loopback port; 0 chooses a free port")
    parser.add_argument("--open", action="store_true", help="Open the local console in a browser")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    try:
        httpd = make_server(port=args.port)
    except OSError:
        parser.exit(1, "Console port unavailable; choose another --port.\n")
    print(httpd.origin, flush=True)
    if args.open:
        webbrowser.open(httpd.origin)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
