from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.generate_showroom import generate_showroom


class GenerateShowroomTest(unittest.TestCase):
    def test_generates_mobile_static_site_with_copied_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            showcase_dir = root / "showcase"
            output_dir = root / "showroom"
            make_showcase_entry(showcase_dir / "box_slide", kind="animation", media=("animation.mp4", "scene.jpg"))
            make_showcase_entry(showcase_dir / "desk_scene", kind="static_scene", media=("scene.png",))

            result = generate_showroom(showcase_dir=showcase_dir, output_dir=output_dir, title="Demo Room", subtitle="Scan this.")

            self.assertEqual(len(result.entries), 2)
            self.assertFalse(result.warnings)
            self.assertTrue((output_dir / "assets" / "box_slide" / "animation.mp4").exists())
            self.assertTrue((output_dir / "assets" / "desk_scene" / "scene.png").exists())

            html = (output_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn('name="viewport"', html)
            self.assertIn("Demo Room", html)
            self.assertIn("Scan this.", html)
            self.assertIn('"primaryMedia": "assets/box_slide/animation.mp4"', html)
            self.assertIn('"readmePath": "../showcase/box_slide/README.md"', html)
            self.assertIn("<dialog", html)
            self.assertIn('data-filter="animation"', html)

    def test_warns_when_entry_has_no_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            showcase_dir = root / "showcase"
            output_dir = root / "showroom"
            make_showcase_entry(showcase_dir / "empty_scene", kind="static_scene", media=())

            result = generate_showroom(showcase_dir=showcase_dir, output_dir=output_dir)

            self.assertEqual(len(result.entries), 1)
            self.assertTrue(any("no media" in warning for warning in result.warnings))
            html = (output_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn('"primaryMedia": null', html)


def make_showcase_entry(path: Path, *, kind: str, media: tuple[str, ...]) -> None:
    path.mkdir(parents=True)
    (path / "README.md").write_text("# Entry\n", encoding="utf-8")
    (path / "source.py").write_text("print('demo')\n", encoding="utf-8")
    (path / "scene.blend").write_bytes(b"blend")
    (path / "prompt.txt").write_text("a tiny demo prompt\n", encoding="utf-8")
    for name in media:
        (path / name).write_bytes(b"media")
    (path / "metadata.json").write_text(
        json.dumps(
            {
                "name": path.name.replace("_", " "),
                "kind": kind,
                "prompt": "a tiny demo prompt",
                "selected_note": "Works well on phones.",
                "source_run": "runs/run_demo",
                "models": {"planner": "openai/test"},
                "validation_reports": [
                    {"file": "one.json", "passed": True, "summary": "Passed."},
                    {"file": "two.json", "passed": False, "summary": "Failed."},
                ],
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
