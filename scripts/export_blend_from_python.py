"""Export a Blender Python scene script to a .blend file.

Example:
    python scripts/export_blend_from_python.py showcase/demo/source.py showcase/demo/scene.blend
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]


DRIVER_SOURCE = r'''
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import traceback

import bpy


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for collection in list(bpy.data.collections):
        if not collection.users:
            bpy.data.collections.remove(collection)
    for mesh in list(bpy.data.meshes):
        if not mesh.users:
            bpy.data.meshes.remove(mesh)
    for material in list(bpy.data.materials):
        if not material.users:
            bpy.data.materials.remove(material)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_py")
    parser.add_argument("output_blend")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--pack", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else [])

    repo_root = Path(args.repo_root).resolve()
    input_py = Path(args.input_py).resolve()
    output_blend = Path(args.output_blend).resolve()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    clear_scene()
    namespace = {"__name__": "__main__", "__file__": str(input_py)}
    try:
        source = input_py.read_text(encoding="utf-8")
        exec(compile(source, str(input_py), "exec"), namespace)
        bpy.context.view_layer.update()
        output_blend.parent.mkdir(parents=True, exist_ok=True)
        if args.pack:
            try:
                bpy.ops.file.pack_all()
            except Exception as exc:
                print(f"[VeriAnim] pack_all failed: {exc}")
        bpy.ops.wm.save_as_mainfile(filepath=str(output_blend))
    except Exception:
        traceback.print_exc()
        return 1
    return 0


raise SystemExit(main())
'''


def _default_blender_executable() -> str:
    env_value = os.environ.get("BLENDER_EXECUTABLE") or os.environ.get("BLENDER")
    if env_value:
        return env_value
    return shutil.which("blender") or "blender"


def build_command(
    blender: str,
    input_py: Path,
    output_blend: Path,
    repo_root: Path,
    driver_path: Path,
    *,
    pack: bool,
) -> list[str]:
    command = [
        blender,
        "--background",
        "--factory-startup",
        "--python",
        str(driver_path),
        "--",
        str(input_py),
        str(output_blend),
        "--repo-root",
        str(repo_root),
    ]
    if pack:
        command.append("--pack")
    return command


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_py", type=Path, help="Python scene script to execute in Blender.")
    parser.add_argument("output_blend", type=Path, help="Output .blend path.")
    parser.add_argument(
        "--blender",
        default=_default_blender_executable(),
        help="Blender executable path. Defaults to BLENDER_EXECUTABLE, BLENDER, or blender on PATH.",
    )
    parser.add_argument("--repo-root", type=Path, default=PROJECT_ROOT, help="Repository root added to Blender sys.path.")
    parser.add_argument("--no-pack", action="store_true", help="Do not pack external assets into the saved .blend.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    input_py = args.input_py.resolve()
    output_blend = args.output_blend.resolve()
    repo_root = args.repo_root.resolve()

    if not input_py.exists():
        print(f"Input script does not exist: {input_py}", file=sys.stderr)
        return 2
    if input_py.suffix.lower() != ".py":
        print(f"Input script should be a .py file: {input_py}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="verianim_blend_export_") as tmp:
        driver_path = Path(tmp) / "driver.py"
        driver_path.write_text(DRIVER_SOURCE, encoding="utf-8")
        command = build_command(
            str(args.blender),
            input_py,
            output_blend,
            repo_root,
            driver_path,
            pack=not args.no_pack,
        )
        completed = subprocess.run(command, text=True)
        if completed.returncode != 0:
            return completed.returncode
    if not output_blend.exists():
        print(f"Blender finished but output file was not created: {output_blend}", file=sys.stderr)
        return 1
    print(output_blend)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
