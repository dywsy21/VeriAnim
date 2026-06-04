from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image

from harness.config import AgentModelConfig, HarnessConfig
from harness.media_diagnostics import run_media_capability_diagnostics, write_probe_assets


def media_config(runs_dir: Path) -> HarnessConfig:
    model = AgentModelConfig(name="test", model="test/model", api_key="test-key")
    vision = AgentModelConfig(name="vision", model="test/vision", api_key="test-key", supports_images=True)
    video = AgentModelConfig(name="video", model="test/video", api_key="test-key", supports_images=True)
    return HarnessConfig(
        planner=model,
        coder=model,
        refiner=model,
        vision=vision,
        video=video,
        max_refinement_rounds=0,
        max_visual_refinement_rounds=0,
        max_video_refinement_rounds=0,
        max_stagnant_refinement_rounds=1,
        planner_max_retries=0,
        rag_docs=(),
        runs_dir=runs_dir,
        blender_host="localhost",
        blender_port=8888,
        headless_rendering=False,
        render_width=64,
        render_height=64,
        render_gif_each_round=False,
        texture_search_enabled=False,
        texture_search_candidate_limit=1,
        texture_search_timeout_seconds=3,
        tui_initial_animation=False,
        tui_skip_vision=True,
        tui_skip_video=True,
    )


class MediaDiagnosticsTest(unittest.TestCase):
    def test_write_probe_assets_creates_image_and_temporal_gif(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets = write_probe_assets(Path(tmp))

            image_path = Path(assets["image_path"])
            video_path = Path(assets["video_path"])
            sampled_frame_paths = [Path(path) for path in assets["sampled_frame_paths"]]

            self.assertTrue(image_path.exists())
            self.assertTrue(video_path.exists())
            self.assertEqual(len(sampled_frame_paths), 2)
            self.assertTrue(all(path.exists() for path in sampled_frame_paths))
            with Image.open(video_path) as image:
                self.assertGreaterEqual(getattr(image, "n_frames", 1), 2)

    def test_diagnostics_report_supported_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            assets = write_probe_assets(tmp_path)
            with mock.patch(
                "harness.agents.LLMClient.json_multimodal",
                return_value={"passed": True, "summary": "image readable", "issues": []},
            ) as json_multimodal, mock.patch(
                "harness.agents.LLMClient.json_video",
                side_effect=[
                    {"can_see_video": True, "attachment_readable": True, "summary": "gif readable"},
                    {"passed": True, "summary": "video readable", "issues": []},
                ],
            ) as json_video:
                report = run_media_capability_diagnostics(
                    media_config(tmp_path),
                    image_path=Path(assets["image_path"]),
                    video_path=Path(assets["video_path"]),
                    sampled_frame_paths=[Path(path) for path in assets["sampled_frame_paths"]],
                )

        self.assertEqual(report["vision"]["status"], "supported")
        self.assertEqual(report["video"]["status"], "supported")
        self.assertEqual(report["vision"]["report"]["mode"], "vision")
        self.assertEqual(report["video"]["report"]["mode"], "video")
        self.assertIn("image_url", report["vision"]["expected_media_parts"])
        self.assertIn("video_url", report["video"]["expected_media_parts"])
        self.assertEqual(json_multimodal.call_count, 1)
        self.assertEqual(json_video.call_count, 2)

    def test_diagnostics_report_unsupported_media(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            assets = write_probe_assets(tmp_path)
            with mock.patch(
                "harness.agents.LLMClient.json_multimodal",
                side_effect=RuntimeError("unknown variant `image_url`, expected `text`"),
            ), mock.patch(
                "harness.agents.LLMClient.json_video",
                side_effect=RuntimeError("unknown variant `video_url`, expected `text`"),
            ):
                report = run_media_capability_diagnostics(
                    media_config(tmp_path),
                    image_path=Path(assets["image_path"]),
                    video_path=Path(assets["video_path"]),
                    sampled_frame_paths=[Path(path) for path in assets["sampled_frame_paths"]],
                )

        self.assertEqual(report["vision"]["status"], "unsupported")
        self.assertEqual(report["video"]["status"], "unsupported")
        self.assertEqual(report["vision"]["report"]["issues"][0]["code"], "VISION_INPUT_UNSUPPORTED")
        self.assertEqual(report["video"]["report"]["issues"][0]["code"], "VIDEO_INPUT_UNSUPPORTED")
        self.assertIn("Configure VERIANIM_VISION_MODEL", report["vision"]["report"]["issues"][0]["suggested_fix"])
        self.assertIn("Configure VERIANIM_VIDEO_MODEL", report["video"]["report"]["issues"][0]["suggested_fix"])


if __name__ == "__main__":
    unittest.main()
