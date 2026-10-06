"""Long-horizon autonomous agent for CHIDVI-556."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from google import genai
from google.genai import types
from core.action_loader import ActionRegistry, discover_actions

class LongHorizonAgent:
    def __init__(self, *, base_dir: Path, context: dict[str, Any] | None = None, max_steps: int = 32, logger=print):
        self.base_dir = Path(base_dir)
        self.context = context or {}
        self.max_steps = max(1, min(int(max_steps), 50))
        self.logger = logger
        self.history: list[dict[str, Any]] = []
        self.registry: ActionRegistry = discover_actions(self.base_dir / "actions", logger=logger)
        self.registry._actions.pop("autonomous_task", None)
        self.client = genai.Client(api_key=self._api_key())

    def _api_key(self) -> str:
        path = self.base_dir / "config" / "api_keys.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        key = str(data.get("gemini_api_key") or data.get("GEMINI_API_KEY") or "").strip()
        if not key: raise RuntimeError("Gemini API key is missing from config/api_keys.json")
        return key

    def _planner_prompt(self, objective: str) -> str:
        tools = self.registry.get_tool_declarations()
        history = json.dumps(self.history[-12:], ensure_ascii=False, default=str)
        return f"""You are CHIDVI-556's long-horizon task planner.
USER OBJECTIVE:\n{objective}
AVAILABLE REAL TOOLS:\n{json.dumps(tools, ensure_ascii=False)}
EXECUTION HISTORY:\n{history}
Plan ONE next action only. Observe its result before choosing the next action. Continue until the objective is actually verified complete. Never claim an action happened unless its tool result says so. Never invent personal information, credentials, answers, dates, or portal data. Use browser_control for websites and memory when needed. Ask the user when required information is missing or ambiguous. Never bypass CAPTCHA, OTP, MFA, passwords, or access controls. Do not answer or submit graded assessments on the user's behalf.
Return JSON only: {{"done":false,"summary":"short status","tool":"tool_name","arguments":{{}},"reason":"why this is next"}}. When verified complete: {{"done":true,"summary":"verified outcome"}}"""

    def _plan(self, objective: str) -> dict[str, Any]:
        response = self.client.models.generate_content(model="gemini-2.5-flash", contents=self._planner_prompt(objective), config=types.GenerateContentConfig(response_mime_type="application/json"))
        decision = json.loads(getattr(response, "text", "") or "{}")
        if not isinstance(decision, dict): raise ValueError("Planner returned invalid JSON")
        return decision

    def run(self, objective: str) -> dict[str, Any]:
        objective = str(objective).strip()
        if not objective: return {"ok":False,"summary":"No objective supplied.","steps":[]}
        self.history = []
        for step in range(1, self.max_steps + 1):
            try: decision = self._plan(objective)
            except Exception as exc:
                self.history.append({"step":step,"tool":"planner","ok":False,"output":f"{type(exc).__name__}: {exc}"})
                return {"ok":False,"summary":"Planner failed; no further action was taken.","steps":self.history}
            if decision.get("done") is True: return {"ok":True,"summary":decision.get("summary","Task completed."),"steps":self.history}
            name = str(decision.get("tool", "")).strip(); args = decision.get("arguments") or {}
            if not name or not isinstance(args, dict):
                self.history.append({"step":step,"tool":name,"ok":False,"output":"Planner produced an invalid action."}); continue
            if not self.registry.has(name):
                self.history.append({"step":step,"tool":name,"ok":False,"output":"Unknown tool."}); continue
            self.logger(f"[Autonomy {step}/{self.max_steps}] {decision.get('reason','Executing next action.')}")
            output = self.registry.run(name, args, self.context)
            self.history.append({"step":step,"tool":name,"ok":True,"output":output})
        return {"ok":False,"summary":f"Stopped after {self.max_steps} steps before the objective was verified complete.","steps":self.history}
