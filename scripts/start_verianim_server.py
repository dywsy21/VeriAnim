"""Start the VeriAnim Blender addon server from a Blender Python process."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import time

import bpy


ROOT = Path(__file__).resolve().parents[1]
ADDON_PATH = ROOT / "blender" / "addon.py"


def _load_project_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_project_env()
PORT = int(os.environ.get("VERIANIM_BLENDER_PORT", "8888"))

spec = importlib.util.spec_from_file_location("verianim_blender_addon", ADDON_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Could not load addon from {ADDON_PATH}")

addon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(addon)
addon.register()

if hasattr(bpy.types, "blendercustomagent_server") and bpy.types.blendercustomagent_server:
    try:
        bpy.types.blendercustomagent_server.stop()
    except Exception:
        pass
bpy.types.blendercustomagent_server = addon.VeriAnimAgentServer(host="127.0.0.1", port=PORT)
bpy.types.blendercustomagent_server.start()
bpy.context.scene.blendercustomagent_port = PORT
bpy.context.scene.blendercustomagent_server_running = bpy.types.blendercustomagent_server.running

print(f"VeriAnim Blender server started on localhost:{PORT}", flush=True)

while True:
    processed = bpy.types.blendercustomagent_server.process_pending_commands()
    time.sleep(0.01 if processed else 0.05)
