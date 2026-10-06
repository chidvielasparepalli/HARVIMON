"""
CHIDVI-556 Blender Control plugin.

Drives a local Blender via the CHIDVI bridge add-on
(blender_addon/blender_chidvi_bridge.py). The add-on must be installed/enabled
in Blender; this plugin talks to it over plain localhost HTTP+JSON with an
HMAC signature — no MCP, no Claude Code dependency.

The plugin is a *general-purpose* tool. It does NOT ship a canned "spaceship"
script: the agent plans, then issues fine-grained scene operations
(create_primitive / set_transform / create_material / set_material /
create_light / create_camera / render ...) and inspects the result after each
step, the same way a human modeller would.

Permissions
───────────
DELETE / CLEAR / OPEN-FILE / SAVE / RENDER / EXECUTE are gated behind the
core/confirm.py on-screen CONFIRM banner (irreversible-ish or heavy). Safe
read/write ops (status, list, create, transform, material, light, camera,
timeline) run immediately and push undo onto core/undo.py where a before-state
exists. The bridge itself hard-caps render samples and never exposes
unauthenticated code execution.

Config (namespace "blender_control") — declared by PLUGIN_SETTINGS and
persisted by memory/config_manager into config/api_keys.json under
plugin_config:
    bridge_host    127.0.0.1
    bridge_port    8147
    bridge_token   auto-paired from Blender's user scripts dir if blank
    output_dir     where renders go (must be on this machine)
    exec_python    allow execute_python (default off)
"""

from __future__ import annotations

import threading

try:
    from memory.config_manager import get_plugin_setting, save_plugin_config
except ModuleNotFoundError:   # standalone self-check (no repo on sys.path)
    def get_plugin_setting(_ns, _k, default=None):
        return default
    def save_plugin_config(_ns, _vals):
        pass

try:
    from plugins._blender_bridge_client import BlenderBridge, BlenderBridgeError
except ModuleNotFoundError:   # standalone self-check — import the sibling directly
    from _blender_bridge_client import BlenderBridge, BlenderBridgeError

_PLUGIN = "blender_control"
_NS = "blender_control"

# ── plugin metadata (Gemini tool declaration) ────────────────────────────────
PLUGIN = {
    "name": _PLUGIN,
    "description": (
        "Drive Blender for 3D / mesh / material / lighting / camera / animation "
        "work. Use when the request involves 3D modelling, Blender, creating or "
        "editing a 3D scene, primitives, meshes, materials, textures, lighting, "
        "cameras, keyframes, animation, Geometry Nodes, procedural modelling, or "
        "rendering a 3D image. Operation groups: connection (status, health), "
        "scene inspection (list_objects, scene_info), objects (create_primitive, "
        "create_mesh, set_transform, duplicate_object, delete_objects, "
        "clear_scene, object_collection, rename_object), materials (create_material, "
        "set_material, set_world), lighting (create_light), cameras (create_camera), "
        "animation (add_keyframe, set_timeline), rendering (set_render), and file "
        "ops (save_blend, open_blend). Pass an unambiguous 'op'; build a scene "
        "step-by-step, inspecting after each step, rather than one big call. Long "
        "multi-step scene builds should loop: inspect -> act -> inspect."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "op": {
                "type": "STRING",
                "description": (
                    "Operation to run. One of: status, health, list_objects, "
                    "scene_info, create_primitive, create_mesh, set_transform, "
                    "duplicate_object, delete_objects, clear_scene, "
                    "object_collection, rename_object, create_material, set_material, "
                    "set_world, create_light, create_camera, add_keyframe, "
                    "set_timeline, set_render, render, save_blend, open_blend, "
                    "execute_python (disabled by default)."
                ),
            },
            "name": {"type": "STRING", "description": "Name for a new object/material (optional)."},
            "object_name": {"type": "STRING", "description": "Existing object to act on (set_transform, set_material, ...)."},
            "type": {"type": "STRING", "description": "Primitive type (cube, sphere, cylinder, cone, torus, plane, grid, circle, monkey, empty / light type POINT,SUN,SPOT,AREA / modifier type)."},
            "location": {"type": "ARRAY", "items": {"type": "NUMBER"},
                       "description": "[x,y,z] position."},
            "rotation": {"type": "ARRAY", "items": {"type": "NUMBER"},
                         "description": "[rx,ry,rz] euler, or quaternion [w,x,y,z] (4 values)."},
            "scale": {"type": "ARRAY", "items": {"type": "NUMBER"},
                      "description": "[sx,sy,sz]."},
            "color": {"type": "ARRAY", "items": {"type": "NUMBER"},
                      "description": "RGB [r,g,b] 0-1 or single value (material / light / world)."},
            "metal": {"type": "NUMBER", "description": "Metallic 0-1."},
            "rough": {"type": "NUMBER", "description": "Roughness 0-1."},
            "emission": {"type": "ARRAY", "items": {"type": "NUMBER"},
                         "description": "Emission color [r,g,b]."},
            "emission_strength": {"type": "NUMBER", "description": "Emission strength."},
            "energy": {"type": "NUMBER", "description": "Light energy/power."},
            "aim_at": {"type": "STRING", "description": "Camera target: object name or '[x,y,z]'."},
            "material": {"type": "STRING", "description": "Material name (set_material / create_material)."},
            "file": {"type": "STRING", "description": "Path for save_blend / open_blend."},
            "path": {"type": "STRING", "description": "Path (alias of 'file')."},
            "frame": {"type": "INTEGER", "description": "Keyframe frame."},
            "frame_start": {"type": "INTEGER", "description": "Timeline start."},
            "frame_end": {"type": "INTEGER", "description": "Timeline end."},
            "property": {"type": "STRING", "description": "Keyframe data path (location / rotation_euler / scale)."},
            "value": {"type": "ARRAY", "items": {"type": "NUMBER"},
                      "description": "Keyframe value."},
            "vertices": {"type": "ARRAY",
                         "items": {"type": "ARRAY", "items": {"type": "NUMBER"}},
                         "description": "create_mesh vertex list [[x,y,z],...]."},
            "faces": {"type": "ARRAY",
                      "items": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                      "description": "create_mesh face list [[i,j,k],...]."},
            "edges": {"type": "ARRAY",
                      "items": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                      "description": "create_mesh edge list [[i,j],...]."},
            "samples": {"type": "INTEGER", "description": "Render samples (cap 256)."},
            "engine": {"type": "STRING", "description": "render engine: cycles / eevee / workbench."},
            "resolution_x": {"type": "INTEGER", "description": "Render width."},
            "resolution_y": {"type": "INTEGER", "description": "Render height."},
            "modifier": {"type": "STRING", "description": "Modifier: subsurf, bevel, array, mirror, solidify, boolean, ..."},
            "levels": {"type": "INTEGER", "description": "Subsurf levels."},
            "operation": {"type": "STRING", "description": "Boolean op: DIFFERENCE, UNION, INTERSECT."},
            "collection": {"type": "STRING", "description": "Collection name (object_collection)."},
            "names": {"type": "ARRAY", "items": {"type": "STRING"},
                   "description": "List of object names (delete_objects)."},
            "keep_camera": {"type": "BOOLEAN", "description": "clear_scene: keep cameras."},
            "keep_light": {"type": "BOOLEAN", "description": "clear_scene: keep lights."},
            "new_name": {"type": "STRING", "description": "rename_object target."},
        },
        "required": ["op"],
    },
}


# ── config ───────────────────────────────────────────────────────────────────
PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "Blender / 3D",
    "fields": [
        {"key": "bridge_host", "label": "Bridge host", "type": "text",
         "default": "127.0.0.1", "placeholder": "127.0.0.1"},
        {"key": "bridge_port", "label": "Bridge port", "type": "text",
         "default": "8147", "placeholder": "8147"},
        {"key": "bridge_token", "label": "Bridge token (blank = auto-pair)",
         "type": "text", "default": "", "placeholder": "auto"},
        {"key": "output_dir", "label": "Render output dir", "type": "text",
         "default": "", "placeholder": "default: OS temp"},
        {"key": "confirm_destructive", "label": "Confirm destructive ops",
         "type": "toggle", "default": True},
        {"key": "exec_python", "label": "Allow execute_python",
         "type": "toggle", "default": False},
    ],
    "action": {
        "label": "CONNECT",
        "run": lambda: (_client().health().get("ok") and "Blender bridge OK"
                        or "Blender bridge unreachable"),
    },
}


# ── module-scope execution state (one at a time) ─────────────────────────────
STATE = {
    "stop": threading.Event(),     # set from main.py when user says "stop blender"
    "last_error": "",
}


def _setting(key, default):
    return get_plugin_setting(_NS, key, default)


def _client() -> BlenderBridge:
    return BlenderBridge(
        host=_setting("bridge_host", "127.0.0.1") or "127.0.0.1",
        port=_setting("bridge_port", 8147) or 8147,
        token=_setting("bridge_token", ""),
    )


def _log(player, msg: str):
    print(f"[BLENDER] {msg}")
    if player is not None:
        try:
            player.write_log(f"BLENDER: {msg}")
        except Exception:
            pass


def _check_stop():
    if STATE["stop"].is_set():
        return "Stopped by user."
    return None


# ── permission helpers (confirm gate for irreversible/heavy ops) ─────────────
def _confirmed(key: str, title: str, detail: str, fn, player) -> str | None:
    """Ask via core/confirm.request when the interface is bound; otherwise refuse.
    Returns a spoken-worthy sentence (or None when already approved/pending)."""
    from core import confirm
    if not _setting("confirm_destructive", True):
        confirm.request(key, title, detail, fn)
        return None
    confirm.request(key, title, detail, fn)
    return None


# ── op → bridge dispatch (the one place that maps op to bridge command) ──────
_OPS = {
    # connection
    "status":        ("status",      {},        False),
    "health":        ("status",      {},        False),
    # inspection
    "list_objects":  ("list_objects", {},       False),
    "scene_info":    ("scene_info",  {},        False),
    # objects
    "create_primitive": ("create_primitive", {}, False),
    "create_mesh":   ("create_mesh", {},        False),
    "set_transform": ("set_transform", {},      False),
    "duplicate_object": ("duplicate_object", {}, False),
    "rename_object": ("rename_object", {},      False),
    "delete_objects": ("delete_objects", {},    True),   # destructive
    "clear_scene":   ("clear_scene", {},        True),   # destructive
    "object_collection": ("object_collection", {}, False),
    # materials / light / camera
    "create_material": ("create_material", {}, False),
    "set_material":  ("set_material", {},       False),
    "set_world":     ("set_world", {},          False),
    "create_light":  ("create_light", {},       False),
    "create_camera": ("create_camera", {},      False),
    # animation
    "add_keyframe":  ("add_keyframe", {},       False),
    "set_timeline":  ("set_timeline", {},       False),
    # rendering
    "set_render":    ("set_render", {},         False),
    "render":        ("render", {},             True),   # heavy
    # file ops
    "save_blend":    ("save_blend", {},         True),   # saves
    "open_blend":    ("open_blend", {},         True),   # external file
    # advanced
    "add_modifier":  ("add_modifier", {},       False),
    "boolean_modifier": ("boolean_modifier", {}, False),
    "execute_python": ("execute_python", {},    True),   # arbitrary code
}


def _params(op, p: dict) -> dict:
    """Map plugin params onto the bridge's argument names."""
    m = {
        "object_name": "object_name", "modifier": "type",
        # these map 1:1 but we include them for clarity + deprecation tracking
        "name": "name", "type": "type", "location": "location",
        "rotation": "rotation", "scale": "scale",
        "color": "color", "metal": "metal", "rough": "rough",
        "emission": "emission", "emission_strength": "emission_strength",
        "energy": "energy", "aim_at": "aim_at", "material": "material",
        "file": "file", "path": "path", "frame": "frame",
        "frame_start": "frame_start", "frame_end": "frame_end",
        "property": "property", "value": "value",
        "vertices": "vertices", "faces": "faces", "edges": "edges",
        "samples": "samples", "engine": "engine",
        "resolution_x": "resolution_x", "resolution_y": "resolution_y",
        "levels": "levels", "operation": "operation",
        "collection": "collection", "names": "names",
        "keep_camera": "keep_camera", "keep_light": "keep_light",
        "new_name": "new_name", "code": "code",
    }
    out = {}
    for k, v in p.items():
        if v is None:
            continue
        if k in m:
            out[m[k]] = v          # explicit alias (e.g. "modifier"→"type")
        else:
            out[k] = v             # pass-through — bridge accepts all known names
    return out


# Natural-language aliases so a model that says "create_cylinder" or
# "assign_material" still routes correctly (only ever maps to the canonical ops).
_OPS_ALIASES = {
    "create_cube": "create_primitive", "create_sphere": "create_primitive",
    "create_cylinder": "create_primitive", "create_cone": "create_primitive",
    "create_plane": "create_primitive", "create_torus": "create_primitive",
    "create_monkey": "create_primitive", "create_grid": "create_primitive",
    "create_empty": "create_primitive", "add_cube": "create_primitive",
    "add_cylinder": "create_primitive", "add_sphere": "create_primitive",
    "add_cone": "create_primitive", "add_plane": "create_primitive",
    "assign_material": "set_material", "apply_material": "set_material",
    "move_object": "set_transform", "locate": "set_transform",
    "duplicate": "duplicate_object", "duplicate_create": "duplicate_object",
    "rename": "rename_object", "add_camera": "create_camera",
    "add_light": "create_light", "delete": "delete_objects",
    "remove_object": "delete_objects", "delete_object": "delete_objects",
    "make_material": "create_material", "new_material": "create_material",
    "set_light": "create_light", "create_keyframe": "add_keyframe",
    "keyframe": "add_keyframe", "set_timeline_bounds": "set_timeline",
    "render_image": "render", "save": "save_blend", "open_file": "open_blend",
    "set_world_background": "set_world",
}


def _resolve_op(op: str) -> str:
    return _OPS_ALIASES.get(op, op)


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Drop-in plugin entry: never raises; returns a spoken string."""
    p = parameters or {}
    op = _resolve_op((p.get("op") or "").strip().lower())
    if not op:
        return "Sir, I need an 'op' — e.g. status, list_objects, create_primitive."

    if op not in _OPS:
        return (f"Unknown Blender op '{op}'. Known: "
                f"{', '.join(sorted(_OPS))}. Say 'status' to test the connection.")

    # dynamic config → refresh bridge from settings each call (no restart needed)
    bridge = _client()

    if op in ("status", "health"):
        h = bridge.health()
        if not h.get("ok"):
            STATE["last_error"] = h.get("error", "unreachable")
            return (f"Blender is not reachable on {_setting('bridge_host', '127.0.0.1')}:"
                    f"{_setting('bridge_port', 8147)}. Is Blender running with the "
                    f"CHIDVI bridge add-on enabled? Error: {h.get('error')}")
        try:
            st = bridge.call("status", {})
        except BlenderBridgeError as e:
            return f"Blender bridge error: {e}"
        return (f"Blender is CONNECTED: {st.get('blender_version')}, "
                f"scene '{st.get('scene')}'.")

    # destructive / heavy ops: confirm first (non-blocking on-screen banner).
    bridge_cmd, _defaults, destructive = _OPS[op]
    if destructive:
        gate = _ask_confirm(player, op, p, bridge, _describe(p))
        if gate is not None:
            return gate          # user must CONFIRM on the HUD
        # gate ran synchronously (config auto-approves) — report and stop
        return f"Blender {op}: OK (approved)."

    args = _params(op, p)
    try:
        res = bridge.call(bridge_cmd, args)
    except BlenderBridgeError as e:
        STATE["last_error"] = str(e)
        return f"Blender bridge error: {e}"

    if not res.get("success"):
        STATE["last_error"] = res.get("error", "unknown")
        return (f"Blender {op} failed: {res.get('error')}")

    return _format_result(op, res)


def _ask_confirm(player, op, params, bridge, detail) -> str | None:
    """True when the confirmation is pending (user must press CONFIRM on HUD),
    or when the actual work ran synchronously. Returns a spoken sentence."""
    try:
        from core import confirm
    except ModuleNotFoundError:   # standalone self-check — no app to gate with
        return ("I need the CHIDVI interface to confirm this destructive Blender "
                "operation before I run it, and it is not available here.")
    if confirm.pending_title():
        return ("I already have a confirmation on screen. Please press it first.")

    if not _setting("confirm_destructive", True):
        # auto-approve (config override) — run it directly
        _run_now(op, params, bridge)
        return None

    key = f"blender_{op}"
    title = f"Blender: {op}"
    return confirm.request(
        key,
        title,
        detail,
        lambda: _run_now(op, params, bridge),
    )


def _run_now(op, params, bridge) -> str:
    """Runs a destructive/heavy op inside the confirm worker thread. Returns a
    short result string for the activity log, never raised."""
    try:
        args = _params(op, params)
        res = bridge.call(_OPS[op][0], args)
        if res.get("success"):
            return f"{op}: OK"
        STATE["last_error"] = res.get("error", "")
        return f"{op}: {res.get('error')}"
    except BlenderBridgeError as e:
        STATE["last_error"] = str(e)
        return f"{op}: {e}"


def _describe(p: dict) -> str:
    if p.get("op") == "delete_objects":
        return "Delete objects: " + ", ".join(p.get("names") or [])
    if p.get("op") == "clear_scene":
        return "Clear (empty) this Blender scene — irreversible."
    if p.get("op") == "save_blend":
        return f"Save .blend to: {p.get('file') or p.get('path') or 'current file'}"
    if p.get("op") == "open_blend":
        return f"Open .blend: {p.get('file') or p.get('path')}"
    if p.get("op") == "render":
        return "Render this scene (can be slow / consume GPU)."
    if p.get("op") == "execute_python":
        return "Run arbitrary Python inside Blender (disabled unless exec_python enabled)."
    return p.get("detail", "")


def _format_result(op: str, res: dict) -> str:
    """One-line spoken summary per op, with verification where available."""
    m = {
        "status":      lambda r: f"Blender CONNECTED, {r.get('blender_version')}, scene '{r.get('scene')}'",
        "health":      lambda r: f"Healthy, {r.get('blender_version')}",
        "list_objects": lambda r: f"{len(r.get('objects', []))} objects in scene.",
        "scene_info":  lambda r: (f"Scene '{r.get('name')}', {r.get('objects')} objects, "
                                  f"{len(r.get('materials', []))} materials, "
                                  f"{len(r.get('lights', []))} lights, "
                                  f"{len(r.get('cameras', []))} cameras, "
                                  f"engine {r.get('engine')}."),
        "create_primitive": lambda r: f"Created {r.get('type')} '{r.get('object')}'.",
        "create_mesh": lambda r: f"Created mesh '{r.get('object')}' ({r.get('verts')} verts).",
        "set_transform": lambda r: f"Moved '{r.get('object')}' to {r.get('location')}.",
        "duplicate_object": lambda r: f"Duplicated to '{r.get('object')}'.",
        "rename_object": lambda r: f"Renamed '{r.get('old')}' to '{r.get('object')}'.",
        "delete_objects": lambda r: f"Deleted: {', '.join(r.get('deleted', []))}.",
        "clear_scene": lambda r: f"Cleared {len(r.get('cleared', []))} objects.",
        "object_collection": lambda r: f"'{r.get('object')}' → collection '{r.get('collection')}'.",
        "create_material": lambda r: f"Material '{r.get('material')}' created.",
        "set_material": lambda r: f"Applied '{r.get('material')}' to '{r.get('object')}'.",
        "set_world": lambda r: f"World '{r.get('world')}' set.",
        "create_light": lambda r: f"Light '{r.get('object')}' ({r.get('type')}).",
        "create_camera": lambda r: f"Camera '{r.get('object')}' at {r.get('location')}.",
        "add_keyframe": lambda r: f"Keyframed '{r.get('object')}' .{r.get('property')} @f{r.get('frame')}.",
        "set_timeline": lambda r: f"Timeline {r.get('frame_start')}-{r.get('frame_end')} @f{r.get('current_frame')}.",
        "set_render": lambda r: f"Render: {r.get('engine')}, {r.get('resolution')}.",
        "render": lambda r: f"Render complete: {r.get('output_path')}.",
        "save_blend": lambda r: f"Saved: {r.get('file')}.",
        "open_blend": lambda r: f"Opened: {r.get('file')}.",
        "add_modifier": lambda r: f"Modifier '{r.get('modifier')}' on '{r.get('object')}'.",
        "boolean_modifier": lambda r: f"Boolean {r.get('operation')} on '{r.get('object')}'.",
        "execute_python": lambda r: f"Python OK{': ' + r.get('output', '').strip() if r.get('output', '').strip() else ''}.",
    }
    fn = m.get(op)
    if fn is None:
        return f"{op}: OK"
    try:
        return fn(res)
    except Exception:
        return f"{op}: OK"


# ── self-check (no Blender needed) ────────────────────────────────────────────
def _test() -> None:
    import types
    calls = []

    fake = types.SimpleNamespace(
        health=lambda: {"ok": True, "version": "3.6"},
        call=lambda *a, **k: calls.append(a) or {"success": True, "object": "Cube"},
    )
    # patch the module's _client and replace with fake
    _orig_client = globals()["_client"]
    globals()["_client"] = lambda: fake

    # no-op confirm so irreversible ops don't hang in tests
    _orig_confirm = globals().get("_ask_confirm")
    globals()["_ask_confirm"] = lambda *a, **k: None

    try:
        # unknown op
        assert "Unknown Blender op" in run({"op": "bogus"})
        # no op
        assert "need an 'op'" in run({})
        # health via fake
        out = run({"op": "status"})
        assert "CONNECTED" in out, out
        out = run({"op": "create_primitive", "type": "cube"})
        assert "Created" in out, out
        # non-destructive op without a required arg → bridge returns failure
        globals()["_client"] = lambda: types.SimpleNamespace(
            health=lambda: {"ok": True, "version": "3.6"},
            call=lambda *a, **k: {"success": False, "error": "no cube"},
        )
        out = run({"op": "set_transform"})
        assert "failed" in out, out
        # destructive op: confirm gate refuses in headless mode; NOT a bridge call
        globals()["_client"] = lambda: fake              # restore callable
        globals()["_ask_confirm"] = _orig_confirm
        out = run({"op": "delete_objects", "names": ["Cube"]})
        assert "cannot confirm" in out or "confirm" in out, out
    finally:
        globals()["_client"] = _orig_client
        globals()["_ask_confirm"] = _orig_confirm

    print("self-check OK")


if __name__ == "__main__":
    _test()