"""
install_blender_addon.py — copy the CHIDVI bridge add-on into Blender's user
addons dir and mark it enabled. Pure stdlib; run with your system Python:

    python blender_addon/install_blender_addon.py

Prints where it copied the file and the expected port. You still enable the
add-on in Blender (Preferences > Add-ons > search "CHIDVI") OR it auto-enables
on next launch because we write prefs via Blender itself — see the
`--no-enable` flag.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ADDON_SRC = Path(__file__).resolve().parent / "blender_chidvi_bridge.py"


def user_addons_dir() -> Path:
    base = Path(os.environ.get("APPDATA", "")) / "Blender Foundation" / "Blender"
    if not base.is_dir():
        raise SystemExit("Blender user config dir not found under %APPDATA%.")
    versions = sorted(v for v in base.iterdir() if v.is_dir() and v.name.replace(".", "").isdigit())
    if not versions:
        raise SystemExit("No Blender version dir found under " + str(base))
    # newest version
    latest = versions[-1]
    d = latest / "scripts" / "addons"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-enable", action="store_true",
                    help="copy only, don't try to enable the add-on")
    ap.add_argument("--blender", default=None,
                    help="path to blender.exe (override autodetect)")
    args = ap.parse_args()

    if not ADDON_SRC.is_file():
        print(f"ERROR: add-on source not found: {ADDON_SRC}")
        return 1

    dest_dir = user_addons_dir()
    dest = dest_dir / ADDON_SRC.name
    shutil.copy2(ADDON_SRC, dest)
    print(f"Installed add-on → {dest}")

    if args.no_enable:
        return 0

    # Try to enable it by scripting Blender itself (needs a runnable Blender).
    blender = args.blender
    if not blender:
        # common locations
        cands = [
            r"C:\Program Files\Blender Foundation\Blender\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender\latest\blender.exe",
            str(Path(os.environ.get("PROGRAMFILES", "")) / "Blender Foundation" /
                "Blender" / "blender.exe"),
        ]
        blender = next((c for c in cands if os.path.isfile(c)), None)
    if not blender:
        print("NOTE: could not find blender.exe — enable the add-on manually in "
              "Preferences > Add-ons, or re-run with --blender <path>.")
        return 0

    # Enable: feed a tiny script via --python-expr (Blender 3.0+).
    expr = (
        "import bpy, addon_utils;"
        "mod=addon_utils.enable('chidvi_556_blender_bridge', default_set=True);"
        f"print('ENABLED', mod);"
        "bpy.ops.wm.save_userpref()"
    )
    print(f"Launching Blender to enable the add-on: {blender}")
    r = subprocess.run([blender, "--background", "--python-expr", expr],
                       capture_output=True, text=True, timeout=120)
    print(r.stdout[-2000:])
    if r.returncode != 0:
        print(r.stderr[-1000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())