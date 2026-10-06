"""
JARVIS ADB plugin — control an Android device over Android Debug Bridge.

Bundled in the same spirit as computer_control: it shells out to the `adb`
binary (no Python ADB library needed; the standard tool is one `adb install`
away and already ships with Android Studio). USB debugging must be enabled on
the device and the device authorized (the "Allow USB debugging?" prompt).
"""

import shutil
import subprocess

_PLUGIN_NAME = "adb_control"

PLUGIN = {
    "name": _PLUGIN_NAME,
    "description": (
        "Control a connected Android phone over ADB (Android Debug Bridge): "
        "tap at screen coordinates, swipe, type text, press hardware keys, or "
        "capture the screen to a PNG. Requires USB debugging enabled on the "
        "device and the adb binary on this computer. Use for phone actions; "
        "NOT for sending WhatsApp/Telegram messages (that is send_message)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": ("What to do: 'devices' (list connected devices), "
                                "'open' (launch an app by its package name in "
                                "'value'; com.whatsapp=WhatsApp, com.android.chrome=Chrome, "
                                "com.instagram.android=Instagram), 'tap' (x y), "
                                "'swipe' (x1 y1 x2 y2 duration_ms), 'text' (string "
                                "in 'value'), 'keyevent' (event id, e.g. 3=home "
                                "4=back 26=power 66=enter 67=backspace, 24/25=volume), "
                                "or 'screencap' (save screen PNG to the path in 'value')."),
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


def _adb(*args) -> str:
    """Run an adb shell command; returns stdout. Raises RuntimeError on failure."""
    if not shutil.which("adb"):
        raise RuntimeError("adb binary not found on PATH. Install it (Android Studio, "
                           "or the standalone platform-tools) and retry.")
    try:
        proc = subprocess.run(["adb", *args], capture_output=True, text=True,
                              timeout=30, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError("adb command timed out.")
    if proc.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def _first_device() -> str:
    out = _adb("devices")
    for line in out.splitlines()[1:]:
        line = line.strip()
        if line and "\tdevice" in line and "unauthorized" not in line:
            return line.split("\t")[0]
    raise RuntimeError("No authorized Android device connected. Enable USB debugging, "
                       "plug in, and accept the authorization prompt.")


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        action = (parameters.get("action") or "").lower().strip()
        if not action:
            return "Sir, I need an 'action' (devices, tap, swipe, text, keyevent, screencap)."

        known = {"devices", "open", "tap", "swipe", "text", "keyevent", "screencap"}
        if action not in known:
            return f"Unknown action '{action}'. Use devices, tap, swipe, text, keyevent, or screencap."

        # Validate args up front so a bad request talks back without touching the device.
        if action in ("tap", "swipe") and ("x" not in parameters or "y" not in parameters):
            return f"Sir, '{action}' needs 'x' and 'y' coordinates."
        if action == "swipe" and ("x2" not in parameters or "y2" not in parameters):
            return "Sir, 'swipe' needs an end point 'x2' and 'y2'."
        if action in ("open", "text") and not parameters.get("value"):
            return f"Sir, '{action}' needs an app package in 'value'." if action == "open" \
                else "Sir, 'text' needs a 'value' to type."
        if action == "screencap" and not parameters.get("value"):
            return "Sir, 'screencap' needs an output PNG path in 'value'."
        if action == "keyevent" and "keyevent" not in parameters:
            return "Sir, 'keyevent' needs a key id in 'keyevent'."

        if action == "devices":
            out = _adb("devices")
            devices = [ln for ln in out.splitlines()[1:] if ln.strip()]
            return f"{len(devices)} device(s) detected." if devices else "No Android devices detected."

        device = _first_device()

        if action == "open":
            _adb("-s", device, "shell", "monkey", "-p", parameters["value"], "1")
            return f"Opened app {parameters['value']}."

        if action == "tap":
            _adb("-s", device, "shell", "input", "tap", str(int(parameters["x"])), str(int(parameters["y"])))
            return "Tapped the screen."

        if action == "swipe":
            x1, y1 = int(parameters["x"]), int(parameters["y"])
            x2, y2 = int(parameters["x2"]), int(parameters["y2"])
            dur = str(int(parameters.get("duration") or 300))
            _adb("-s", device, "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), dur)
            return "Swiped."

        if action == "keyevent":
            _adb("-s", device, "shell", "input", "keyevent", str(int(parameters["keyevent"])))
            return "Key pressed."

        if action == "text":
            value = parameters["value"]
            _adb("-s", device, "shell", "input", "text", value)
            return f"Typed: {value}"

        if action == "screencap":
            path = parameters["value"]
            _adb("-s", device, "shell", "screencap", "-p", "/sdcard/_jarvis_tmp.png")
            _adb("-s", device, "pull", "/sdcard/_jarvis_tmp.png", path)
            return f"Screen saved to {path}."

        return "Done."  # unreachable — all actions handled above.
    except Exception as e:
        return f"Sir, adb_control failed: {e}"


if __name__ == "__main__":
    import types

    # Bad-request guards: no device or adb binary needed.
    for case, out in [
        ({}, "Sir, I need an 'action'"),
        ({"action": "nonsense"}, "Unknown action"),
        ({"action": "open"}, "needs an app package"),
        ({"action": "text"}, "needs a 'value'"),
        ({"action": "screencap"}, "needs an output PNG path"),
    ]:
        assert out in run(case), (case, run(case))

    # Success branches, with a fake device layer so the real adb is never called.
    calls = []

    def _fake_which(_name):
        return "adb"  # present on PATH

    def _fake_adb(*args) -> str:
        calls.append(args)
        if args[-1] == "devices":
            return "List of devices attached\nFAKE001\tdevice\n"
        return ""

    real_rec = {
        "shutil.which": globals()["shutil"].which,
        "_adb": None,
    }
    globals()["shutil"].which = _fake_which
    globals()["_adb"] = _fake_adb

    success_cases = [
        ({"action": "devices"}, "1 device(s) detected."),
        ({"action": "open", "value": "com.whatsapp"}, "Opened app"),
        ({"action": "tap", "x": 100, "y": 200}, "Tapped"),
        ({"action": "swipe", "x": 0, "y": 0, "x2": 500, "y2": 500}, "Swiped"),
        ({"action": "keyevent", "keyevent": 3}, "Key pressed"),
        ({"action": "text", "value": "hello"}, "Typed: hello"),
        ({"action": "screencap", "value": "phone.png"}, "Screen saved to phone.png."),
    ]
    for case, expect in success_cases:
        out = run(case)
        assert expect in out, (case, out)

    # Restore the real functions so a subsequent real run() still works.
    globals()["shutil"].which = real_rec["shutil.which"]
    globals()["_adb"] = real_rec["_adb"]

    # The branches that touch the device all ran — would have flagged the
    # old bare-`value` NameError as an assertion failure.
    assert any("monkey" in a for a in calls), "open never dispatched to monkey"
    assert any("screencap" in a for a in calls), "screencap never dispatched"
    print("self-check OK")