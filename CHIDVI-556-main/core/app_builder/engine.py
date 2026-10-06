"""CHIDVI-556 autonomous full-stack web application builder.

Generated projects live outside the CHIDVI repository. Builder state never stores
secret values. The builder can plan, ask for missing information, generate files,
run a production build, open the app in Playwright, inspect the result, and repair
bounded failures before delivering the project.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from google import genai
from google.genai import types


DEFAULT_MODEL = os.getenv("CHIDVI_BUILDER_MODEL", "gemini-2.5-flash")
DEFAULT_ROOT = Path.home() / "Desktop" / "JarvisProjects"
MAX_FILES = 45
MAX_REPAIRS = 3


@dataclass
class BuilderResult:
    ok: bool
    status: str
    message: str
    project_dir: str | None = None
    details: dict[str, Any] | None = None

    def text(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "status": self.status,
                "message": self.message,
                "project_dir": self.project_dir,
                "details": self.details or {},
            },
            ensure_ascii=False,
        )


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip()).strip("-").lower()
    return (value or "chidvi-app")[:64]


def _api_key(base_dir: Path) -> str:
    for path in (
        base_dir / "config" / "api_keys.json",
        Path.home() / ".config" / "chidvi" / "api_keys.json",
    ):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            key = str(data.get("gemini_api_key") or data.get("GEMINI_API_KEY") or "").strip()
            if key:
                return key
        except Exception:
            pass
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key:
        return key
    raise RuntimeError("Gemini API key is missing. Configure CHIDVI's Gemini key first.")


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    fence = chr(96) * 3
    if text.startswith(fence):
        lines = text.splitlines()
        lines = lines[1:] if lines else []
        if lines and lines[-1].strip().startswith(fence):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _safe_rel(path: str) -> str:
    p = Path(path.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"Unsafe generated path: {path}")
    return p.as_posix()


def _run(cmd: list[str], cwd: Path, timeout: int = 120) -> tuple[int, str]:
    env = os.environ.copy()
    env.setdefault("CI", "1")
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "env": env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(cmd, **kwargs)
        return proc.returncode, ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return 124, "Command timed out."
    except FileNotFoundError:
        return 127, f"Command not found: {cmd[0]}"
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"


class AppBuilder:
    def __init__(
        self,
        base_dir: Path,
        *,
        root_dir: Path | None = None,
        logger: Callable[[str], None] = print,
        narrator: Callable[[str], None] | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.root_dir = Path(root_dir or DEFAULT_ROOT)
        self.logger = logger
        self.narrator = narrator
        self.model_name = model
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.client = genai.Client(api_key=_api_key(self.base_dir))

    @staticmethod
    def _state_path(project_dir: Path) -> Path:
        return project_dir / ".chidvi" / "builder_state.json"

    @classmethod
    def _load_state(cls, project_dir: Path) -> dict[str, Any]:
        return json.loads(cls._state_path(project_dir).read_text(encoding="utf-8"))

    @classmethod
    def _save_state(cls, project_dir: Path, state: dict[str, Any]) -> None:
        path = cls._state_path(project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = time.time()
        path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    def _event(self, project_dir: Path, message: str, state: dict[str, Any] | None = None) -> None:
        self.logger(f"[AppBuilder] {message}")
        if self.narrator:
            try:
                self.narrator(message)
            except Exception:
                pass
        if state is not None:
            state.setdefault("events", []).append({"ts": time.time(), "message": message})
            state["events"] = state["events"][-120:]
            self._save_state(project_dir, state)

    def _generate(self, prompt: str, image: bytes | None = None) -> str:
        contents: Any = prompt
        if image:
            contents = [
                types.Part.from_text(text=prompt),
                types.Part.from_bytes(data=image, mime_type="image/png"),
            ]
        response = self.client.models.generate_content(model=self.model_name, contents=contents)
        return (getattr(response, "text", "") or "").strip()

    def _plan(self, request: str, answers: dict[str, str]) -> dict[str, Any]:
        prompt = f"""
You are the principal product architect for CHIDVI-556.

USER REQUEST:
{request}

CLARIFICATION ANSWERS:
{json.dumps(answers, ensure_ascii=False, indent=2)}

Return ONLY valid JSON:
{{
  "project_name": "human readable",
  "slug": "kebab-case",
  "stack": {{
    "framework": "Next.js",
    "language": "TypeScript",
    "styling": "Tailwind CSS",
    "database": "none|PostgreSQL|Supabase"
  }},
  "pages": [{{"path": "/", "purpose": "..."}}],
  "features": ["..."],
  "design": {{
    "direction": "specific visual direction for this product",
    "primary_color": "...",
    "surface": "...",
    "typography": "...",
    "layout": "...",
    "motion": "..."
  }},
  "required_secrets": [
    {{"name":"GEMINI_API_KEY","reason":"...","secret":true}}
  ],
  "clarifying_questions": [
    {{"id":"question_id","question":"...","kind":"text|choice|secret","blocking":true}}
  ],
  "files": [
    {{"path":"package.json","purpose":"...","notes":"..."}}
  ]
}}

Rules:
- Prefer a single Next.js full-stack application for normal products.
- Use TypeScript.
- Keep the file list <= {MAX_FILES} and dependency-ordered.
- Include package.json, tsconfig.json, next config, app pages, components,
  server/API code when required, and README.
- Do not invent integrations the user did not request.
- Ask only for information that genuinely blocks implementation.
- Never invent credential values.
- Design must avoid generic AI boilerplate such as a purple gradient, excessive
  glassmorphism, identical rounded cards, and AI-sounding marketing filler.
"""
        plan = json.loads(_strip_fences(self._generate(prompt)))
        if not isinstance(plan, dict) or not isinstance(plan.get("files"), list):
            raise ValueError("Planner returned an incomplete application plan.")
        plan["files"] = plan["files"][:MAX_FILES]
        return plan

    def _file_prompt(self, plan: dict[str, Any], info: dict[str, Any], existing: list[str], answers: dict[str, str]) -> str:
        return f"""
You are the lead engineer implementing one file of a production-ready web app.

APPLICATION PLAN:
{json.dumps(plan, ensure_ascii=False, indent=2)}

CURRENT PROJECT FILES:
{json.dumps(existing, ensure_ascii=False)}

USER DECISIONS:
{json.dumps(answers, ensure_ascii=False)}

TARGET FILE: {info.get("path")}
PURPOSE: {info.get("purpose","")}
NOTES: {info.get("notes","")}

Return ONLY the complete file contents.

Rules:
- Use current idiomatic Next.js + TypeScript patterns.
- No TODOs, fake API calls, lorem ipsum, dead buttons, or empty placeholders.
- Keep client/server boundaries correct.
- Include real loading, error, empty, and success states where relevant.
- Use accessible semantic HTML and keyboard-friendly controls.
- Use the planned design system consistently.
- Make the content sound like a real product, not generated filler.
- Do not copy a generic dashboard pattern unless the product actually needs one.
"""

    def _write_file(self, project_dir: Path, path: str, content: str) -> None:
        rel = _safe_rel(path)
        target = project_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def _ensure_gitignore(self, project_dir: Path) -> None:
        (project_dir / ".gitignore").write_text(
            "node_modules\n.next\n.env\n.env.local\n.env.*.local\n!.env.example\n.DS_Store\ndist\ncoverage\n.chidvi/*.log\n.chidvi/preview.png\n",
            encoding="utf-8",
        )

    def _write_env(self, project_dir: Path, key: str, value: str) -> None:
        path = project_dir / ".env.local"
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        prefix = f"{key}="
        for index, line in enumerate(lines):
            if line.startswith(prefix):
                lines[index] = prefix + value
                break
        else:
            lines.append(prefix + value)
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def start(self, request: str) -> BuilderResult:
        request = (request or "").strip()
        if not request:
            return BuilderResult(False, "ERROR", "Tell me what website you want me to build.")

        plan = self._plan(request, {})
        slug = _slug(str(plan.get("slug") or plan.get("project_name") or "chidvi-app"))
        project_dir = self.root_dir / slug

        if project_dir.exists() and self._state_path(project_dir).exists():
            state = self._load_state(project_dir)
            return BuilderResult(True, state.get("status", "EXISTING"), f"A builder task already exists for {slug}.", str(project_dir), {"state": state})

        project_dir.mkdir(parents=True, exist_ok=True)
        state: dict[str, Any] = {
            "version": 1,
            "status": "PLANNING",
            "request": request,
            "project_dir": str(project_dir),
            "plan": plan,
            "answers": {},
            "provided_secrets": [],
            "pending_question": None,
            "repair_attempts": 0,
            "events": [],
            "created_at": time.time(),
        }
        self._save_state(project_dir, state)
        self._event(project_dir, "I understand the goal. I’m creating the implementation plan now.", state)

        questions = plan.get("clarifying_questions") or []
        secrets = [x for x in (plan.get("required_secrets") or []) if x.get("secret")]
        if questions:
            pending = questions[0]
            state["pending_question"] = pending
            state["status"] = "WAITING_FOR_USER"
            self._save_state(project_dir, state)
            message = pending.get("question", "I need one decision before I continue.")
            self._event(project_dir, message, state)
            return BuilderResult(True, "WAITING_FOR_USER", message, str(project_dir), {"question": pending})
        if secrets:
            pending = {"id": "secret_0", **secrets[0], "kind": "secret", "blocking": True}
            state["pending_question"] = pending
            state["status"] = "WAITING_FOR_USER"
            self._save_state(project_dir, state)
            message = f"I need {pending['name']} before I can wire the requested integration. I’ll keep it out of the builder state."
            self._event(project_dir, message, state)
            return BuilderResult(True, "WAITING_FOR_USER", message, str(project_dir), {"question": pending})

        return self._continue(project_dir, state)

    def resume(self, project_dir: Path, answer: str) -> BuilderResult:
        state = self._load_state(project_dir)
        pending = state.get("pending_question")
        if not pending:
            return self._continue(project_dir, state)
        answer = (answer or "").strip()
        if not answer:
            return BuilderResult(False, "WAITING_FOR_USER", "I still need the requested information.", str(project_dir), {"question": pending})

        if pending.get("kind") == "secret":
            secret_name = str(pending.get("name") or pending.get("id") or "CHIDVI_SECRET").upper()
            self._write_env(project_dir, secret_name, answer)
            state.setdefault("provided_secrets", [])
            if secret_name not in state["provided_secrets"]:
                state["provided_secrets"].append(secret_name)
            self._event(project_dir, "Thanks. I stored the credential in the generated app’s local environment file and will not echo it back.", state)
        else:
            state.setdefault("answers", {})[str(pending.get("id") or "answer")] = answer

        state["pending_question"] = None
        state["status"] = "PLANNING"
        state["plan"] = self._plan(state["request"], state.get("answers", {}))
        self._save_state(project_dir, state)

        questions = state["plan"].get("clarifying_questions") or []
        if questions:
            state["pending_question"] = questions[0]
            state["status"] = "WAITING_FOR_USER"
            self._save_state(project_dir, state)
            return BuilderResult(True, "WAITING_FOR_USER", questions[0].get("question", "I need one more decision."), str(project_dir), {"question": questions[0]})

        provided = set(state.get("provided_secrets") or [])
        secrets = [
            x for x in (state["plan"].get("required_secrets") or [])
            if x.get("secret") and str(x.get("name") or "").upper() not in provided
        ]
        if secrets:
            pending = {"id": f"secret_{len(provided)}", **secrets[0], "kind": "secret", "blocking": True}
            state["pending_question"] = pending
            state["status"] = "WAITING_FOR_USER"
            self._save_state(project_dir, state)
            message = f"I need {pending['name']} before I can wire the requested integration. I’ll keep it out of the builder state."
            self._event(project_dir, message, state)
            return BuilderResult(True, "WAITING_FOR_USER", message, str(project_dir), {"question": pending})
        return self._continue(project_dir, state)

    def status(self, project_dir: Path) -> BuilderResult:
        state = self._load_state(project_dir)
        return BuilderResult(True, state.get("status", "UNKNOWN"), f"{project_dir} — {state.get('status')}", str(project_dir), {"events": state.get("events", [])[-10:], "pending_question": state.get("pending_question")})

    def cancel(self, project_dir: Path) -> BuilderResult:
        state = self._load_state(project_dir)
        state["status"] = "CANCELLED"
        state["pending_question"] = None
        self._save_state(project_dir, state)
        self._event(project_dir, "I stopped the app-building task. The generated files remain available for review.", state)
        return BuilderResult(True, "CANCELLED", "Builder task cancelled.", str(project_dir))

    def _continue(self, project_dir: Path, state: dict[str, Any]) -> BuilderResult:
        plan = state["plan"]
        state["status"] = "IMPLEMENTING"
        self._save_state(project_dir, state)
        self._event(project_dir, "The plan is ready. I’m building the project structure and UI now.", state)
        self._ensure_gitignore(project_dir)

        completed: list[str] = []
        files = plan.get("files") or []
        for index, info in enumerate(files, start=1):
            path = _safe_rel(str(info.get("path") or ""))
            if not path:
                continue
            if (project_dir / path).exists() and path not in {"package.json"}:
                completed.append(path)
                continue
            percent = round(index * 100 / max(1, len(files)))
            self._event(project_dir, f"I’m implementing {path} ({percent}% of the planned source).", state)
            content = _strip_fences(self._generate(self._file_prompt(plan, info, completed, state.get("answers", {}))))
            if not content:
                raise RuntimeError(f"No content was generated for {path}.")
            self._write_file(project_dir, path, content)
            completed.append(path)

        state["completed_files"] = completed
        state["status"] = "INSTALLING"
        self._save_state(project_dir, state)
        self._event(project_dir, "The source is in place. I’m installing dependencies and checking the production build.", state)

        code, output = _run(["npm", "install"], project_dir, 240)
        if code != 0:
            return self._repair(project_dir, state, "dependency_install", output)

        state["status"] = "BUILDING"
        self._save_state(project_dir, state)
        code, output = _run(["npm", "run", "build"], project_dir, 240)
        if code != 0:
            return self._repair(project_dir, state, "build", output)

        self._event(project_dir, "The production build passes. I’m opening the app in a real browser and inspecting the UI.", state)
        visual = self._browser_verify(project_dir, state)
        if not visual["ok"]:
            return self._repair(project_dir, state, "visual", visual.get("message", "Visual verification failed."), visual.get("screenshot"))

        state["status"] = "COMPLETED"
        state["verification"] = {"build": "passed", "browser": visual, "completed_at": time.time()}
        self._save_state(project_dir, state)
        self._event(project_dir, "The application passed build and browser verification. Your full-stack project is ready.", state)
        return BuilderResult(True, "COMPLETED", f"Finished building the application at {project_dir}", str(project_dir), {"verification": state["verification"], "files": completed})

    def _repair(self, project_dir: Path, state: dict[str, Any], kind: str, details: str, screenshot: bytes | None = None) -> BuilderResult:
        attempt = int(state.get("repair_attempts", 0)) + 1
        state["repair_attempts"] = attempt
        if attempt > MAX_REPAIRS:
            state["status"] = "FAILED"
            state["last_error"] = details[-6000:]
            self._save_state(project_dir, state)
            self._event(project_dir, "I reached the repair limit without a verified result, so I’m stopping instead of pretending it worked.", state)
            return BuilderResult(False, "FAILED", "The generated project needs manual attention.", str(project_dir), {"error": details[-4000:]})

        self._event(project_dir, f"I found a {kind} issue. I’m diagnosing it and applying repair pass {attempt}.", state)
        files = [
            p.as_posix()
            for p in project_dir.rglob("*")
            if p.is_file() and ".chidvi" not in p.parts and "node_modules" not in p.parts
        ]
        prompt = f"""
You are debugging a real generated web application.

FAILURE TYPE:
{kind}

FAILURE DETAILS:
{details[-12000:]}

PROJECT FILES:
{json.dumps(files, ensure_ascii=False)}

Return ONLY JSON:
{{
  "path": "relative/path/to/file",
  "content": "complete replacement contents",
  "reason": "short root-cause explanation"
}}

Fix only the root cause. Do not change unrelated files.
"""
        if screenshot:
            prompt += "\nA screenshot is attached. Use it to diagnose the visual issue.\n"
        fix = json.loads(_strip_fences(self._generate(prompt, screenshot)))
        path = _safe_rel(str(fix.get("path") or ""))
        content = str(fix.get("content") or "")
        if not path or not content:
            return BuilderResult(False, "FAILED", "The repair model returned an invalid fix.", str(project_dir))
        self._write_file(project_dir, path, content)
        return self._continue(project_dir, state)

    def _browser_verify(self, project_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            return {"ok": False, "message": f"Playwright is unavailable: {exc}"}

        port = int(os.getenv("CHIDVI_BUILDER_PORT", "3180"))
        chidvi_dir = project_dir / ".chidvi"
        chidvi_dir.mkdir(parents=True, exist_ok=True)
        log_path = chidvi_dir / "dev_server.log"
        handle = log_path.open("w", encoding="utf-8")
        proc = None
        try:
            kwargs: dict[str, Any] = {
                "cwd": str(project_dir),
                "stdout": handle,
                "stderr": subprocess.STDOUT,
                "text": True,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            proc = subprocess.Popen(["npm", "run", "dev", "--", "--hostname", "127.0.0.1", "--port", str(port)], **kwargs)
            url = f"http://127.0.0.1:{port}"
            ready = False
            for _ in range(30):
                time.sleep(0.75)
                if proc.poll() is not None:
                    break
                try:
                    import urllib.request
                    with urllib.request.urlopen(url, timeout=2) as response:
                        if response.status < 500:
                            ready = True
                            break
                except Exception:
                    pass
            if not ready:
                return {"ok": False, "message": log_path.read_text(encoding="utf-8", errors="replace")[-6000:]}

            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=True)
                except Exception:
                    self._event(project_dir, "Playwright is installed but Chromium is missing. I’m installing the browser dependency once so I can verify the real UI.", state)
                    code, install_output = _run(
                        [os.sys.executable, "-m", "playwright", "install", "chromium"],
                        self.base_dir,
                        300,
                    )
                    if code != 0:
                        return {"ok": False, "message": f"Chromium installation failed: {install_output[-4000:]}"}
                    browser = pw.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
                console_errors: list[str] = []
                page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
                page.on("pageerror", lambda exc: console_errors.append(str(exc)))
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                screenshot_path = chidvi_dir / "preview.png"
                page.screenshot(path=str(screenshot_path), full_page=True)
                body_text = page.locator("body").inner_text(timeout=10000)
                title = page.title()
                screenshot = screenshot_path.read_bytes()
                browser.close()

            if console_errors:
                return {"ok": False, "message": "\n".join(console_errors[-20:]), "screenshot": screenshot}
            if not body_text.strip():
                return {"ok": False, "message": "The browser rendered an empty page.", "screenshot": screenshot}

            review_prompt = f"""
Review this screenshot of a generated web application.
Title: {title}
Visible text excerpt: {body_text[:3000]}

Return ONLY JSON:
{{"verdict":"pass|repair","reason":"specific reason","issues":["..."]}}

Use repair only for clearly broken, empty, unstyled, overflowing, dead, or obviously
generic boilerplate UI. Do not reject normal product choices merely because they
differ from your own taste.
"""
            review = json.loads(_strip_fences(self._generate(review_prompt, screenshot)))
            return {
                "ok": review.get("verdict") != "repair",
                "message": review.get("reason", "Browser verification passed."),
                "title": title,
                "review": review,
            }
        finally:
            if proc is not None:
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=4)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                except Exception:
                    pass
            handle.close()
