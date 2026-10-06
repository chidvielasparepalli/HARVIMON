from __future__ import annotations

import asyncio
import sys
import threading
import types

from dashboard.web_ui import WebJarvisUI


# main.py imports `JarvisUI` from the `ui` module at import time. In web mode we
# provide a small replacement module so the assistant runtime imports cleanly
# without constructing the PyQt desktop window.
ui_stub = types.ModuleType("ui")
ui_stub.JarvisUI = WebJarvisUI
sys.modules["ui"] = ui_stub

try:
    from main import JarvisLive  # noqa: E402
except ModuleNotFoundError as exc:
    missing = exc.name or "a dependency"
    print("")
    print(f"Cannot start CHIDVI web runtime because '{missing}' is not installed.")
    print("")
    print("Install the original assistant dependencies in this environment:")
    print("  python -m pip install -r requirements.txt")
    print("")
    print("Then run:")
    print("  python web_main.py")
    print("")
    raise SystemExit(1) from exc


def main() -> None:
    ui = WebJarvisUI("face.png")

    def runner() -> None:
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\nCHIDVI web runtime stopped.")

    threading.Thread(target=runner, daemon=True, name="chidvi-web-runtime").start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()
