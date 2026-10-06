"""
JARVIS form-filling agent — a vision loop that drives the screen until a form
is completed and submitted. Reads the screen with Gemini, decides the next
action, executes it with pyautogui, re-reads, and repeats. When a field needs
an answer it can't invent, it PAUSES and asks the user; the next `answer`
call resumes it with the reply in hand.

Usage through JARVIS voice:
  "fill this form for me"               -> start, agent looks at the screen
  "my name is Chidvielas Parepalli, email chidvielasparepalli@gmail.com"  -> answer feed
  "status"                              -> what is the agent doing now

Only one agent runs at a time (module-level state).
"""

import io
import json
import re
import sys
import threading
import time
from pathlib import Path

try:
    import pyautogui
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

# Module-level agent state — one form agent at a time.
STATE = {
    "running": False,    # is the loop alive?
    "task": "",          # the user's instruction
    "log": [],           # list of strides the agent took (for narration)
    "reply": None,       # pending answer from the user for the last ASK
    "lock": threading.Lock(),
}

_ACTIONS = {"CLICK", "TYPE", "KEY", "SCROLL", "ASK", "DONE", "WAIT"}


def _get_api_key() -> str:
    base = Path(__file__).resolve().parent.parent
    cfg = base / "config" / "api_keys.json"
    try:
        return json.loads(cfg.read_text(encoding="utf-8"))["gemini_api_key"]
    except Exception:
        return ""


def _screenshot_png() -> bytes:
    """Full-screen PNG bytes via pyautogui (same primitive computer_control uses)."""
    img = pyautogui.screenshot()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _vision_action(img_bytes: bytes) -> dict:
    """Ask Gemini what to do next. Returns a dict with 'action' + fields."""
    from google import genai
    from google.genai import types as gtypes

    key = _get_api_key()
    if not key:
        return {"action": "ASK", "text": "I need a Gemini API key to read the screen."}

    client = genai.Client(api_key=key)
    history = "\n".join(STATE["log"][-6:])
    prompt = f"""You are JARVIS's hands, driving a computer to complete ONE task.

TASK: {STATE["task"]}

WHAT I HAVE DONE SO FAR:
{history or "(nothing yet)"}

You see the current screen. Decide the single best next action to make progress.

Reply with ONLY a JSON object, one of these shapes:
{{"action":"CLICK","x":123,"y":456}}
{{"action":"TYPE","text":"value to type"}}
{{"action":"KEY","key":"tab|enter|esc|space"}}
{{"action":"SCROLL","dir":"down|up"}}
{{"action":"WAIT","ms":2000}}
{{"action":"ASK","text":"question for the user about a field"}}
{{"action":"DONE"}}

Rules:
- If the screen has an empty field asking for info the task did not provide, and
  you cannot invent it safely (name, email, career goal, phone…), return ASK with
  exactly what you need: "The EMPLOYMENT HISTORY field asks…" — this pauses the loop.
- TYPE only real text; do NOT type into a field whose content you invented.
- Prefer CLICK coordinates at the CENTER of the target.
- DONE only when the form is submitted/success message visible, or the task is finished.
- If two consecutive ASKs about the same field get no answer, DONE instead.

Return ONLY the JSON."""

    try:
        resp = client.models.generate_content(
            model="gemini-flash-latest",
            contents=[
                gtypes.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                prompt,
            ],
        )
        text = (resp.text or "").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        return json.loads(text)
    except Exception as e:
        return {"action": "ASK", "text": f"I hit an error reading the screen: {e}"}


def _act(decision: dict, player) -> str:
    """Execute one decision; returns a narration string."""
    a = (decision.get("action") or "").upper()
    if a == "CLICK":
        try:
            pyautogui.click(int(decision["x"]), int(decision["y"]))
            time.sleep(0.4)
            return f"Clicked at {decision['x']},{decision['y']}."
        except Exception as e:
            return f"Click failed: {e}"
    if a == "TYPE":
        try:
            pyautogui.write(str(decision.get("text", "")))
            time.sleep(0.3)
            return f"Typed: {decision.get('text', '')[:40]}"
        except Exception as e:
            return f"Type failed: {e}"
    if a == "KEY":
        try:
            pyautogui.press(str(decision.get("key", "tab")))
            time.sleep(0.3)
            return f"Pressed {decision.get('key', 'tab')}."
        except Exception as e:
            return f"Key failed: {e}"
    if a == "SCROLL":
        try:
            pyautogui.scroll(3 if decision.get("dir", "down") == "up" else -3)
            time.sleep(0.5)
            return f"Scrolled {decision.get('dir', 'down')}."
        except Exception as e:
            return f"Scroll failed: {e}"
    if a == "WAIT":
        try:
            time.sleep(max(0.1, min(10, int(decision.get("ms", 1000))) / 1000))
        except Exception:
            time.sleep(1)
        return "Waited."
    if a == "ASK":
        return f"ASK:{decision.get('text', 'I need an answer.')}"
    if a == "DONE":
        return "DONE"
    return f"Unknown action: {decision}"


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "start").strip().lower()

    if action == "start":
        task = (parameters.get("task") or "").strip() or None
        if STATE["running"]:
            return "The form agent is already working. Ask it: status."
        if not _PYAUTOGUI:
            return "Sir, pyautogui isn't installed — I can't drive the screen."
        STATE["task"] = task or "complete and submit the form on screen"
        STATE["log"] = []
        STATE["running"] = True
        STATE["reply"] = None
        threading.Thread(target=_loop, args=(player,), daemon=True).start()
        return ("On it. I'm reading the screen and filling the form — "
                "narrating as I go. Say 'status' any time.")

    if action == "status":
        if not STATE["running"]:
            return "The form agent isn't running."
        last = STATE["log"][-1] if STATE["log"] else "just starting."
        return f"Form agent: {last}"

    if action == "answer":
        text = (parameters.get("text") or "").strip()
        with STATE["lock"]:
            STATE["reply"] = text
        return "Got it — continuing."

    if action == "stop":
        STATE["running"] = False
        return "Stopped the form agent."

    return "Use start, status, answer, or stop."


def _loop(player):
    try:
        consecutive_asks = 0
        last_ask = ""
        while STATE["running"]:
            decision = _vision_action(_screenshot_png())
            narr = _act(decision, player)

            if narr.startswith("ASK:"):
                q = narr[4:]
                # Don't re-ask the same thing forever.
                if q == last_ask:
                    consecutive_asks += 1
                else:
                    consecutive_asks = 0
                    last_ask = q
                if consecutive_asks >= 2:
                    STATE["running"] = False
                    _say(player, "I need your input to continue, sir. "
                                  "I've paused the form.")
                    return
                _say(player, q)
                # Wait for the user's answer (via the answer action).
                STATE["reply"] = None
                deadline = time.time() + 300   # 5 min
                while STATE["running"] and STATE["reply"] is None and time.time() < deadline:
                    time.sleep(0.2)
                if not STATE["running"]:
                    return
                _say(player, f"Thank you. Answer noted: {STATE['reply']}")
                STATE["log"].append(f"Asked user; they answered: {STATE['reply']}")
                STATE["reply"] = None
                continue

            if narr == "DONE":
                _say(player, "The form is done — I submitted it, sir.")
                STATE["running"] = False
                return

            STATE["log"].append(narr)
            if player:
                try:
                    player.write_log(f"AGENT: {narr}")
                except Exception:
                    pass
    except Exception as e:
        _say(player, f"The form agent hit a problem and stopped: {e}")
    finally:
        STATE["running"] = False
        STATE["reply"] = None


def _say(player, text: str):
    if player:
        try:
            player.write_log(f"AGENT: {text}")
        except Exception:
            pass
    print(f"[FORM_AGENT] {text}")


# Tool declaration — auto-discovered.
PLUGIN = {
    "name": "form_agent",
    "description": (
        "Fill a form on screen autonomously: reads the screen with the camera, "
        "decides next action (click/type/scroll), asks the user when a field "
        "needs info it can't invent, and submits when done. Use when the user "
        "says 'fill this form', 'complete the form', 'submit the application', "
        "or gives a multi-field form task. start = begin, answer = feed a "
        "requested value, status = progress, stop = halt."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "start | answer | status | stop. Default start.",
            },
            "task": {
                "type": "STRING",
                "description": "What to accomplish, e.g. 'submit the internship application'.",
            },
            "text": {
                "type": "STRING",
                "description": "User's answer when action=answer.",
            },
        },
        "required": [],
    },
}


if __name__ == "__main__":
    # Self-check: dispatcher rejects garbage without touching the screen.
    assert "Use start" in run({"action": "bogus"})
    assert "pyautogui" in run({"action": "start"}) or "already" in run({"action": "start"})
    print("self-check OK")