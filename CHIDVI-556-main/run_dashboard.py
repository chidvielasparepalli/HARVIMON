"""
Development launcher for the CHIDVI FastAPI dashboard.

This starts only the web dashboard, prints a temporary login key, and keeps the
server running. For full assistant execution, launch main.py instead; the real
desktop runtime consumes the dashboard command queue.
"""

from __future__ import annotations

import asyncio
import sys

from dashboard import server as dashboard_server


def _missing_deps_message() -> str:
    return (
        "FastAPI dashboard dependencies are not installed in this Python environment.\n\n"
        "Install them with:\n"
        "  python -m pip install fastapi \"uvicorn[standard]\" cryptography python-multipart\n\n"
        "Or install all project dependencies with:\n"
        "  python -m pip install -r requirements.txt\n"
    )


async def _main() -> int:
    if not dashboard_server._DEPS_OK:
        print(_missing_deps_message())
        return 1

    dash = dashboard_server.DashboardServer()
    key = dash.new_key(expiry_secs=3600)

    print("")
    print("CHIDVI dashboard is starting.")
    print(f"URL: {dash.get_url()}")
    print(f"Login key: {key}")
    print("")
    print("Note: run main.py for the full assistant. This launcher is for opening")
    print("and checking the FastAPI web UI only; it does not start Gemini Live.")
    print("")

    await dash.serve()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main()))
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        raise SystemExit(0)
