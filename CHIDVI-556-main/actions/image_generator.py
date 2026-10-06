"""CHIDVI image generation action powered by Gemini with live narration."""
from __future__ import annotations
import base64, json, os, sys, threading, time
from pathlib import Path

def _base_dir() -> Path:
    if getattr(sys, "frozen", False): return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent
BASE_DIR = _base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
OUTPUT_DIR = BASE_DIR / "generated_images"
MODEL = "gemini-3.1-flash-image"

def _api_key() -> str:
    with API_CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    key = data.get("gemini_api_key") or data.get("GEMINI_API_KEY")
    if not key: raise ValueError("Gemini API key is missing from config/api_keys.json.")
    return key

def _safe_filename(filename: str = "") -> str:
    name = Path(filename).name.strip()
    if not name: name = f"chidvi_image_{time.strftime('%Y%m%d_%H%M%S')}.png"
    if Path(name).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}: name += ".png"
    return name

def _open_file(path: Path) -> None:
    try:
        if sys.platform.startswith("win"): os.startfile(str(path))
        elif sys.platform == "darwin": os.system(f'open "{path}"')
        else: os.system(f'xdg-open "{path}" >/dev/null 2>&1 &')
    except Exception: pass

def _extract_image(response):
    for part in (getattr(response, "parts", None) or []):
        if getattr(part, "inline_data", None) is not None: return part
    for candidate in (getattr(response, "candidates", None) or []):
        for part in (getattr(getattr(candidate, "content", None), "parts", None) or []):
            if getattr(part, "inline_data", None) is not None: return part
    return None

def _save_part(part, path: Path) -> None:
    data = getattr(getattr(part, "inline_data", None), "data", None)
    if isinstance(data, str): data = base64.b64decode(data)
    if not data: raise RuntimeError("Gemini returned empty image data.")
    path.write_bytes(data)

def _say(speak, player, text: str) -> None:
    if player:
        try: player.write_log(f"[Image] {text}")
        except Exception: pass
    if speak:
        try: speak(text)
        except Exception: pass

def create_image(prompt: str = "", filename: str = "", aspect_ratio: str = "1:1", image_size: str = "1K", open_after: bool = True, player=None, speak=None, **_kwargs) -> str:
    prompt = (prompt or "").strip()
    if not prompt: return "Please describe the image you want me to create."
    valid_ratios = {"1:1","3:2","2:3","3:4","4:3","4:5","5:4","9:16","16:9","21:9"}
    if aspect_ratio not in valid_ratios: aspect_ratio = "1:1"
    if image_size not in {"512","1K","2K","4K"}: image_size = "1K"
    _say(speak, player, f"I'm creating the image now: {prompt[:100]}.")
    stop = threading.Event()
    def narrate():
        updates = ["I've started the image generation. I'm waiting for the render to finish.","The image is still rendering. I'm keeping an eye on the generation.","The render is taking a little longer, but it's still in progress."]
        i = 0
        while not stop.wait(6):
            _say(speak, player, updates[min(i, len(updates)-1)]); i += 1
    thread = threading.Thread(target=narrate, daemon=True); thread.start()
    try:
        from google import genai
        client = genai.Client(api_key=_api_key())
        response = client.models.generate_content(model=MODEL, contents=prompt, config={"response_modalities":["TEXT","IMAGE"],"image_config":{"aspect_ratio":aspect_ratio,"image_size":image_size}})
        part = _extract_image(response)
        if part is None:
            msg = f"Gemini did not return an image. {(getattr(response, 'text', '') or '')[:500]}".strip(); _say(speak, player, msg); return msg
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_DIR / _safe_filename(filename)
        _save_part(part, path)
        if open_after: _open_file(path)
        msg = f"Image created successfully and saved to: {path}"
        _say(speak, player, "The image is finished. I've saved it and opened the result for you." if open_after else "The image is finished and saved.")
        return msg
    except Exception as exc:
        error = f"Image generation failed: {exc}"; _say(speak, player, error); return error
    finally: stop.set()

TOOL = {"name":"create_image","description":"Generate an image from a natural-language prompt using Gemini, save it locally, optionally open it, and narrate progress while rendering.","parameters":{"type":"OBJECT","properties":{"prompt":{"type":"STRING","description":"Detailed description of the image to create."},"filename":{"type":"STRING","description":"Optional output filename."},"aspect_ratio":{"type":"STRING","description":"Aspect ratio such as 1:1, 16:9, or 9:16."},"image_size":{"type":"STRING","description":"Resolution: 512, 1K, 2K, or 4K."},"open_after":{"type":"BOOLEAN","description":"Open the generated image after saving."}},"required":["prompt"]},"handler":create_image}
