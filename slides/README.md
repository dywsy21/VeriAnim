# VeriAnim Reveal.js Slides

Build the classroom-report slides:

```bash
python slides/build_reveal.py slides/deck.md -o slides/index.html --title "VeriAnim Class Report"
```

Open `slides/index.html` in a browser. The generated deck uses Reveal.js from a CDN.

Export the slides as a 1920x1080 PDF:

```bash
pip install -e ".[slides]"
playwright install chromium
python slides/build_reveal.py slides/deck.md -o slides/index.html --pdf-output slides/deck.pdf --title "VeriAnim Class Report"
```

PDF export uses Playwright/Chromium and maps 1920x1080 CSS pixels to a 20in x 11.25in PDF page.

## Markdown Dialect

- `---` separates slides.
- `<!-- .slide: class="title-slide" -->` adds a class to the current slide.
- `:::{card}` ... `:::` creates a styled block. Existing styles include `card`, `callout`, `warning`, `metric`, `kicker`, `subtitle`, `muted`, `pipeline`, and `tag-row`.
- `[columns]`, `[column]`, `[/column]`, and `[/columns]` create responsive columns.
- `[[placeholder: GIF 1 | prompt=Prompt: TBD]]` creates an empty showcase slot.
- `???` starts Reveal speaker notes for the current slide.
