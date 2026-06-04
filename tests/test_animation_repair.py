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


def bridge_ir_with_wide_terminal_ground_support() -> GenerationIR:
    ir = bridge_ir_with_terminal_ground_support()
    ir.animation.contact_constraints[0].end_frame = 20
    ir.animation.contact_constraints[1].start_frame = 100
    return ir


def scene_graph() -> dict:
    return {
        "objects": [
            {
                "name": "car",
                "verianim_id": "car",
                "bbox": {
                    "min": [-3.0, -0.2, 0.0],
                    "max": [-2.0, 0.2, 0.4],
                },
            },
            {
                "name": "bridge_deck",
                "verianim_id": "bridge_deck",
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
            "verianim_id": "ground",
            "bbox": {
                "min": [-10.0, -10.0, 0.0],
                "max": [10.0, 10.0, 0.0],
            },
        }
    )
    return graph


def bridge_ir_with_ground_description_mentioning_bridge() -> GenerationIR:
    ir = bridge_ir()
    ir.scene.objects[1].id = "bridge"
    ir.scene.objects[1].label = "Bridge"
    ir.scene.objects[1].description = "a low bridge with a flat deck"
    for constraint in ir.animation.events[0].contact_constraints:
        constraint.object_id = "bridge"
    ir.scene.objects.append(
        ObjectSpec(
            id="ground",
            label="Ground",
            description="flat ground under the bridge",
            collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX),
        )
    )
    ir.animation.contact_constraints.append(
        ContactConstraintSpec(
            id="final_car_ground_support",
            constraint_type=ContactConstraintType.SUPPORT,
            subject_id="car",
            object_id="ground",
            start_frame=120,
            end_frame=120,
            description="car ends resting on the ground",
        )
    )
    return ir


def scene_graph_with_bridge_and_ground() -> dict:
    return {
        "objects": [
            {
                "name": "car",
                "verianim_id": "car",
                "bbox": {"min": [-2.6, -0.3, 0.0], "max": [-1.4, 0.3, 0.4]},
            },
            {
                "name": "bridge",
                "verianim_id": "bridge",
                "bbox": {"min": [-1.0, -0.75, 0.0], "max": [1.0, 0.75, 0.5]},
            },
            {
                "name": "ground",
                "verianim_id": "ground",
                "bbox": {"min": [-5.0, -5.0, 0.0], "max": [5.0, 5.0, 0.0]},
            },
        ]
    }


def table_slide_ir() -> GenerationIR:
    ir = bridge_ir()
    ir.prompt = SourcePrompt(text="a crate slides across a wide table while staying on top the entire time")
    ir.scene.objects = [
        ObjectSpec(id="crate", description="red crate", collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX)),
        ObjectSpec(id="table", description="wide gray table", collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX)),
    ]
    event = ir.animation.events[0]
    event.id = "crate_slide"
    event.subject_ids = ["crate"]
    event.description = "crate slides on the table for the entire animation"
    event.start_transform = TransformSpec(location=(-1.0, 0.0, 1.2))
    event.end_transform = TransformSpec(location=(1.0, 0.0, 1.2))
    event.contact_constraints = [
        ContactConstraintSpec("crate_table_support", ContactConstraintType.SUPPORT, "crate", "table", 1, 120),
        ContactConstraintSpec("crate_table_nonpen", ContactConstraintType.NONPENETRATION, "crate", "table", 1, 120),
    ]
    return ir


def table_slide_graph() -> dict:
    return {
        "objects": [
            {"name": "crate", "verianim_id": "crate", "bbox": {"min": [-1.2, -0.2, 1.0], "max": [-0.8, 0.2, 1.4]}},
            {"name": "table", "verianim_id": "table", "bbox": {"min": [-1.4, -0.8, 0.8], "max": [1.4, 0.8, 1.0]}},
        ]
    }


def conveyor_platform_ride_ir() -> GenerationIR:
    ir = table_slide_ir()
    ir.scene.objects[1].id = "conveyor_belt"
    ir.scene.objects[1].description = "long flat gray conveyor belt platform"
    event = ir.animation.events[0]
    event.id = "box_ride_belt"
    event.contact_constraints = [
        ContactConstraintSpec("belt_support", ContactConstraintType.SUPPORT, "crate", "conveyor_belt", 1, 120),
        ContactConstraintSpec("belt_nonpen", ContactConstraintType.NONPENETRATION, "crate", "conveyor_belt", 1, 120),
    ]
    return ir


def conveyor_platform_ride_graph() -> dict:
    graph = table_slide_graph()
    graph["objects"][1]["name"] = "conveyor_belt"
    graph["objects"][1]["verianim_id"] = "conveyor_belt"
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

        self.assertIn("_VERIANIM_ANIMATION_REPAIR_PLAN", script)
        self.assertIn("keyframe_insert", script)
        self.assertIn("LINEAR", script)
        self.assertIn("_verianim_repair_descendants", script)
        self.assertIn("_verianim_repair_normalize_child_offsets", script)
        self.assertIn("_verianim_repair_add_parent_roots", script)
        self.assertIn("_verianim_repair_select_anchor", script)
        self.assertIn('_verianim_repair_obj["verianim_id"]', script)
        self.assertIn("_verianim_repair_apply_flat_group_keyframes", script)

    def test_repair_uses_aggregate_child_bbox_for_subject_root(self) -> None:
        graph = {
            "objects": [
                {
                    "name": "car_body",
                    "verianim_id": "car_body",
                    "parent": "car",
                    "type": "MESH",
                    "bbox": {"min": [-3.0, -0.2, 0.0], "max": [-2.0, 0.2, 0.4]},
                },
                {
                    "name": "car_wheel",
                    "verianim_id": "car_wheel",
                    "parent": "car",
                    "type": "MESH",
                    "bbox": {"min": [-2.9, -0.25, -0.1], "max": [-2.1, 0.25, 0.1]},
                },
                {
                    "name": "bridge_deck_mesh",
                    "verianim_id": "bridge_deck_mesh",
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

        self.assertIn("_verianim_repair_descendants", script)
        self.assertIn("_verianim_repair_normalize_child_offsets", script)
        self.assertIn("_verianim_repair_add_parent_roots", script)
        self.assertIn("_verianim_repair_select_anchor", script)
        self.assertIn("_verianim_repair_uses_flat_group", script)
        self.assertIn("_verianim_repair_apply_flat_group_keyframes", script)
        self.assertIn("_verianim_repair_support_top", script)
        self.assertIn("_verianim_repair_recalibrate_keyframes", script)
        self.assertIn('for terminal_id in ("ground", "floor", "terrain")', script)
        self.assertIn('or "lift outside support footprint" in label', script)
        self.assertIn('or "support height" in label', script)
        self.assertIn("_verianim_repair_bpy.context.view_layer.update()", script)
        self.assertIn("centered on support", script)
        self.assertIn("max(root_extent * 2.0, 10.0)", script)

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
                {"name": "car", "verianim_id": "car", "bbox": {"min": [-2.5, -0.2, 0.5], "max": [-1.5, 0.2, 0.9]}},
                {"name": "conveyor_belt", "verianim_id": "conveyor_belt", "bbox": {"min": [-2.5, -0.8, 0.0], "max": [2.5, 0.8, 0.5]}},
            ]
        }

        _, plan = repair_animation_ir(ir, graph)

        self.assertFalse(plan.applied, plan.to_dict())
        self.assertIn("no deck/platform support constraint", plan.skipped[0])

    def test_single_table_support_ride_does_not_get_bridge_crossing_path(self) -> None:
        repaired, plan = repair_animation_ir(table_slide_ir(), table_slide_graph())

        self.assertFalse(plan.applied, plan.to_dict())
        self.assertIn("single support ride on table", plan.skipped[0])
        event = repaired.animation.events[0]
        self.assertEqual(event.start_transform.location, (-1.0, 0.0, 1.2))
        self.assertEqual(event.end_transform.location, (1.0, 0.0, 1.2))

    def test_single_conveyor_platform_ride_does_not_get_bridge_crossing_path(self) -> None:
        _, plan = repair_animation_ir(conveyor_platform_ride_ir(), conveyor_platform_ride_graph())

        self.assertFalse(plan.applied, plan.to_dict())
        self.assertIn("single support ride on conveyor_belt", plan.skipped[0])

    def test_pick_carry_place_event_does_not_get_support_crossing_repair(self) -> None:
        ir = GenerationIR(
            prompt=SourcePrompt(text="a gripper picks a box from a conveyor and places it on a tray"),
            scene=SceneSpec(
                objects=[
                    ObjectSpec(id="box", description="orange box"),
                    ObjectSpec(id="belt", description="gray conveyor belt"),
                    ObjectSpec(id="tray", description="blue tray"),
                ]
            ),
            animation=AnimationSpec(
                duration_frames=120,
                events=[
                    AnimationEventSpec(
                        id="box_travel",
                        action=AnimationAction.TRANSLATE,
                        subject_ids=["box"],
                        start_frame=1,
                        end_frame=120,
                        description="box rides on belt, lifts, carries to tray, and is placed down",
                        start_transform=TransformSpec(location=(-1.4, 0.0, 0.2)),
                        end_transform=TransformSpec(location=(1.5, 0.0, 0.15)),
                        path=MotionPathSpec(),
                        contact_constraints=[
                            ContactConstraintSpec("belt_support", ContactConstraintType.SUPPORT, "box", "belt", 1, 60),
                            ContactConstraintSpec("tray_support", ContactConstraintType.SUPPORT, "box", "tray", 90, 120),
                        ],
                    )
                ],
            ),
        )
        graph = {
            "objects": [
                {"name": "box", "verianim_id": "box", "bbox": {"min": [-1.5, -0.1, 0.1], "max": [-1.3, 0.1, 0.3]}},
                {"name": "belt", "verianim_id": "belt", "bbox": {"min": [-1.5, -0.3, 0.0], "max": [1.5, 0.3, 0.1]}},
                {"name": "tray", "verianim_id": "tray", "bbox": {"min": [1.25, -0.25, 0.0], "max": [1.75, 0.25, 0.05]}},
            ]
        }

        repaired, plan = repair_animation_ir(ir, graph)

        self.assertFalse(plan.applied, plan.to_dict())
        self.assertIn("manipulation event", plan.skipped[0])
        event = repaired.animation.events[0]
        self.assertEqual(event.start_transform.location, (-1.4, 0.0, 0.2))
        self.assertEqual(event.end_transform.location, (1.5, 0.0, 0.15))

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
                {"name": "car", "verianim_id": "car", "bbox": {"min": [-1.0, -0.2, 0.4], "max": [-0.5, 0.2, 0.8]}},
                {"name": "road", "verianim_id": "road", "bbox": {"min": [-1.0, -0.7, 0.0], "max": [1.0, 0.7, 0.1]}},
                {"name": "ramp", "verianim_id": "ramp", "bbox": {"min": [2.5, -0.7, 0.3], "max": [3.5, 0.7, 0.9]}},
                {"name": "platform", "verianim_id": "platform", "bbox": {"min": [5.0, -0.7, 0.3], "max": [7.0, 0.7, 0.9]}},
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
                {"name": "road", "verianim_id": "road", "bbox": {"min": [-4.0, -0.8, 0.0], "max": [-2.0, 0.8, 0.2]}},
                {"name": "platform", "verianim_id": "platform", "bbox": {"min": [2.0, -0.8, 0.5], "max": [4.0, 0.8, 0.7]}},
            ]
        )

        repaired, plan = repair_animation_ir(ir, graph)

        self.assertTrue(plan.applied, plan.to_dict())
        self.assertEqual([keyframe.frame for keyframe in repaired.animation.events[0].path.keyframes], [1, 30, 37, 84, 100, 120])

    def test_overlapping_support_windows_become_monotonic_sequence(self) -> None:
        ir = bridge_ir()
        ir.scene.objects = [
            ObjectSpec(id="car", description="toy car", collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX)),
            ObjectSpec(id="road", description="lower road", collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX)),
            ObjectSpec(id="ramp", description="ramp", collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX)),
            ObjectSpec(id="platform", description="platform", collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX)),
        ]
        event = ir.animation.events[0]
        event.target_ids = ["road", "ramp", "platform"]
        event.contact_constraints = [
            ContactConstraintSpec("road_support", ContactConstraintType.SUPPORT, "car", "road", 1, 32),
            ContactConstraintSpec("ramp_support", ContactConstraintType.SUPPORT, "car", "ramp", 28, 92),
            ContactConstraintSpec("platform_support", ContactConstraintType.SUPPORT, "car", "platform", 88, 120),
        ]
        graph = {
            "objects": [
                {"name": "car", "verianim_id": "car", "bbox": {"min": [-2.2, -0.2, 0.0], "max": [-1.8, 0.2, 0.15]}},
                {"name": "road", "verianim_id": "road", "bbox": {"min": [-4.0, -1.0, 0.0], "max": [0.0, 1.0, 0.0]}},
                {"name": "ramp", "verianim_id": "ramp", "bbox": {"min": [0.0, -1.0, 0.0], "max": [3.0, 1.0, 1.0]}},
                {"name": "platform", "verianim_id": "platform", "bbox": {"min": [3.0, -1.0, 0.0], "max": [6.0, 1.0, 1.0]}},
            ]
        }

        repaired, plan = repair_animation_ir(ir, graph)

        self.assertTrue(plan.applied, plan.to_dict())
        event = repaired.animation.events[0]
        self.assertEqual([keyframe.frame for keyframe in event.path.keyframes], [1, 27, 28, 87, 88, 120])
        self.assertEqual([keyframe.transform.location[0] for keyframe in event.path.keyframes], [-2.0, -2.0, 1.5, 1.5, 4.5, 4.5])
        self.assertEqual([(c.id, c.start_frame, c.end_frame) for c in event.contact_constraints], [
            ("road_support", 1, 27),
            ("ramp_support", 28, 87),
            ("platform_support", 88, 120),
        ])

    def test_terminal_support_constraints_set_ground_height_endpoints(self) -> None:
        repaired, plan = repair_animation_ir(bridge_ir_with_terminal_ground_support(), scene_graph_with_ground())

        self.assertTrue(plan.applied, plan.to_dict())
        keyframes = repaired.animation.events[0].path.keyframes
        self.assertAlmostEqual(keyframes[0].transform.location[2], 0.201)
        self.assertAlmostEqual(keyframes[-1].transform.location[2], 0.201)

    def test_wide_terminal_support_constraints_are_narrowed_to_endpoints(self) -> None:
        repaired, plan = repair_animation_ir(bridge_ir_with_wide_terminal_ground_support(), scene_graph_with_ground())

        self.assertTrue(plan.applied, plan.to_dict())
        self.assertEqual(
            [(constraint.id, constraint.start_frame, constraint.end_frame) for constraint in repaired.animation.contact_constraints],
            [("car_ground_start", 1, 1), ("car_ground_end", 120, 120)],
        )

    def test_bridge_crossing_prefers_bridge_support_over_ground_description(self) -> None:
        repaired, plan = repair_animation_ir(
            bridge_ir_with_ground_description_mentioning_bridge(),
            scene_graph_with_bridge_and_ground(),
        )

        self.assertTrue(plan.applied, plan.to_dict())
        self.assertEqual(plan.plans[0].support_id, "bridge")
        support = repaired.animation.events[0].contact_constraints[0]
        self.assertEqual(support.object_id, "bridge")
        self.assertEqual((support.start_frame, support.end_frame), (37, 84))


if __name__ == "__main__":
    unittest.main()
