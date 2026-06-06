"""Promote a VeriAnim run directory into a curated showcase entry.

Example:
    python scripts/promote_run_to_showcase.py runs/run_20260606_231203 wooden_chair_table --note "Clean static furniture scene."
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}
REPORT_SKIP_TOKENS = ("_execution", "_render_", "refiner_failed")


@dataclass(slots=True)
class PromotionResult:
    destination: Path
    artifacts: list[str]
    warnings: list[str]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote a VeriAnim run_* directory into showcase/<name>.")
    parser.add_argument("run_dir", type=Path, help="Source run directory, for example runs/run_20260606_231203.")
    parser.add_argument("name", help="Showcase directory name. Spaces are converted to snake_case.")
    parser.add_argument("--showcase-dir", type=Path, default=Path("showcase"), help="Directory containing showcase entries.")
    parser.add_argument("--title", help="Display title for README.md. Defaults to a title-cased form of name.")
    parser.add_argument("--kind", choices=("auto", "static_scene", "animation"), default="auto", help="Showcase entry type.")
    parser.add_argument("--note", default=None, help="Short curation note used in README.md and metadata.json.")
    parser.add_argument("--cover", type=Path, help="Specific screenshot/image to use for scene.png and scene.jpg.")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing showcase entry directory.")
    parser.add_argument("--include-ir", action="store_true", help="Also copy ir.json into the showcase entry.")
    parser.add_argument("--copy-frames", action="store_true", help="Copy sampled animation frame_*.png files when available.")
    parser.add_argument("--max-frames", type=int, default=12, help="Maximum sampled frames to copy with --copy-frames.")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg executable used to create animation.mp4 from animation.gif.")
    parser.add_argument("--no-generate-mp4", action="store_true", help="Do not create animation.mp4 when the source run only has a GIF.")
    parser.add_argument("--update-index", action="store_true", help="Insert the new entry into showcase/README.md.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = promote_run(
            run_dir=args.run_dir,
            name=args.name,
            showcase_dir=args.showcase_dir,
            title=args.title,
            kind=args.kind,
            note=args.note,
            cover=args.cover,
            force=args.force,
            include_ir=args.include_ir,
            copy_frames=args.copy_frames,
            max_frames=args.max_frames,
            generate_mp4=not args.no_generate_mp4,
            ffmpeg=args.ffmpeg,
            update_index=args.update_index,
        )
    except Exception as exc:
        print(f"[showcase] failed: {exc}", file=sys.stderr)
        return 1

    print(f"[showcase] wrote {result.destination}")
    if result.artifacts:
        print("[showcase] artifacts: " + ", ".join(result.artifacts))
    for warning in result.warnings:
        print(f"[showcase] warning: {warning}", file=sys.stderr)
    return 0


def promote_run(
    *,
    run_dir: Path,
    name: str,
    showcase_dir: Path = Path("showcase"),
    title: str | None = None,
    kind: str = "auto",
    note: str | None = None,
    cover: Path | None = None,
    force: bool = False,
    include_ir: bool = False,
    copy_frames: bool = False,
    max_frames: int = 12,
    generate_mp4: bool = True,
    ffmpeg: str = "ffmpeg",
    update_index: bool = False,
) -> PromotionResult:
    run_dir = run_dir.resolve()
    showcase_dir = showcase_dir.resolve()
    if not run_dir.exists() or not run_dir.is_dir():
        raise FileNotFoundError(f"Run directory does not exist: {run_dir}")

    slug = slugify(name)
    if not slug:
        raise ValueError("Showcase name must contain at least one letter or number.")
    destination = showcase_dir / slug
    if destination.exists():
        if not force:
            raise FileExistsError(f"Showcase entry already exists: {destination}. Pass --force to overwrite it.")
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    prompt = read_prompt(run_dir) or ""
    models = read_models(run_dir)
    entry_kind = resolve_kind(kind, run_dir)
    display_title = title or humanize_slug(slug)
    selected_note = note or default_note(entry_kind, run_dir)

    source_code = choose_source_code(run_dir)
    shutil.copy2(source_code, destination / "source.py")
    (destination / "prompt.txt").write_text(prompt + ("\n" if prompt else ""), encoding="utf-8")
    (destination / "models.json").write_text(json.dumps(models, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    blend_path = run_dir / "scene.blend"
    if blend_path.exists():
        shutil.copy2(blend_path, destination / "scene.blend")
    else:
        warnings.append("scene.blend was not found in the run. Re-export with scripts/export_blend_from_python.py if needed.")

    if include_ir and (run_dir / "ir.json").exists():
        shutil.copy2(run_dir / "ir.json", destination / "ir.json")

    cover_path = cover.resolve() if cover else choose_cover_image(run_dir)
    if cover_path and cover_path.exists():
        copy_scene_images(cover_path, destination, warnings)
    else:
        warnings.append("No screenshot cover was found; scene.png and scene.jpg were not created.")

    if entry_kind == "animation":
        gif_path = latest_animation_gif(run_dir)
        gif_target = destination / "animation.gif"
        if gif_path:
            shutil.copy2(gif_path, gif_target)
        else:
            warnings.append("No animation.gif was found for this animation showcase entry.")
        video_path = latest_animation_video(run_dir)
        if video_path:
            shutil.copy2(video_path, destination / "animation.mp4")
        elif generate_mp4 and gif_path:
            create_mp4_from_gif(gif_target, destination / "animation.mp4", ffmpeg=ffmpeg, warnings=warnings)
        elif gif_path:
            warnings.append("No source MP4 was found and MP4 generation is disabled.")
        if copy_frames:
            copy_sampled_frames(run_dir, destination, max_frames=max_frames)

    reports = collect_validation_reports(run_dir)
    metadata = {
        "name": slug.replace("_", " "),
        "kind": entry_kind,
        "source_run": display_path(run_dir),
        "source_code": display_path(source_code),
        "prompt": prompt,
        "models": models,
        "selected_note": selected_note,
        "validation_reports": reports,
        "artifacts": [],
    }
    write_readme(destination / "README.md", display_title, selected_note, entry_kind, run_dir, prompt, models, reports)
    artifact_names = sorted({path.name for path in destination.iterdir() if path.is_file()} | {"metadata.json"})
    metadata["artifacts"] = artifact_names
    (destination / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if update_index:
        update_showcase_index(showcase_dir / "README.md", slug, selected_note, entry_kind)

    return PromotionResult(destination=destination, artifacts=artifact_names, warnings=warnings)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return re.sub(r"_+", "_", slug)


def humanize_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("_") if part)


def default_note(kind: str, run_dir: Path) -> str:
    label = "animation" if kind == "animation" else "static scene"
    return f"Promoted {label} artifact from {run_dir.name}."


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def read_prompt(run_dir: Path) -> str | None:
    for name in ("ir.json", "ir_planned.json", "ir_animation_stage.json", "ir_scene_stage.json"):
        data = read_json(run_dir / name)
        if not data:
            continue
        prompt = data.get("prompt")
        if isinstance(prompt, dict):
            text = prompt.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()
    return None


def read_models(run_dir: Path) -> dict[str, str]:
    data = read_json(run_dir / "agent_models.json")
    if not data:
        return {}
    models: dict[str, str] = {}
    for name, record in data.items():
        if isinstance(record, dict) and isinstance(record.get("model"), str):
            models[str(name)] = record["model"]
        elif isinstance(record, str):
            models[str(name)] = record
    return models


def resolve_kind(kind: str, run_dir: Path) -> str:
    if kind != "auto":
        return kind
    data = read_json(run_dir / "ir.json") or read_json(run_dir / "ir_planned.json") or {}
    if data.get("animation"):
        return "animation"
    if latest_animation_gif(run_dir) or latest_animation_video(run_dir):
        return "animation"
    return "static_scene"


def choose_source_code(run_dir: Path) -> Path:
    preferred = [
        run_dir / "code" / "final_scene.py",
        run_dir / "code" / "generated_animation_stage.py",
        run_dir / "code" / "generated_scene.py",
    ]
    for path in preferred:
        if path.exists():
            return path
    candidates = sorted((run_dir / "code").glob("*.py"), key=lambda path: (path.stat().st_mtime, path.name))
    if candidates:
        return candidates[-1]
    raise FileNotFoundError(f"No generated Python source found under {run_dir / 'code'}")


def choose_cover_image(run_dir: Path) -> Path | None:
    screenshot_dir = latest_screenshot_dir(run_dir)
    if not screenshot_dir:
        return None
    images = sorted(path for path in screenshot_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)
    if not images:
        return None
    return max(images, key=cover_score)


def cover_score(path: Path) -> tuple[int, str]:
    name = path.stem.lower()
    score = 0
    for token, value in (
        ("three_quarter", 100),
        ("3q", 90),
        ("overall", 80),
        ("scene", 70),
        ("view", 10),
        ("top", -10),
        ("close", -20),
    ):
        if token in name:
            score += value
    return score, path.name


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


def latest_animation_video(run_dir: Path) -> Path | None:
    root = run_dir / "animation"
    if not root.exists():
        return None
    candidates = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.as_posix()))


def copy_scene_images(source: Path, destination: Path, warnings: list[str]) -> None:
    png_target = destination / "scene.png"
    jpg_target = destination / "scene.jpg"
    suffix = source.suffix.lower()
    if suffix == ".png":
        shutil.copy2(source, png_target)
        convert_image(source, jpg_target, "JPEG", warnings)
    elif suffix in {".jpg", ".jpeg"}:
        shutil.copy2(source, jpg_target)
        convert_image(source, png_target, "PNG", warnings)
    else:
        convert_image(source, png_target, "PNG", warnings)
        convert_image(source, jpg_target, "JPEG", warnings)


def convert_image(source: Path, target: Path, image_format: str, warnings: list[str]) -> None:
    try:
        from PIL import Image
    except Exception:
        warnings.append(f"Pillow is not available; could not create {target.name} from {source.name}.")
        return
    try:
        with Image.open(source) as image:
            if image_format == "JPEG":
                image = image.convert("RGB")
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, image_format)
    except Exception as exc:
        warnings.append(f"Could not create {target.name} from {source}: {exc}")


def copy_sampled_frames(run_dir: Path, destination: Path, *, max_frames: int) -> None:
    root = run_dir / "animation"
    if not root.exists() or max_frames <= 0:
        return
    candidates = sorted(path for path in root.rglob("*.png") if path.is_file())
    if not candidates:
        return
    for path in candidates[-max_frames:]:
        shutil.copy2(path, destination / path.name)


def create_mp4_from_gif(gif_path: Path, mp4_path: Path, *, ffmpeg: str, warnings: list[str]) -> None:
    executable = shutil.which(ffmpeg) if not Path(ffmpeg).exists() else ffmpeg
    if not executable:
        warnings.append("No source MP4 was found and ffmpeg is not available; animation.mp4 was not created.")
        return
    command = [
        executable,
        "-y",
        "-i",
        str(gif_path),
        "-movflags",
        "faststart",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        str(mp4_path),
    ]
    try:
        completed = subprocess.run(command, text=True, capture_output=True)
    except Exception as exc:
        warnings.append(f"Could not create animation.mp4 from animation.gif: {exc}")
        return
    if completed.returncode != 0 or not mp4_path.exists():
        detail = (completed.stderr or completed.stdout or "").strip()
        if len(detail) > 500:
            detail = detail[:497] + "..."
        warnings.append(f"ffmpeg could not create animation.mp4 from animation.gif. {detail}".strip())


def collect_validation_reports(run_dir: Path) -> list[dict[str, Any]]:
    reports_dir = run_dir / "reports"
    if not reports_dir.exists():
        return []
    reports: list[dict[str, Any]] = []
    for path in sorted(reports_dir.glob("*.json")):
        if any(token in path.name for token in REPORT_SKIP_TOKENS):
            continue
        data = read_json(path)
        if not data or not isinstance(data.get("passed"), bool):
            continue
        reports.append(
            {
                "file": path.name,
                "passed": data["passed"],
                "summary": data.get("summary"),
                "issues": summarize_issues(data.get("issues", [])),
            }
        )
    return reports


def summarize_issues(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    issues = []
    for issue in value:
        if not isinstance(issue, dict):
            continue
        issues.append(
            {
                key: issue[key]
                for key in ("code", "message", "severity", "target_id", "relation_id", "frame")
                if key in issue and issue[key] is not None
            }
        )
    return issues


def write_readme(
    path: Path,
    title: str,
    note: str,
    kind: str,
    run_dir: Path,
    prompt: str,
    models: dict[str, str],
    reports: list[dict[str, Any]],
) -> None:
    lines = [f"# {title}", "", note, "", f"Source run: `{display_path(run_dir)}`", f"Type: `{kind}`", ""]
    if models:
        lines.extend(["Models:"])
        for name, model in models.items():
            lines.append(f"- {name}: `{model}`")
        lines.append("")
    lines.append("Validation snapshot:")
    if reports:
        for report in reports:
            status = "passed" if report.get("passed") else "failed"
            summary = report.get("summary") or ("Validation passed." if report.get("passed") else "Validation failed.")
            lines.append(f"- {report['file']}: {status} - {summary}")
    else:
        lines.append("- No validation reports found.")
    lines.extend(["", "Prompt:", "```text", prompt, "```", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def update_showcase_index(index_path: Path, slug: str, note: str, kind: str) -> None:
    if not index_path.exists():
        return
    text = index_path.read_text(encoding="utf-8")
    if f"`{slug}`" in text:
        return
    heading = "## Animation" if kind == "animation" else "## Static Scenes"
    marker = f"{heading}\n"
    if marker not in text:
        return
    insertion = f"- `{slug}`: {note}\n"
    start = text.index(marker) + len(marker)
    next_heading = text.find("\n## ", start)
    if next_heading == -1:
        updated = text[:start] + "\n" + insertion + text[start:]
    else:
        updated = text[:next_heading].rstrip() + "\n" + insertion + text[next_heading:]
    index_path.write_text(updated, encoding="utf-8")


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
