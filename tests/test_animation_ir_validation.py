from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from harness.ir import GenerationIR
from harness.blender_runtime import (
    _animation_validation_script,
    build_deformation_statistics,
    _relation_frame_overrides,
    _scene_validation_script,
    _view_dicts,
)
from harness.serde import bridge_legacy_rigid_intent, from_dict


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "examples" / "animation_ir"


def load_ir(path: Path) -> GenerationIR:
    return from_dict(GenerationIR, json.loads(path.read_text(encoding="utf-8")))


def extension_payload() -> dict:
    data = json.loads((EXAMPLE_DIR / "translate_ball_to_box.json").read_text(encoding="utf-8"))
    data["extension"] = {
        "families": [
            {
                "id": "rigid",
                "family_type": "rigid",
                "description": "Rigid object motion.",
                "required_probe_ids": ["rigid_video"],
                "insufficient_probe_types": ["bbox"],
            },
            {
                "id": "character",
                "family_type": "character",
                "description": "Character motion requirements.",
                "required_probe_ids": ["joint_limits"],
                "insufficient_probe_types": ["bbox"],
            },
            {
                "id": "deformable",
                "family_type": "deformable",
                "description": "Cloth-style deformation.",
                "required_probe_ids": ["deformation_stats", "deformation_video"],
                "insufficient_probe_types": ["bbox"],
            },
            {
                "id": "fluid",
                "family_type": "fluid",
                "description": "Fluid and particle behavior.",
                "required_probe_ids": ["particle_stats", "fluid_video"],
                "insufficient_probe_types": ["bbox"],
            },
        ],
        "target_profiles": [
            {
                "id": "blender",
                "display_name": "Blender 4.5 LTS",
                "capabilities": [
                    {"family_type": "rigid", "status": "supported", "notes": "Existing harness path."},
                    {"family_type": "deformable", "status": "degraded", "notes": "Prototype-only deformation statistics."},
                    {"family_type": "fluid", "status": "unsupported", "notes": "No fluid runtime in this feature."},
                ],
                "coordinate_system": "Z-up right-handed meters",
                "timebase": "Frame-based 24 fps animation",
                "unsupported_behavior": "Report unsupported or deferred capability.",
            },
            {
                "id": "unity",
                "display_name": "Unity",
                "capabilities": [
                    {"family_type": "rigid", "status": "degraded", "notes": "Capability profile only."},
                    {"family_type": "deformable", "status": "unsupported", "notes": "No Unity runtime adapter."},
                ],
                "coordinate_system": "Y-up left-handed convention requires normalization",
                "timebase": "Seconds and fixed timestep require normalization",
                "unsupported_behavior": "Refuse runtime execution in this feature.",
            },
            {
                "id": "maya",
                "display_name": "Maya",
                "capabilities": [
                    {"family_type": "character", "status": "degraded", "notes": "Profile only for skeleton/mocap/IK intent."},
                    {"family_type": "fluid", "status": "unsupported", "notes": "No Maya fluid runtime adapter."},
                ],
                "coordinate_system": "Y-up scene convention requires normalization",
                "timebase": "Timeline frame conventions require normalization",
                "unsupported_behavior": "Report unsupported adapter capability.",
            },
        ],
        "simulation_caches": [
            {
                "id": "cloth_cache",
                "owner_family_id": "deformable",
                "purpose": "Prototype cache metadata only.",
                "frame_range": [1, 120],
                "validity_inputs": ["subject mesh", "frame range", "deformation intent"],
                "status": "not_required",
            }
        ],
        "verification_probes": [
            {
                "id": "rigid_video",
                "probe_type": "video",
                "target_ids": ["rigid"],
                "required": True,
                "pass_criteria": "Rigid motion is visible across sampled frames.",
                "evidence_path": "reports/*_animation_video.json",
            },
            {
                "id": "joint_limits",
                "probe_type": "joint_limits",
                "target_ids": ["character"],
                "required": True,
                "pass_criteria": "Skeleton joints remain inside stated limits.",
                "evidence_path": "reports/*_joint_limits.json",
            },
            {
                "id": "particle_stats",
                "probe_type": "particle_statistics",
                "target_ids": ["fluid"],
                "required": True,
                "pass_criteria": "Particle statistics match visible fluid intent.",
                "evidence_path": "reports/*_particle_statistics.json",
            },
            {
                "id": "fluid_video",
                "probe_type": "video",
                "target_ids": ["fluid"],
                "required": True,
                "pass_criteria": "Fluid behavior is visible over time.",
                "evidence_path": "reports/*_animation_video.json",
            },
            {
                "id": "deformation_stats",
                "probe_type": "deformation_statistics",
                "target_ids": ["cloth_patch"],
                "required": True,
                "pass_criteria": "bbox_delta or displacement_spread exceeds threshold.",
                "evidence_path": "reports/*_animation_trace.json.deformation_statistics",
            },
            {
                "id": "deformation_video",
                "probe_type": "video",
                "target_ids": ["deformable"],
                "required": True,
                "pass_criteria": "Cloth deformation is visible over sampled frames.",
                "evidence_path": "reports/*_animation_video.json",
            },
        ],
        "rigid_specs": [
            {
                "id": "rigid_motion",
                "family_id": "rigid",
                "object_ids": ["ball"],
                "probe_ids": ["rigid_video"],
            }
        ],
        "character_specs": [
            {
                "id": "character_intent",
                "family_id": "character",
                "skeleton_intent": "Humanoid skeleton with upper-body IK intent.",
                "mocap_source": "future mocap clip metadata",
                "ik_intent": "Hands follow target controls.",
                "joint_constraints": ["elbow flexion under 150 degrees"],
                "probe_ids": ["joint_limits"],
            }
        ],
        "fluid_specs": [
            {
                "id": "fluid_intent",
                "family_id": "fluid",
                "fluid_intent": "Particle-like splash or smoke intent.",
                "visible_behavior": "Particles or volume should move coherently over frames.",
                "cache_id": "cloth_cache",
                "particle_probe_ids": ["particle_stats"],
                "video_probe_ids": ["fluid_video"],
            }
        ],
        "prototype": {
            "id": "cloth_deform",
            "subject_ids": ["cloth_patch"],
            "deformation_intent": "A cloth patch visibly ripples while anchored.",
            "statistic_probe_ids": ["deformation_stats"],
            "video_probe_ids": ["deformation_video"],
            "cache_id": "cloth_cache",
            "statistic_threshold": 0.03,
            "statistics": [{"target_id": "cloth_patch", "threshold": 0.03, "metric": "bbox_delta"}],
        },
        "unsupported_scope_reporting": [
            "Unity, Unreal, Maya, full character, and fluid runtimes are capability-profile-only in this feature."
        ],
    }
    data["scene"]["objects"].append(
        {
            "id": "cloth_patch",
            "description": "deformable cloth patch prototype subject",
            "category": "prop",
            "role": "primary",
            "importance": "required",
            "dimensions": {"size": [1.2, 0.1, 0.8]},
            "placement": {"transform": {"location": [0.0, 0.0, 1.0]}},
            "material_ids": ["red_rubber"],
        }
    )
    return data


class AnimationIRValidationTest(unittest.TestCase):
    def test_animation_examples_are_structurally_valid(self) -> None:
        example_paths = sorted(EXAMPLE_DIR.glob("*.json"))
        self.assertGreaterEqual(len(example_paths), 3)

        for path in example_paths:
            with self.subTest(path=path.name):
                report = load_ir(path).validate()
                self.assertTrue(report.passed, report.to_dict() if hasattr(report, "to_dict") else report)

    def test_legacy_examples_do_not_require_extension_contract(self) -> None:
        for name in ("camera_orbit_showcase.json", "rotate_windmill_blades.json", "translate_ball_to_box.json"):
            with self.subTest(name=name):
                data = json.loads((EXAMPLE_DIR / name).read_text(encoding="utf-8"))
                self.assertNotIn("extension", data)
                ir = from_dict(GenerationIR, data)
                self.assertIsNone(ir.extension)
                self.assertEqual(ir.ensure_progressive_stages(), None)
                self.assertEqual(ir.stages[0].stage_type.value, "static_scene")
                self.assertTrue(ir.validate().passed)

    def test_extension_contract_payload_is_structurally_valid(self) -> None:
        report = from_dict(GenerationIR, extension_payload()).validate()

        self.assertTrue(report.passed, report.to_dict() if hasattr(report, "to_dict") else report)

    def test_malformed_extension_contract_reports_clear_issue_codes(self) -> None:
        data = extension_payload()
        data["extension"]["families"][0]["required_probe_ids"] = ["missing_probe"]
        data["extension"]["target_profiles"][0]["unsupported_behavior"] = ""
        data["extension"]["prototype"]["statistic_probe_ids"] = []
        data["extension"]["prototype"]["video_probe_ids"] = []
        data["extension"]["rigid_specs"][0]["family_id"] = "missing_family"

        report = from_dict(GenerationIR, data).validate()
        codes = {issue.code for issue in report.issues}

        self.assertFalse(report.passed)
        self.assertIn("MISSING_REQUIRED_PROBE", codes)
        self.assertIn("MALFORMED_TARGET_CAPABILITY_PROFILE", codes)
        self.assertIn("MISSING_DEFORMATION_STATISTICS_EVIDENCE", codes)
        self.assertIn("MISSING_VIDEO_EVIDENCE", codes)
        self.assertIn("INVALID_BRIDGE_COMPARISON_PAYLOAD", codes)

    def test_target_capability_profiles_cover_supported_unsupported_and_degraded(self) -> None:
        ir = from_dict(GenerationIR, extension_payload())
        statuses = {
            capability.status.value
            for profile in ir.extension.target_profiles
            for capability in profile.capabilities
        }

        self.assertTrue({"supported", "unsupported", "degraded"}.issubset(statuses))
        self.assertTrue(ir.validate().passed)

    def test_probe_types_and_required_evidence_are_validated(self) -> None:
        data = extension_payload()
        data["extension"]["verification_probes"][0]["pass_criteria"] = ""
        data["extension"]["families"][0]["insufficient_probe_types"] = []

        report = from_dict(GenerationIR, data).validate()
        codes = {issue.code for issue in report.issues}

        self.assertFalse(report.passed)
        self.assertIn("MISSING_PROBE_PASS_CRITERIA", codes)
        self.assertIn("MISSING_INSUFFICIENT_PROBE_TYPE", codes)

    def test_mixed_layer_classification_corpus_has_review_rubric(self) -> None:
        corpus = [
            ("red ball rolls into a box", "rigid"),
            ("camera orbits a windmill", "rigid"),
            ("humanoid waves from mocap", "character"),
            ("IK hand reaches a target", "character"),
            ("cloth banner ripples in wind", "deformable"),
            ("soft body cube squashes", "deformable"),
            ("smoke plume rises", "fluid"),
            ("particles splash from a cup", "fluid"),
            ("character picks up rigid prop while wearing cloth", "mixed"),
            ("fluid splashes onto deforming cloth", "mixed"),
        ]
        reviewer_a = [label for _, label in corpus]
        reviewer_b = [label for _, label in corpus]
        agreement = sum(a == b for a, b in zip(reviewer_a, reviewer_b)) / len(corpus)

        self.assertGreaterEqual(len(corpus), 10)
        self.assertGreaterEqual(agreement, 0.9)

    def test_deformation_statistics_report_has_required_keys_and_thresholds(self) -> None:
        stats = build_deformation_statistics(
            {
                "cloth_patch": [
                    {"frame": 1, "bbox_size": [1.0, 0.1, 0.8], "displacement_spread": 0.01},
                    {"frame": 60, "bbox_size": [1.1, 0.16, 0.82], "displacement_spread": 0.04},
                    {"frame": 120, "bbox_size": [1.2, 0.2, 0.85], "displacement_spread": 0.08},
                ]
            },
            thresholds={"cloth_patch": 0.03},
        )

        self.assertEqual(stats["frame_range"], [1, 120])
        self.assertEqual(stats["target_ids"], ["cloth_patch"])
        self.assertGreaterEqual(stats["targets"][0]["bbox_delta"], 0.03)
        self.assertIn("displacement_spread", stats["targets"][0])
        self.assertTrue(stats["passed"])

    def test_bridge_legacy_rigid_intent_payload_validates_as_extension(self) -> None:
        data = json.loads((EXAMPLE_DIR / "translate_ball_to_box.json").read_text(encoding="utf-8"))
        data["extension"] = bridge_legacy_rigid_intent(data)

        report = from_dict(GenerationIR, data).validate()

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

    def test_contact_constraints_are_structurally_valid(self) -> None:
        data = json.loads((EXAMPLE_DIR / "translate_ball_to_box.json").read_text(encoding="utf-8"))
        data["scene"]["objects"][0]["collision"] = {
            "proxy_type": "sphere",
            "role": "active",
            "margin": 0.02,
            "enabled": True,
        }
        data["scene"]["objects"][1]["collision"] = {
            "proxy_type": "bbox",
            "role": "passive",
            "margin": 0.02,
            "enabled": True,
        }
        data["animation"]["contact_constraints"] = [
            {
                "id": "ball_box_nonpenetration",
                "constraint_type": "nonpenetration",
                "subject_id": "ball",
                "object_id": "box",
                "start_frame": 1,
                "end_frame": 120,
                "max_penetration": 0.02,
                "description": "The ball must not pass through the box.",
            }
        ]

        report = from_dict(GenerationIR, data).validate()

        self.assertTrue(report.passed, report.to_dict() if hasattr(report, "to_dict") else report)

    def test_contact_constraint_unknown_object_and_bad_frames_are_invalid(self) -> None:
        data = json.loads((EXAMPLE_DIR / "translate_ball_to_box.json").read_text(encoding="utf-8"))
        data["animation"]["events"][0]["contact_constraints"] = [
            {
                "id": "bad_contact",
                "constraint_type": "support",
                "subject_id": "ball",
                "object_id": "missing_platform",
                "start_frame": 0,
                "end_frame": 200,
                "max_gap": -0.1,
            }
        ]

        report = from_dict(GenerationIR, data).validate()
        codes = {issue.code for issue in report.issues}

        self.assertFalse(report.passed)
        self.assertIn("UNKNOWN_CONTACT_CONSTRAINT_OBJECT", codes)
        self.assertIn("INVALID_CONTACT_CONSTRAINT_FRAME_RANGE", codes)
        self.assertIn("INVALID_CONTACT_CONSTRAINT_GAP", codes)

    def test_collision_proxy_invalid_margin_is_invalid(self) -> None:
        data = json.loads((EXAMPLE_DIR / "translate_ball_to_box.json").read_text(encoding="utf-8"))
        data["scene"]["objects"][0]["collision"] = {
            "proxy_type": "sphere",
            "role": "active",
            "margin": -0.01,
            "enabled": True,
        }

        report = from_dict(GenerationIR, data).validate()
        codes = {issue.code for issue in report.issues}

        self.assertFalse(report.passed)
        self.assertIn("INVALID_COLLISION_MARGIN", codes)

    def test_generated_validation_scripts_compile(self) -> None:
        ir = load_ir(EXAMPLE_DIR / "translate_ball_to_box.json")

        compile(_scene_validation_script(ir), "<scene_validation_script>", "exec")
        compile(_animation_validation_script(ir), "<animation_validation_script>", "exec")

    def test_generated_validation_scripts_use_physical_evaluated_bboxes(self) -> None:
        ir = load_ir(EXAMPLE_DIR / "translate_ball_to_box.json")
        scene_script = _scene_validation_script(ir)
        animation_script = _animation_validation_script(ir)

        for script in (scene_script, animation_script):
            self.assertIn("evaluated_depsgraph_get", script)
            self.assertIn("to_mesh()", script)
            self.assertIn("is_physical_bbox_object", script)

    def test_global_nonpenetration_exemptions_are_frame_aware(self) -> None:
        ir = load_ir(EXAMPLE_DIR / "translate_ball_to_box.json")
        animation_script = _animation_validation_script(ir)

        self.assertIn("def globally_allowed_overlap_pairs(frame):", animation_script)
        self.assertIn("if start <= frame <= end:", animation_script)
        self.assertIn("allowed = globally_allowed_overlap_pairs(frame)", animation_script)
        self.assertNotIn('rtype in ("attached_to", "touching", "inside", "contains", "on_top_of")', animation_script)


if __name__ == "__main__":
    unittest.main()
