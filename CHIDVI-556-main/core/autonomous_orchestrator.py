"""Autonomous task orchestration for CHIDVI-556.

This layer turns a high-level user objective into an execution loop. It is
provider-agnostic: local CHIDVI actions/plugins and external MCP adapters can
register tools through the same interface.

Safety model:
- planning is bounded by max_steps;
- every tool call is observable and recorded;
- failures are returned to the planner instead of being hidden;
- destructive/irreversible actions can be marked as requiring confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    provider: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "OBJECT", "properties": {}})
    requires_confirmation: bool = False
    executor: Callable[[dict[str, Any]], Any] | None = None


@dataclass
class StepResult:
    step: int
    tool: str
    ok: bool
    output: Any


class Planner(Protocol):
    def plan(self, objective: str, tools: list[ToolSpec], history: list[StepResult]) -> dict[str, Any]: ...


class AutonomousOrchestrator:
    """Execute bounded multi-tool plans while keeping execution state explicit."""

    def __init__(self, planner: Planner, *, max_steps: int = 12):
        self.planner = planner
        self.max_steps = max(1, max_steps)
        self.tools: dict[str, ToolSpec] = {}
        self.history: list[StepResult] = []

    def register(self, tool: ToolSpec) -> None:
        if not tool.name or tool.executor is None:
            raise ValueError("Autonomous tools need a name and executor")
        self.tools[tool.name] = tool

    def declarations(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": f"[{t.provider}] {t.description}",
                "parameters": t.parameters,
            }
            for t in self.tools.values()
        ]

    def run(self, objective: str, *, confirm: Callable[[ToolSpec, dict[str, Any]], bool] | None = None) -> dict[str, Any]:
        self.history = []
        for step in range(1, self.max_steps + 1):
            decision = self.planner.plan(objective, list(self.tools.values()), self.history)
            if not isinstance(decision, dict):
                return self._finish(False, "Planner returned an invalid decision.")

            if decision.get("done") is True:
                return self._finish(True, decision.get("summary", "Task completed."))

            name = decision.get("tool")
            args = decision.get("arguments") or {}
            tool = self.tools.get(name)
            if tool is None:
                self.history.append(StepResult(step, str(name), False, "Unknown tool."))
                continue

            if tool.requires_confirmation and (confirm is None or not confirm(tool, args)):
                self.history.append(StepResult(step, tool.name, False, "Confirmation required."))
                return self._finish(False, f"Stopped before '{tool.name}' because confirmation is required.")

            try:
                output = tool.executor(args)
                self.history.append(StepResult(step, tool.name, True, output))
            except Exception as exc:
                self.history.append(StepResult(step, tool.name, False, f"{type(exc).__name__}: {exc}"))

        return self._finish(False, f"Stopped after reaching the {self.max_steps}-step limit.")

    def _finish(self, ok: bool, summary: str) -> dict[str, Any]:
        return {
            "ok": ok,
            "summary": summary,
            "steps": [
                {"step": r.step, "tool": r.tool, "ok": r.ok, "output": r.output}
                for r in self.history
            ],
        }
