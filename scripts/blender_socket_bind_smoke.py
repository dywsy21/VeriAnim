"""Minimal socket bind smoke test inside Blender."""

from __future__ import annotations

from pathlib import Path
import socket
import traceback


ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = ROOT / "runs" / "blender_socket_bind_smoke.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 18888))
    sock.listen(1)
    LOG_PATH.write_text("bind-ok\n", encoding="utf-8")
    sock.close()
except Exception:
    LOG_PATH.write_text(traceback.format_exc(), encoding="utf-8")
    raise
