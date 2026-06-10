#!/usr/bin/env python3
"""Build a Reveal.js deck from a small Markdown dialect.

The converter is intentionally self-contained so the slides can be built in a
fresh Python environment. It supports enough Markdown for research talks plus a
few layout directives used by deck.md:

- `---` on its own line starts a new slide.
- `<!-- .slide: class="name" -->` sets a slide class.
- `:::{class-name}` ... `:::` wraps content in a styled div.
- `[columns]`, `[column]`, and matching closing tags create column layouts.
- `[[placeholder: Title | prompt=...]]` creates an empty media slot.
- `???` starts speaker notes for the current slide.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


REVEAL_CSS = "https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/dist/reveal.css"
REVEAL_THEME = "https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/dist/theme/white.css"
REVEAL_JS = "https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/dist/reveal.js"
REVEAL_NOTES = "https://cdn.jsdelivr.net/npm/reveal.js@5.1.0/plugin/notes/notes.js"
SLIDE_WIDTH = 1920
SLIDE_HEIGHT = 1080
CSS_PIXELS_PER_INCH = 96


@dataclass
class Slide:
    body: str
    classes: list[str] = field(default_factory=list)


def split_slides(markdown_text: str) -> list[str]:
    slides: list[list[str]] = [[]]
    for line in markdown_text.splitlines():
        if line.strip() == "---":
            slides.append([])
        else:
            slides[-1].append(line.rstrip())
    return ["\n".join(part).strip() for part in slides if "\n".join(part).strip()]


def inline_md(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
    return escaped


def image_line(line: str) -> str | None:
    match = re.match(r"!\[([^\]]*)\]\(([^)]+)\)(?:\{([^}]+)\})?$", line.strip())
    if not match:
        return None
    alt, src, attrs = match.groups()
    attr_text = ""
    if attrs:
        for token in attrs.split():
            if token.startswith("."):
                attr_text += f' class="{html.escape(token[1:])}"'
    return f'<img src="{html.escape(src)}" alt="{html.escape(alt)}"{attr_text}>'


def placeholder_line(line: str) -> str | None:
    match = re.match(r"\[\[placeholder:\s*(.+?)\s*\]\]$", line.strip())
    if not match:
        return None
    raw = match.group(1)
    parts = [part.strip() for part in raw.split("|")]
    title = parts[0]
    prompt = ""
    for part in parts[1:]:
        if part.startswith("prompt="):
            prompt = part.removeprefix("prompt=").strip()
    prompt_html = f"<p>{inline_md(prompt)}</p>" if prompt else "<p>Prompt placeholder</p>"
    return (
        '<div class="media-placeholder">'
        f"<strong>{inline_md(title)}</strong>"
        f"{prompt_html}"
        "</div>"
    )


def flush_paragraph(output: list[str], paragraph: list[str]) -> None:
    if paragraph:
        output.append(f"<p>{inline_md(' '.join(paragraph))}</p>")
        paragraph.clear()


def close_lists(output: list[str], stack: list[str], target_indent: int = -1) -> None:
    while stack and stack[-1][0] >= target_indent:
        _, tag = stack.pop()
        output.append(f"</{tag}>")


def render_lines(lines: Iterable[str]) -> str:
    output: list[str] = []
    paragraph: list[str] = []
    list_stack: list[tuple[int, str]] = []
    in_code = False
    code_lang = ""
    code_lines: list[str] = []
    in_notes = False
    notes_lines: list[str] = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped == "???":
            flush_paragraph(output, paragraph)
            close_lists(output, list_stack)
            in_notes = True
            continue
        if in_notes:
            notes_lines.append(line)
            continue

        code_match = re.match(r"```(\w+)?$", stripped)
        if code_match:
            flush_paragraph(output, paragraph)
            close_lists(output, list_stack)
            in_code = not in_code
            if in_code:
                code_lang = code_match.group(1) or ""
                code_lines = []
            else:
                code = html.escape("\n".join(code_lines))
                output.append(f'<pre><code class="language-{code_lang}">{code}</code></pre>')
            continue
        if in_code:
            code_lines.append(line)
            continue

        if not stripped:
            flush_paragraph(output, paragraph)
            close_lists(output, list_stack)
            continue

        if stripped.startswith("<!--"):
            continue

        if stripped == "[columns]":
            flush_paragraph(output, paragraph)
            close_lists(output, list_stack)
            output.append('<div class="columns">')
            continue
        if stripped == "[/columns]":
            flush_paragraph(output, paragraph)
            close_lists(output, list_stack)
            output.append("</div>")
            continue
        if stripped == "[column]":
            flush_paragraph(output, paragraph)
            close_lists(output, list_stack)
            output.append('<div class="column">')
            continue
        if stripped == "[/column]":
            flush_paragraph(output, paragraph)
            close_lists(output, list_stack)
            output.append("</div>")
            continue

        div_open = re.match(r":::\s*(?:\{([A-Za-z0-9_-]+)\}|([A-Za-z0-9_-]+))$", stripped)
        if div_open:
            flush_paragraph(output, paragraph)
            close_lists(output, list_stack)
            class_name = div_open.group(1) or div_open.group(2)
            output.append(f'<div class="{html.escape(class_name)}">')
            continue
        if stripped == ":::":
            flush_paragraph(output, paragraph)
            close_lists(output, list_stack)
            output.append("</div>")
            continue

        placeholder = placeholder_line(stripped)
        if placeholder:
            flush_paragraph(output, paragraph)
            close_lists(output, list_stack)
            output.append(placeholder)
            continue

        image = image_line(stripped)
        if image:
            flush_paragraph(output, paragraph)
            close_lists(output, list_stack)
            output.append(image)
            continue

        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading:
            flush_paragraph(output, paragraph)
            close_lists(output, list_stack)
            level = min(len(heading.group(1)), 4)
            output.append(f"<h{level}>{inline_md(heading.group(2))}</h{level}>")
            continue

        bullet = re.match(r"^(\s*)([-*])\s+(.+)$", line)
        ordered = re.match(r"^(\s*)(\d+)\.\s+(.+)$", line)
        if bullet or ordered:
            flush_paragraph(output, paragraph)
            match = bullet or ordered
            indent = len(match.group(1)) // 2
            tag = "ul" if bullet else "ol"
            while list_stack and list_stack[-1][0] > indent:
                _, close_tag = list_stack.pop()
                output.append(f"</{close_tag}>")
            if not list_stack or list_stack[-1][0] < indent or list_stack[-1][1] != tag:
                output.append(f"<{tag}>")
                list_stack.append((indent, tag))
            output.append(f"<li>{inline_md(match.group(3))}</li>")
            continue

        paragraph.append(stripped)

    flush_paragraph(output, paragraph)
    close_lists(output, list_stack)
    if in_notes:
        output.append(f'<aside class="notes">{render_lines(notes_lines)}</aside>')
    return "\n".join(output)


def parse_slide(raw: str) -> Slide:
    classes: list[str] = []
    lines = raw.splitlines()
    body_lines: list[str] = []
    class_re = re.compile(r'<!--\s*\.slide:\s*class="([^"]+)"\s*-->')
    for line in lines:
        match = class_re.match(line.strip())
        if match:
            classes.extend(match.group(1).split())
        else:
            body_lines.append(line)
    return Slide(body=render_lines(body_lines), classes=classes)


def build_html(markdown_text: str, title: str) -> str:
    slides = [parse_slide(raw) for raw in split_slides(markdown_text)]
    section_html = []
    for slide in slides:
        classes = f' class="{" ".join(slide.classes)}"' if slide.classes else ""
        section_html.append(f"<section{classes}>\n{slide.body}\n</section>")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="{REVEAL_CSS}">
  <link rel="stylesheet" href="{REVEAL_THEME}">
  <style>
{CUSTOM_CSS}
  </style>
</head>
<body>
  <div class="reveal">
    <div class="slides">
{chr(10).join(section_html)}
    </div>
  </div>
  <script src="{REVEAL_JS}"></script>
  <script src="{REVEAL_NOTES}"></script>
  <script>
    Reveal.initialize({{
      hash: true,
      slideNumber: "c/t",
      transition: "fade",
      backgroundTransition: "fade",
      controlsTutorial: false,
      width: {SLIDE_WIDTH},
      height: {SLIDE_HEIGHT},
      plugins: [ RevealNotes ]
    }});
  </script>
</body>
</html>
"""


CUSTOM_CSS = r"""
:root {
  --ink: #182230;
  --muted: #5d6b82;
  --line: #d8dee8;
  --blue: #2457a7;
  --green: #2f855a;
  --orange: #b55b18;
  --red: #b42318;
  --panel: #f7f8fb;
}

.reveal {
  color: var(--ink);
  font-family: "Aptos", "Inter", "Helvetica Neue", Arial, sans-serif;
  font-size: 52px;
}

.reveal .slides {
  text-align: left;
}

.reveal h1,
.reveal h2,
.reveal h3 {
  color: var(--ink);
  font-weight: 780;
  letter-spacing: 0;
  line-height: 1.08;
  margin: 0 0 0.45em;
}

.reveal h1 {
  font-size: 1.55em;
}

.reveal h2 {
  font-size: 1.25em;
}

.reveal h3 {
  font-size: 0.9em;
  color: var(--blue);
}

.reveal p,
.reveal li {
  font-size: 0.66em;
  line-height: 1.32;
}

.reveal ul,
.reveal ol {
  margin-left: 0;
  padding-left: 0.9em;
}

.reveal li + li {
  margin-top: 0.22em;
}

.reveal code {
  color: #0f4c81;
  background: #eef4fb;
  border-radius: 5px;
  padding: 0.06em 0.22em;
}

.reveal pre code {
  display: block;
  max-height: 460px;
  padding: 1em;
}

.title-slide {
  text-align: left;
}

.title-slide h1 {
  max-width: 880px;
  font-size: 1.75em;
}

.subtitle {
  margin-top: 1.1em;
  color: var(--muted);
  font-size: 0.72em;
}

.kicker {
  color: var(--blue);
  font-size: 0.58em;
  font-weight: 760;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.columns {
  align-items: stretch;
  display: grid;
  gap: 0.65em;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  margin-top: 0.65em;
}

.column {
  min-width: 0;
}

.column > .card,
.column > .callout,
.column > .warning,
.column > .metric,
.column > .media-placeholder,
.column > .showcase-card {
  box-sizing: border-box;
  height: 100%;
}

.card,
.callout,
.warning,
.metric,
.media-placeholder,
.showcase-card {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 0.68em;
}

.card {
  background: var(--panel);
}

.showcase-card {
  background: #ffffff;
  box-sizing: border-box;
  height: 100%;
}

.callout {
  background: #eef6ff;
  border-color: #b8d6ff;
}

.warning {
  background: #fff4ed;
  border-color: #ffd2bd;
}

.metric {
  background: #eefaf4;
  border-color: #bfe8d0;
}

.card strong,
.callout strong,
.warning strong,
.metric strong,
.media-placeholder strong,
.showcase-card strong {
  display: block;
  font-size: 0.95em;
  font-weight: 820;
  margin-bottom: 0.35em;
}

.warning strong,
.metric strong,
.callout strong {
  color: var(--ink);
}

.columns + .callout,
.columns + .warning,
.columns + .metric,
.columns + .card {
  margin-top: 0.72em;
}

.card ul,
.card ol,
.callout ul,
.callout ol,
.warning ul,
.warning ol,
.metric ul,
.metric ol,
.showcase-card ul,
.showcase-card ol {
  list-style-position: inside;
}

.showcase-card img.showcase-gif {
  background: #f2f4f8;
  border: 1px solid var(--line);
  border-radius: 6px;
  display: block;
  height: 330px;
  margin: 0 0 0.55em;
  object-fit: contain;
  width: 100%;
}

.showcase-card p,
.showcase-card li {
  font-size: 0.53em;
  line-height: 1.26;
}

.showcase-card code {
  font-size: 0.9em;
}

.iteration-wall-slide h1 {
  font-size: 0.56em;
  margin-bottom: 0.14em;
}

.iteration-grid {
  display: grid;
  align-items: stretch;
  gap: 0.14em;
  grid-template-columns: repeat(4, 1fr);
  grid-template-rows: repeat(4, minmax(0, 1fr));
  height: 850px;
}

.iteration-grid img.iteration-gif {
  background: #f2f4f8;
  border: 1px solid var(--line);
  border-radius: 6px;
  display: block;
  height: 100%;
  min-height: 0;
  object-fit: contain;
  width: 100%;
}

.media-placeholder {
  align-items: center;
  background:
    linear-gradient(135deg, rgba(36, 87, 167, 0.08), rgba(47, 133, 90, 0.08)),
    repeating-linear-gradient(45deg, #f4f6fa 0, #f4f6fa 12px, #eef1f6 12px, #eef1f6 24px);
  color: var(--muted);
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: 340px;
  text-align: center;
}

.pipeline {
  display: grid;
  gap: 0.42em;
  grid-template-columns: repeat(4, 1fr);
  margin-top: 0.75em;
}

.pipeline .card {
  min-height: 170px;
}

.tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35em;
  margin: 0.75em 0;
}

.tag-row code {
  background: #edf4ee;
  color: #1f6845;
  font-size: 0.58em;
  font-weight: 700;
}

.split-emphasis {
  border-left: 6px solid var(--blue);
  padding-left: 0.75em;
}

.muted {
  color: var(--muted);
}

.danger {
  color: var(--red);
}

.small p,
.small li {
  font-size: 0.68em;
}

.center {
  text-align: center;
}

@media (max-width: 900px) {
  .pipeline {
    grid-template-columns: repeat(2, 1fr);
  }
}
"""


async def export_pdf(html_path: Path, pdf_path: Path, width: int = SLIDE_WIDTH, height: int = SLIDE_HEIGHT) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise SystemExit(
            "PDF export requires Playwright. Install it with `pip install playwright` "
            "and install a browser with `playwright install chromium`."
        ) from exc

    page_width = width / CSS_PIXELS_PER_INCH
    page_height = height / CSS_PIXELS_PER_INCH
    url = html_path.resolve().as_uri() + "?print-pdf"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_function("window.Reveal && Reveal.isReady && Reveal.isReady()", timeout=30_000)
        await page.emulate_media(media="print")
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        await page.pdf(
            path=str(pdf_path),
            width=f"{page_width}in",
            height=f"{page_height}in",
            margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            print_background=True,
            prefer_css_page_size=False,
        )
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Reveal.js HTML from deck Markdown.")
    parser.add_argument("input", type=Path, help="Markdown deck path")
    parser.add_argument("-o", "--output", type=Path, default=Path("index.html"), help="HTML output path")
    parser.add_argument("--pdf-output", type=Path, help="Optional PDF output path, exported at 1920x1080 slide aspect.")
    parser.add_argument("--title", default="VeriAnim Slides", help="HTML title")
    args = parser.parse_args()

    markdown_text = args.input.read_text(encoding="utf-8")
    html_text = build_html(markdown_text, args.title)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html_text, encoding="utf-8")
    print(f"Built {args.output} from {args.input}")
    if args.pdf_output:
        asyncio.run(export_pdf(args.output, args.pdf_output))
        print(f"Exported {args.pdf_output} at {SLIDE_WIDTH}x{SLIDE_HEIGHT}")


if __name__ == "__main__":
    main()
