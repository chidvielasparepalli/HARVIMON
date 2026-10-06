# CHIDVI-556 Blender Bridge add-on

A tiny HTTP+JSON server that runs **inside Blender** so CHIDVI-556 (or anything
else on this machine) can drive 3D scenes through natural-language requests.

- Localhost only (`127.0.0.1`), no CORS, no web UI.
- Every state-changing call is HMAC-SHA256 signed.
- Whitelisted command table; no arbitrary-code endpoint (disabled by default).
- Works in GUI mode (timers) and `--background` mode (via `headless_driver.py`).

## Install

```
python blender_addon/install_blender_addon.py
```

or manually: Blender → Edit → Preferences → **Add-ons** → **Install…** → select
`blender_chidvi_bridge.py` → enable **"CHIDVI 556 Blender Bridge"**.

Then launch Blender. The add-on prints:

```
CHIDVI Bridge: ready on http://127.0.0.1:8147 (Blender X.Y.Z)
```

## Files

| File | Purpose |
|---|---|
| `blender_chidvi_bridge.py` | The add-on (this is what you install) |
| `headless_driver.py` | Run the bridge in `blender -b` background mode |
| `install_blender_addon.py` | Copy + enable the add-on in Blender |
| `test_agent_task.py` | End-to-end agent test (Gemini plans, bridge executes) |

## Headless

```
"<blender.exe>" --background --python blender_addon/headless_driver.py
```

The bridge auto-starts in sync-serve mode (timers don't tick in `-b`, so the
driver drains the queue on the main thread). Ctrl+C to stop.

## The protocol

`POST http://127.0.0.1:8147/rpc` with:

```json
{
  "cmd": "create_primitive",
  "args": {"type": "cube", "name": "Hull", "location": [0, 0, 0]}
}
```

Header `X-CHIDVI-Sig: <hex(hsac(key=token, data=body))>`.

`GET /health` returns `{"ok": true, "blender_version": "..."}` (unauthenticated,
loopback only — used for connectivity probes).

### Commands

`status` `list_objects` `scene_info` `create_primitive` `create_mesh`
`set_transform` `duplicate_object` `rename_object` `delete_objects`
`clear_scene` `object_collection` `create_material` `set_material` `set_world`
`create_light` `create_camera` `add_keyframe` `set_timeline` `set_render`
`render` `save_blend` `open_blend` `add_modifier` `boolean_modifier`
`execute_python` (disabled unless allowed).

Every response is `{"success": bool, ...}`. On failure `success` is `false` and
an `error` string is included. The bridge **never claims success it didn't
achieve**: `render` returns `found` only when the image file exists on disk.

## Security

- Binds `127.0.0.1` — local only.
- HMAC-signed requests; 401 otherwise.
- `execute_python` off by default, gated in preferences.
- Render samples capped at 256.
- Non-JSON `Content-Type` rejected (no HTML-form CSRF / DNS-rebinding).

## Auto-pairing

On start the add-on writes a random token to:

```
<APPDATA>\Blender Foundation\Blender\<ver>\scripts\CHIDVI_bridge_token.txt
```

The CHIDVI-556 plugin reads that same file (via `plugins/_blender_bridge_client.py`)
so no manual key handshake is required. If you set an explicit token in the
add-on preferences, set the same value in the CHIDVI plugin settings.

## Rendering notes

`render` is synchronous and can take seconds for high-sample frames; the
`format` freezes the whole bridge for that period (nothing else queues during a
render). Keep samples low (`samples: 32`) for interactive work, or set the
output engine to EEVEE. The client in CHIDVI-556 gives `render` a 120 s timeout.