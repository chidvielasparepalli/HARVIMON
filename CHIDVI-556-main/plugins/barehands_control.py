"""
JARVIS barehands plugin — launch and drive the barehands hand-tracked board
with your voice. Lets JARVIS start the board, open it in Chrome, and stage
cards/media on it, all over localhost.

barehands is a vendored AGPL-3.0 project (github.com/jaredrhod/barehands) that
ships inside this repo at ./barehands/ and is served by its stdlib-Python HTTP
server on http://127.0.0.1:8794. This plugin only shells out to start it /
POSTs commands to its localhost /cmd channel.

Commands you can send (a = action):
  present  {"a":"present","title":"X","body":"Y"}        spotlight center-stage
  add_card {"a":"add_card","title":"X","body":"Y"}       add a note card
  add_img  {"a":"add_img","src":"path/under/media"}      add an image
  hand     {"a":"hand","src":"models/x.glb"}             deliver a 3D prop
  explode  {"a":"explode"}                               part an open model
  reset    {"a":"reset"}                                 bring ring back to center

Files are jail-checked server-side against ./media/ — this channel is safe.
"""

import json
import shutil
import socket
import subprocess
import urllib.request
from pathlib import Path

# Where the barehands board lives. The vendored copy ships in this repo at
# ./barehands/; a pre-existing clone elsewhere takes precedence (legacy setups
# that were installed before the tree was vendored).
_LOCAL = Path(__file__).resolve().parent.parent / "barehands"
_LEGACY = Path(r"D:\CLONES\HandsMode")
BAREHANDS_DIR = _LOCAL if _LOCAL.exists() else _LEGACY
PORT = 8794
URL = f"http://127.0.0.1:{PORT}/cmd"
STAGE_URL = f"http://127.0.0.1:{PORT}/stage.html"

# What the server will actually accept (its own allowlist).
_ACTIONS = {"present", "add_card", "add_img", "hand", "explode", "reset"}

PLUGIN = {
    "name": "barehands_control",
    "description": (
        "Launch and control the barehands hand-tracked board — the webcam "
        "hands interface that floats notes/images over the screen, moved by "
        "your bare hands. Use when the user says 'hands mode', 'play with "
        "hands', or 'it's time to play with hands': action 'launch' starts the "
        "board and opens it in Chrome. Or stage things on the board with "
        "action 'cmd' (present/add_card/add_img/hand/explode/reset). Also has "
        "'status'. This is NOT for opening regular apps — that is open_app."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": ("'launch' = start board + open Chrome; "
                                "'status' = is it running; 'cmd' = send a board "
                                "command (then provide 'a')."),
            },
            "a": {
                "type": "STRING",
                "description": "Board command when action='cmd': present, add_card, add_img, hand, explode, reset.",
            },
            "title": {"type": "STRING", "description": "Card/spotlight title."},
            "body":  {"type": "STRING", "description": "Card/spotlight body text."},
            "src":   {"type": "STRING", "description": "Path of a file under the board's media/ airlock."},
        },
        "required": ["action"],
    },
}


def _find_python() -> str | None:
    """A working Python interpreter that can run server.py (stdlib only)."""
    for name in ("python", "python3"):
        exe = shutil.which(name)
        if exe:
            try:
                subprocess.run([exe, "-c", "pass"], capture_output=True,
                               timeout=10, check=True)
                return exe
            except Exception:
                continue
    return None


def _server_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=1):
            return True
    except OSError:
        return False


def _open_browser() -> None:
    import platform
    import time as _t
    if not _server_up():
        return
    try:
        if platform.system() == "Windows":
            # `start` is a cmd builtin, not an exe on PATH — always shell it.
            subprocess.Popen(["start", STAGE_URL], shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif shutil.which("xdg-open"):  # Linux
            subprocess.Popen(["xdg-open", STAGE_URL],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:  # macOS
            subprocess.Popen(["open", STAGE_URL],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _t.sleep(0.5)
    except Exception:
        pass   # browser opening is best-effort; the board still runs


def _launch() -> str:
    if _server_up():
        _open_browser()
        return "The hands board is already running."
    if not BAREHANDS_DIR.exists():
        return f"Sir, barehands isn't at {BAREHANDS_DIR}. Clone it there first."
    py = _find_python()
    if not py:
        return "Sir, I couldn't find a working Python to run the hands server."
    try:
        subprocess.Popen(
            [py, str(BAREHANDS_DIR / "server.py")],
            cwd=str(BAREHANDS_DIR),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        return f"Sir, starting the hands server failed: {e}"

    import time as _t
    for _ in range(60):   # 15 s max wait
        if _server_up():
            _open_browser()
            return "Hands board is up at http://127.0.0.1:8794/stage.html"
        _t.sleep(0.25)
    return "Sir, the hands server started but never came up on the port."


def _send(cmd: dict) -> str | None:
    """POST one command to /cmd. Returns None on success, else an error msg."""
    try:
        req = urllib.request.Request(
            URL, data=json.dumps(cmd).encode(), method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 204:
                return None
            return f"Board rejected the command (HTTP {resp.status})."
    except Exception as e:
        return f"Could not reach the hands board: {e}"


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().lower()
    if action not in ("launch", "status", "cmd"):
        return ("Sir, use launch, status, or cmd with a='present'…'reset'.")

    if action == "launch":
        # Show the board inside jarvis even if the server was already up.
        if player is not None:
            try:
                player.show_hands_board()
            except Exception:
                pass
        return _launch()
    if action == "status":
        return ("Hands board is up." if _server_up()
                else "Hands board is not running.")

    # action == "cmd"
    cmd = {"a": parameters.get("a", "").strip().lower()}
    if cmd["a"] not in _ACTIONS:
        return ("Sir, unknown board command. Use present, add_card, add_img, "
                "hand, explode, or reset.")
    for k in ("title", "body", "src"):
        if parameters.get(k):
            cmd[k] = str(parameters[k])
    if not _server_up():
        return "The hands board isn't running. Say: it's time to play with hands."
    err = _send(cmd)
    if err:
        return err
    return f"On the board: {cmd['a']}."


if __name__ == "__main__":
    # Self-check: bad inputs always answer without ever starting the server.
    bad = [
        {}, {"action": "nope"}, {"action": "cmd"}, {"action": "cmd", "a": "nope"},
    ]
    for b in bad:
        out = run(b)
        assert isinstance(out, str) and not out.startswith("Sir, adb"), (b, out)
    print("self-check OK")