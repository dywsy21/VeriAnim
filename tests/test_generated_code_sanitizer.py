from __future__ import annotations

import unittest

from harness.agents import _sanitize_generated_blender_code


class GeneratedCodeSanitizerTest(unittest.TestCase):
    def test_rewrites_llm_utils_import_alias(self) -> None:
        code = "from blender import llm_utils as llm\nscene = llm.clear_scene()\n"

        sanitized = _sanitize_generated_blender_code(code)

        self.assertIn("from blender import ll3m_utils as llm", sanitized)
        self.assertNotIn("from blender import llm_utils", sanitized)

    def test_rewrites_make_material_spec_dict_keyword(self) -> None:
        code = "mat = ll3m.make_material(spec_dict={'id': 'mat', 'base_color': [1, 0, 0, 1]})\n"

        sanitized = _sanitize_generated_blender_code(code)

        self.assertIn("ll3m.make_material({'id': 'mat'", sanitized)
        self.assertNotIn("spec_dict=", sanitized)

    def test_wraps_helper_scale_and_rotation_keywords(self) -> None:
        code = """
from blender import ll3m_utils as ll3m
floor = ll3m.add_plane(name="floor", size=1.0, scale=(10, 10, 1), rotation=(0, 0, 0))
ramp = ll3m.add_cube("ramp", size=1.0, scale=(3, 1, 0.2))
print("ll3m.add_cube(scale='text only')")
"""

        sanitized = _sanitize_generated_blender_code(code)

        self.assertIn("def ll3m_safe_add_cube", sanitized)
        self.assertIn("def ll3m_safe_add_plane", sanitized)
        self.assertIn("floor = ll3m_safe_add_plane(", sanitized)
        self.assertIn("ramp = ll3m_safe_add_cube(", sanitized)
        self.assertIn("print(\"ll3m.add_cube(scale='text only')\")", sanitized)

    def test_removes_wave_modifier_falloff_assignment_only(self) -> None:
        code = """
wave = cloth.modifiers["Wave"]
wave.falloff = "NONE"
other.falloff = "KEEP"
text = "wave.falloff = 'NONE'"
"""

        sanitized = _sanitize_generated_blender_code(code)

        self.assertIn("removed unsupported WaveModifier falloff assignment", sanitized)
        self.assertIn('other.falloff = "KEEP"', sanitized)
        self.assertIn('text = "wave.falloff = \'NONE\'"', sanitized)
        self.assertNotIn('wave.falloff = "NONE"', sanitized)

    def test_rewrites_mathutils_vector_call_patterns(self) -> None:
        code = """
bbox = [ramp.matrix_world @ Vector(v) for v in ramp.data.vertices]
origin = mathutils.Vector()
plain = Vector()
text = "Vector()"
"""

        sanitized = _sanitize_generated_blender_code(code)

        self.assertIn("ramp.matrix_world @ v.co for v in ramp.data.vertices", sanitized)
        self.assertIn("origin = mathutils.Vector((0.0, 0.0, 0.0))", sanitized)
        self.assertIn("plain = Vector((0.0, 0.0, 0.0))", sanitized)
        self.assertIn('text = "Vector()"', sanitized)

    def test_patches_direct_action_fcurve_loop_without_touching_helper_body(self) -> None:
        code = """
def ll3m_iter_action_fcurves(action):
    if hasattr(action, "fcurves"):
        for fcurve in action.fcurves:
            yield fcurve

for fcurve in action.fcurves:
    fcurve.update()
"""

        sanitized = _sanitize_generated_blender_code(code)

        self.assertIn("for fcurve in action.fcurves:\n            yield fcurve", sanitized)
        self.assertIn("for fcurve in ll3m_iter_action_fcurves(action):", sanitized)
        self.assertEqual(sanitized.count("def ll3m_iter_action_fcurves"), 1)

    def test_adds_action_fcurve_helper_when_missing(self) -> None:
        code = """
if obj.animation_data and obj.animation_data.action:
    action = obj.animation_data.action
    for fc in action.fcurves:
        fc.update()
"""

        sanitized = _sanitize_generated_blender_code(code)

        self.assertIn("def ll3m_iter_action_fcurves", sanitized)
        self.assertIn("for fc in ll3m_iter_action_fcurves(action):", sanitized)

    def test_preserves_valid_interpolation_assignment(self) -> None:
        code = """
key.interpolation = 'EASE_IN_OUT'
other_key.interpolation = "LINEAR"
"""

        sanitized = _sanitize_generated_blender_code(code)

        self.assertIn("key.interpolation = 'SINE'", sanitized)
        self.assertIn('other_key.interpolation = "LINEAR"', sanitized)
        self.assertNotIn("EASE_IN_OUT", sanitized)


class HistoricalGeneratedCodeSanitizerSmokeTest(unittest.TestCase):
    def test_historical_failure_scripts_no_longer_contain_known_fragments(self) -> None:
        cases = [
            (
                "make_material spec_dict keyword",
                "mat = ll3m.make_material(spec_dict={'id': 'mat', 'base_color': [1, 0, 0, 1]})\n",
                ["spec_dict="],
                ["ll3m.make_material({"],
            ),
            (
                "helper scale keywords",
                """
from blender import ll3m_utils as ll3m
ground = ll3m.add_plane("ground", size=4, scale=(1, 1, 1), rotation=(0, 0, 0))
deck = ll3m.add_cube("deck", size=1, scale=(2, 1, 0.2))
""",
                ["ll3m.add_plane(", "ll3m.add_cube("],
                ["ll3m_safe_add_plane(", "ll3m_safe_add_cube("],
            ),
            (
                "wave modifier falloff and helper scale",
                """
from blender import ll3m_utils as ll3m
ground = ll3m.add_plane("ground", size=4, scale=(1, 1, 1))
wave = cloth.modifiers["Wave"]
wave.falloff = "NONE"
""",
                ["wave.falloff"],
                ["removed unsupported WaveModifier falloff assignment", "ll3m_safe_add_plane("],
            ),
            (
                "legacy llm_utils import alias",
                "from blender import llm_utils as llm\nscene = llm.clear_scene()\n",
                ["from blender import llm_utils as llm"],
                ["from blender import ll3m_utils as llm"],
            ),
            (
                "direct action fcurve loop",
                """
action = obj.animation_data.action
for fcurve in action.fcurves:
    fcurve.update()
""",
                [],
                ["for fcurve in ll3m_iter_action_fcurves(action):"],
            ),
            (
                "look_at object target",
                "ll3m.look_at(bpy.data.objects['ground'], leg)\n",
                ["ll3m.look_at(bpy.data.objects['ground'], leg)"],
                ["ll3m_safe_look_at(bpy.data.objects['ground'], leg)"],
            ),
        ]

        for name, code, removed_fragments, expected_fragments in cases:
            with self.subTest(name=name):
                sanitized = _sanitize_generated_blender_code(code)
                for fragment in removed_fragments:
                    self.assertNotIn(fragment, sanitized)
                for fragment in expected_fragments:
                    self.assertIn(fragment, sanitized)


if __name__ == "__main__":
    unittest.main()
