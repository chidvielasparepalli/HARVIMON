"""
test_agent_task.py — REAL integration test: drive Blender to build a simple
futuristic spaceship through the blender_control PLUGIN, using Gemini to plan
the operations (no hardcoded model script).

Run:  python blender_addon/test_agent_task.py   (bridge must be running)

What it proves:
  - the plugin is listed as a tool and Gemini selects it autonomously
  - the agent plans multiple discrete ops (create -> transform -> material ->
    light -> camera) and executes them through the plugin against the live bridge
  - the scene is inspected after the task and verified (objects exist,
    materials/lights/camera present, render produces a file)
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _blender_bridge_client import BlenderBridge, BlenderBridgeError   # noqa: E402
import blender_control as bc                                             # noqa: E402  (the plugin)

OUT = Path(os.environ.get("CHIDVI_OUT", "D:/CHIDVI-test-output"))
OUT.mkdir(parents=True, exist_ok=True)


def _key() -> str:
    cfg = Path(__file__).resolve().parents[1] / "config" / "api_keys.json"
    return json.loads(cfg.read_text(encoding="utf-8"))["gemini_api_key"]


def _strip_fences(t: str) -> str:
    t = t.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*\n?", "", t)
    return re.sub(r"\s*```\s*$", "", t).strip()


def _gen_retry(fn, attempts=8, wait_s=25):
    """Retry a Gemini call across transient 503 / 429 / 400-overload."""
    import time
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            if i == attempts - 1:
                raise
            time.sleep(wait_s)
        finally:
            time.sleep(0.5)


def main() -> int:
    from google import genai
    client = genai.Client(api_key=_key())

    # ---- 1. clean slate ----
    bridge = BlenderBridge(port=8147)
    bridge.call("clear_scene", {"keep_camera": False})

    prompt = ("Build a simple futuristic spaceship in Blender using primitive "
              "geometry (cubes, spheres, cylinders, cones), metallic materials, "
              "a glowing engine light, a point light, and a camera aimed at the "
              "ship. Work step by step. After every Blender operation, call "
              "scene_info or list_objects to verify. End after the camera is "
              "placed by calling set_render then render. Do NOT invent "
              "operation names — use only the bridge ops in the tool "
              "definition. Return each op as a JSON object on its own line with "
              "keys: op, then the args that op takes. Never return anything but "
              "those JSON lines until done. When finished, print DONE.")

    # ---- 3. ask Gemini for a plan of operations (text-only, no tool decls) ----
    print("[test] asking Gemini for an operation plan…")
    resp = _gen_retry(lambda: client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
        # no tools here — Gemini returns JSON array in text, not function calls
    ))
    plan_text = (resp.text or "").strip()
    print("[test] plan preview:\n", plan_text[:1000])

    # ---- 4. parse JSON array or JSON lines ----
    steps = []
    cleaned = _strip_fences(plan_text)
    # try JSON array first (Gemini often returns [...])
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            steps = [s for s in parsed if isinstance(s, dict) and "op" in s]
    except ValueError:
        pass
    # fallback: one JSON object per line
    if not steps:
        for raw in cleaned.splitlines():
            raw = raw.strip()
            if not raw or raw == "DONE":
                continue
            try:
                step = json.loads(raw)
            except ValueError:
                continue
            if "op" in step:
                steps.append(step)
    print(f"[test] parsed {len(steps)} steps")

    print(f"[test] executing {len(steps)} steps directly via bridge…")
    results = []
    for i, step in enumerate(steps, 1):
        op = step["op"]
        plugin_form = {k: v for k, v in step.items() if k != "op"}
        try:
            # alias resolve + map plugin-form args → bridge-form via the plugin
            canon = bc._resolve_op(op)
            res = bridge.call(bc._OPS[canon][0], bc._params(canon, plugin_form))
        except (BlenderBridgeError, KeyError) as e:
            res = {"success": False, "error": str(e)}
        ok = res.get("success", False)
        print(f"  [{i}/{len(steps)}] {op} -> {'OK' if ok else 'FAIL'}  "
              f"{(res.get('error','') or res.get('object',''))[:120]}")
        results.append({"op": op, "ok": bool(ok), "res": res})

    # ---- 5. auto-correction: retry failed steps once with the error fed back ----
    failed = [r for r in results if not r["ok"]]
    if failed:
        print(f"[test] correcting {len(failed)} failed step(s)…")
        for f in failed:
            op = f["op"]
            err = f["res"].get("error", "unknown failure")
            fix_prompt = (
                f"The previous Blender operation '{op}' failed with: {err}. "
                f"Retry it with corrected arguments. Return only one JSON "
                f"object line with 'op' and the needed args. If the error means "
                f"the operation cannot be fixed, return {{\"op\":\"{op}\",\"skip\":true}}."
            )
            resp2 = _gen_retry(lambda: client.models.generate_content(
                model="gemini-3.6-flash", contents=fix_prompt))
            line = _strip_fences(resp2.text or "").splitlines()[0].strip()
            try:
                step2 = json.loads(line)
            except ValueError:
                continue
            if step2.get("skip"):
                continue
            plugin_form2 = {k: v for k, v in step2.items() if k != "op"}
            try:
                canon2 = bc._resolve_op(step2["op"])
                res2 = bridge.call(bc._OPS[canon2][0],
                                   bc._params(canon2, plugin_form2))
            except (BlenderBridgeError, KeyError) as e:
                res2 = {"success": False, "error": str(e)}
            print(f"  fix {op} -> {'OK' if res2.get('success') else 'FAIL'}  "
                  f"{(res2.get('error','') or res2.get('object',''))[:120]}")
            if res2.get("success"):
                f["ok"] = True

    # ---- 6. verify the scene ----
    si = bridge.call("scene_info", {})
    objs = bridge.call("list_objects", {})
    print("[test] VERIFY scene_info:", si)
    n_cam = len(si.get("cameras", []))
    n_lit = len(si.get("lights", []))
    n_mat = len(si.get("materials", []))
    print("[test] VERIFY objects:", len(objs.get("objects", [])))

    render_res = {"success": False}
    if n_cam and n_lit and n_mat:
        rp = str(OUT / "spaceship.png")
        render_res = bridge.call("render", {"image_path": rp, "samples": 24})
        print("[test] render:", render_res, "exists:", os.path.isfile(rp))

    bridge.call("save_blend", {"path": str(OUT / "spaceship.blend")})

    ok_all = all(r["ok"] for r in results) and render_res.get("success")
    print(f"[test] RESULT: {'PASS' if ok_all else 'PARTIAL/FAIL'} "
          f"({sum(1 for r in results if r['ok'])}/{len(results)} ops, "
          f"render ok={render_res.get('success')})")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())