# FastAPI Wrapper Audit

## Audit Summary

The project is a Python desktop assistant. The desktop runtime starts from `main.py`, builds a PyQt UI from `ui.py`, connects to Gemini Live, discovers action modules from `actions/*.py`, discovers plugins from `plugins/*.py`, and executes tool calls through the central `_execute_tool` dispatcher.

The project already included a FastAPI-based local dashboard in `dashboard/server.py`. That dashboard is the safest place to turn the app into a web application because it already relays web commands into the existing runtime through `_command_queue`. This preserves the assistant's current architecture and action modules.

## Wrapper Approach

The wrapper does not rewrite the voice assistant, action registry, plugins, memory system, LLM session, desktop UI, or task execution logic.

The web flow is now:

1. Browser sends a command to `POST /api/intent/preview`.
2. FastAPI decrypts the command when encryption is enabled.
3. FastAPI creates a short-lived `execution_token` and returns an approval preview.
4. The UI displays the exact command and risk level.
5. Browser calls `POST /api/intent/execute` with `approved: true` only after the user allows it.
6. FastAPI queues the approved command into the existing `_command_queue`.
7. The original CHIDVI runtime receives and handles the command as before.
8. The final assistant output is displayed in the existing web feed through the dashboard WebSocket log stream.

## Permission Layer

Every web command must pass through the two-step flow. The old `POST /api/command` route now returns HTTP 428 and tells clients to use the preview/execute endpoints.

The WebSocket command shortcut also no longer queues commands directly; it returns a `permission_required` event.

Uploads, login, wake, and microphone streaming remain separate web features. Browser microphone access is still controlled by the browser permission prompt before audio streaming begins.

## New API Surface

```text
POST /api/intent/preview
POST /api/intent/execute
```

Preview response shape:

```json
{
  "ok": true,
  "execution_token": "...",
  "intent": "web_command",
  "description": "open chrome",
  "permission_required": true,
  "risk_level": "medium",
  "expires_in": 120,
  "notice": "..."
}
```

Execute request shape:

```json
{
  "execution_token": "...",
  "approved": true
}
```

## Files Changed

```text
dashboard/server.py
dashboard/static/app.html
dashboard/static/desktop.html
dashboard/web_ui.py
run_dashboard.py
web_main.py
```

## How To Open The Dashboard

Full application mode:

```text
python main.py
```

After CHIDVI starts, press `Remote Control` in the desktop UI. The app generates a temporary login key and shows the dashboard URL.

Dashboard-only check mode:

```text
python run_dashboard.py
```

This prints a dashboard URL and temporary login key. It is useful for checking the FastAPI web UI, but it does not start the full Gemini Live assistant runtime. Use `main.py` for real task execution.

Full browser-application mode:

```text
python web_main.py
```

This starts the existing `JarvisLive` assistant runtime with `dashboard.web_ui.WebJarvisUI` instead of the PyQt `ui.JarvisUI`. The assistant brain, action registry, memory, plugins, and tool execution stay in the original runtime; only the interface is swapped to FastAPI/WebSocket plus the browser UI.

The Python environment used for `web_main.py` must have the original assistant dependencies from `requirements.txt`, not just FastAPI.

If dependencies are missing, install the dashboard dependencies:

```text
python -m pip install fastapi "uvicorn[standard]" cryptography python-multipart
```

## Important Boundaries

The wrapper gates commands before they enter the existing assistant. It intentionally does not modify individual task handlers. This keeps the original application logic intact while making the web UI permission-first.
