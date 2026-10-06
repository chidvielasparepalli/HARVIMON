"""
JARVIS wireless phone control plugin — control an Android device over WiFi
via the JARVIS Companion app. No USB cable or USB debugging required.

The companion app runs an HTTP server on the phone (default port 8795).
This plugin sends JSON commands over HTTP using only stdlib (urllib.request).

Both devices must be on the same Tailscale network (or same LAN).
Configure the phone's Tailscale IP and auth token in plugin settings.
"""

import base64
import json
import urllib.request
import urllib.error
from memory.config_manager import get_plugin_setting, save_plugin_config

_PLUGIN_NAME = "wireless_control"
_DEFAULT_PORT = 8795

PLUGIN = {  
    "name": _PLUGIN_NAME,
    "description": (
        "Control an Android phone wirelessly over WiFi: tap at screen "
        "coordinates, swipe, type text, press hardware keys, launch apps, "
        "or capture the screen to a PNG. Requires the JARVIS Companion app "
        "installed and running on the phone (no USB cable needed). Use for "
        "phone actions; NOT for sending WhatsApp/Telegram messages (that is "
        "send_message)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "What to do: 'devices' (check phone connection), "
                    "'open' (launch app by package name in 'value'; "
                    "com.whatsapp=WhatsApp, com.android.chrome=Chrome, "
                    "com.instagram.android=Instagram), 'tap' (x y), "
                    "'swipe' (x1 y1 x2 y2 duration_ms), 'text' (string "
                    "in 'value'), 'keyevent' (event id, e.g. 3=home "
                    "4=back 26=power 66=enter 67=backspace, 24/25=volume), "
                    "'screencap' (save screen PNG to path in 'value'), "
                    "'back', 'home', or 'notifications'."
                ),
            },
            "x": {"type": "NUMBER", "description": "First/only x coordinate (tap = tap point)."},
            "y": {"type": "NUMBER", "description": "First/only y coordinate."},
            "x2": {"type": "NUMBER", "description": "Swipe end x."},
            "y2": {"type": "NUMBER", "description": "Swipe end y."},
            "duration": {"type": "NUMBER", "description": "Swipe duration in ms (default 300)."},
            "keyevent": {"type": "NUMBER", "description": "Hardware key event id (action 'keyevent')."},
            "value": {"type": "STRING", "description": "Text to type (action 'text') or output PNG path (action 'screencap')."},
        },
        "required": ["action"],
    },
}

PLUGIN_SETTINGS = {
    "namespace": _PLUGIN_NAME,
    "title": "Wireless Phone Control",
    "fields": [
        {"key": "phone_ip", "label": "Phone IP Address (Tailscale)", "type": "text",
         "placeholder": "100.x.x.x or 192.168.1.x"},
        {"key": "phone_token", "label": "Auth Token", "type": "text",
         "placeholder": "shown on phone companion app"},
        {"key": "port", "label": "Port", "type": "number", "default": _DEFAULT_PORT},
    ],
}


def _phone_url(path: str) -> str:
    ip = get_plugin_setting(_PLUGIN_NAME, "phone_ip", "")
    port = get_plugin_setting(_PLUGIN_NAME, "port", _DEFAULT_PORT)
    return f"http://{ip}:{port}/{path}"


def _phone_token() -> str:
    return get_plugin_setting(_PLUGIN_NAME, "phone_token", "")


def _post(path: str, body: dict | None = None) -> dict:
    """POST JSON to the phone app. Returns parsed response. Raises on error."""
    token = _phone_token()
    url = _phone_url(path)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _get(path: str) -> dict:
    """GET from the phone app. Returns parsed response. Raises on error."""
    token = _phone_token()
    url = _phone_url(path)
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        action = (parameters.get("action") or "").lower().strip()
        if not action:
            return "Sir, I need an 'action' (devices, tap, swipe, text, keyevent, screencap, back, home)."

        known = {"devices", "open", "tap", "swipe", "text", "keyevent",
                 "screencap", "back", "home", "notifications"}
        if action not in known:
            return f"Unknown action '{action}'. Use devices, tap, swipe, text, keyevent, screencap, back, home, or notifications."

        # Validate args up front.
        if action in ("tap",) and ("x" not in parameters or "y" not in parameters):
            return "Sir, 'tap' needs 'x' and 'y' coordinates."
        if action == "swipe":
            if "x" not in parameters or "y" not in parameters:
                return "Sir, 'swipe' needs start 'x' and 'y'."
            if "x2" not in parameters or "y2" not in parameters:
                return "Sir, 'swipe' needs end 'x2' and 'y2'."
        if action == "open" and not parameters.get("value"):
            return "Sir, 'open' needs an app package in 'value'."
        if action == "text" and not parameters.get("value"):
            return "Sir, 'text' needs a 'value' to type."
        if action == "screencap" and not parameters.get("value"):
            return "Sir, 'screencap' needs an output PNG path in 'value'."
        if action == "keyevent" and "keyevent" not in parameters:
            return "Sir, 'keyevent' needs a key id in 'keyevent'."

        # Check phone connection.
        if action == "devices":
            try:
                info = _get("/ping")
                version = info.get("version", "?")
                screen = "screen capture on" if info.get("screen") else "screen capture off"
                return f"Phone connected (v{version}, {screen})."
            except Exception as e:
                return f"Sir, phone not reachable: {e}"

        if action == "open":
            _post("/launch", {"package": parameters["value"]})
            return f"Opened app {parameters['value']}."

        if action == "tap":
            _post("/tap", {"x": int(parameters["x"]), "y": int(parameters["y"])})
            return "Tapped the screen."

        if action == "swipe":
            dur = int(parameters.get("duration") or 300)
            _post("/swipe", {
                "x1": int(parameters["x"]), "y1": int(parameters["y"]),
                "x2": int(parameters["x2"]), "y2": int(parameters["y2"]),
                "duration": dur,
            })
            return "Swiped."

        if action == "keyevent":
            _post("/keyevent", {"keycode": int(parameters["keyevent"])})
            return "Key pressed."

        if action == "text":
            _post("/type", {"text": parameters["value"]})
            return f"Typed: {parameters['value']}"

        if action == "screencap":
            resp = _post("/screenshot", {"quality": 80})
            png_b64 = resp.get("png", "")
            if not png_b64:
                return "Sir, phone returned empty screenshot."
            path = parameters["value"]
            with open(path, "wb") as f:
                f.write(base64.b64decode(png_b64))
            return f"Screen saved to {path}."

        if action == "back":
            _post("/back")
            return "Back pressed."

        if action == "home":
            _post("/home")
            return "Home pressed."

        if action == "notifications":
            resp = _post("/notifications")
            notifs = resp.get("notifications", [])
            if not notifs:
                return "No notifications."
            lines = [f"- {n.get('title', '?')}: {n.get('text', '')}" for n in notifs[:10]]
            return f"{len(notifs)} notification(s):\n" + "\n".join(lines)

        return "Done."
    except urllib.error.URLError as e:
        return f"Sir, cannot reach phone: {e}. Check IP and token in plugin settings."
    except Exception as e:
        return f"Sir, wireless_control failed: {e}"


if __name__ == "__main__":
    import sys as _sys
    # `python -m` creates two module objects: the import and __main__.
    # run() and helpers live in __main__; patch that dict directly.
    _g = vars(_sys.modules["__main__"])

    # ── Bad-request guards: no phone needed. ──
    for case, expect in [
        ({}, "I need an 'action'"),
        ({"action": "nonsense"}, "Unknown action"),
        ({"action": "open"}, "needs an app package"),
        ({"action": "text"}, "needs a 'value'"),
        ({"action": "screencap"}, "needs an output PNG path"),
        ({"action": "keyevent"}, "needs a key id"),
        ({"action": "tap"}, "needs 'x' and 'y'"),
        ({"action": "swipe"}, "needs start"),
        ({"action": "swipe", "x": 0, "y": 0}, "needs end"),
    ]:
        out = run(case)
        assert expect in out, f"FAIL {case}: {out}"

    # ── Mock HTTP + config layer for success branches. ──
    _calls: list[tuple[str, dict | None]] = []

    def _mock_post(path: str, body: dict | None = None) -> dict:
        _calls.append((path, body))
        if path == "/screenshot":
            import struct, zlib
            sig = b"\x89PNG\r\n\x1a\n"
            ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
            ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
            raw = b"\x00\xff\x00\x00"
            compressed = zlib.compress(raw)
            idat_crc = zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF
            idat = struct.pack(">I", len(compressed)) + b"IDAT" + compressed + struct.pack(">I", idat_crc)
            iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
            iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
            png = base64.b64encode(sig + ihdr + idat + iend).decode()
            return {"png": png, "width": 1, "height": 1}
        if path == "/notifications":
            return {"notifications": [{"title": "Test", "text": "Hello"}]}
        return {"ok": True}

    def _mock_get(path: str) -> dict:
        _calls.append((path, None))
        if path == "/ping":
            return {"ok": True, "version": "1.0", "screen": True}
        return {"ok": True}

    def _mock_setting(ns: str, key: str, default=None):
        return {"phone_ip": "10.0.0.1", "phone_token": "test123", "port": 8795}.get(key, default)

    # Monkey-patch __main__ globals dict directly (run() resolves names from here).
    _real = {k: _g[k] for k in ("_post", "_get", "get_plugin_setting")}
    _g["_post"] = _mock_post
    _g["_get"] = _mock_get
    _g["get_plugin_setting"] = _mock_setting
    # DEBUG
    print("DEBUG _g is run globals?", run.__globals__ is _g)
    print("DEBUG _post in _g?", "_post" in _g, "_get" in _g, "get_plugin_setting" in _g)

    success_cases = [
        ({"action": "devices"}, "Phone connected"),
        ({"action": "open", "value": "com.whatsapp"}, "Opened app"),
        ({"action": "tap", "x": 100, "y": 200}, "Tapped"),
        ({"action": "swipe", "x": 0, "y": 0, "x2": 500, "y2": 500}, "Swiped"),
        ({"action": "keyevent", "keyevent": 3}, "Key pressed"),
        ({"action": "text", "value": "hello"}, "Typed: hello"),
        ({"action": "screencap", "value": "_test_screen.png"}, "Screen saved"),
        ({"action": "back"}, "Back pressed"),
        ({"action": "home"}, "Home pressed"),
        ({"action": "notifications"}, "notification"),
    ]
    for case, expect in success_cases:
        _calls.clear()
        out = run(case)
        assert expect in out, f"FAIL {case}: {out}"

    # Verify dispatch paths hit the right endpoints.
    assert any(p == "/ping" for p, _ in _calls), "/ping never called"
    assert any(p == "/tap" for p, _ in _calls), "/tap never called"
    assert any(p == "/swipe" for p, _ in _calls), "/swipe never called"
    assert any(p == "/screenshot" for p, _ in _calls), "/screenshot never called"

    # Restore.
    _g.update(_real)

    # Clean up test file.
    import os
    try:
        os.remove("_test_screen.png")
    except OSError:
        pass

    print("self-check OK")
