"""Collect final run previews into a compact runs-summary directory.

The script is idempotent by default: if a run already has a summary manifest,
it is skipped. Use --refresh to rebuild existing summaries.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from typing import Any


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize LL3M run previews.")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"), help="Directory containing run_* folders.")
    parser.add_argument("--output-dir", type=Path, default=Path("runs-summary"), help="Directory to write summaries into.")
    parser.add_argument("--refresh", action="store_true", help="Rebuild summaries that already exist.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    processed = 0
    skipped = 0
    for run_dir in sorted((path for path in args.runs_dir.iterdir() if path.is_dir()), key=lambda path: path.name):
        summary_dir = args.output_dir / run_dir.name
        manifest_path = summary_dir / "manifest.json"
        if manifest_path.exists() and not args.refresh:
            skipped += 1
            continue
        if summary_dir.exists() and args.refresh:
            shutil.rmtree(summary_dir)
        summary = summarize_run(run_dir, summary_dir)
        if summary:
            processed += 1
        else:
            skipped += 1
    write_index(args.output_dir)
    print(f"processed={processed} skipped={skipped} output={args.output_dir}")
    return 0


def summarize_run(run_dir: Path, summary_dir: Path) -> dict[str, Any] | None:
    prompt = read_prompt(run_dir)
    screenshot_dir = latest_screenshot_dir(run_dir)
    animation_gif = latest_animation_gif(run_dir)
    if not screenshot_dir and not animation_gif:
        return None

    summary_dir.mkdir(parents=True, exist_ok=True)
    copied_screenshots: list[str] = []
    if screenshot_dir:
        target_dir = summary_dir / "screenshots"
        target_dir.mkdir(exist_ok=True)
        for image_path in sorted(path for path in screenshot_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES):
            target_path = target_dir / image_path.name
            shutil.copy2(image_path, target_path)
            copied_screenshots.append(str(target_path.relative_to(summary_dir)).replace("\\", "/"))

    animation_target: str | None = None
    if animation_gif:
        target_path = summary_dir / "animation.gif"
        shutil.copy2(animation_gif, target_path)
        animation_target = target_path.name

    prompt_path = summary_dir / "prompt.txt"
    prompt_path.write_text(prompt or "", encoding="utf-8")
    manifest = {
        "run": run_dir.name,
        "source_run_dir": str(run_dir.resolve()),
        "prompt": prompt,
        "screenshot_source_dir": str(screenshot_dir.resolve()) if screenshot_dir else None,
        "screenshots": copied_screenshots,
        "animation_gif_source": str(animation_gif.resolve()) if animation_gif else None,
        "animation_gif": animation_target,
        "agent_models_source": str((run_dir / "agent_models.json").resolve()) if (run_dir / "agent_models.json").exists() else None,
    }
    (summary_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def read_prompt(run_dir: Path) -> str | None:
    for name in ("ir.json", "ir_planned.json", "ir_animation_stage.json", "ir_scene_stage.json"):
        path = run_dir / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        prompt = data.get("prompt")
        if isinstance(prompt, dict):
            text = prompt.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()
    return None


def latest_screenshot_dir(run_dir: Path) -> Path | None:
    root = run_dir / "screenshots"
    if not root.exists():
        return None
    candidates = [
        path
        for path in root.rglob("*")
        if path.is_dir() and any(child.suffix.lower() in IMAGE_SUFFIXES for child in path.iterdir() if child.is_file())
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.as_posix()))


def latest_animation_gif(run_dir: Path) -> Path | None:
    root = run_dir / "animation"
    if not root.exists():
        return None
    candidates = [path for path in root.rglob("animation.gif") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.as_posix()))


def write_index(output_dir: Path) -> None:
    manifests = []
    for manifest_path in sorted(output_dir.glob("run_*/manifest.json")):
        try:
            manifests.append(json.loads(manifest_path.read_text(encoding="utf-8")))
        except Exception:
            continue
    lines = ["# Runs Summary", ""]
    for manifest in manifests:
        run = manifest.get("run", "")
        prompt = (manifest.get("prompt") or "").replace("\n", " ").strip()
        if len(prompt) > 240:
            prompt = prompt[:237] + "..."
        lines.append(f"## {run}")
        lines.append("")
        lines.append(prompt)
        lines.append("")
        if manifest.get("screenshots"):
            lines.append(f"- screenshots: `{run}/screenshots/`")
        if manifest.get("animation_gif"):
            lines.append(f"- animation: `{run}/animation.gif`")
        lines.append("")
    (output_dir / "index.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
