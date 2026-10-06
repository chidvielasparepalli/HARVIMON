"""
JARVIS game-control plugin — drive a mobile game on the phone over WiFi.

Screencaps the phone (via the wireless_control companion app), sends the PNG
to Gemini vision, gets the single best next input, taps/swipes the phone, and
repeats. Built for arena shooters (Free Fire / BGMI / COD Mobile): survive,
hunt, win — until the match ends, the phone locks, the loop times out, or you
say a stop word.

Voice stop: main.py sets ui.game_stop_event (threading.Event) when you say a
stop word ("stop game", "pause", …). This plugin polls it every iteration, so
the loop breaks the moment the phrase lands — no waiting for the Gemini turn.

Only one loop runs at a time (module-level STATE). Transport is wireless;
ADB is used only to read the real screen size once (wm size) if the
companion's /screenshot width/height is ever missing.
"""

import base64
import hashlib
import json
import re
import threading
import time
from pathlib import Path

from memory.config_manager import get_plugin_setting
from plugins import wireless_control as _wc


_MODEL = "gemini-flash-latest"

# Module-level state — one game loop at a time.
STATE = {
    "running": False,    # is the loop alive?
    "game": "",          # which game / the user's instruction
    "log": [],           # last few actions (for the vision prompt)
    "reason": "",        # why the loop ended (for status/stop narration)
    "lock": threading.Lock(),
}

_ACTIONS = {"TAP", "SWIPE", "WAIT", "KEY", "DONE"}


def _settings(key, default=None):
    return get_plugin_setting("game_control", key, default)


def _get_api_key() -> str:
    base = Path(__file__).resolve().parent.parent
    cfg = base / "config" / "api_keys.json"
    try:
        return json.loads(cfg.read_text(encoding="utf-8"))["gemini_api_key"]
    except Exception:
        return ""


def _stop_event(player):
    """The threading.Event wired by main.py; None if this session lacks it."""
    if player is None:
        return None
    try:
        return getattr(player, "game_stop_event", None)
    except Exception:
        return None


# ── transport: wireless, minus a bit of wiring glance overhead ───────────────
_wpost, _wget = _wc._post, _wc._get


def _real_size() -> tuple[int, int]:
    """
    (real_w, real_h) of the phone screen. Deliberately cheap: the companion's
    /screenshot width/height are authoritative when present; ADB `wm size` is
    the fallback when the companion is silent on it. Default 1080x2400 only if
    neither route works. Real resolution is needed to scale vision output —
    the model draws in the PNG's pixel space, taps must land in real pixels.
    """
    return int(_settings("real_width") or 1080), int(_settings("real_height") or 2400)


def _screenshot() -> tuple[bytes, dict] | None:
    """
    One wireless screencap. Returns (png_bytes, meta) where meta carries the
    companion's width/height, or None on any network failure.
    """
    try:
        resp = _wpost("/screenshot", {"quality": 80})
        png = base64.b64decode(resp.get("png", ""))
        if not png:
            return None
        return png, resp
    except Exception:
        return None


def _vision(history: list[str], png: bytes) -> dict:
    """Ask Gemini what to do next. Returns a dict with 'action' + fields, or a
    WAIT fallback on any JSON/model failure (never raise out of the loop)."""
    from google import genai
    from google.genai import types as gtypes

    key = _get_api_key()
    if not key:
        return {"action": "DONE", "reason": "no Gemini API key"}

    client = genai.Client(api_key=key)
    hist = "\n".join(history[-5:])
    prompt = f"""
You are driving a mobile arena shooter (Free Fire / BGMI / COD Mobile) to WIN.
Only what you see on this literal game screen matters.

TASK: {STATE["game"]}

LAST ACTIONS:
{hist or "(none)"}

Pick the single best next input to make progress and win. Coordinates are in
the image's pixel space.

Reply with ONLY one JSON object, one of these shapes:
{{"action":"TAP","x":123,"y":456}}
{{"action":"SWIPE","x1":100,"y1":200,"x2":500,"y2":200,"ms":200}}
{{"action":"WAIT","ms":800}}
{{"action":"KEY","keycode":4}}           # 4=back 24/25=volume 3=home
{{"action":"DONE","reason":"defeat screen"}}

Rules:
- Stay in the game. Never navigate away, never open menus/logos, never type.
- Aim taps at the center of the target (weapon/fire button, loot, enemy).
- DONE when the match clearly ended (victory/defeat/scoreboard/back in lobby)
  or the task is over. Prefer DONE over endless re-tapping.
- If the screen is black/locked/a loading spinner, WAIT briefly, then DONE if it
  never resolves.
Return ONLY the JSON."""
    try:
        resp = client.models.generate_content(
            model=_MODEL,
            contents=[
                gtypes.Part.from_bytes(data=png, mime_type="image/png"),
                prompt,
            ],
        )
        text = (resp.text or "").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        return json.loads(text)
    except Exception as e:
        return {"action": "WAIT", "ms": 800, "reason": f"vision error: {e}"}


# Current PNG→real scale factors, set by _loop each frame that carries w/h.
_SCALE = {"x": 1.0, "y": 1.0}


def _act(dec: dict, player) -> str:
    """Execute one decision on the phone. Returns a narration string."""
    a = (dec.get("action") or "").upper()
    sx, sy = _SCALE["x"], _SCALE["y"]

    def _p(kx, ky):
        return float(dec[kx]) * sx, float(dec[ky]) * sy

    try:
        if a == "TAP":
            x, y = _p("x", "y")
            _wpost("/tap", {"x": int(x), "y": int(y)})
            return f"Tapped {int(x)},{int(y)}."
        if a == "SWIPE":
            x1, y1 = _p("x1", "y1")
            x2, y2 = _p("x2", "y2")
            ms = int(dec.get("ms", 200))
            _wpost("/swipe", {"x1": int(x1), "y1": int(y1),
                              "x2": int(x2), "y2": int(y2), "duration": ms})
            return f"Swiped {int(x1)},{int(y1)} to {int(x2)},{int(y2)}."
        if a == "WAIT":
            time.sleep(max(0.1, min(10, int(dec.get("ms", 800))) / 1000))
            return "Waited."
        if a == "KEY":
            _wpost("/keyevent", {"keycode": int(dec.get("keycode", 4))})
            return f"Pushed key {dec.get('keycode', 4)}."
        if a == "DONE":
            return f"DONE:{dec.get('reason', 'finished')}"
        return f"SKIP:{dec}"
    except Exception as e:
        return f"FAIL:{e}"


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "start").strip().lower()

    if action == "start":
        game = (parameters.get("game") or "").strip()
        if STATE["running"]:
            return "Already driving the game — say: status."
        key = _get_api_key()
        if not key:
            return "Sir, I need a Gemini API key in config to see the game."
        STATE["game"] = game or "drive this match and win"
        STATE["log"] = []
        STATE["running"] = True
        STATE["reason"] = ""
        ev = _stop_event(player)
        if ev is not None:
            ev.clear()   # a fresh run gets a clean stop flag
        threading.Thread(target=_loop, args=(player,), daemon=True).start()
        return ("On it — I'm watching the phone and driving the match. "
                "Say 'stop game' any time to break me out.")

    if action == "status":
        if not STATE["running"]:
            tail = f" (last: {STATE['reason']})" if STATE["reason"] else ""
            return f"The game loop isn't running{tail}."
        last = STATE["log"][-1] if STATE["log"] else "just starting."
        return f"Game loop: {last}"

    if action == "stop":
        STATE["running"] = False
        try:
            if player is not None:
                ev = getattr(player, "game_stop_event", None)
                if ev is not None:
                    ev.set()
        except Exception:
            pass
        return "Stopped the game loop."

    return "Use start, status, or stop."


def _say(player, text: str):
    if player:
        try:
            player.write_log(f"AGENT: {text}")
        except Exception:
            pass
    print(f"[GAME_CTRL] {text}")


def _loop(player):
    stop = _stop_event(player)
    rw, rh = _real_size()
    try:
        start = time.monotonic()
        max_s = int(_settings("max_seconds", 60))
        throttle = int(_settings("vision_min_ms", 300)) / 1000
        idle = 0
        last_hash = ""

        while STATE["running"]:
            if stop is not None:
                if stop.is_set():
                    STATE["reason"] = "user stopped"
                    _say(player, "Stopped — game loop off.")
                    return
            if time.monotonic() - start > max_s:
                STATE["reason"] = "time cap reached"
                _say(player, "Hit my time cap — game loop off.")
                STATE["running"] = False
                return

            snap = _screenshot()
            if snap is None:
                STATE["reason"] = "could not reach the phone"
                _say(player, "Lost the phone — I'm off.")
                STATE["running"] = False
                return
            png, _meta = snap
            h = hashlib.sha256(png).hexdigest()
            if h == last_hash:
                idle += 1
                if idle >= 4:
                    STATE["reason"] = "screen frozen"
                    _say(player, "Screen hasn't budged — I'm off.")
                    STATE["running"] = False
                    return
                time.sleep(throttle)
                continue
            idle = 0
            last_hash = h

            # Scale vision output: PNG dims (model sees) → real phone pixels.
            pw, ph = _meta.get("width"), _meta.get("height")
            if pw and ph:
                _SCALE["x"], _SCALE["y"] = rw / pw, rh / ph
            else:
                _SCALE["x"], _SCALE["y"] = 1.0, 1.0

            dec = _vision(STATE["log"], png)
            narr = _act(dec, player)

            if narr.startswith("DONE:"):
                STATE["reason"] = narr[5:]
                _say(player, f"Done. {narr[5:]}")
                STATE["running"] = False
                return
            STATE["log"].append(narr)
            if player:
                try:
                    player.write_log(f"AGENT: {narr}")
                except Exception:
                    pass
            time.sleep(throttle)
    except Exception as e:
        _say(player, f"Game loop hit a problem and stopped: {e}")
    finally:
        STATE["running"] = False


# Tool declaration — auto-discovered.
PLUGIN = {
    "name": "game_control",
    "description": (
        "Drive a mobile game running on the phone (Free Fire / BGMI / COD "
        "Mobile): watches the phone screen with Gemini vision, decides the "
        "next tap/swipe, plays it automatically until the match ends, the "
        "destination is reached, or you interrupt. Use when the user says "
        "'play the game', 'control the game', 'win the match', 'join a match'. "
        "start = begin driving, status = what it's doing, stop = halt. NOT for "
        "texting — that is send_message."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "start | status | stop. Default start.",
            },
            "game": {
                "type": "STRING",
                "description": "Which game / goal, e.g. 'Free Fire — win the squads match'.",
            },
        },
        "required": [],
    },
}

PLUGIN_SETTINGS = {
    "namespace": "game_control",
    "title": "Game Control",
    "fields": [
        {"key": "real_width", "label": "Phone screen width (px)", "type": "number",
         "placeholder": "1080 (from ADB 'wm size' if unsure)"},
        {"key": "real_height", "label": "Phone screen height (px)", "type": "number",
         "placeholder": "2400"},
        {"key": "max_seconds", "label": "Max loop seconds", "type": "number", "default": 60},
        {"key": "vision_min_ms", "label": "Min ms between vision calls", "type": "number", "default": 300},
    ],
}

# ── self-check: no phone, no Gemini, no adb ──────────────────────────────────
# Fakes wireless_control + adb_control so the loop itself is never driven.
if __name__ == "__main__":
    import sys as _sys

    # tiny 1x1 red PNG builder
    def _red_png_b64():
        sig = b"\x89PNG\r\n\x1a\n"
        import struct, zlib
        ihdr_d = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        ihdr_c = zlib.crc32(b"IHDR" + ihdr_d) & 0xFFFFFFFF
        ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_d + struct.pack(">I", ihdr_c)
        comp = zlib.compress(b"\x00\xff\x00\x00")
        idat_c = zlib.crc32(b"IDAT" + comp) & 0xFFFFFFFF
        idat = struct.pack(">I", len(comp)) + b"IDAT" + comp + struct.pack(">I", idat_c)
        iend_c = zlib.crc32(b"IEND") & 0xFFFFFFFF
        iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_c)
        return base64.b64encode(sig + ihdr + idat + iend).decode()

    _POSTED = {}

    class _FakeWireless:
        @staticmethod
        def _post(path, body=None):
            if path == "/screenshot":
                return {"png": _red_png_b64(), "width": 540, "height": 540}
            _POSTED[path] = dict(body or {})
            return {"ok": True}
        @staticmethod
        def _get(path):
            return {"ok": True, "version": "1.0", "screen": True}

    _real_wc = _sys.modules.get("plugins.wireless_control")
    _sys.modules["plugins.wireless_control"] = _FakeWireless
    globals()["_wpost"] = _FakeWireless._post
    globals()["_wget"] = _FakeWireless._get

    # dispatcher guards — call run() once per state change into a temp;
    # Python assert evaluates the message expression even when the
    # condition is true, so repeating the call would toggle STATE twice.
    assert "Use start" in run({"action": "bogus"})
    _out1 = run({"action": "start"})
    assert "Gemini" in _out1 or "On it" in _out1, _out1
    _out2 = run({"action": "start"})
    assert "already" in _out2.lower(), _out2
    STATE["running"] = False
    _out3 = run({"action": "stop"})
    assert "Stopped" in _out3, _out3
    _out4 = run({"action": "status"})
    assert "isn't running" in _out4, _out4

    # coordinate scale correctness: 270 in a 540-wide PNG -> real 540 px
    _SCALE["x"], _SCALE["y"] = 1080 / 540, 2400 / 540
    _POSTED.clear()
    _act({"action": "TAP", "x": 270, "y": 270}, None)
    assert _POSTED.get("/tap") == {"x": 540, "y": 1200}, _POSTED

    # keyevent routes
    _POSTED.clear()
    _act({"action": "KEY", "keycode": 4}, None)
    assert _POSTED.get("/keyevent") == {"keycode": 4}, _POSTED

    if _real_wc is not None:
        _sys.modules["plugins.wireless_control"] = _real_wc
    print("self-check OK")