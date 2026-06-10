"""Generate a mobile-friendly static showroom for curated showcase entries.

Example:
    python scripts/generate_showroom.py --showcase-dir showcase --output-dir showroom
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import html
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any


MEDIA_PRIORITY = ("animation.mp4", "animation.gif", "scene.jpg", "scene.png")
COPY_SUFFIXES = {".gif", ".jpg", ".jpeg", ".mp4", ".png", ".webp"}


@dataclass(slots=True)
class IterationMedia:
    label: str
    media: str


@dataclass(slots=True)
class ShowroomEntry:
    slug: str
    title: str
    kind: str
    prompt: str
    note: str
    source_run: str
    validation_passed: int
    validation_failed: int
    models: dict[str, str]
    primary_media: str | None
    poster: str | None
    source_path: str | None
    blend_path: str | None
    readme_path: str | None
    assets: list[str]
    iteration_media: list[IterationMedia]


@dataclass(slots=True)
class ShowroomResult:
    output_dir: Path
    entries: list[ShowroomEntry]
    warnings: list[str]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a static showroom website from showcase entries.")
    parser.add_argument("--showcase-dir", type=Path, default=Path("showcase"), help="Directory containing showcase/<slug> entries.")
    parser.add_argument("--output-dir", type=Path, default=Path("showroom"), help="Directory where the static website is written.")
    parser.add_argument("--title", default="VeriAnim Showroom", help="Title shown on the generated website.")
    parser.add_argument(
        "--subtitle",
        default="A scan-friendly gallery of generated Blender scenes and animations.",
        help="Subtitle shown under the title.",
    )
    parser.add_argument("--no-copy-media", action="store_true", help="Reference showcase media in place instead of copying it into output-dir.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = generate_showroom(
            showcase_dir=args.showcase_dir,
            output_dir=args.output_dir,
            title=args.title,
            subtitle=args.subtitle,
            copy_media=not args.no_copy_media,
        )
    except Exception as exc:
        print(f"[showroom] failed: {exc}", file=sys.stderr)
        return 1

    print(f"[showroom] wrote {result.output_dir / 'index.html'}")
    print(f"[showroom] entries: {len(result.entries)}")
    for warning in result.warnings:
        print(f"[showroom] warning: {warning}", file=sys.stderr)
    return 0


def generate_showroom(
    *,
    showcase_dir: Path = Path("showcase"),
    output_dir: Path = Path("showroom"),
    title: str = "VeriAnim Showroom",
    subtitle: str = "A scan-friendly gallery of generated Blender scenes and animations.",
    copy_media: bool = True,
) -> ShowroomResult:
    showcase_dir = showcase_dir.resolve()
    output_dir = output_dir.resolve()
    if not showcase_dir.exists() or not showcase_dir.is_dir():
        raise FileNotFoundError(f"Showcase directory does not exist: {showcase_dir}")

    warnings: list[str] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    if copy_media:
        reset_assets_dir(output_dir / "assets")

    entries = collect_entries(showcase_dir=showcase_dir, output_dir=output_dir, copy_media=copy_media, warnings=warnings)
    if not entries:
        warnings.append(f"No showcase entries with metadata.json were found under {showcase_dir}.")

    (output_dir / "index.html").write_text(render_html(entries, title=title, subtitle=subtitle), encoding="utf-8")
    return ShowroomResult(output_dir=output_dir, entries=entries, warnings=warnings)


def reset_assets_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def collect_entries(*, showcase_dir: Path, output_dir: Path, copy_media: bool, warnings: list[str]) -> list[ShowroomEntry]:
    entries: list[ShowroomEntry] = []
    for entry_dir in sorted(path for path in showcase_dir.iterdir() if path.is_dir()):
        metadata_path = entry_dir / "metadata.json"
        if not metadata_path.exists():
            continue
        metadata = read_json(metadata_path)
        if not metadata:
            warnings.append(f"Skipping {entry_dir.name}: metadata.json could not be parsed.")
            continue
        entries.append(
            build_entry(
                entry_dir=entry_dir,
                metadata=metadata,
                output_dir=output_dir,
                copy_media=copy_media,
                warnings=warnings,
            )
        )
    return sorted(entries, key=lambda entry: (kind_rank(entry.kind), entry.title.lower()))


def build_entry(
    *,
    entry_dir: Path,
    metadata: dict[str, Any],
    output_dir: Path,
    copy_media: bool,
    warnings: list[str],
) -> ShowroomEntry:
    slug = entry_dir.name
    title = metadata_title(metadata, slug)
    kind = str(metadata.get("kind") or "static_scene")
    prompt = str(metadata.get("prompt") or read_text(entry_dir / "prompt.txt") or "")
    note = str(metadata.get("selected_note") or "")
    source_run = str(metadata.get("source_run") or "")
    models = normalize_models(metadata.get("models"))
    validation_passed, validation_failed = validation_counts(metadata.get("validation_reports"))

    copied_assets: list[str] = []
    asset_map: dict[str, str] = {}
    source_media = [entry_dir / name for name in MEDIA_PRIORITY if (entry_dir / name).exists()]
    source_media.extend(path for path in sorted(entry_dir.iterdir()) if path.is_file() and path.suffix.lower() in COPY_SUFFIXES and path.name not in MEDIA_PRIORITY)

    if copy_media:
        dest_dir = output_dir / "assets" / slug
        dest_dir.mkdir(parents=True, exist_ok=True)
        for path in source_media:
            target = dest_dir / path.name
            shutil.copy2(path, target)
            rel = target.relative_to(output_dir).as_posix()
            asset_map[path.name] = rel
            copied_assets.append(rel)
    else:
        for path in source_media:
            rel = relative_from_output(path, output_dir)
            asset_map[path.name] = rel
            copied_assets.append(rel)

    primary_media = first_present(asset_map, MEDIA_PRIORITY)
    poster = first_present(asset_map, ("scene.jpg", "scene.png"))
    if not primary_media:
        warnings.append(f"{slug} has no media preview file.")

    iteration_media = collect_iteration_media(
        entry_dir=entry_dir,
        metadata=metadata,
        output_dir=output_dir,
        copy_media=copy_media,
        warnings=warnings,
        slug=slug,
    )
    copied_assets.extend(item.media for item in iteration_media if item.media not in copied_assets)

    return ShowroomEntry(
        slug=slug,
        title=title,
        kind=kind,
        prompt=prompt,
        note=note,
        source_run=source_run,
        validation_passed=validation_passed,
        validation_failed=validation_failed,
        models=models,
        primary_media=primary_media,
        poster=poster,
        source_path=entry_link(entry_dir / "source.py", output_dir),
        blend_path=entry_link(entry_dir / "scene.blend", output_dir),
        readme_path=entry_link(entry_dir / "README.md", output_dir),
        assets=copied_assets,
        iteration_media=iteration_media,
    )


def collect_iteration_media(
    *,
    entry_dir: Path,
    metadata: dict[str, Any],
    output_dir: Path,
    copy_media: bool,
    warnings: list[str],
    slug: str,
) -> list[IterationMedia]:
    raw_items = metadata.get("iteration_media")
    if not isinstance(raw_items, list):
        return []

    items: list[IterationMedia] = []
    used_names: set[str] = set()
    for index, raw_item in enumerate(raw_items, start=1):
        label, raw_path = parse_iteration_item(raw_item, index)
        if not raw_path:
            warnings.append(f"{slug} iteration {index} is missing a media path.")
            continue
        source = resolve_metadata_path(raw_path, entry_dir)
        if not source or not source.exists() or not source.is_file():
            warnings.append(f"{slug} iteration {index} media was not found: {raw_path}")
            continue
        if source.suffix.lower() not in COPY_SUFFIXES:
            warnings.append(f"{slug} iteration {index} media has unsupported suffix: {source.name}")
            continue

        if copy_media:
            dest_dir = output_dir / "assets" / slug / "iterations"
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = dest_dir / unique_iteration_filename(label=label, source=source, used_names=used_names)
            shutil.copy2(source, target)
            media = target.relative_to(output_dir).as_posix()
        else:
            media = relative_from_output(source, output_dir)
        items.append(IterationMedia(label=label, media=media))
    return items


def parse_iteration_item(raw_item: Any, index: int) -> tuple[str, str]:
    default_label = f"Round {index:02d}"
    if isinstance(raw_item, str):
        return default_label, raw_item
    if not isinstance(raw_item, dict):
        return default_label, ""
    label = raw_item.get("label")
    path = raw_item.get("path")
    clean_label = str(label).strip() if isinstance(label, str) and label.strip() else default_label
    return clean_label, str(path).strip() if isinstance(path, str) else ""


def resolve_metadata_path(raw_path: str, entry_dir: Path) -> Path | None:
    path = Path(raw_path)
    if path.is_absolute():
        return path

    local_path = (entry_dir / path).resolve()
    if local_path.exists():
        return local_path

    repo_path = (entry_dir.parent.parent / path).resolve()
    if repo_path.exists():
        return repo_path

    return local_path


def unique_iteration_filename(*, label: str, source: Path, used_names: set[str]) -> str:
    stem = "".join(char.lower() if char.isalnum() else "_" for char in label).strip("_") or source.stem
    name = f"{stem}{source.suffix.lower()}"
    counter = 2
    while name in used_names:
        name = f"{stem}_{counter}{source.suffix.lower()}"
        counter += 1
    used_names.add(name)
    return name


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return None


def metadata_title(metadata: dict[str, Any], slug: str) -> str:
    name = metadata.get("name")
    if isinstance(name, str) and name.strip():
        return humanize(name)
    return humanize(slug.replace("_", " "))


def humanize(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("_", " ").split())


def normalize_models(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    models: dict[str, str] = {}
    for key, model in value.items():
        if isinstance(model, str):
            models[str(key)] = model
    return models


def validation_counts(value: Any) -> tuple[int, int]:
    if not isinstance(value, list):
        return 0, 0
    passed = 0
    failed = 0
    for report in value:
        if not isinstance(report, dict):
            continue
        if report.get("passed") is True:
            passed += 1
        elif report.get("passed") is False:
            failed += 1
    return passed, failed


def first_present(asset_map: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        if name in asset_map:
            return asset_map[name]
    return None


def entry_link(path: Path, output_dir: Path) -> str | None:
    if not path.exists():
        return None
    return relative_from_output(path, output_dir)


def relative_from_output(path: Path, output_dir: Path) -> str:
    return Path(os.path.relpath(path.resolve(), start=output_dir.resolve())).as_posix()


def kind_rank(kind: str) -> int:
    return 0 if kind == "animation" else 1


def render_html(entries: list[ShowroomEntry], *, title: str, subtitle: str) -> str:
    payload = json.dumps([entry_payload(entry) for entry in entries], ensure_ascii=False).replace("</", "<\\/")
    animation_count = sum(1 for entry in entries if entry.kind == "animation")
    static_count = len(entries) - animation_count
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#f7f4ed">
  <title>{escape(title)}</title>
  <style>
{CSS}
  </style>
</head>
<body>
  <header class="hero">
    <div class="hero-copy">
      <p class="eyebrow">Showcase gallery</p>
      <h1>{escape(title)}</h1>
      <p class="subtitle">{escape(subtitle)}</p>
    </div>
    <dl class="stats" aria-label="Showroom stats">
      <div><dt>{len(entries)}</dt><dd>Total</dd></div>
      <div><dt>{animation_count}</dt><dd>Animations</dd></div>
      <div><dt>{static_count}</dt><dd>Scenes</dd></div>
    </dl>
  </header>

  <main>
    <section class="controls" aria-label="Showcase controls">
      <label class="search">
        <span>Search</span>
        <input id="search" type="search" placeholder="Search prompt, title, model..." autocomplete="off">
      </label>
      <div class="segments" role="group" aria-label="Filter showcase kind">
        <button class="segment is-active" type="button" data-filter="all">All</button>
        <button class="segment" type="button" data-filter="animation">Animations</button>
        <button class="segment" type="button" data-filter="static_scene">Scenes</button>
      </div>
    </section>
    <section id="gallery" class="gallery" aria-live="polite"></section>
    <p id="empty" class="empty" hidden>No matching showcase entries.</p>
  </main>

  <dialog id="detail">
    <button class="close" type="button" aria-label="Close detail">x</button>
    <div id="detail-body"></div>
  </dialog>

  <script>
    const ENTRIES = {payload};
{JS}
  </script>
</body>
</html>
"""


def entry_payload(entry: ShowroomEntry) -> dict[str, Any]:
    return {
        "slug": entry.slug,
        "title": entry.title,
        "kind": entry.kind,
        "prompt": entry.prompt,
        "note": entry.note,
        "sourceRun": entry.source_run,
        "validationPassed": entry.validation_passed,
        "validationFailed": entry.validation_failed,
        "models": entry.models,
        "primaryMedia": entry.primary_media,
        "poster": entry.poster,
        "sourcePath": entry.source_path,
        "blendPath": entry.blend_path,
        "readmePath": entry.readme_path,
        "assets": entry.assets,
        "iterationMedia": [{"label": item.label, "media": item.media} for item in entry.iteration_media],
    }


def escape(value: str) -> str:
    return html.escape(value, quote=True)


CSS = """
:root {
  color-scheme: light;
  --ink: #19201c;
  --muted: #687168;
  --paper: #f7f4ed;
  --panel: #fffdf8;
  --line: #ddd7c9;
  --accent: #1f7a5c;
  --accent-soft: #d9eee6;
  --warn: #ba593f;
  --shadow: 0 20px 48px rgba(25, 32, 28, 0.12);
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-height: 100vh;
  background: var(--paper);
  color: var(--ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

button,
input {
  font: inherit;
}

.hero {
  display: grid;
  gap: 24px;
  padding: max(28px, env(safe-area-inset-top)) clamp(18px, 5vw, 64px) 24px;
  border-bottom: 1px solid var(--line);
  background: linear-gradient(180deg, #fffdf8 0%, #f7f4ed 100%);
}

.hero-copy {
  max-width: 920px;
}

.eyebrow {
  margin: 0 0 10px;
  color: var(--accent);
  font-size: 0.78rem;
  font-weight: 800;
  letter-spacing: 0;
  text-transform: uppercase;
}

h1 {
  margin: 0;
  max-width: 12ch;
  font-size: clamp(2.25rem, 8vw, 5.4rem);
  line-height: 0.95;
  letter-spacing: 0;
}

.subtitle {
  margin: 18px 0 0;
  max-width: 680px;
  color: var(--muted);
  font-size: clamp(1rem, 2.4vw, 1.2rem);
  line-height: 1.55;
}

.stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin: 0;
}

.stats div {
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255, 253, 248, 0.78);
}

.stats dt {
  font-size: 1.65rem;
  font-weight: 850;
}

.stats dd {
  margin: 2px 0 0;
  color: var(--muted);
  font-size: 0.78rem;
  text-transform: uppercase;
}

main {
  width: min(1180px, 100%);
  margin: 0 auto;
  padding: 18px clamp(14px, 4vw, 28px) 56px;
}

.controls {
  position: sticky;
  top: 0;
  z-index: 5;
  display: grid;
  gap: 12px;
  padding: 12px 0 16px;
  background: rgba(247, 244, 237, 0.94);
  backdrop-filter: blur(12px);
}

.search {
  display: grid;
  gap: 6px;
  color: var(--muted);
  font-size: 0.8rem;
  font-weight: 700;
  text-transform: uppercase;
}

.search input {
  width: 100%;
  min-height: 46px;
  padding: 0 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  color: var(--ink);
  outline: none;
}

.search input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.segments {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

.segment {
  min-height: 42px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  color: var(--muted);
  font-weight: 800;
}

.segment.is-active {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
}

.gallery {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr));
  gap: 16px;
}

.card {
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  box-shadow: 0 10px 26px rgba(25, 32, 28, 0.07);
}

.media {
  position: relative;
  aspect-ratio: 16 / 10;
  background: #e7e1d5;
}

.media img,
.media video {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
}

.badge {
  position: absolute;
  left: 10px;
  top: 10px;
  padding: 6px 9px;
  border-radius: 999px;
  background: rgba(25, 32, 28, 0.78);
  color: #fff;
  font-size: 0.74rem;
  font-weight: 800;
}

.card-body {
  display: grid;
  gap: 12px;
  padding: 14px;
}

.card h2 {
  margin: 0;
  font-size: 1.15rem;
  line-height: 1.22;
}

.prompt {
  display: -webkit-box;
  min-height: 3.9em;
  margin: 0;
  overflow: hidden;
  color: var(--muted);
  line-height: 1.3;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.chip {
  padding: 5px 8px;
  border-radius: 999px;
  background: #efe9dc;
  color: #4d574f;
  font-size: 0.74rem;
  font-weight: 750;
}

.chip.fail {
  background: #f7ded6;
  color: var(--warn);
}

.chip.iterations {
  background: #dcefeb;
  color: #1c634f;
}

.actions {
  display: grid;
  grid-template-columns: 1fr;
  gap: 8px;
}

.details {
  min-height: 44px;
  border: 0;
  border-radius: 8px;
  background: var(--ink);
  color: #fff;
  font-weight: 850;
}

.links {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  min-height: 24px;
}

a {
  color: var(--accent);
  font-weight: 800;
  text-decoration-thickness: 2px;
  text-underline-offset: 3px;
}

.empty {
  margin: 28px 0;
  color: var(--muted);
  text-align: center;
}

dialog {
  width: min(940px, calc(100% - 22px));
  max-height: min(820px, calc(100% - 22px));
  padding: 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  color: var(--ink);
  box-shadow: var(--shadow);
}

dialog::backdrop {
  background: rgba(25, 32, 28, 0.42);
}

.close {
  position: sticky;
  top: 10px;
  float: right;
  z-index: 2;
  width: 42px;
  height: 42px;
  margin: 10px;
  border: 0;
  border-radius: 999px;
  background: var(--ink);
  color: #fff;
  font-size: 1.2rem;
  font-weight: 850;
}

.detail-wrap {
  display: grid;
  gap: 18px;
  padding: 16px;
}

.detail-wrap .media {
  border-radius: 8px;
  overflow: hidden;
}

.detail-wrap h2 {
  margin: 0;
  font-size: clamp(1.55rem, 6vw, 2.5rem);
  line-height: 1;
}

.detail-grid {
  display: grid;
  gap: 14px;
}

.detail-grid section {
  padding-top: 14px;
  border-top: 1px solid var(--line);
}

.detail-grid h3 {
  margin: 0 0 8px;
  font-size: 0.8rem;
  text-transform: uppercase;
  color: var(--muted);
}

.detail-grid p {
  margin: 0;
  line-height: 1.5;
}

.iteration-section {
  display: grid;
  gap: 10px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}

.iteration-section h3 {
  margin: 0;
  font-size: 0.8rem;
  text-transform: uppercase;
  color: var(--muted);
}

.iteration-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 10px;
}

.iteration-item {
  overflow: hidden;
  margin: 0;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f2ede3;
}

.iteration-item img,
.iteration-item video {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 10;
  object-fit: cover;
  background: #e7e1d5;
}

.iteration-item span {
  display: block;
  padding: 7px 8px;
  color: var(--muted);
  font-size: 0.74rem;
  font-weight: 800;
}

.model-list {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.model-list li {
  overflow-wrap: anywhere;
  color: var(--muted);
}

@media (min-width: 760px) {
  .hero {
    grid-template-columns: minmax(0, 1fr) minmax(280px, 420px);
    align-items: end;
    padding-bottom: 36px;
  }

  .controls {
    grid-template-columns: minmax(260px, 1fr) 390px;
    align-items: end;
  }

  .actions {
    grid-template-columns: 140px 1fr;
    align-items: center;
  }

  .detail-wrap {
    padding: 20px;
  }

  .detail-grid {
    grid-template-columns: 1.2fr 0.8fr;
  }
}
"""


JS = """
    const gallery = document.querySelector("#gallery");
    const empty = document.querySelector("#empty");
    const search = document.querySelector("#search");
    const detail = document.querySelector("#detail");
    const detailBody = document.querySelector("#detail-body");
    const closeButton = document.querySelector(".close");
    let activeFilter = "all";

    function mediaMarkup(entry) {
      if (!entry.primaryMedia) {
        return `<div class="media"><span class="badge">No media</span></div>`;
      }
      const poster = entry.poster ? ` poster="${escapeAttr(entry.poster)}"` : "";
      if (entry.primaryMedia.endsWith(".mp4")) {
        return `<div class="media"><video src="${escapeAttr(entry.primaryMedia)}"${poster} muted loop autoplay playsinline controls preload="metadata"></video><span class="badge">${labelFor(entry.kind)}</span></div>`;
      }
      return `<div class="media"><img src="${escapeAttr(entry.primaryMedia)}" alt="${escapeAttr(entry.title)} preview" loading="lazy"><span class="badge">${labelFor(entry.kind)}</span></div>`;
    }

    function labelFor(kind) {
      return kind === "animation" ? "Animation" : "Scene";
    }

    function iterationMediaMarkup(item) {
      const label = escapeHtml(item.label || "Iteration");
      const media = escapeAttr(item.media);
      if (item.media.endsWith(".mp4")) {
        return `<figure class="iteration-item"><video src="${media}" muted loop autoplay playsinline controls preload="metadata"></video><span>${label}</span></figure>`;
      }
      return `<figure class="iteration-item"><img src="${media}" alt="${label}" loading="lazy"><span>${label}</span></figure>`;
    }

    function iterationSectionMarkup(entry) {
      const items = entry.iterationMedia || [];
      if (!items.length) return "";
      return `<section class="iteration-section">
        <h3>Iteration process</h3>
        <div class="iteration-strip">${items.map(iterationMediaMarkup).join("")}</div>
      </section>`;
    }

    function cardMarkup(entry) {
      const failChip = entry.validationFailed ? `<span class="chip fail">${entry.validationFailed} failed</span>` : "";
      const iterationChip = entry.iterationMedia?.length ? `<span class="chip iterations">${entry.iterationMedia.length} iterations</span>` : "";
      const readme = entry.readmePath ? `<a href="${escapeAttr(entry.readmePath)}">Readme</a>` : "";
      const source = entry.sourcePath ? `<a href="${escapeAttr(entry.sourcePath)}">Source</a>` : "";
      return `<article class="card" data-slug="${escapeAttr(entry.slug)}">
        ${mediaMarkup(entry)}
        <div class="card-body">
          <h2>${escapeHtml(entry.title)}</h2>
          <p class="prompt">${escapeHtml(entry.prompt || entry.note || "No prompt recorded.")}</p>
          <div class="chips">
            <span class="chip">${labelFor(entry.kind)}</span>
            <span class="chip">${entry.validationPassed} passed</span>
            ${failChip}
            ${iterationChip}
          </div>
          <div class="actions">
            <button class="details" type="button" data-detail="${escapeAttr(entry.slug)}">Details</button>
            <div class="links">${readme}${source}</div>
          </div>
        </div>
      </article>`;
    }

    function render() {
      const query = search.value.trim().toLowerCase();
      const visible = ENTRIES.filter((entry) => {
        const matchesKind = activeFilter === "all" || entry.kind === activeFilter;
        const haystack = [
          entry.title,
          entry.prompt,
          entry.note,
          entry.sourceRun,
          (entry.iterationMedia || []).map((item) => item.label).join(" "),
          Object.values(entry.models || {}).join(" ")
        ].join(" ").toLowerCase();
        return matchesKind && (!query || haystack.includes(query));
      });
      gallery.innerHTML = visible.map(cardMarkup).join("");
      empty.hidden = visible.length !== 0;
    }

    function showDetail(slug) {
      const entry = ENTRIES.find((candidate) => candidate.slug === slug);
      if (!entry) return;
      const models = Object.entries(entry.models || {}).map(([name, model]) => `<li><strong>${escapeHtml(name)}:</strong> ${escapeHtml(model)}</li>`).join("");
      const source = entry.sourcePath ? `<a href="${escapeAttr(entry.sourcePath)}">Generated source</a>` : "";
      const blend = entry.blendPath ? `<a href="${escapeAttr(entry.blendPath)}">Blend file</a>` : "";
      const readme = entry.readmePath ? `<a href="${escapeAttr(entry.readmePath)}">Entry readme</a>` : "";
      detailBody.innerHTML = `<div class="detail-wrap">
        ${mediaMarkup(entry)}
        <h2>${escapeHtml(entry.title)}</h2>
        <div class="chips">
          <span class="chip">${labelFor(entry.kind)}</span>
          <span class="chip">${entry.validationPassed} validation passed</span>
          ${entry.validationFailed ? `<span class="chip fail">${entry.validationFailed} validation failed</span>` : ""}
          ${entry.iterationMedia?.length ? `<span class="chip iterations">${entry.iterationMedia.length} iterations</span>` : ""}
        </div>
        ${iterationSectionMarkup(entry)}
        <div class="detail-grid">
          <section>
            <h3>Prompt</h3>
            <p>${escapeHtml(entry.prompt || "No prompt recorded.")}</p>
          </section>
          <section>
            <h3>Artifacts</h3>
            <p class="links">${source}${blend}${readme}</p>
          </section>
          <section>
            <h3>Curation note</h3>
            <p>${escapeHtml(entry.note || "No note recorded.")}</p>
          </section>
          <section>
            <h3>Models</h3>
            <ul class="model-list">${models || "<li>No model manifest recorded.</li>"}</ul>
          </section>
        </div>
      </div>`;
      detail.showModal();
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\\"": "&quot;",
        "'": "&#39;"
      })[char]);
    }

    function escapeAttr(value) {
      return escapeHtml(value);
    }

    document.querySelectorAll(".segment").forEach((button) => {
      button.addEventListener("click", () => {
        activeFilter = button.dataset.filter;
        document.querySelectorAll(".segment").forEach((segment) => segment.classList.toggle("is-active", segment === button));
        render();
      });
    });

    search.addEventListener("input", render);
    gallery.addEventListener("click", (event) => {
      const button = event.target.closest("[data-detail]");
      if (button) showDetail(button.dataset.detail);
    });
    closeButton.addEventListener("click", () => detail.close());
    detail.addEventListener("click", (event) => {
      if (event.target === detail) detail.close();
    });
    render();
"""


if __name__ == "__main__":
    raise SystemExit(main())
