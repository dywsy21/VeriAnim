from __future__ import annotations

import unittest

from harness.ir import (
    GenerationIR,
    ObjectSpec,
    RelationType,
    SceneSpec,
    SourcePrompt,
    SpatialRelationSpec,
    ValidationIssue,
    ValidationReport,
    VerificationMode,
)
from harness.static_support_repair import blender_static_support_repair_script, repair_static_support


def support_ir() -> GenerationIR:
    return GenerationIR(
        prompt=SourcePrompt(text="support repair"),
        scene=SceneSpec(
            objects=[
                ObjectSpec(id="mug", description="mug"),
                ObjectSpec(id="table", description="table"),
            ],
            relations=[
                SpatialRelationSpec(
                    id="mug_on_table",
                    relation_type=RelationType.ON_TOP_OF,
                    subject_id="mug",
                    object_id="table",
                )
            ],
        ),
    )


def failed_report(*, overlap_x: float = 0.4, overlap_y: float = 0.4, support_z: float = 0.5) -> ValidationReport:
    return ValidationReport.failed(
        VerificationMode.DETERMINISTIC,
        [
            ValidationIssue(
                code="RELATION_ON_TOP_OF_FAILED",
                message="mug is not on table",
                relation_id="mug_on_table",
                target_id="mug",
                evidence={"overlap_x": overlap_x, "overlap_y": overlap_y, "support_z": support_z},
            )
        ],
    )


def graph_with_mug(*, mug_min=(0.0, 0.0, 1.0), mug_max=(0.2, 0.2, 1.4), table_min=(-1.0, -1.0, 0.0), table_max=(1.0, 1.0, 0.5)) -> dict:
    return {
        "objects": [
            {"name": "mug", "ll3m_id": "mug", "type": "MESH", "bbox": {"min": list(mug_min), "max": list(mug_max)}},
            {"name": "table", "ll3m_id": "table", "type": "MESH", "bbox": {"min": list(table_min), "max": list(table_max)}},
        ]
    }


class StaticSupportRepairTest(unittest.TestCase):
    def test_repairs_z_gap_without_changing_xy_when_overlap_exists(self) -> None:
        plan = repair_static_support(support_ir(), graph_with_mug(), failed_report())

        self.assertTrue(plan.applied, plan.to_dict())
        adjustment = plan.adjustments[0]
        self.assertEqual(adjustment.subject_id, "mug")
        self.assertEqual(adjustment.support_id, "table")
        self.assertAlmostEqual(adjustment.delta[0], 0.0)
        self.assertAlmostEqual(adjustment.delta[1], 0.0)
        self.assertAlmostEqual(adjustment.delta[2], -0.5)
        self.assertAlmostEqual(adjustment.subject_bottom_after, 0.5)

    def test_repairs_z_penetration_by_lifting_subject(self) -> None:
        graph = graph_with_mug(mug_min=(0.0, 0.0, 0.2), mug_max=(0.2, 0.2, 0.6))

        plan = repair_static_support(support_ir(), graph, failed_report())

        self.assertTrue(plan.applied, plan.to_dict())
        self.assertAlmostEqual(plan.adjustments[0].delta[2], 0.3)
        self.assertAlmostEqual(plan.adjustments[0].subject_bottom_after, 0.5)

    def test_uses_scene_graph_support_top_when_report_support_z_is_stale(self) -> None:
        graph = graph_with_mug(
            mug_min=(0.0, 0.0, 0.05),
            mug_max=(0.2, 0.2, 0.25),
            table_min=(-1.0, -1.0, -0.05),
            table_max=(1.0, 1.0, 0.05),
        )

        plan = repair_static_support(support_ir(), graph, failed_report(support_z=0.5))

        self.assertFalse(plan.applied, plan.to_dict())
        self.assertIn("already satisfies", plan.skipped[0])

    def test_repairs_xy_overlap_by_moving_subject_inside_support(self) -> None:
        graph = graph_with_mug(mug_min=(2.0, 0.0, 1.0), mug_max=(2.2, 0.2, 1.4))

        plan = repair_static_support(support_ir(), graph, failed_report(overlap_x=-1.0))

        self.assertTrue(plan.applied, plan.to_dict())
        adjustment = plan.adjustments[0]
        self.assertLess(adjustment.delta[0], 0.0)
        self.assertGreater(adjustment.overlap_after[0], 0.0)
        self.assertGreater(adjustment.overlap_after[1], 0.0)
        self.assertAlmostEqual(adjustment.subject_bottom_after, 0.5)

    def test_already_valid_failure_report_is_noop(self) -> None:
        graph = graph_with_mug(mug_min=(0.0, 0.0, 0.5), mug_max=(0.2, 0.2, 0.9))

        plan = repair_static_support(support_ir(), graph, failed_report())

        self.assertFalse(plan.applied, plan.to_dict())
        self.assertIn("already satisfies", plan.skipped[0])

    def test_blender_repair_script_translates_subject_roots(self) -> None:
        plan = repair_static_support(support_ir(), graph_with_mug(), failed_report())

        script = blender_static_support_repair_script(plan)

        self.assertIn("LL3M deterministic static support repair", script)
        self.assertIn("_LL3M_STATIC_SUPPORT_REPAIR_PLAN", script)
        self.assertIn("_ll3m_static_repair_apply_delta", script)


if __name__ == "__main__":
    unittest.main()
