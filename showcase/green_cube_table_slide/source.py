def verianim_safe_look_at(obj, target):
    if hasattr(target, "location"):
        target = target.location
    return _verianim_safe_utils().look_at(obj, target)

def _verianim_safe_utils():
    utils = globals().get("verianim") or globals().get("llm")
    if utils is None:
        raise RuntimeError("VERIANIM helper alias verianim/llm is not available")
    return utils


def verianim_safe_add_cube(*args, scale=None, **kwargs):
    obj = _verianim_safe_utils().add_cube(*args, **kwargs)
    if scale is not None:
        obj.scale = scale
    return obj


def verianim_safe_add_plane(*args, scale=None, rotation=None, **kwargs):
    obj = _verianim_safe_utils().add_plane(*args, **kwargs)
    if rotation is not None:
        obj.rotation_euler = rotation
    if scale is not None:
        obj.scale = scale
    return obj

import bpy
import blender.verianim_utils as verianim

# -------------------------------------------------------------------
# 1.  Scene setup
# -------------------------------------------------------------------
scene = verianim.clear_scene()
main_collection = verianim.create_collection("SceneCollection")

# -------------------------------------------------------------------
# 2.  Materials
# -------------------------------------------------------------------
gray_mat = verianim.make_material({
    "id": "gray_table_mat",
    "base_color": (0.4, 0.4, 0.4, 1.0),
    "metallic": 0.0,
    "roughness": 0.5,
    "alpha": 1.0,
})
green_mat = verianim.make_material({
    "id": "green_cube_mat",
    "base_color": (0.2, 0.8, 0.2, 1.0),
    "metallic": 0.0,
    "roughness": 0.3,
    "alpha": 1.0,
})
floor_mat = verianim.make_material({
    "id": "floor_mat",
    "base_color": (0.8, 0.8, 0.8, 1.0),
    "metallic": 0.0,
    "roughness": 0.5,
    "alpha": 1.0,
})

# Ensure shader inputs are correctly set on each material
for mat, color, roughness, metallic in [
    (gray_mat,  (0.4, 0.4, 0.4, 1.0), 0.5, 0.0),
    (green_mat, (0.2, 0.8, 0.2, 1.0), 0.3, 0.0),
    (floor_mat, (0.8, 0.8, 0.8, 1.0), 0.5, 0.0),
]:
    mat.diffuse_color = color  # RGBA tuple required
    if mat.node_tree:
        principled = verianim.find_node_by_type(mat.node_tree, "BSDF_PRINCIPLED")
        if principled:
            principled.inputs["Base Color"].default_value = color
            principled.inputs["Roughness"].default_value = roughness
            principled.inputs["Metallic"].default_value = metallic

# -------------------------------------------------------------------
# 3.  Floor
# -------------------------------------------------------------------
floor = verianim_safe_add_plane(
    "floor", size=10.0,
    collection=main_collection,
    material=floor_mat,
    location=(0, 0, 0),
)
verianim.set_verianim_properties(floor, verianim_id="floor", verianim_role="background")

# -------------------------------------------------------------------
# 4.  Table (root empty + parts)
# -------------------------------------------------------------------
table_root = bpy.data.objects.new("gray_table", None)
verianim.link_object(table_root, main_collection)
table_root.location = (0, 0, 0)
verianim.set_verianim_properties(table_root, verianim_id="gray_table", verianim_role="support")

# Tabletop: centered so top face is at Z = 1.0
tabletop = verianim_safe_add_cube(
    "tabletop", size=1.0,
    collection=main_collection,
    material=gray_mat,
    location=(0, 0, 0.95),   # half thickness = 0.05 → top = 1.0
)
tabletop.scale = (4.0, 2.0, 0.1)
tabletop.parent = table_root
verianim.set_verianim_properties(tabletop, verianim_part="tabletop")

# Four legs – positions under the tabletop corners
leg_positions = [
    ( 1.8,  0.8, 0.45),
    ( 1.8, -0.8, 0.45),
    (-1.8,  0.8, 0.45),
    (-1.8, -0.8, 0.45),
]
leg_names = ["leg_FR", "leg_FL", "leg_BR", "leg_BL"]
for name, pos in zip(leg_names, leg_positions):
    leg = verianim_safe_add_cube(
        name, size=1.0,
        collection=main_collection,
        material=gray_mat,
        location=pos,
    )
    leg.scale = (0.1, 0.1, 0.9)   # cross‑section 0.1×0.1 m, height 0.9 m
    leg.parent = table_root
    verianim.set_verianim_properties(leg, verianim_part="leg")

# -------------------------------------------------------------------
# 5.  Green cube
# -------------------------------------------------------------------
cube = verianim_safe_add_cube(
    "green_cube", size=0.5,
    collection=main_collection,
    material=green_mat,
    location=(-1.5, 0, 1.25),   # bottom face at Z = 1.0
)
verianim.set_verianim_properties(cube, verianim_id="green_cube", verianim_role="primary")

# -------------------------------------------------------------------
# 6.  Cameras
# -------------------------------------------------------------------
cam_side = verianim.create_camera(
    "camera_side",
    location=(0, -5, 1.5),
)
verianim_safe_look_at(cam_side, (0, 0, 1.0))
cam_side.data.lens = 50.0
verianim.link_object(cam_side, main_collection)

cam_three_q = verianim.create_camera(
    "camera_three_quarter",
    location=(4, -4, 3.0),
)
verianim_safe_look_at(cam_three_q, (0, 0, 1.0))
cam_three_q.data.lens = 35.0
verianim.link_object(cam_three_q, main_collection)

cam_top = verianim.create_camera(
    "camera_top",
    location=(0, 0, 5.0),
)
verianim_safe_look_at(cam_top, (0, 0, 1.0))
cam_top.data.lens = 50.0
verianim.link_object(cam_top, main_collection)

scene.camera = cam_side

# -------------------------------------------------------------------
# 7.  Lights
# -------------------------------------------------------------------
key_light = verianim.create_area_light(
    "key_light",
    location=(3, -3, 4),
    energy=500,
    size=4,
)
verianim.link_object(key_light, main_collection)

fill_light = verianim.create_area_light(
    "fill_light",
    location=(-2, -4, 3),
    energy=300,
    size=3,
)
verianim.link_object(fill_light, main_collection)

# -------------------------------------------------------------------
# 8.  Render engine
# -------------------------------------------------------------------
verianim.configure_render(scene, engine="workbench")

# -------------------------------------------------------------------
# 9.  Animation setup
# -------------------------------------------------------------------
# Frame range and fps
verianim.set_frame_range(scene, start=1, end=120, fps=24)

# Perform the slide using the support-slide helper, which computes
# the correct Z from the table's world bbox top.
table_support = bpy.data.objects["gray_table"]
cube_subject = bpy.data.objects["green_cube"]

verianim.animate_support_slide(
    cube_subject,
    table_support,
    start_xy=(-1.5, 0.0),
    end_xy=(1.5, 0.0),
    start_frame=1,
    end_frame=120,
    margin=0.001,
)

# Insert the explicit midpoint keyframe required by the AnimationSpec.
# At frame 60 the cube should be at (0, 0, 1.25).
mid_location = (0.0, 0.0, 1.25)
verianim.insert_location_keyframe(cube_subject, 60, mid_location, interpolation="BEZIER")

# Set all location curves to Bezier for a smooth ease-in-out feel.
verianim.set_keyframe_interpolation(cube_subject, interpolation="BEZIER")

# -------------------------------------------------------------------
# 10.  Metadata for the harness
# -------------------------------------------------------------------
VERIANIM_METADATA = {
    "object_ids": {
        "floor": "floor",
        "gray_table": "gray_table",
        "tabletop": "tabletop",
        "leg": ["leg_FR", "leg_FL", "leg_BR", "leg_BL"],
        "green_cube": "green_cube",
    },
    "camera_names": [
        "camera_side",
        "camera_three_quarter",
        "camera_top",
    ],
    "light_names": [
        "key_light",
        "fill_light",
    ],
}

if bpy.context.scene.camera is None:
    for _verianim_camera in bpy.data.objects:
        if _verianim_camera.type == "CAMERA":
            bpy.context.scene.camera = _verianim_camera
            break