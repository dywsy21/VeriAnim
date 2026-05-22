from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from harness.ir import GenerationIR
from harness.blender_runtime import _relation_frame_overrides, _view_dicts
from harness.serde import from_dict


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "examples" / "animation_ir"


def load_ir(path: Path) -> GenerationIR:
    return from_dict(GenerationIR, json.loads(path.read_text(encoding="utf-8")))


class AnimationIRValidationTest(unittest.TestCase):
    def test_animation_examples_are_structurally_valid(self) -> None:
        example_paths = sorted(EXAMPLE_DIR.glob("*.json"))
        self.assertGreaterEqual(len(example_paths), 3)

        for path in example_paths:
            with self.subTest(path=path.name):
                report = load_ir(path).validate()
                self.assertTrue(report.passed, report.to_dict() if hasattr(report, "to_dict") else report)

    def test_translate_event_requires_middle_state_and_video_questions(self) -> None:
        data = json.loads((EXAMPLE_DIR / "translate_ball_to_box.json").read_text(encoding="utf-8"))
        broken = copy.deepcopy(data)
        event = broken["animation"]["events"][0]
        event.pop("path")
        broken["animation"]["verifier"]["sampled_frames"] = [1, 120]
        broken["animation"]["verifier"]["questions"] = []

        report = from_dict(GenerationIR, broken).validate()
        codes = {issue.code for issue in report.issues}

        self.assertFalse(report.passed)
        self.assertIn("MISSING_INTERMEDIATE_KEYFRAME", codes)
        self.assertIn("MISSING_ANIMATION_SAMPLE_FRAMES", codes)
        self.assertIn("MISSING_VIDEO_VERIFIER_QUESTIONS", codes)

    def test_camera_orbit_requires_camera_subject_and_target(self) -> None:
        data = json.loads((EXAMPLE_DIR / "camera_orbit_showcase.json").read_text(encoding="utf-8"))
        broken = copy.deepcopy(data)
        event = broken["animation"]["camera_events"][0]
        event["subject_ids"] = ["missing_camera"]
        event["target_ids"] = []

        report = from_dict(GenerationIR, broken).validate()
        codes = {issue.code for issue in report.issues}

        self.assertFalse(report.passed)
        self.assertIn("UNKNOWN_CAMERA_ANIMATION_SUBJECT", codes)
        self.assertIn("MISSING_CAMERA_ORBIT_TARGET", codes)

    def test_texture_policy_conflict_is_invalid(self) -> None:
        data = json.loads((EXAMPLE_DIR / "translate_ball_to_box.json").read_text(encoding="utf-8"))
        material = data["scene"]["materials"][0]
        material["texture_policy"] = "solid_only"
        material["needs_texture"] = True
        material["texture_query"] = "wood grain"

        report = from_dict(GenerationIR, data).validate()
        codes = {issue.code for issue in report.issues}

        self.assertFalse(report.passed)
        self.assertIn("TEXTURE_POLICY_CONFLICT", codes)

    def test_relation_verification_method_mismatch_is_invalid(self) -> None:
        data = json.loads((EXAMPLE_DIR / "translate_ball_to_box.json").read_text(encoding="utf-8"))
        relation = data["scene"]["relations"][0]
        relation["relation_type"] = "left_of"
        relation["verification_method"] = "bbox_contact"

        report = from_dict(GenerationIR, data).validate()
        codes = {issue.code for issue in report.issues}

        self.assertFalse(report.passed)
        self.assertIn("RELATION_METHOD_MISMATCH", codes)

    def test_animation_final_relation_uses_end_frame_for_verification_views(self) -> None:
        ir = load_ir(EXAMPLE_DIR / "translate_ball_to_box.json")

        self.assertEqual(_relation_frame_overrides(ir), {"ball_final_near_box": 120})

        views = {view["id"]: view for view in _view_dicts(ir)}
        self.assertEqual(views["three_quarter"]["frame"], 120)
        self.assertEqual(views["top_path"]["frame"], 120)


if __name__ == "__main__":
    unittest.main()
