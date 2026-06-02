from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from harness.agents import (
    MaterialAgent,
    _is_multimodal_input_unsupported,
    _report_from_model,
    _sanitize_generated_blender_code,
    _sanitize_planner_data,
)
from harness.config import AgentModelConfig, HarnessConfig
from harness.ir import (
    CameraSpec,
    GenerationIR,
    MaterialSpec,
    ObjectSpec,
    SceneSpec,
    SourcePrompt,
    TexturePolicy,
    VerificationMode,
)
from harness.llm import LLMError, extract_json_object
from harness.serde import (
    EXTENSION_IR,
    INVALID_IR,
    LEGACY_RIGID_IR,
    IRDecodeError,
    bridge_legacy_rigid_intent,
    detect_ir_format,
    from_dict,
)
from harness.textures import TextureCandidate


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "animation_ir" / "translate_ball_to_box.json"


def minimal_material_config(runs_dir: Path, *, supports_images: bool = True) -> HarnessConfig:
    model = AgentModelConfig(name="test", model="test/model", api_key="test-key")
    vision = AgentModelConfig(name="vision", model="test/vision", api_key="test-key", supports_images=supports_images)
    return HarnessConfig(
        planner=model,
        coder=model,
        refiner=model,
        vision=vision,
        video=model,
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
        texture_search_enabled=True,
        texture_search_candidate_limit=2,
        texture_search_timeout_seconds=3,
        tui_initial_animation=False,
        tui_skip_vision=True,
        tui_skip_video=True,
    )


def material_ir() -> GenerationIR:
    return GenerationIR(
        prompt=SourcePrompt(text="wood cube"),
        scene=SceneSpec(
            objects=[ObjectSpec(id="cube", description="cube", material_ids=["wood_material"])],
            materials=[
                MaterialSpec(
                    id="wood_material",
                    description="visible wood grain material",
                    texture_policy=TexturePolicy.REQUIRED,
                    needs_texture=True,
                    texture_query="wood grain",
                )
            ],
            cameras=[CameraSpec(id="camera_main", target_object_ids=["cube"])],
        ),
    )


class SerdeStrictDecodeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def test_from_dict_decodes_valid_ir_types(self) -> None:
        ir = from_dict(GenerationIR, self.data)

        self.assertIsInstance(ir, GenerationIR)
        self.assertEqual(ir.scene.objects[0].id, "ball")
        self.assertEqual(ir.scene.objects[0].placement.transform.location, (-2.0, 0.0, 0.225))

    def test_from_dict_rejects_unknown_fields_by_default(self) -> None:
        broken = copy.deepcopy(self.data)
        broken["scene"]["objects"][0]["unexpected"] = "ignored-before"

        with self.assertRaisesRegex(IRDecodeError, r"Unknown field.*GenerationIR\.scene\.objects\[0\].*unexpected"):
            from_dict(GenerationIR, broken)

    def test_from_dict_can_decode_leniently_for_legacy_payloads(self) -> None:
        payload = copy.deepcopy(self.data)
        payload["scene"]["objects"][0]["unexpected"] = "legacy"

        ir = from_dict(GenerationIR, payload, strict=False)

        self.assertIsInstance(ir, GenerationIR)

    def test_from_dict_rejects_bad_container_type(self) -> None:
        broken = copy.deepcopy(self.data)
        broken["scene"]["objects"] = "not-a-list"

        with self.assertRaisesRegex(IRDecodeError, r"Expected list at GenerationIR\.scene\.objects"):
            from_dict(GenerationIR, broken)

    def test_from_dict_rejects_invalid_enum_with_path(self) -> None:
        broken = copy.deepcopy(self.data)
        broken["scene"]["relations"][0]["relation_type"] = "beside-ish"

        with self.assertRaisesRegex(IRDecodeError, r"RelationType at GenerationIR\.scene\.relations\[0\]\.relation_type"):
            from_dict(GenerationIR, broken)

    def test_detect_ir_format_classifies_legacy_extension_and_invalid_payloads(self) -> None:
        extension_payload = copy.deepcopy(self.data)
        extension_payload["extension"] = {
            "families": [],
            "target_profiles": [],
            "simulation_caches": [],
            "verification_probes": [],
        }

        self.assertEqual(detect_ir_format(self.data), LEGACY_RIGID_IR)
        self.assertEqual(detect_ir_format(extension_payload), EXTENSION_IR)
        self.assertEqual(detect_ir_format({"scene": {}}), INVALID_IR)
        self.assertEqual(detect_ir_format({"prompt": {}, "scene": {}, "extension": []}), INVALID_IR)

    def test_bridge_legacy_rigid_intent_creates_rigid_extension_view(self) -> None:
        bridged = bridge_legacy_rigid_intent(self.data)

        self.assertEqual(bridged["families"][0]["family_type"], "rigid")
        self.assertEqual(bridged["rigid_specs"][0]["family_id"], "rigid")
        self.assertIn("ball", bridged["rigid_specs"][0]["object_ids"])

    def test_bridge_rejects_non_legacy_payload(self) -> None:
        extension_payload = copy.deepcopy(self.data)
        extension_payload["extension"] = {"families": []}

        with self.assertRaisesRegex(IRDecodeError, "Invalid bridge/comparison payload"):
            bridge_legacy_rigid_intent(extension_payload)

    def test_from_dict_decodes_extension_contract(self) -> None:
        payload = copy.deepcopy(self.data)
        payload["extension"] = bridge_legacy_rigid_intent(self.data)

        ir = from_dict(GenerationIR, payload)

        self.assertIsNotNone(ir.extension)
        self.assertEqual(ir.extension.families[0].family_type.value, "rigid")
        self.assertEqual(ir.extension.rigid_specs[0].object_ids[0], "ball")

    def test_planner_sanitizer_normalizes_relation_visual_priority_alias(self) -> None:
        payload = copy.deepcopy(self.data)
        payload["scene"]["relations"][0]["visual_priority"] = "normal"

        _sanitize_planner_data(payload)
        ir = from_dict(GenerationIR, payload)

        self.assertEqual(ir.scene.relations[0].visual_priority.value, "preferred")

    def test_planner_sanitizer_normalizes_stage_visual_mode_alias(self) -> None:
        payload = copy.deepcopy(self.data)
        payload["stages"] = [
            {
                "id": "static_scene",
                "stage_type": "static_scene",
                "description": "Generate and verify static scene.",
                "verifier_modes": ["deterministic", "visual"],
            }
        ]

        _sanitize_planner_data(payload)
        ir = from_dict(GenerationIR, payload)

        self.assertEqual(ir.stages[0].verifier_modes, [VerificationMode.DETERMINISTIC, VerificationMode.VISION])

    def test_planner_sanitizer_converts_ramp_support_to_visual_attachment(self) -> None:
        payload = copy.deepcopy(self.data)
        payload["scene"]["objects"].extend(
            [
                {
                    "id": "ramp",
                    "label": "slanted ramp",
                    "category": "prop",
                    "role": "support",
                    "description": "Inclined ramp.",
                    "placement": {
                        "transform": {"location": [0, 0, 0.3]},
                        "anchor": "center",
                    },
                },
                {
                    "id": "leg",
                    "label": "ramp support leg",
                    "category": "prop",
                    "role": "support",
                    "description": "Bracket holding the incline.",
                    "placement": {
                        "transform": {"location": [0, 0, 0.15]},
                        "anchor": "center",
                    },
                },
            ]
        )
        payload["scene"]["relations"] = [
            {
                "id": "ramp_on_support_leg",
                "relation_type": "on_top_of",
                "subject_id": "ramp",
                "object_id": "leg",
                "description": "slanted ramp on its support bracket",
                "required": True,
                "verification_method": "bbox_contact",
            }
        ]

        _sanitize_planner_data(payload)
        ir = from_dict(GenerationIR, payload)

        self.assertEqual(ir.scene.relations[0].relation_type.value, "attached_to")
        self.assertEqual(ir.scene.relations[0].verification_method.value, "visual_only")

    def test_planner_sanitizer_converts_slanted_ramp_surface_to_visual_touching(self) -> None:
        payload = copy.deepcopy(self.data)
        payload["scene"]["objects"].extend(
            [
                {
                    "id": "cube",
                    "label": "sliding cube",
                    "category": "prop",
                    "role": "primary",
                    "description": "Cube sliding down the ramp.",
                    "placement": {
                        "transform": {"location": [0, 0, 0.8]},
                        "anchor": "center",
                    },
                },
                {
                    "id": "ramp",
                    "label": "inclined ramp",
                    "category": "prop",
                    "role": "support",
                    "description": "Slanted ramp surface.",
                    "placement": {
                        "transform": {"location": [0, 0, 0.3]},
                        "anchor": "center",
                    },
                },
            ]
        )
        payload["scene"]["relations"] = [
            {
                "id": "cube_on_slanted_ramp",
                "relation_type": "on_top_of",
                "subject_id": "cube",
                "object_id": "ramp",
                "description": "cube on the slanted ramp surface",
                "required": True,
                "verification_method": "bbox_contact",
            }
        ]

        _sanitize_planner_data(payload)
        ir = from_dict(GenerationIR, payload)

        self.assertEqual(ir.scene.relations[0].relation_type.value, "touching")
        self.assertEqual(ir.scene.relations[0].verification_method.value, "visual_only")

    def test_planner_sanitizer_scopes_no_image_texture_to_named_statue(self) -> None:
        payload = copy.deepcopy(self.data)
        payload["prompt"]["text"] = "a stone pedestal with a bronze statue, no image textures on the statue"
        payload["scene"]["materials"] = [
            {
                "id": "stone_material",
                "description": "rough gray stone pedestal material",
                "texture_policy": "required",
                "needs_texture": True,
                "texture_query": "gray stone surface",
            },
            {
                "id": "bronze_statue_material",
                "description": "bronze statue material",
                "texture_policy": "required",
                "needs_texture": True,
                "texture_query": "bronze patina",
            },
        ]

        _sanitize_planner_data(payload)

        materials = {material["id"]: material for material in payload["scene"]["materials"]}
        self.assertEqual(materials["stone_material"]["texture_policy"], "required")
        self.assertTrue(materials["stone_material"]["needs_texture"])
        self.assertEqual(materials["bronze_statue_material"]["texture_policy"], "solid_only")
        self.assertFalse(materials["bronze_statue_material"]["needs_texture"])

    def test_planner_sanitizer_scopes_solid_color_to_plastic_ball_not_grass(self) -> None:
        payload = copy.deepcopy(self.data)
        payload["prompt"]["text"] = "a green grass patch with a simple solid-color plastic ball"
        payload["scene"]["materials"] = [
            {
                "id": "grass_material",
                "description": "green grass patch material",
                "texture_policy": "required",
                "needs_texture": True,
                "texture_query": "green grass ground",
            },
            {
                "id": "plastic_ball_material",
                "description": "simple solid-color plastic ball material",
                "texture_policy": "required",
                "needs_texture": True,
                "texture_query": "plastic ball",
            },
        ]

        _sanitize_planner_data(payload)

        materials = {material["id"]: material for material in payload["scene"]["materials"]}
        self.assertEqual(materials["grass_material"]["texture_policy"], "required")
        self.assertTrue(materials["grass_material"]["needs_texture"])
        self.assertEqual(materials["plastic_ball_material"]["texture_policy"], "solid_only")
        self.assertFalse(materials["plastic_ball_material"]["needs_texture"])

    def test_planner_sanitizer_adds_common_missing_floor_support(self) -> None:
        payload = copy.deepcopy(self.data)
        payload["scene"]["objects"] = [
            {
                "id": "pedestal",
                "description": "stone pedestal",
                "category": "prop",
                "role": "primary",
            }
        ]
        payload["scene"]["relations"] = [
            {
                "id": "pedestal_on_floor",
                "relation_type": "on_top_of",
                "subject_id": "pedestal",
                "object_id": "floor",
                "verification_method": "bbox_contact",
            }
        ]
        payload["scene"]["cameras"] = [{"id": "camera_main", "target_object_ids": ["pedestal", "floor"]}]
        payload["animation"] = None

        _sanitize_planner_data(payload)
        ir = from_dict(GenerationIR, payload)
        report = ir.validate()

        object_ids = {obj.id for obj in ir.scene.objects}
        floor = next(obj for obj in ir.scene.objects if obj.id == "floor")
        self.assertIn("floor", object_ids)
        self.assertEqual(floor.role.value, "support")
        self.assertTrue(floor.collision.enabled)
        self.assertTrue(report.passed, [issue.code for issue in report.issues])

    def test_planner_sanitizer_does_not_invent_arbitrary_missing_objects(self) -> None:
        payload = copy.deepcopy(self.data)
        payload["scene"]["relations"] = [
            {
                "id": "box_on_missing_platform",
                "relation_type": "on_top_of",
                "subject_id": "box",
                "object_id": "missing_platform",
            }
        ]

        _sanitize_planner_data(payload)
        report = from_dict(GenerationIR, payload).validate()
        codes = {issue.code for issue in report.issues}

        self.assertFalse(report.passed)
        self.assertIn("UNKNOWN_RELATION_OBJECT", codes)

    def test_planner_sanitizer_prunes_invalid_verifier_references(self) -> None:
        payload = copy.deepcopy(self.data)
        payload["scene"]["cameras"] = [
            {"id": "camera_main", "target_object_ids": ["ball", "missing_camera_target"]}
        ]
        payload["scene"]["verifier"] = {
            "screenshot_plan": {
                "views": [
                    {
                        "id": "bad_view",
                        "view_type": "close-up",
                        "camera_id": "missing_camera",
                        "target_object_ids": ["ball", "missing_view_target"],
                        "relation_ids": ["ball_final_near_box", "missing_relation"],
                    }
                ]
            }
        }

        _sanitize_planner_data(payload)

        self.assertEqual(payload["scene"]["cameras"][0]["target_object_ids"], ["ball"])
        view = payload["scene"]["verifier"]["screenshot_plan"]["views"][0]
        self.assertIsNone(view["camera_id"])
        self.assertEqual(view["target_object_ids"], ["ball"])
        self.assertEqual(view["relation_ids"], ["ball_final_near_box"])

    def test_planner_sanitizer_drops_bbox_contact_for_non_contact_relation(self) -> None:
        payload = copy.deepcopy(self.data)
        payload["scene"]["relations"] = [
            {
                "id": "ball_not_intersecting_box",
                "relation_type": "not_intersecting",
                "subject_id": "ball",
                "object_id": "box",
                "verification_method": "bbox_contact",
            }
        ]

        _sanitize_planner_data(payload)
        ir = from_dict(GenerationIR, payload)
        report = ir.validate()

        self.assertEqual(ir.scene.relations[0].verification_method.value, "auto")
        self.assertTrue(report.passed, [issue.code for issue in report.issues])

    def test_blender_code_sanitizer_rewrites_invalid_keyframe_interpolation_enum(self) -> None:
        code = """
for fc in obj.animation_data.action.fcurves:
    for key in fc.keyframe_points:
        key.interpolation = 'EASE_IN_OUT'
        key.easing = "EASE_IN_OUT"
        other_key.easing = 'SINE'
other_key.interpolation = "LINEAR"
"""

        sanitized = _sanitize_generated_blender_code(code)

        self.assertIn("key.interpolation = 'SINE'", sanitized)
        self.assertIn('key.easing = "EASE_IN_OUT"', sanitized)
        self.assertIn("other_key.easing = 'EASE_IN_OUT'", sanitized)
        self.assertIn('other_key.interpolation = "LINEAR"', sanitized)


class MaterialAgentTextureSearchTest(unittest.TestCase):
    def test_material_agent_records_vision_blocked_with_candidate_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            local_image = tmp_path / "wood.jpg"
            local_image.write_bytes(b"fake image")
            candidate = TextureCandidate(
                title="Brown Wooden Planks",
                page_url="https://freestocktextures.com/texture/brown-wooden-planks,1.html",
                image_url="https://example.test/wood.jpg",
                download_url="https://example.test/download/wood.jpg",
                tags=["wood"],
                local_path=local_image,
                score=2.0,
            )
            config = minimal_material_config(tmp_path)
            agent = MaterialAgent(config)
            agent.client.search = mock.Mock(return_value=[candidate])
            agent.client.download_candidate = mock.Mock(return_value=candidate)
            agent.llm.json_multimodal = mock.Mock(
                side_effect=RuntimeError("unknown variant `image_url`, expected `text` at line 1 column 25")
            )

            ir = agent.resolve(material_ir(), tmp_path / "textures")

        result = agent.last_results[0]
        material = ir.scene.materials[0]
        self.assertEqual(result["status"], "vision_blocked")
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["downloaded_count"], 1)
        self.assertEqual(result["candidates"][0]["title"], "Brown Wooden Planks")
        self.assertIsNone(result["selected"])
        self.assertFalse(material.needs_texture)
        self.assertFalse(material.texture_source.approved_by_vision)
        self.assertIn("does not accept image input", material.texture_source.vision_summary)

    def test_material_agent_records_selected_texture_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            local_image = tmp_path / "wood.jpg"
            local_image.write_bytes(b"fake image")
            candidate = TextureCandidate(
                title="Brown Wooden Planks",
                page_url="https://freestocktextures.com/texture/brown-wooden-planks,1.html",
                image_url="https://example.test/wood.jpg",
                download_url="https://example.test/download/wood.jpg",
                tags=["wood"],
                local_path=local_image,
                score=2.0,
            )
            config = minimal_material_config(tmp_path)
            agent = MaterialAgent(config)
            agent.client.search = mock.Mock(return_value=[candidate])
            agent.client.download_candidate = mock.Mock(return_value=candidate)
            agent.llm.json_multimodal = mock.Mock(return_value={"passed": True, "selected_index": 1, "summary": "usable wood"})

            ir = agent.resolve(material_ir(), tmp_path / "textures")

        result = agent.last_results[0]
        material = ir.scene.materials[0]
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["selected"], "Brown Wooden Planks")
        self.assertEqual(result["selected_candidate"]["local_path"], str(local_image))
        self.assertTrue(material.texture_source.approved_by_vision)
        self.assertEqual(material.texture_source.local_path, str(local_image.resolve()))


class ExtractJsonObjectTest(unittest.TestCase):
    def test_extracts_plain_json(self) -> None:
        self.assertEqual(extract_json_object('{"passed": true}'), {"passed": True})

    def test_extracts_fenced_json(self) -> None:
        text = """Here is the result:

```json
{"passed": false, "issues": [{"code": "X"}]}
```
"""
        self.assertEqual(extract_json_object(text)["issues"][0]["code"], "X")

    def test_extracts_json_from_surrounding_text(self) -> None:
        text = 'prefix {"summary": "contains the word error but is valid"} suffix'
        self.assertEqual(extract_json_object(text), {"summary": "contains the word error but is valid"})

    def test_raises_when_no_json_object_exists(self) -> None:
        with self.assertRaises(LLMError):
            extract_json_object("no structured object here")

    def test_report_from_model_handles_non_object_json(self) -> None:
        report = _report_from_model(1, VerificationMode.VIDEO)  # type: ignore[arg-type]

        self.assertFalse(report.passed)
        self.assertEqual(report.issues[0].code, "MODEL_VERIFIER_INVALID_RESPONSE")

    def test_detects_text_only_multimodal_backend_error(self) -> None:
        exc = RuntimeError("unknown variant `image_url`, expected `text` at line 1 column 25")

        self.assertTrue(_is_multimodal_input_unsupported(exc))


if __name__ == "__main__":
    unittest.main()
