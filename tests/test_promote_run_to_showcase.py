from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from scripts.promote_run_to_showcase import promote_run, slugify


class PromoteRunToShowcaseTest(unittest.TestCase):
    def test_promotes_static_run_with_metadata_and_cover_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_run(root, animation=False)
            showcase_dir = root / "showcase"

            result = promote_run(
                run_dir=run_dir,
                name="Warm Chair Table",
                showcase_dir=showcase_dir,
                note="Clean static chair and table scene.",
                update_index=True,
            )

            dest = showcase_dir / "warm_chair_table"
            self.assertEqual(result.destination.resolve(), dest.resolve())
            self.assertTrue((dest / "source.py").exists())
            self.assertTrue((dest / "scene.blend").exists())
            self.assertTrue((dest / "scene.png").exists())
            self.assertTrue((dest / "scene.jpg").exists())
            self.assertEqual((dest / "prompt.txt").read_text(encoding="utf-8").strip(), "a chair beside a table")

            models = json.loads((dest / "models.json").read_text(encoding="utf-8"))
            self.assertEqual(models["planner"], "openai/test-planner")
            metadata = json.loads((dest / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["kind"], "static_scene")
            self.assertEqual(metadata["name"], "warm chair table")
            self.assertIn("source.py", metadata["artifacts"])
            self.assertEqual(metadata["validation_reports"][0]["file"], "initial_round_0_scene_deterministic.json")
            self.assertIn("Clean static chair and table scene.", (dest / "README.md").read_text(encoding="utf-8"))
            self.assertIn("`warm_chair_table`", (showcase_dir / "README.md").read_text(encoding="utf-8"))

    def test_promotes_animation_run_with_existing_mp4_and_optional_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_run(root, animation=True, include_mp4=True)
            showcase_dir = root / "showcase"

            promote_run(
                run_dir=run_dir,
                name="box slide",
                showcase_dir=showcase_dir,
                kind="auto",
                copy_frames=True,
                max_frames=2,
            )

            dest = showcase_dir / "box_slide"
            self.assertTrue((dest / "animation.gif").exists())
            self.assertTrue((dest / "animation.mp4").exists())
            self.assertEqual(len(list(dest.glob("frame_*.png"))), 2)
            metadata = json.loads((dest / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["kind"], "animation")
            self.assertIn("animation.gif", metadata["artifacts"])
            self.assertIn("animation.mp4", metadata["artifacts"])

    def test_promotes_animation_run_generates_mp4_from_gif(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_run(root, animation=True, include_mp4=False)
            showcase_dir = root / "showcase"

            with mock.patch("scripts.promote_run_to_showcase.shutil.which", return_value="/usr/bin/ffmpeg"), mock.patch(
                "scripts.promote_run_to_showcase.subprocess.run"
            ) as run_mock:
                def fake_run(command: list[str], **_kwargs: object) -> object:
                    Path(command[-1]).write_bytes(b"mp4")
                    return subprocess_result(0)

                run_mock.side_effect = fake_run
                result = promote_run(run_dir=run_dir, name="generated mp4", showcase_dir=showcase_dir)

            dest = showcase_dir / "generated_mp4"
            self.assertTrue((dest / "animation.mp4").exists())
            self.assertFalse(result.warnings)
            metadata = json.loads((dest / "metadata.json").read_text(encoding="utf-8"))
            self.assertIn("animation.mp4", metadata["artifacts"])
            args = run_mock.call_args.args[0]
            self.assertIn("-pix_fmt", args)
            self.assertEqual(Path(args[-1]).resolve(), (dest / "animation.mp4").resolve())

    def test_promotes_animation_run_can_disable_mp4_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = make_run(root, animation=True, include_mp4=False)
            showcase_dir = root / "showcase"

            result = promote_run(
                run_dir=run_dir,
                name="gif only",
                showcase_dir=showcase_dir,
                generate_mp4=False,
            )

            dest = showcase_dir / "gif_only"
            self.assertFalse((dest / "animation.mp4").exists())
            self.assertTrue(any("disabled" in warning for warning in result.warnings))

    def test_slugify_normalizes_names(self) -> None:
        self.assertEqual(slugify(" Green Cube: Table Slide! "), "green_cube_table_slide")


def make_run(root: Path, *, animation: bool, include_mp4: bool = False) -> Path:
    run_dir = root / ("run_20260607_010101" if not animation else "run_20260607_020202")
    (run_dir / "code").mkdir(parents=True)
    (run_dir / "reports").mkdir()
    (run_dir / "screenshots" / "initial_round_0").mkdir(parents=True)
    (run_dir / "animation" / "final").mkdir(parents=True)
    (root / "showcase").mkdir()
    (root / "showcase" / "README.md").write_text(
        "# VeriAnim Showcase\n\n## Animation\n\n## Static Scenes\n\n## Re-exporting Blend Files\n",
        encoding="utf-8",
    )

    prompt = "a box slides on a table" if animation else "a chair beside a table"
    ir = {"prompt": {"text": prompt}, "scene": {"objects": []}}
    if animation:
        ir["animation"] = {"duration_frames": 60}
    (run_dir / "ir.json").write_text(json.dumps(ir), encoding="utf-8")
    (run_dir / "agent_models.json").write_text(
        json.dumps({"planner": {"model": "openai/test-planner"}, "coder": {"model": "openai/test-coder"}}),
        encoding="utf-8",
    )
    (run_dir / "code" / "final_scene.py").write_text("print('scene')\n", encoding="utf-8")
    (run_dir / "scene.blend").write_bytes(b"blend")
    write_png(run_dir / "screenshots" / "initial_round_0" / "three_quarter.png")
    write_png(run_dir / "screenshots" / "initial_round_0" / "top_closeup.png")
    (run_dir / "reports" / "initial_round_0_scene_deterministic.json").write_text(
        json.dumps({"mode": "deterministic", "passed": True, "summary": "Scene deterministic validation passed.", "issues": []}),
        encoding="utf-8",
    )
    if animation:
        (run_dir / "animation" / "final" / "animation.gif").write_bytes(b"GIF89a")
        if include_mp4:
            (run_dir / "animation" / "final" / "animation.mp4").write_bytes(b"mp4")
        write_png(run_dir / "animation" / "final" / "frame_0001.png")
        write_png(run_dir / "animation" / "final" / "frame_0030.png")
        write_png(run_dir / "animation" / "final" / "frame_0060.png")
        (run_dir / "reports" / "animation_stage_round_0_animation_video.json").write_text(
            json.dumps({"mode": "video", "passed": True, "summary": "Animation video verification passed.", "issues": []}),
            encoding="utf-8",
        )
    return run_dir


def write_png(path: Path) -> None:
    image = Image.new("RGB", (4, 4), color=(120, 80, 40))
    image.save(path, "PNG")


def subprocess_result(returncode: int) -> object:
    return type("Completed", (), {"returncode": returncode, "stdout": "", "stderr": ""})()


if __name__ == "__main__":
    unittest.main()
