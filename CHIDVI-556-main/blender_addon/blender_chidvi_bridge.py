"""
CHIDVI-556 Blender Bridge add-on (Blender 3.x / 4.x / 5.x).

A tiny localhost HTTP server INSIDE Blender that exposes a small, whitelisted
JSON-RPC API so the CHIDVI-556 blender_control plugin can build/modify 3D
scenes through natural-language requests.

Why this and not Blender MCP
────────────────────────────
The Blender MCP addon uses a WebSocket protocol wired to Claude Code's MCP
framework. CHIDVI-556 has no MCP stack (and must not depend on Claude Code's
private protocol). This addon speaks plain HTTP + JSON with a stdlib-only
client, stays fully local, and every state-changing call is signed with an
HMAC-SHA256 token.

Thread-safety
─────────────
bpy is NOT safe from background threads. The HTTP handler thread only
*queues* a request (cmd, args, event); a bpy.app.timers callback runs on
Blender's main thread, executes it, and signals the handler, which then
returns. In GUI Blender the timer loop runs continuously. In --background
mode timers do not tick, so the bridge refuses to start there (same caveat
as Blender MCP itself).

Security model
──────────────
- Binds to 127.0.0.1 only. No CORS, no OPTIONS, no web UI.
- Every /rpc call must carry a valid HMAC-SHA256 signature over the exact
  request body. The token is generated at startup (random) or set in the
  add-on preferences; the same token must be set in the CHIDVI-556 plugin
  settings, or the generated token is auto-written to the Blender user
  "scripts" dir and the plugin's client auto-pairs with it.
- Content-Type must be application/json (blocks HTML-form CSRF/DNS-rebinding
  POSTs before the signature check).
- Only whitelisted named commands exist. execute_python is OFF by default
  and remains off unless explicitly enabled in the add-on preferences.
- Render samples are hard-capped at 256; expensive renders additionally
  need user confirmation on the CHIDVI-556 side.

Install: Preferences > Add-ons > Install... > select blender_chidvi_bridge.py
         then enable "CHIDVI 556 Blender Bridge".
"""

import hashlib
import hmac
import json
import os
import queue
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bpy  # noqa: F401  (this addon only ever runs inside Blender)

ADDON_ID    = "chidvi_556_blender_bridge"
ADDON_NAME  = "CHIDVI 556 Blender Bridge"
VERSION     = (0, 1, 0)
MAX_SAMPLES = 256
DEFAULT_PORT = 8147          # NOT 9876 — that's Blender MCP's port.
TIMEOUT_S   = 20.0           # max seconds a command may run on the main thread


class _BridgeState:
    """Module state that no other thread reads without the lock it owns."""
    def __init__(self):
        self.token = secrets.token_hex(16)
        self.port = DEFAULT_PORT
        self.output_dir = ""
        self.execute_python = False
        self.running = False
        self.background = False   # True when serving synchronously (-b headless)
        self.server = None
        self.jobs = queue.Queue()   # (cmd:str, args:dict, event, result_slot)
        self.lock = threading.Lock()

_bs = _BridgeState()


def get_config() -> dict:
    """Non-secret config snapshot (for status/logging)."""
    with _bs.lock:
        return {
            "port": _bs.port,
            "auth": bool(_bs.token),
            "execute_python": _bs.execute_python,
            "output_dir": _bs.output_dir or "",
        }


AVAILABLE_CMDS = None  # built lazily below after ALL command fns are defined


def _result(success=True, **kw):
    return dict({"success": success}, **kw)


# ── render plumbing ───────────────────────────────────────────────────────────
def _write_render(scene, image_path: str) -> dict:
    img = (image_path or "").strip().replace("\\", "/")
    if not img.lower().endswith((".png", ".jpg", ".jpeg")):
        img += ".png"
    scene.render.filepath = img
    try:
        bpy.ops.render.render(write_still=True)
    except Exception as e:
        return _result(False, output_path=img, error=str(e))
    return _result(output_path=img, found=os.path.isfile(img))


def _default_render_path(scene) -> str:
    d = (_bs.output_dir or "").strip()
    if not d:
        import tempfile
        d = tempfile.gettempdir()
    return os.path.join(d, f"CHIDVI_render_{scene.name or 'scene'}.png")


# ── commands (run on Blender's MAIN thread only) ─────────────────────────────
def cmd_status(_a):
    return _result(blender_version=bpy.app.version_string,
                   scene=bpy.context.scene.name,
                   server=get_config())


def cmd_list_objects(_a):
    out = []
    for o in bpy.data.objects:
        out.append({
            "name": o.name, "type": o.type,
            "location": list(o.location),
            "rotation": list(o.rotation_euler),
            "scale": list(o.scale),
            "visible": not o.hide_render,
            "parent": o.parent.name if o.parent else None,
            "materials": [s.name for s in o.material_slots],
            "modifiers": [m.name for m in o.modifiers],
        })
    return _result(objects=out)


def cmd_scene_info(_a):
    s = bpy.context.scene
    return _result(
        name=s.name,
        objects=len(bpy.data.objects),
        collections=[c.name for c in bpy.data.collections],
        cameras=[o.name for o in bpy.data.objects if o.type == "CAMERA"],
        lights=[o.name for o in bpy.data.objects if o.type == "LIGHT"],
        materials=[m.name for m in bpy.data.materials],
        frame_start=s.frame_start, frame_end=s.frame_end,
        current_frame=s.frame_current,
        engine=s.render.engine,
    )


def _obj(a, key="name"):
    name = (a.get(key) or a.get("object") or a.get("obj") or a.get("object_name") or "").strip()
    o = bpy.data.objects.get(name)
    if o is None:
        raise KeyError(f"Object '{name}' not found.")
    return o


def cmd_create_primitive(a):
    t = (a.get("type") or "cube").lower()
    loc = [float(v) for v in (a.get("location") or [0, 0, 0])]
    # Accept Blender-standard creation kwargs (radius, depth, vertices, size, etc.)
    _r  = float(a.get("radius") or 1.0)
    _r1 = float(a.get("radius1") or a.get("radius") or 1.0)
    _r2 = float(a.get("radius2") or 0.25)
    _d  = float(a.get("depth") or 2.0)
    _sz = float(a.get("size") or 2.0)
    _v  = int(a.get("vertices") or 32)
    _xs = int(a.get("x_subdivisions") or 10)
    _ys = int(a.get("y_subdivisions") or 10)
    setters = {
        "cube":     (lambda: bpy.ops.mesh.primitive_cube_add(size=_sz, location=loc)),
        "sphere":   (lambda: bpy.ops.mesh.primitive_uv_sphere_add(radius=_r, location=loc)),
        "uvsphere": (lambda: bpy.ops.mesh.primitive_uv_sphere_add(radius=_r, location=loc)),
        "uv_sphere":(lambda: bpy.ops.mesh.primitive_uv_sphere_add(radius=_r, location=loc)),
        "ico":      (lambda: bpy.ops.mesh.primitive_ico_sphere_add(radius=_r, location=loc)),
        "cylinder": (lambda: bpy.ops.mesh.primitive_cylinder_add(radius=_r, depth=_d, location=loc)),
        "cone":     (lambda: bpy.ops.mesh.primitive_cone_add(radius1=_r1, radius2=_r2, depth=_d, location=loc)),
        "torus":    (lambda: bpy.ops.mesh.primitive_torus_add(location=loc)),
        "plane":    (lambda: bpy.ops.mesh.primitive_plane_add(size=_sz, location=loc)),
        "grid":     (lambda: bpy.ops.mesh.primitive_grid_add(x_subdivisions=_xs, y_subdivisions=_ys, size=_sz, location=loc)),
        "circle":   (lambda: bpy.ops.mesh.primitive_circle_add(vertices=_v, radius=_r, location=loc)),
        "monkey":   (lambda: bpy.ops.mesh.primitive_monkey_add(size=_sz, location=loc)),
        "empty":    (lambda: bpy.ops.object.empty_add(type="PLAIN_AXES", location=loc)),
    }
    fn = setters.get(t)
    if fn is None:
        return _result(False, error=f"Unknown primitive '{t}'. Known: {sorted(setters)}")
    try:
        fn()
    except Exception as e:
        return _result(False, error=str(e))
    obj = bpy.context.object
    if a.get("name"):
        obj.name = a["name"]
    return _result(object=obj.name, type=obj.type)


def cmd_delete_objects(a):
    names = a.get("names") or []
    if not names:
        return _result(False, error="No object names provided.")
    gone = []
    for n in names:
        o = bpy.data.objects.get(n)
        if o is not None:
            bpy.data.objects.remove(o, do_unlink=True)
            gone.append(n)
    return _result(deleted=gone)


def cmd_clear_scene(a):
    keep = "keep_camera" in a or "keep_light" in a
    keep_cam = bool(a.get("keep_camera"))
    keep_light = bool(a.get("keep_light"))
    objs = [o for o in bpy.data.objects
            if not (keep_cam and o.type == "CAMERA")
            and not (keep_light and o.type == "LIGHT")]
    names = [o.name for o in objs]
    for o in objs:
        bpy.data.objects.remove(o, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.lights):
        for d in list(coll):
            if not d.users:
                coll.remove(d)
    return _result(cleared=names)


def cmd_set_transform(a):
    o = _obj(a)
    if a.get("location") is not None:
        o.location = [float(v) for v in a["location"]]
    if a.get("rotation") is not None:
        r = a["rotation"]
        if len(r) == 4:
            from mathutils import Quaternion
            o.rotation_mode = "QUATERNION"
            o.rotation_quaternion = Quaternion([float(v) for v in r])
        else:
            o.rotation_mode = "XYZ"
            o.rotation_euler = [float(v) for v in r]
    if a.get("scale") is not None:
        o.scale = [float(v) for v in a["scale"]]
    return _result(object=o.name,
                   location=list(o.location),
                   rotation=list(o.rotation_euler),
                   scale=list(o.scale))


def cmd_duplicate_object(a):
    o = _obj(a)
    new = o.copy()
    if o.data:
        new.data = o.data.copy()
    bpy.context.collection.objects.link(new)
    return _result(object=new.name)


def cmd_rename_object(a):
    o = _obj(a)
    old = o.name
    o.name = a.get("new_name") or (o.name + "_copy")
    return _result(object=o.name, old=old)


def cmd_create_material(a):
    name = a.get("name") or "Material"
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        def _c(col):
            if isinstance(col, (int, float)):
                return [float(col)] * 4
            c = [float(x) for x in list(col)[:3]] + [1.0]
            return c[:4]
        if a.get("color") is not None:
            bsdf.inputs["Base Color"].default_value = _c(a["color"])
        metal = a.get("metal") or a.get("metallic")
        if metal is not None:
            bsdf.inputs["Metallic"].default_value = float(metal)
        rough = a.get("rough") or a.get("roughness")
        if rough is not None:
            bsdf.inputs["Roughness"].default_value = float(rough)
        emission = a.get("emission") or a.get("emission_color")
        if emission is not None:
            bsdf.inputs["Emission Color"].default_value = _c(emission)
        if a.get("emission_strength") is not None:
            bsdf.inputs["Emission Strength"].default_value = float(a["emission_strength"])
    return _result(material=mat.name)


def cmd_set_material(a):
    obj = _obj(a, "object_name")
    mat_name = a.get("material") or a.get("material_name") or ""
    mat = bpy.data.materials.get(mat_name)
    if mat is None:
        return _result(False, error=f"Material '{mat_name}' not found.")
    if obj.type == "MESH":
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)
    else:
        return _result(False, error=f"Object '{obj.name}' is not a mesh.")
    return _result(object=obj.name, material=mat.name)


def cmd_set_world(a):
    w = bpy.context.scene.world
    if w is None:
        w = bpy.data.worlds.new("World")
        bpy.context.scene.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes.get("Background")
    if bg:
        if a.get("color") is not None:
            c = a["color"]
            c = c if isinstance(c, (list, tuple)) else [c] * 3
            bg.inputs[0].default_value = ([float(x) for x in c] + [1.0])[:4]
        if a.get("strength") is not None:
            bg.inputs[1].default_value = float(a["strength"])
    return _result(world=w.name)


def cmd_create_light(a):
    t = (a.get("type") or "POINT").upper()
    if t not in ("POINT", "SUN", "SPOT", "AREA"):
        return _result(False, error=f"Unknown light type '{t}'.")
    loc = [float(v) for v in (a.get("location") or [0, 0, 0])]
    try:
        bpy.ops.object.light_add(type=t, location=loc)
    except Exception as e:
        return _result(False, error=str(e))
    o = bpy.context.object
    if a.get("energy") is not None:
        o.data.energy = float(a["energy"])
    if a.get("color") is not None:
        c = a["color"]
        o.data.color = [float(x) for x in (c if isinstance(c, (list, tuple)) else [c, c, c])]
    if a.get("name"):
        o.name = a["name"]
    return _result(object=o.name, type=t, energy=o.data.energy)


def cmd_create_camera(a):
    loc = [float(v) for v in (a.get("location") or [0, -10, 2])]
    try:
        bpy.ops.object.camera_add(location=loc)
    except Exception as e:
        return _result(False, error=str(e))
    cam = bpy.context.object
    if a.get("name"):
        cam.name = a["name"]
    if a.get("aim_at") is not None:
        _aim_camera(cam, a["aim_at"])
    # make this the scene's active camera so bpy.ops.render.render uses it
    try:
        bpy.context.scene.camera = cam
    except Exception:
        pass
    return _result(object=cam.name, location=list(cam.location))


def _aim_camera(cam, target):
    t = bpy.data.objects.get(target) if isinstance(target, str) else None
    p = t.location if t is not None else [float(v) for v in target]
    from mathutils import Vector
    direction = Vector(p) - cam.location
    if direction.length_squared < 1e-9:
        return
    rot = direction.to_track_quat("-Z", "Y")
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = rot


def cmd_add_keyframe(a):
    o = _obj(a, "object_name")
    dp = a.get("property")
    if not dp:
        return _result(False, error="Missing 'property' (location / rotation_euler / scale / ...)")
    frame = int(a.get("frame") or bpy.context.scene.frame_current)
    if a.get("value") is not None:
        setattr(o, dp, [float(v) for v in a["value"]])
    try:
        o.keyframe_insert(data_path=dp, frame=frame)
    except Exception as e:
        return _result(False, error=str(e))
    return _result(object=o.name, property=dp, frame=frame)


def cmd_set_timeline(a):
    s = bpy.context.scene
    if a.get("frame_start") is not None:
        s.frame_start = int(a["frame_start"])
    if a.get("frame_end") is not None:
        s.frame_end = int(a["frame_end"])
    if a.get("frame") is not None:
        s.frame_set(int(a["frame"]))
    return _result(frame_start=s.frame_start, frame_end=s.frame_end,
                   current_frame=s.frame_current)


def cmd_set_render(a):
    s = bpy.context.scene
    r = s.render
    if a.get("engine") is not None:
        e = str(a["engine"]).lower()
        # valid engine ids vary by Blender version: 4.x EEVEE_NEXT,
        # 5.x just BLENDER_EEVEE. Resolve against the live enum.
        try:
            valid = {x.identifier for x in
                     bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
        except Exception:
            valid = set()
        want = {
            "cycles": "CYCLES",
            "eevee": "BLENDER_EEVEE",
            "eevee_next": "BLENDER_EEVEE_NEXT",
            "blender_eevee": "BLENDER_EEVEE",
            "workbench": "BLENDER_WORKBENCH",
        }.get(e, r.engine)
        if want in valid:
            r.engine = want
        # else keep current engine (unknown/unsupported name is non-fatal)
    if a.get("resolution_x") is not None:
        r.resolution_x = int(a["resolution_x"])
    if a.get("resolution_y") is not None:
        r.resolution_y = int(a["resolution_y"])
    samples = min(max(int(a["samples"]), 0), MAX_SAMPLES) if a.get("samples") is not None else None
    if samples is not None:
        try:
            r.cycles.samples = samples
        except AttributeError:
            pass
    if a.get("output_path") is not None:
        r.filepath = str(a["output_path"])
    try:
        samples_now = r.cycles.samples
    except AttributeError:
        samples_now = None
    return _result(engine=r.engine, samples=samples_now,
                   resolution=(r.resolution_x, r.resolution_y))


def cmd_render(a):
    s = bpy.context.scene
    path = a.get("image_path")
    if isinstance(a.get("samples"), (int, float)):
        try:
            s.render.cycles.samples = min(max(int(a["samples"]), 1), MAX_SAMPLES)
        except AttributeError:
            pass
    return _write_render(s, path or _default_render_path(s))


def cmd_open_blend(a):
    path = a.get("path") or ""
    if not path or not os.path.isfile(path):
        return _result(False, error=f"File not found: {path or '(none)'}")
    try:
        bpy.ops.wm.open_mainfile(filepath=path)
    except Exception as e:
        return _result(False, error=str(e))
    return _result(file=path, scene=bpy.context.scene.name)


def cmd_save_blend(a):
    path = (a.get("path") or "").strip()
    try:
        if path:
            bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(path))
        else:
            bpy.ops.wm.save_mainfile()
    except Exception as e:
        return _result(False, error=str(e))
    return _result(file=bpy.data.filepath or path)


def cmd_object_collection(a):
    o = _obj(a, "object_name")
    coll_name = a.get("collection") or "Master Collection"
    scene_root = bpy.context.scene.collection
    for c in list(o.users_collection):
        # leave it in the scene root; move out of any other collection
        if c.name != scene_root.name:
            try:
                c.objects.unlink(o)
            except Exception:
                pass
    coll = bpy.data.collections.get(coll_name)
    if coll is None:
        coll = bpy.data.collections.new(coll_name)
        scene_root.children.link(coll)
    if o.name not in coll.objects:
        coll.objects.link(o)
    return _result(object=o.name, collection=coll.name)


def cmd_create_mesh(a):
    verts = a.get("vertices") or []
    if len(verts) < 3:
        return _result(False, error="Need at least 3 vertices.")
    me = bpy.data.meshes.new(a.get("name") or "Mesh")
    try:
        me.from_pydata(verts, a.get("edges") or [], a.get("faces") or [])
    except Exception as e:
        bpy.data.meshes.remove(me)
        return _result(False, error=str(e))
    me.update()
    obj = bpy.data.objects.new(a.get("name") or "Mesh", me)
    bpy.context.collection.objects.link(obj)
    if a.get("location") is not None:
        obj.location = [float(v) for v in a["location"]]
    return _result(object=obj.name, verts=len(verts), faces=len(a.get("faces") or []))


def cmd_add_modifier(a):
    o = _obj(a, "object_name")
    mtype = (a.get("type") or "").upper().replace(" ", "_")
    aliases = {
        "SUBSURF": "SUBSURF", "SUBDIVISION": "SUBSURF", "SUBDIVIDE": "SUBSURF",
        "BEVEL": "BEVEL", "ARRAY": "ARRAY", "MIRROR": "MIRROR",
        "SOLIDIFY": "SOLIDIFY", "DISPLACE": "DISPLACE", "TRIANGULATE": "TRIANGULATE",
        "DECIMATE": "DECIMATE", "REMESH": "REMESH", "SHRINKWRAP": "SHRINKWRAP",
        "SIMPLE_DEFORM": "SIMPLE_DEFORM", "WELD": "WELD", "BOOLEAN": "BOOLEAN",
    }
    alias = aliases.get(mtype, mtype)
    valid = {m.identifier for m in bpy.types.Modifier.bl_rna.properties["type"].enum_items}
    if alias not in valid:
        return _result(False, error=f"Unknown/unavailable modifier '{mtype}'.")
    mod = o.modifiers.new(a.get("name") or alias.title(), alias)
    if a.get("levels") is not None and getattr(mod, "levels", None) is not None:
        mod.levels = int(a["levels"])
    if a.get("width") is not None and getattr(mod, "width", None) is not None:
        mod.width = float(a["width"])
    if a.get("strength") is not None and getattr(mod, "strength", None) is not None:
        mod.strength = float(a["strength"])
    return _result(object=o.name, modifier=mod.name, type=alias)


def cmd_boolean_modifier(a):
    o = _obj(a, "object_name")
    other = _obj(a, "operand")
    op = (a.get("operation") or "DIFFERENCE").upper()
    if op not in ("DIFFERENCE", "UNION", "INTERSECT"):
        return _result(False, error=f"Unknown operation '{op}'.")
    mod = o.modifiers.new(name="Boolean", type="BOOLEAN")
    mod.object = other
    mod.operation = op
    return _result(object=o.name, modifier=mod.name, operation=op)


def cmd_execute_python(a):
    if not _bs.execute_python:
        return _result(False, error="execute_python is disabled. Enable it in the "
                                    "CHIDVI bridge add-on preferences to allow this.")
    code = (a.get("code") or "").strip()
    if not code:
        return _result(False, error="No code provided.")
    import io
    import contextlib
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(code, "<bridge>", "exec"), {"bpy": bpy})
        return _result(output=buf.getvalue())
    except Exception as e:
        return _result(False, error=str(e))


_COMMANDS = {
    fn.__name__[len("cmd_"):]: fn
    for _k, fn in list(globals().items())
    if callable(fn) and fn.__name__.startswith("cmd_")
} if False else {
    "status": cmd_status, "list_objects": cmd_list_objects,
    "scene_info": cmd_scene_info,
    "create_primitive": cmd_create_primitive,
    "delete_objects": cmd_delete_objects, "clear_scene": cmd_clear_scene,
    "set_transform": cmd_set_transform, "duplicate_object": cmd_duplicate_object,
    "rename_object": cmd_rename_object,
    "create_material": cmd_create_material, "set_material": cmd_set_material,
    "set_world": cmd_set_world,
    "create_light": cmd_create_light, "create_camera": cmd_create_camera,
    "add_keyframe": cmd_add_keyframe, "set_timeline": cmd_set_timeline,
    "set_render": cmd_set_render, "render": cmd_render,
    "open_blend": cmd_open_blend, "save_blend": cmd_save_blend,
    "object_collection": cmd_object_collection, "create_mesh": cmd_create_mesh,
    "add_modifier": cmd_add_modifier, "boolean_modifier": cmd_boolean_modifier,
    "execute_python": cmd_execute_python,
}


# ── HTTP layer: only QUEUES work for the main thread ─────────────────────────
class _Handler(BaseHTTPRequestHandler):
    server_version = "CHIDVI-Bridge/0.1"

    def _json(self, code, payload):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sig_ok(self, body: bytes, sig: str) -> bool:
        try:
            actual = hmac.new(_bs.token.encode(), body, hashlib.sha256).hexdigest()
        except Exception:
            return False
        return bool(sig) and hmac.compare_digest(actual, sig)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True, "blender_version": bpy.app.version_string})
            return
        self._json(404, {"error": "not found"})

    def do_OPTIONS(self):
        self._json(204, {})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
        except Exception:
            return self._json(400, {"success": False, "error": "bad body"})

        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype and ctype != "application/json":
            return self._json(415, {"success": False,
                                    "error": "Content-Type must be application/json"})

        req = self.path
        if req == "/health":
            return self._json(200, {"ok": True})

        if req != "/rpc":
            return self._json(404, {"success": False, "error": "not found"})

        if not self._sig_ok(body, self.headers.get("X-CHIDVI-Sig", "")):
            return self._json(401, {"success": False,
                                    "error": "invalid or missing HMAC signature"})

        try:
            msg = json.loads(body or b"{}")
        except ValueError:
            return self._json(400, {"success": False, "error": "invalid JSON"})

        cmd = msg.get("cmd")
        args = msg.get("args") or {}
        if cmd not in _COMMANDS:
            return self._json(404, {"success": False,
                                    "error": f"unknown command '{cmd}'"})

        fn = _COMMANDS[cmd]
        event = threading.Event()
        slot = {}

        def _run():
            try:
                slot["result"] = fn(args)
            except Exception as e:
                slot["error"] = str(e)
            finally:
                event.set()

        _bs.jobs.put(_run)
        if not event.wait(TIMEOUT_S):
            return self._json(504, {"success": False,
                                    "error": "command timed out (is Blender's main "
                                             "loop running? background mode is not "
                                             "supported)"})
        if "error" in slot:
            return self._json(500, {"success": False, "error": slot["error"]})
        return self._json(200, slot["result"])


def _drain_jobs():
    """bpy.app.timers callback: run pending commands on the main thread.
    Returns 0.05s to keep itself registered (GUI main loop only)."""
    while True:
        try:
            job = _bs.jobs.get_nowait()
        except queue.Empty:
            return 0.05
        job()


def start_server():
    from bpy.app import timers
    if _bs.running:
        return
    if bpy.app.background:
        print("CHIDVI Bridge: headless mode — starting SYNCHRONOUS tag-in server. "
              "Call serve_once() from a script loop to process requests.")
        _bs.background = True
        _bs.running = True
        _write_token_file()
        _bs.server = _build_server()
        print(f"CHIDVI Bridge: ready on http://127.0.0.1:{_bs.port} "
              f"in background (sync) mode (Blender {bpy.app.version_string})")
        return
    _bs.background = False
    from http.server import ThreadingHTTPServer
    try:
        _bs.server = ThreadingHTTPServer(("127.0.0.1", _bs.port), _Handler)
    except OSError as e:
        print(f"CHIDVI Bridge: cannot bind 127.0.0.1:{_bs.port} — {e}")
        return
    _bs.running = True
    _write_token_file()
    if not timers.is_registered(_drain_jobs):
        timers.register(_drain_jobs)
    threading.Thread(target=_bs.server.serve_forever, daemon=True,
                     name="chidvi-bridge").start()
    print(f"CHIDVI Bridge: ready on http://127.0.0.1:{_bs.port} "
          f"(Blender {bpy.app.version_string})")


def _build_server():
    from http.server import ThreadingHTTPServer
    try:
        return ThreadingHTTPServer(("127.0.0.1", _bs.port), _Handler)
    except OSError as e:
        print(f"CHIDVI Bridge: cannot bind 127.0.0.1:{_bs.port} — {e}")
        return None


_henry_thread = None


def serve_once():
    """Headless tick: run any queued jobs. Call from a loop in a -b script."""
    global _henry_thread
    if _henry_thread is None and _bs.server is not None:
        _henry_thread = threading.Thread(target=_bs.server.serve_forever,
                                         daemon=True, name="chidvi-http")
        _henry_thread.start()
    while True:
        try:
            job = _bs.jobs.get_nowait()
        except queue.Empty:
            return
        job()


def stop_server():
    from bpy.app import timers
    if _bs.server is not None and not _bs.background:
        try:
            _bs.server.shutdown()
            _bs.server.server_close()
        except Exception:
            pass
    _bs.server = None
    _bs.running = False
    try:
        if timers.is_registered(_drain_jobs):
            timers.unregister(_drain_jobs)
    except Exception:
        pass


def _write_token_file():
    try:
        scripts = bpy.utils.user_resource("SCRIPTS")
        os.makedirs(scripts, exist_ok=True)
        with open(os.path.join(scripts, "CHIDVI_bridge_token.txt"), "w") as f:
            f.write(_bs.token)
    except Exception:
        pass


# ── Blender add-on plumbing ───────────────────────────────────────────────────
from bpy.props import (BoolProperty, IntProperty, StringProperty)  # noqa: E402


class BridgePrefs(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    port: IntProperty(name="Port", default=DEFAULT_PORT, min=1024, max=65535)
    token: StringProperty(name="Token (empty = random)", default="",
                          subtype="PASSWORD")
    execute_python: BoolProperty(
        name="Allow execute_python (dangerous, expert only)", default=False)
    output_dir: StringProperty(name="Default render output dir", default="",
                               subtype="DIR_PATH")

    def draw(self, context):
        for p in ("port", "token", "execute_python", "output_dir"):
            self.layout.prop(self, p)


def register():
    from bpy.utils import register_class
    register_class(BridgePrefs)
    prefs = bpy.context.preferences.addons.get(ADDON_ID)
    if prefs is not None:
        p = prefs.preferences
        _bs.port = int(p.port)
        _bs.execute_python = bool(p.execute_python)
        _bs.output_dir = (p.output_dir or "").strip()
        if p.token:
            _bs.token = p.token
    start_server()


def unregister():
    from bpy.utils import unregister_class
    stop_server()
    try:
        unregister_class(BridgePrefs)
    except Exception:
        pass