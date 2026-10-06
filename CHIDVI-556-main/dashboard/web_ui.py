from __future__ import annotations

import asyncio
import json
import secrets
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import uvicorn


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
DOWNLOAD_DIR = BASE_DIR / "downloads"
CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PORT = 8000
PERMISSION_TTL_SECONDS = 120.0

_RISK_RULES = [
    ("high", ("delete", "remove file", "erase", "format", "shutdown", "restart",
              "send email", "send message", "purchase", "buy", "payment",
              "install", "uninstall", "run command", "execute", "terminal",
              "cmd", "powershell")),
    ("medium", ("open app", "open website", "open file", "read file", "move",
                "rename", "copy", "browser", "search web", "upload",
                "download", "camera", "screen", "monitor", "reminder")),
]


def _read_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_config(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=4), encoding="utf-8")


def _preview(text: str) -> dict:
    lowered = text.lower()
    risk = "low"
    for level, words in _RISK_RULES:
        if any(word in lowered for word in words):
            risk = level
            break
    return {
        "intent": "web_command",
        "description": text,
        "permission_required": True,
        "risk_level": risk,
        "expires_in": int(PERMISSION_TTL_SECONDS),
        "notice": "This task runs through the existing CHIDVI runtime only after approval.",
    }


class _RootShim:
    def __init__(self, ui: "WebJarvisUI"):
        self._ui = ui

    def mainloop(self) -> None:
        self._ui.serve_forever()

    def protocol(self, *_args: Any) -> None:
        pass


class WebJarvisUI:
    """FastAPI/WebSocket implementation of the JarvisUI interface used by main.py."""

    skip_embedded_dashboard = True

    def __init__(self, face_path: str = "face.png", size=None):
        cfg = _read_config()
        self.face_path = face_path
        self.root = _RootShim(self)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: set[WebSocket] = set()
        self._history: list[dict] = []
        self._tokens: set[str] = set()
        self._pending: dict[str, dict] = {}

        self._assistant_name = (cfg.get("assistant_name") or "CHIDVI-556").strip()
        self._personality = cfg.get("personality", "tony")
        self._state = "INITIALISING"
        self._muted = False
        self._current_file: str | None = None
        self._ready = bool(cfg.get("gemini_api_key")) and bool(cfg.get("os_system"))

        self.on_text_command: Callable[[str], None] | None = None
        self.on_remote_clicked = None
        self.on_interrupt = None
        self.on_voice_change = None
        self.on_audio_device_change = None
        self.on_personality_change = None
        self.get_plugins = None
        self.get_plugin_settings = None
        self.on_wake_toggle = None
        self.on_wake_manual = None
        self.on_wake_install = None
        self.wake_is_ready = None
        self.wake_get_state = None
        self.request_say = None
        self.game_stop_event = None

        self.app = self._build_app()

    @property
    def muted(self) -> bool:
        return self._muted

    @muted.setter
    def muted(self, value: bool) -> None:
        self._muted = bool(value)
        self._send({"type": "muted", "muted": self._muted})
        self.set_state("MUTED" if self._muted else "LISTENING")

    @property
    def current_file(self) -> str | None:
        return self._current_file

    @property
    def assistant_name(self) -> str:
        return self._assistant_name

    def _build_app(self) -> FastAPI:
        app = FastAPI(docs_url="/api/docs", redoc_url=None)

        def auth(req: Request) -> str:
            return req.headers.get("authorization", "").removeprefix("Bearer ").strip()

        def authed(req: Request) -> bool:
            return auth(req) in self._tokens

        def safe_static_path(filename: str) -> Path | None:
            try:
                path = (STATIC_DIR / filename).resolve()
                path.relative_to(STATIC_DIR.resolve())
                return path if path.is_file() else None
            except Exception:
                return None

        @app.get("/", response_class=HTMLResponse)
        async def landing():
            return HTMLResponse((STATIC_DIR / "landing.html").read_text(encoding="utf-8"))

        @app.get("/dashboard", response_class=HTMLResponse)
        async def dashboard():
            return HTMLResponse((STATIC_DIR / "desktop.html").read_text(encoding="utf-8"))

        @app.get("/mobile", response_class=HTMLResponse)
        async def mobile():
            return HTMLResponse((STATIC_DIR / "app.html").read_text(encoding="utf-8"))

        @app.get("/login", response_class=HTMLResponse)
        async def login():
            return HTMLResponse((STATIC_DIR / "login.html").read_text(encoding="utf-8"))

        @app.get("/static/crypto.js")
        async def crypto():
            path = STATIC_DIR / "crypto-js.min.js"
            if path.exists():
                return FileResponse(str(path), media_type="application/javascript")
            return JSONResponse({"error": "crypto-js.min.js not found"}, status_code=404)

        @app.get("/static/{filename:path}")
        async def static_asset(filename: str):
            path = safe_static_path(filename)
            if not path:
                return JSONResponse({"error": "Not found"}, status_code=404)
            return FileResponse(str(path))

        @app.get("/downloads/{filename}")
        async def download_harvimon(filename: str):
            if filename != "HARVIMON-AI.exe":
                return JSONResponse({"error": "Not found"}, status_code=404)
            path = DOWNLOAD_DIR / filename
            if not path.exists() or not path.is_file():
                return JSONResponse({"error": "HARVIMON download unavailable"}, status_code=404)
            return FileResponse(
                str(path),
                media_type="application/vnd.microsoft.portable-executable",
                filename=filename,
            )

        @app.post("/api/local-session")
        async def local_session():
            token = secrets.token_urlsafe(32)
            self._tokens.add(token)
            return JSONResponse({
                "ok": True,
                "token": token,
                "ready": self._ready,
                "assistant_name": self._assistant_name,
            })

        @app.get("/api/state")
        async def state(req: Request):
            if not authed(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            wake = self.wake_get_state() if self.wake_get_state else {}
            return JSONResponse({
                "assistant_name": self._assistant_name,
                "state": self._state,
                "muted": self._muted,
                "ready": self._ready,
                "personality": self._personality,
                "wake": wake,
            })

        @app.post("/api/setup")
        async def setup(req: Request):
            body = await req.json()
            cfg = _read_config()
            if body.get("gemini_api_key"):
                cfg["gemini_api_key"] = str(body["gemini_api_key"]).strip()
            if body.get("os_system"):
                cfg["os_system"] = str(body["os_system"]).strip()
            cfg.setdefault("personality", "tony")
            _write_config(cfg)
            self._ready = bool(cfg.get("gemini_api_key")) and bool(cfg.get("os_system"))
            await self.broadcast({"type": "setup", "ready": self._ready})
            return JSONResponse({"ok": True, "ready": self._ready})

        @app.post("/api/intent/preview")
        async def intent_preview(req: Request):
            if not authed(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body = await req.json()
            text = str(body.get("text") or "").strip()
            if not text:
                return JSONResponse({"error": "Command text is required"}, status_code=400)
            token = secrets.token_urlsafe(24)
            data = _preview(text)
            self._prune_pending()
            self._pending[token] = {
                "auth": auth(req),
                "text": text,
                "expires": time.time() + PERMISSION_TTL_SECONDS,
            }
            return JSONResponse({"ok": True, "execution_token": token, **data})

        @app.post("/api/intent/execute")
        async def intent_execute(req: Request):
            if not authed(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body = await req.json()
            token = str(body.get("execution_token") or "").strip()
            approved = bool(body.get("approved"))
            self._prune_pending()
            pending = self._pending.pop(token, None)
            if not pending or pending["auth"] != auth(req):
                return JSONResponse({"error": "Execution token expired or invalid"}, status_code=404)
            if not approved:
                await self.broadcast({"type": "sys", "text": "Permission cancelled."})
                return JSONResponse({"ok": True, "status": "cancelled"})
            text = pending["text"]
            await self.broadcast({"type": "log", "speaker": "user", "text": text})
            if self.on_text_command:
                self.on_text_command(text)
            return JSONResponse({"ok": True, "status": "queued"})

        @app.post("/api/mute")
        async def mute(req: Request):
            if not authed(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body = await req.json()
            self.muted = bool(body.get("muted", not self._muted))
            return JSONResponse({"ok": True, "muted": self._muted})

        @app.post("/api/interrupt")
        async def interrupt(req: Request):
            if not authed(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if self.on_interrupt:
                self.on_interrupt()
            return JSONResponse({"ok": True})

        @app.post("/api/wake")
        async def wake(req: Request):
            if not authed(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            if self.on_wake_manual:
                self.on_wake_manual()
            return JSONResponse({"ok": True})

        @app.post("/api/confirm")
        async def confirm(req: Request):
            if not authed(req):
                return JSONResponse({"error": "Unauthorized"}, status_code=401)
            body = await req.json()
            from core.confirm import resolve
            resolve(bool(body.get("accepted")))
            await self.broadcast({"type": "confirm", "active": False})
            return JSONResponse({"ok": True})

        @app.websocket("/ws")
        async def ws(websocket: WebSocket, token: str = ""):
            if token not in self._tokens:
                await websocket.close(code=4001)
                return
            await websocket.accept()
            self._clients.add(websocket)
            try:
                for item in self._history[-80:]:
                    await websocket.send_json(item)
                await websocket.send_json({"type": "status", "state": self._state, "muted": self._muted})
                while True:
                    await websocket.receive_json()
            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(websocket)

        return app

    def _prune_pending(self) -> None:
        now = time.time()
        self._pending = {k: v for k, v in self._pending.items() if v["expires"] > now}

    async def broadcast(self, message: dict) -> None:
        self._history.append(message)
        self._history = self._history[-300:]
        dead = set()
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    def _send(self, message: dict) -> None:
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(message), self._loop)

    def serve_forever(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        print("")
        print("CHIDVI browser application is running.")
        print(f"URL: http://127.0.0.1:{PORT}")
        print("")
        config = uvicorn.Config(self.app, host="0.0.0.0", port=PORT, log_level="warning")
        await uvicorn.Server(config).serve()

    def wait_for_api_key(self) -> None:
        while not self._ready:
            self.write_log("SYS: Waiting for setup in the browser.")
            time.sleep(2.0)

    def set_personality_state(self, personality_id: str) -> None:
        self._personality = personality_id
        self._send({"type": "personality", "personality": personality_id})

    def show_confirm(self, title: str, detail: str) -> None:
        self._send({"type": "confirm", "active": True, "title": str(title)[:120], "detail": str(detail)[:300]})

    def hide_confirm(self) -> None:
        self._send({"type": "confirm", "active": False})

    def set_audio_level(self, level: float, source: str = "mic") -> None:
        self._send({"type": "audio_level", "source": source, "level": float(level)})

    def notify_phone_connected(self) -> None:
        self.write_log("SYS: Phone connected via Remote Dashboard.")

    def show_hands_board(self) -> None:
        self.write_log("SYS: Hands board requested.")

    def set_state(self, state: str) -> None:
        self._state = state
        self._send({"type": "status", "state": state, "muted": self._muted})

    def write_log(self, text: str) -> None:
        raw = str(text)
        if raw.startswith("You:"):
            self._send({"type": "log", "speaker": "user", "text": raw[4:].strip()})
        elif ":" in raw and not raw.startswith(("SYS:", "ERR:", "NET:")):
            self._send({"type": "log", "speaker": "jarvis", "text": raw.split(":", 1)[1].strip()})
        else:
            self._send({"type": "sys", "text": raw})

    def show_content(self, title: str, text: str) -> None:
        self._send({"type": "content", "title": str(title)[:48], "text": str(text)[:8000]})

    def prompt_reconfig(self) -> None:
        self._ready = False
        self._send({"type": "setup_required"})

    def show_camera_frame(self, img_bytes: bytes) -> None:
        self._send({"type": "camera_frame", "bytes": len(img_bytes)})

    def start_camera_stream(self) -> None:
        self._send({"type": "camera", "active": True})

    def stop_camera_stream(self) -> None:
        self._send({"type": "camera", "active": False})

    def start_speaking(self) -> None:
        self.set_state("SPEAKING")

    def stop_speaking(self) -> None:
        if not self.muted:
            self.set_state("LISTENING")
