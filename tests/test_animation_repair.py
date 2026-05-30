from __future__ import annotations

import unittest

from harness.animation_repair import blender_repair_script, repair_animation_ir
from harness.ir import (
    AnimationAction,
    AnimationEventSpec,
    AnimationSpec,
    CollisionProxySpec,
    CollisionProxyType,
    ContactConstraintSpec,
    ContactConstraintType,
    GenerationIR,
    MotionPathSpec,
    ObjectSpec,
    SceneSpec,
    SourcePrompt,
    TransformSpec,
    VideoVerifierSpec,
)


def bridge_ir() -> GenerationIR:
    return GenerationIR(
        prompt=SourcePrompt(text="a toy car drives over a low bridge without clipping through the bridge deck"),
        scene=SceneSpec(
            objects=[
                ObjectSpec(
                    id="car",
                    description="toy car",
                    collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX),
                ),
                ObjectSpec(
                    id="bridge_deck",
                    description="bridge deck",
                    collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX),
                ),
            ]
        ),
        animation=AnimationSpec(
            duration_frames=120,
            verifier=VideoVerifierSpec(sampled_frames=[1, 60, 120]),
            events=[
                AnimationEventSpec(
                    id="car_drive",
                    action=AnimationAction.TRANSLATE,
                    subject_ids=["car"],
                    start_frame=1,
                    end_frame=120,
                    description="car crosses bridge deck",
                    start_transform=TransformSpec(location=(-2.5, 0.0, 0.2)),
                    end_transform=TransformSpec(location=(2.5, 0.0, 0.2)),
                    path=MotionPathSpec(),
                    contact_constraints=[
                        ContactConstraintSpec(
                            id="car_deck_support",
                            constraint_type=ContactConstraintType.SUPPORT,
                            subject_id="car",
                            object_id="bridge_deck",
                            start_frame=1,
                            end_frame=120,
                        ),
                        ContactConstraintSpec(
                            id="car_deck_nonpen",
                            constraint_type=ContactConstraintType.NONPENETRATION,
                            subject_id="car",
                            object_id="bridge_deck",
                            start_frame=1,
                            end_frame=120,
                        ),
                    ],
                )
            ],
        ),
    )


def bridge_ir_with_terminal_ground_support() -> GenerationIR:
    ir = bridge_ir()
    ir.scene.objects.append(
        ObjectSpec(
            id="ground",
            description="ground plane",
            collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX),
        )
    )
    ir.animation.contact_constraints.append(
        ContactConstraintSpec(
            id="car_ground_start",
            constraint_type=ContactConstraintType.SUPPORT,
            subject_id="car",
            object_id="ground",
            start_frame=1,
            end_frame=1,
        )
    )
    ir.animation.contact_constraints.append(
        ContactConstraintSpec(
            id="car_ground_end",
            constraint_type=ContactConstraintType.SUPPORT,
            subject_id="car",
            object_id="ground",
            start_frame=120,
            end_frame=120,
        )
    )
    return ir


def scene_graph() -> dict:
    return {
        "objects": [
            {
                "name": "car",
                "ll3m_id": "car",
                "bbox": {
                    "min": [-3.0, -0.2, 0.0],
                    "max": [-2.0, 0.2, 0.4],
                },
            },
            {
                "name": "bridge_deck",
                "ll3m_id": "bridge_deck",
                "bbox": {
                    "min": [-1.0, -0.8, 0.5],
                    "max": [1.0, 0.8, 0.7],
                },
            },
        ]
    }


def scene_graph_with_ground() -> dict:
    graph = scene_graph()
    graph["objects"].append(
        {
            "name": "ground",
            "ll3m_id": "ground",
            "bbox": {
                "min": [-10.0, -10.0, 0.0],
                "max": [10.0, 10.0, 0.0],
            },
        }
    )
    return graph


class AnimationRepairTest(unittest.TestCase):
    def test_repair_builds_outside_lift_crossing_path(self) -> None:
        repaired, plan = repair_animation_ir(bridge_ir(), scene_graph())

        self.assertTrue(plan.applied, plan.to_dict())
        event = repaired.animation.events[0]
        locations = [keyframe.transform.location for keyframe in event.path.keyframes]

        self.assertEqual([keyframe.frame for keyframe in event.path.keyframes], [1, 22, 37, 60, 84, 99, 120])
        self.assertLess(locations[0][0], -1.0)
        self.assertEqual(locations[0][0], locations[1][0])
        self.assertAlmostEqual(locations[1][2], 0.901, places=6)
        self.assertGreater(locations[2][0], -1.0)
        self.assertAlmostEqual(locations[3][0], 0.0, places=6)
        self.assertLess(locations[4][0], 1.0)
        self.assertGreater(locations[5][0], 1.0)
        self.assertEqual(locations[5][0], locations[6][0])
        self.assertEqual(event.end_transform.location, locations[-1])

    def test_repair_shrinks_support_window_to_deck_overlap_frames(self) -> None:
        repaired, plan = repair_animation_ir(bridge_ir(), scene_graph())

        event = repaired.animation.events[0]
        support = event.contact_constraints[0]

        self.assertEqual((support.start_frame, support.end_frame), (37, 84))
        self.assertEqual((plan.plans[0].support_start_frame, plan.plans[0].support_end_frame), (37, 84))
        self.assertIn(37, repaired.animation.verifier.sampled_frames)
        self.assertIn(84, repaired.animation.verifier.sampled_frames)

    def test_blender_repair_script_contains_explicit_keyframes(self) -> None:
        _, plan = repair_animation_ir(bridge_ir(), scene_graph())

        script = blender_repair_script(plan)

        self.assertIn("_LL3M_ANIMATION_REPAIR_PLAN", script)
        self.assertIn("keyframe_insert", script)
        self.assertIn("LINEAR", script)
        self.assertIn("_ll3m_repair_descendants", script)
        self.assertIn("_ll3m_repair_normalize_child_offsets", script)

    def test_repair_uses_aggregate_child_bbox_for_subject_root(self) -> None:
        graph = {
            "objects": [
                {
                    "name": "car_body",
                    "ll3m_id": "car_body",
                    "parent": "car",
                    "type": "MESH",
                    "bbox": {"min": [-3.0, -0.2, 0.0], "max": [-2.0, 0.2, 0.4]},
                },
                {
                    "name": "car_wheel",
                    "ll3m_id": "car_wheel",
                    "parent": "car",
                    "type": "MESH",
                    "bbox": {"min": [-2.9, -0.25, -0.1], "max": [-2.1, 0.25, 0.1]},
                },
                {
                    "name": "bridge_deck_mesh",
                    "ll3m_id": "bridge_deck_mesh",
                    "parent": "bridge_deck",
                    "type": "MESH",
                    "bbox": {"min": [-1.0, -0.8, 0.5], "max": [1.0, 0.8, 0.7]},
                },
            ]
        }

        repaired, plan = repair_animation_ir(bridge_ir(), graph)

        self.assertTrue(plan.applied, plan.to_dict())
        repaired_z = repaired.animation.events[0].path.keyframes[1].transform.location[2]
        self.assertAlmostEqual(repaired_z, 1.001, places=6)

    def test_blender_script_recalibrates_z_and_normalizes_child_offsets(self) -> None:
        _, plan = repair_animation_ir(bridge_ir(), scene_graph())

        script = blender_repair_script(plan)

        self.assertIn("_ll3m_repair_descendants", script)
        self.assertIn("_ll3m_repair_normalize_child_offsets", script)
        self.assertIn("_ll3m_repair_support_top", script)
        self.assertIn("_ll3m_repair_recalibrate_keyframes", script)
        self.assertIn("_ll3m_repair_bpy.context.view_layer.update()", script)
        self.assertIn("centered on support", script)

    def test_repair_can_use_animation_level_contact_constraints(self) -> None:
        ir = bridge_ir()
        event_constraint = ir.animation.events[0].contact_constraints.pop(0)
        ir.animation.contact_constraints.append(event_constraint)

        repaired, plan = repair_animation_ir(ir, scene_graph())

        self.assertTrue(plan.applied, plan.to_dict())
        self.assertEqual((repaired.animation.contact_constraints[0].start_frame, repaired.animation.contact_constraints[0].end_frame), (37, 84))

    def test_event_support_constraint_takes_priority_over_global_terminal_support(self) -> None:
        ir = bridge_ir_with_terminal_ground_support()

        repaired, plan = repair_animation_ir(ir, scene_graph_with_ground())

        self.assertTrue(plan.applied, plan.to_dict())
        self.assertEqual(plan.plans[0].support_id, "bridge_deck")
        self.assertEqual(repaired.animation.events[0].contact_constraints[0].object_id, "bridge_deck")

    def test_support_crossing_repair_does_not_treat_suitable_as_table(self) -> None:
        ir = bridge_ir()
        ir.scene.objects[1].id = "conveyor_belt"
        ir.scene.objects[1].description = "long conveyor belt suitable for items to ride on"
        ir.animation.events[0].contact_constraints[0].object_id = "conveyor_belt"
        ir.animation.events[0].contact_constraints[1].object_id = "conveyor_belt"
        graph = {
            "objects": [
                {"name": "car", "ll3m_id": "car", "bbox": {"min": [-2.5, -0.2, 0.5], "max": [-1.5, 0.2, 0.9]}},
                {"name": "conveyor_belt", "ll3m_id": "conveyor_belt", "bbox": {"min": [-2.5, -0.8, 0.0], "max": [2.5, 0.8, 0.5]}},
            ]
        }

        _, plan = repair_animation_ir(ir, graph)

        self.assertFalse(plan.applied, plan.to_dict())
        self.assertIn("no deck/platform support constraint", plan.skipped[0])

    def test_support_sequence_repair_uses_scene_graph_centers_when_crossing_plan_fails(self) -> None:
        ir = bridge_ir()
        ir.scene.objects = [
            ObjectSpec(id="car", description="toy car", collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX)),
            ObjectSpec(id="road", description="lower road", collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX)),
            ObjectSpec(id="ramp", description="ramp", collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX)),
            ObjectSpec(id="platform", description="platform", collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX)),
        ]
        event = ir.animation.events[0]
        event.target_ids = ["road", "ramp", "platform"]
        event.end_transform = TransformSpec(location=(3.0, 0.0, 1.15))
        event.contact_constraints = [
            ContactConstraintSpec("road_support", ContactConstraintType.SUPPORT, "car", "road", 1, 30),
            ContactConstraintSpec("ramp_support", ContactConstraintType.SUPPORT, "car", "ramp", 45, 95),
            ContactConstraintSpec("platform_support", ContactConstraintType.SUPPORT, "car", "platform", 105, 120),
        ]
        graph = {
            "objects": [
                {"name": "car", "ll3m_id": "car", "bbox": {"min": [-1.0, -0.2, 0.4], "max": [-0.5, 0.2, 0.8]}},
                {"name": "road", "ll3m_id": "road", "bbox": {"min": [-1.0, -0.7, 0.0], "max": [1.0, 0.7, 0.1]}},
                {"name": "ramp", "ll3m_id": "ramp", "bbox": {"min": [2.5, -0.7, 0.3], "max": [3.5, 0.7, 0.9]}},
                {"name": "platform", "ll3m_id": "platform", "bbox": {"min": [5.0, -0.7, 0.3], "max": [7.0, 0.7, 0.9]}},
            ]
        }

        repaired, plan = repair_animation_ir(ir, graph)

        self.assertTrue(plan.applied, plan.to_dict())
        locations = [keyframe.transform.location for keyframe in repaired.animation.events[0].path.keyframes]
        self.assertEqual([keyframe.frame for keyframe in repaired.animation.events[0].path.keyframes], [1, 30, 45, 95, 105, 120])
        self.assertEqual([location[0] for location in locations], [0.0, 0.0, 3.0, 3.0, 6.0, 6.0])
        self.assertEqual((plan.plans[0].support_start_frame, plan.plans[0].support_end_frame), (45, 95))
        self.assertEqual(repaired.animation.events[0].end_transform.location, locations[-1])

    def test_three_phase_support_motion_prefers_sequence_repair(self) -> None:
        ir = bridge_ir()
        ir.scene.objects.extend(
            [
                ObjectSpec(id="road", description="road", collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX)),
                ObjectSpec(id="platform", description="platform", collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX)),
            ]
        )
        event = ir.animation.events[0]
        event.target_ids = ["road", "bridge_deck", "platform"]
        event.contact_constraints = [
            ContactConstraintSpec("road_support", ContactConstraintType.SUPPORT, "car", "road", 1, 30),
            ContactConstraintSpec("deck_support", ContactConstraintType.SUPPORT, "car", "bridge_deck", 37, 84),
            ContactConstraintSpec("platform_support", ContactConstraintType.SUPPORT, "car", "platform", 100, 120),
        ]
        graph = scene_graph()
        graph["objects"].extend(
            [
                {"name": "road", "ll3m_id": "road", "bbox": {"min": [-4.0, -0.8, 0.0], "max": [-2.0, 0.8, 0.2]}},
                {"name": "platform", "ll3m_id": "platform", "bbox": {"min": [2.0, -0.8, 0.5], "max": [4.0, 0.8, 0.7]}},
            ]
        )

        repaired, plan = repair_animation_ir(ir, graph)

        self.assertTrue(plan.applied, plan.to_dict())
        self.assertEqual([keyframe.frame for keyframe in repaired.animation.events[0].path.keyframes], [1, 30, 37, 84, 100, 120])

    def test_terminal_support_constraints_set_ground_height_endpoints(self) -> None:
        repaired, plan = repair_animation_ir(bridge_ir_with_terminal_ground_support(), scene_graph_with_ground())

        self.assertTrue(plan.applied, plan.to_dict())
        keyframes = repaired.animation.events[0].path.keyframes
        self.assertAlmostEqual(keyframes[0].transform.location[2], 0.201)
        self.assertAlmostEqual(keyframes[-1].transform.location[2], 0.201)


if __name__ == "__main__":
    unittest.main()
