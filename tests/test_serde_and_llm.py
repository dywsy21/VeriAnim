from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from harness.agents import _is_multimodal_input_unsupported, _sanitize_planner_data
from harness.ir import GenerationIR
from harness.llm import LLMError, extract_json_object
from harness.serde import IRDecodeError, from_dict


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

    def test_planner_sanitizer_normalizes_relation_visual_priority_alias(self) -> None:
        payload = copy.deepcopy(self.data)
        payload["scene"]["relations"][0]["visual_priority"] = "normal"

        _sanitize_planner_data(payload)
        ir = from_dict(GenerationIR, payload)

        self.assertEqual(ir.scene.relations[0].visual_priority.value, "preferred")


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
