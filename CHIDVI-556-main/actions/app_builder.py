"""Agent action that exposes CHIDVI-556's autonomous web app builder."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from core.app_builder.engine import AppBuilder


TOOL = {
    "name": "app_builder",
    "description": (
        "Builds a complete full-stack web application from a natural-language request. "
        "CHIDVI plans the product, asks only for blocking information or credentials, "
        "generates the codebase, installs dependencies, builds it, opens it in a real "
        "browser, checks the UI, repairs bounded failures, narrates progress, and "
        "returns the generated project path. Use action='start' for a new app, "
        "action='resume' after the user answers a builder question, action='status' "
        "to inspect an existing task, and action='cancel' to stop one."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "start | resume | status | cancel",
            },
            "request": {
                "type": "STRING",
                "description": "Natural-language description of the application to build. Required for start.",
            },
            "project_dir": {
                "type": "STRING",
                "description": "Existing builder workspace path. Use the path returned by a previous builder call.",
            },
            "answer": {
                "type": "STRING",
                "description": "Answer to the latest blocking builder question. Required for resume.",
            },
        },
        "required": ["action"],
    },
}


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _narrate(speak, player, text: str) -> None:
    if player is not None:
        try:
            player.write_log("BUILD: " + text)
        except Exception:
            pass
    if speak is not None:
        try:
            speak(text)
        except Exception:
            pass


def app_builder(
    parameters: dict[str, Any],
    player=None,
    speak=None,
    **_: Any,
) -> str:
    action = str(parameters.get("action") or "start").strip().lower()
    project_dir = str(parameters.get("project_dir") or "").strip()

    def log(message: str) -> None:
        _narrate(speak, player, message)

    builder = AppBuilder(_base_dir(), logger=lambda message: log(message), narrator=None)

    try:
        if action == "start":
            request = str(parameters.get("request") or "").strip()
            if not request:
                return "I need the website/app description to start building."
            result = builder.start(request)
        elif action == "resume":
            if not project_dir:
                return "I need the project_dir returned by the previous builder response."
            result = builder.resume(Path(project_dir), str(parameters.get("answer") or ""))
        elif action == "status":
            if not project_dir:
                return "I need the project_dir to inspect the builder task."
            result = builder.status(Path(project_dir))
        elif action == "cancel":
            if not project_dir:
                return "I need the project_dir to cancel the builder task."
            result = builder.cancel(Path(project_dir))
        else:
            return "Unknown app_builder action. Use start, resume, status, or cancel."

        if result.project_dir:
            log(result.message)
        return result.text()
    except Exception as exc:
        log(f"App Builder stopped safely: {type(exc).__name__}: {exc}")
        return f'{{"ok":false,"status":"ERROR","message":{__import__("json").dumps(str(exc))}}}'
