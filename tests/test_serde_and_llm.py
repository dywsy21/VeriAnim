from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from harness.agents import _is_multimodal_input_unsupported, _sanitize_planner_data
from harness.ir import GenerationIR, VerificationMode
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


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "animation_ir" / "translate_ball_to_box.json"


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

    def test_detects_text_only_multimodal_backend_error(self) -> None:
        exc = RuntimeError("unknown variant `image_url`, expected `text` at line 1 column 25")

        self.assertTrue(_is_multimodal_input_unsupported(exc))


if __name__ == "__main__":
    unittest.main()
