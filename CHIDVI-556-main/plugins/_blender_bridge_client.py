"""
_blender_bridge_client.py — shared HTTP+HMAC client for the CHIDVI-556
Blender bridge add-on (blender_addon/blender_chidvi_bridge.py).

Internal shared helper (leading `_` in the filename means the plugin loader
skips it — import it explicitly from blender_control.py). Never exposes a
network endpoint; only outbound localhost calls.
"""

import hashlib
import hmac
import json
import os
import socket
import threading
from pathlib import Path

import requests


def read_token() -> str:
    """Best-effort auto-pair: read the token the add-on wrote to Blender's
    user scripts dir. Returns '' if not found (caller falls back to settings)."""
    base = Path(os.environ.get("APPDATA", "")) / "Blender Foundation" / "Blender"
    try:
        for v in base.iterdir():
            f = v / "scripts" / "CHIDVI_bridge_token.txt"
            if f.is_file():
                return f.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


class BlenderBridgeError(Exception):
    """Raised on any transport/auth/error response, never silently swallowed."""


class BlenderBridge:
    def __init__(self, host: str = "127.0.0.1", port: int = 8147,
                 token: str = "", timeout: float = 5.0):
        self.host = host
        self.port = int(port)
        self._token = token or read_token()
        self.timeout = timeout
        self._lock = threading.Lock()   # health probes can be concurrent

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def _sign(self, body: bytes) -> str:
        return hmac.new(self._token.encode(), body, hashlib.sha256).hexdigest()

    def health(self) -> dict:
        """Quick connectivity probe (no auth)."""
        try:
            r = requests.get(f"{self.base_url}/health", timeout=self.timeout)
            return {"ok": r.status_code == 200, "version": r.json().get("blender_version", "")}
        except requests.RequestException as e:
            return {"ok": False, "error": str(e)}

    def call(self, cmd: str, args: dict | None = None, timeout: float | None = None) -> dict:
        """Invoke a bridge command. Returns the *result* dict (which carries
        success/error). Raises BlenderBridgeError only on transport failure,
        bad auth, or a server 500 — the caller decides what 'success' means."""
        body = json.dumps({"cmd": cmd, "args": args or {}}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-CHIDVI-Sig": self._sign(body),
        }
        # long commands (render, file ops, heavy scenes) need room to breathe
        if timeout is None:
            timeout = 120.0 if cmd in ("render", "open_blend", "save_blend") else self.timeout
        t = timeout
        try:
            r = requests.post(f"{self.base_url}/rpc", data=body,
                              headers=headers, timeout=t)
        except requests.RequestException as e:
            raise BlenderBridgeError(f"bridge unreachable: {e}")
        if r.status_code == 401:
            raise BlenderBridgeError("bridge rejected signature — token mismatch "
                                     "(set the same token in CHIDVI settings and "
                                     "the bridge add-on)")
        if r.status_code != 200:
            raise BlenderBridgeError(f"bridge HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError:
            raise BlenderBridgeError("bridge returned non-JSON")

    def status(self) -> dict:
        try:
            return self.call("status", {}, timeout=3)
        except BlenderBridgeError as e:
            return {"success": False, "error": str(e)}