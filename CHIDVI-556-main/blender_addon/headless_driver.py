"""
Headless driver for the CHIDVI bridge (blender -b --python headless_driver.py).

Run Blender in background mode and keep serving the bridge so the plugin (or
any local client) can drive it. The normal GUI path uses bpy.app.timers; in
`-b` timers never tick, so this script loops serve_once() which drains the
job queue plus serves HTTP.

Usage:
    blender --background --python blender_addon/headless_driver.py

The loop runs forever (Ctrl+C to stop) — ideal for the "bridge runs inside
Blender" pattern. Exit when the token file is removed, or the port is wrong.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_chidvi_bridge as bridge

bridge.register()

print("CHIDVI headless bridge running — Ctrl+C to stop.")
try:
    while True:
        bridge.serve_once()
        time.sleep(0.02)
except KeyboardInterrupt:
    bridge.stop_server()
    print("CHIDVI headless bridge stopped.")