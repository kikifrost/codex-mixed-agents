"""Native plugin-detail action backed by the existing local console."""
from __future__ import annotations

import threading
import webbrowser

CAPABILITY = {"readTool": "read_plugin_settings", "updateTool": "update_plugin_settings"}
READ_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"schema": {"type": "object"}, "values": {"type": "object"},
                   "layout": {"type": "array", "items": {"type": "object"}}},
    "required": ["schema", "values"],
}
_lock = threading.Lock()


def read_settings(manager):
    return {"schema": {"type": "object", "properties": {}}, "values": {},
            "layout": [{"kind": "group", "title": "Mixed Agents 配置",
                        "items": [{"kind": "tool", "tool": "open_console",
                                   "description": "在本机浏览器中管理模板、Project、Provider 和模型。"}]}]}


def update_settings(manager, set):
    if not isinstance(set, dict) or set:
        try:
            from .runtime import BridgeError
        except ImportError:
            from runtime import BridgeError
        raise BridgeError("invalid_argument", "请在配置控制台中修改设置。")
    return {"values": {}}


def stop_console(manager):
    """Release sockets and the HTTP thread when this MCP process stops."""
    with _lock:
        running = getattr(manager, "_plugin_console", None)
        if running is None:
            return
        httpd, thread = running
        manager._plugin_console = None
        if thread.is_alive():
            httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


def open_console(manager):
    # Import lazily: the HTTP module itself imports the MCP dispatcher.
    try:
        from .console import make_server
        from .runtime import BridgeError
    except ImportError:
        from console import make_server
        from runtime import BridgeError
    with _lock:
        running = getattr(manager, "_plugin_console", None)
        if running is not None and not running[1].is_alive():
            running[0].server_close()
            running = None
            manager._plugin_console = None
        if running is None:
            httpd = None
            try:
                httpd = make_server(manager=manager, port=0)
                thread = threading.Thread(target=httpd.serve_forever,
                                          name="mixed-agents-console", daemon=True)
                thread.start()
            except Exception as exc:
                if httpd is not None:
                    httpd.server_close()
                raise BridgeError("console_start_failed", "控制台启动失败，请重试。") from exc
            running = (httpd, thread)
            manager._plugin_console = running
        url = running[0].origin
    try:
        opened = bool(webbrowser.open(url, new=2))
    except Exception:
        opened = False
    message = ("已请求在本机浏览器打开配置控制台。" if opened else
               "控制台已启动，浏览器未能自动打开，请手动打开链接。")
    return {"url": url, "browser_opened": opened, "message": message}


TOOLS = {"read_plugin_settings": read_settings, "update_plugin_settings": update_settings,
         "open_console": open_console}


def tool_schema(name):
    updating = name == "update_plugin_settings"
    schema = {"name": name, "title": "打开配置控制台" if name == "open_console" else
              "读取插件设置" if name == "read_plugin_settings" else "更新插件设置",
              "description": "Open the local configuration console in the desktop browser; no inference."
              if name == "open_console" else "Native plugin detail settings integration.",
              "annotations": {"readOnlyHint": name == "read_plugin_settings",
                              "destructiveHint": False, "openWorldHint": False},
              "inputSchema": {"type": "object", "additionalProperties": False,
                              "properties": {"set": {"type": "object", "properties": {},
                                                     "additionalProperties": False}} if updating else {},
                              "required": ["set"] if updating else []}}
    if name == "read_plugin_settings":
        schema["outputSchema"] = READ_OUTPUT_SCHEMA
    elif updating:
        schema["outputSchema"] = {"type": "object", "properties": {"values": {"type": "object"}},
                                  "required": ["values"]}
    return schema
