"""
test_plugin_flow.py — drive the blender_control PLUGIN's public `run()` entry
(no Gemini) end-to-end against the live bridge. Proves the plugin executes a
multi-step general modeling task through the exact tool the CHIDVI-556 agent
selects: create primitives → transform → materials → light → camera → render →
verify. Pure stdlib; depends on a running bridge (headless_driver.py or GUI).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import blender_control as bc

OUT = Path("D:/CHIDVI-test-output")


def logline(tag, msg):
    print(f"[{tag}] {msg}")


def main() -> int:
    # Destructive ops (render, save) go through the confirm gate. Headless has no
    # HUD to press CONFIRM, so hook the gate to dispatch the op directly — this
    # exercises the destructive-dispatch + result-verification path.
    _bridge = None
    def _run_now_hook(player, op, params, bridge, detail):
        nonlocal _bridge
        _bridge = bridge
        return bc._run_now(op, params, bridge)   # runs synchronously, returns text
    bc._ask_confirm = _run_now_hook

    ops = [
        # scene inspection
        ("status",           {}),
        # build a small rocket: body (cylinder) + nose (cone) + fins (cube)
        ("create_primitive", {"type": "cylinder", "name": "RocketBody",
                              "location": [0, 0, 0], "radius": 0.6, "depth": 3.0}),
        ("create_primitive", {"type": "cone", "name": "RocketNose",
                              "location": [0, 0, 1.8], "radius": 0.6, "depth": 1.2}),
        ("create_primitive", {"type": "cube", "name": "Fin_L",
                              "location": [-0.9, 0, -1.2], "scale": [0.06, 0.8, 1.0]}),
        ("create_primitive", {"type": "cube", "name": "Fin_R",
                              "location": [0.9, 0, -1.2], "scale": [0.06, 0.8, 1.0]}),
        ("create_primitive", {"type": "sphere", "name": "EngineGlow",
                              "location": [0, 0, -1.7], "radius": 0.25}),
        # materials
        ("create_material",  {"name": "HullMetal", "color": [0.75, 0.75, 0.8],
                              "metal": 0.85, "rough": 0.3}),
        ("create_material",  {"name": "EngineMat", "color": [0.1, 0.2, 0.3],
                              "emission": [0.0, 0.9, 1.0], "emission_strength": 6.0}),
        # assign
        ("set_material",     {"object_name": "RocketBody", "material": "HullMetal"}),
        ("set_material",     {"object_name": "RocketNose", "material": "HullMetal"}),
        ("set_material",     {"object_name": "EngineGlow", "material": "EngineMat"}),
        # organize
        ("object_collection", {"object_name": "RocketBody", "collection": "Rocket"}),
        ("object_collection", {"object_name": "RocketNose", "collection": "Rocket"}),
        ("object_collection", {"object_name": "EngineGlow", "collection": "Rocket"}),
        # light + camera
        ("create_light",     {"type": "POINT", "location": [2, 3, 2], "energy": 300}),
        ("create_camera",    {"location": [5, -4, 2], "aim_at": [0, 0, 0], "name": "Cam"}),
        # render prefs + render (heavy → confirm gate bypassed by hook)
        ("set_render",       {"engine": "eevee", "resolution_x": 400, "resolution_y": 400}),
        ("render",           {"image_path": "D:/CHIDVI-test-output/rocket.png", "samples": 16}),
        # save + verify
        ("save_blend",       {"path": "D:/CHIDVI-test-output/rocket.blend"}),
        ("scene_info",       {}),
        ("list_objects",     {}),
    ]

    ok = 0
    for op, args in ops:
        try:
            out = bc.run({"op": op, **args}, player=None)
        except Exception as e:
            print(f"[FAIL] {op}: {e}")
            continue
        status = "OK" if "failed" not in out.lower() and "error" not in out.lower() else "FAIL"
        if status == "OK":
            ok += 1
        print(f"[{status}] {op} -> {out[:130]}")

    print(f"\nSUMMARY: {ok}/{len(ops)} ops OK")
    render_path = OUT / "rocket.png"
    blend_path = OUT / "rocket.blend"
    print(f"render exists: {render_path.is_file()} ({render_path.stat().st_size if render_path.is_file() else 0} b)")
    print(f"blend exists:  {blend_path.is_file()}")
    return 0 if ok == len(ops) and render_path.is_file() and blend_path.is_file() else 1


if __name__ == "__main__":
    sys.exit(main())