"""High-level long-horizon autonomous task entry point for CHIDVI-556."""
from __future__ import annotations

from core.long_horizon_agent import LongHorizonAgent

PLUGIN = None

TOOL = {
    "name": "autonomous_task",
    "description": "Execute a bounded long-horizon objective using CHIDVI's real actions; plan one step, observe its result, and continue until verified.",
    "parameters": {"type":"OBJECT","properties":{"objective":{"type":"STRING"},"max_steps":{"type":"INTEGER","description":"Maximum autonomous iterations, capped at 50."}},"required":["objective"]},
    "handler": None,
}

def run(parameters: dict, **ctx) -> str:
    objective = str(parameters.get("objective", "")).strip()
    if not objective:
        return "No autonomous objective was supplied."
    try: max_steps = int(parameters.get("max_steps", 32))
    except (TypeError, ValueError): max_steps = 32
    try:
        from main import BASE_DIR
    except Exception:
        BASE_DIR = __import__("pathlib").Path(__file__).resolve().parents[1]
    def log(message: str) -> None:
        text = str(message)
        speaker = ctx.get("speak")
        if callable(speaker):
            try: speaker(text)
            except Exception: pass
        player = ctx.get("player")
        writer = getattr(player, "write_log", None)
        if callable(writer):
            try: writer(text)
            except Exception: pass
        print(text)
    agent = LongHorizonAgent(base_dir=BASE_DIR, context=ctx, max_steps=max_steps, logger=log)
    return str(agent.run(objective))

TOOL["handler"] = run
