"""Reproduce the parallel gripper pick-place showcase in a running Blender server."""

from __future__ import annotations

import importlib

import bpy

import blender.verianim_utils as verianim


verianim = importlib.reload(verianim)

scene = verianim.clear_scene()
scene = verianim.set_frame_range(scene, start=1, end=130, fps=24)
collection = verianim.create_collection("factory_showcase")

orange = verianim.make_material("orange_plastic", base_color=(1.0, 0.38, 0.02, 1.0))
gray = verianim.make_material("gray_metal", base_color=(0.42, 0.42, 0.42, 1.0))
blue = verianim.make_material("blue_plastic", base_color=(0.05, 0.22, 0.9, 1.0))
silver = verianim.make_material("gripper_metal", base_color=(0.78, 0.78, 0.78, 1.0), metallic=0.4, roughness=0.35)
green = verianim.make_material("green_light", base_color=(0.0, 1.0, 0.1, 1.0))

conveyor = verianim.add_cube("conveyor", size=1.0, collection=collection, material=gray, verianim_id="conveyor", verianim_role="support")
conveyor.scale = (1.5, 0.34, 0.12)
conveyor.location = (0.0, 0.0, 0.72)

tray = verianim.add_cube("tray", size=1.0, collection=collection, material=blue, verianim_id="tray", verianim_role="support")
tray.scale = (0.48, 0.34, 0.10)
tray.location = (0.82, 0.0, 0.72)

box = verianim.add_cube("box", size=1.0, collection=collection, material=orange, verianim_id="box", verianim_role="carried")
box.scale = (0.20, 0.20, 0.20)
box.location = (-0.45, 0.0, 1.0)
verianim.align_bottom_to_top(box, conveyor, margin=0.002)

parts = verianim.create_parallel_gripper(
    "gripper",
    carried=box,
    location=(0.10, 0.0, 1.15),
    collection=collection,
    material=silver,
    verianim_id="gripper",
    axis="Y",
)

indicator = verianim.add_uv_sphere(
    "indicator_light",
    radius=0.06,
    collection=collection,
    material=green,
    location=(1.2, -0.28, 0.98),
    verianim_id="indicator_light",
)
for frame, visible in ((1, False), (112, False), (118, True)):
    indicator.hide_viewport = not visible
    indicator.hide_render = not visible
    indicator.keyframe_insert(data_path="hide_viewport", frame=frame)
    indicator.keyframe_insert(data_path="hide_render", frame=frame)

verianim.animate_support_slide(box, conveyor, (-0.45, 0.0), (0.10, 0.0), 1, 30)
verianim.animate_parallel_gripper_pick_place(
    parts["root"],
    box,
    conveyor,
    tray,
    fingers=parts["fingers"],
    axis="Y",
    source_xy=(0.10, 0.0),
    dest_xy=(0.82, 0.0),
    frames=(30, 45, 65, 95, 115, 130),
    carry_height=0.55,
    clearance=0.04,
)

verianim.add_camera("camera_main", location=(2.3, -2.2, 1.7), look_at_target=(0.35, 0.0, 0.9), lens=35, make_active=True)
verianim.add_light("key_light", light_type="AREA", location=(0.0, -3.0, 4.0), energy=600, size=4)
verianim.configure_render(scene, width=960, height=540, fps=24, engine="workbench")

bpy.context.scene.render.image_settings.file_format = "PNG"

VERIANIM_METADATA = {
    "objects": {
        "box": "box",
        "conveyor": "conveyor",
        "tray": "tray",
        "gripper": "gripper",
        "indicator_light": "indicator_light",
    }
}
