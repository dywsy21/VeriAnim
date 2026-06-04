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
# Clear and setup scene
# -------------------------------------------------------------------
scene = verianim.clear_scene()
col = verianim.create_collection("Scene")

# -------------------------------------------------------------------
# Materials
# -------------------------------------------------------------------
mat_floor = verianim.make_material("floor_gray", base_color=(0.8, 0.8, 0.8, 1.0))
mat_floor["verianim_id"] = "floor_gray"

mat_belt = verianim.make_material("belt_gray", base_color=(0.4, 0.4, 0.4, 1.0))
mat_belt["verianim_id"] = "belt_gray"

mat_box = verianim.make_material("box_orange", base_color=(1.0, 0.5, 0.0, 1.0))
mat_box["verianim_id"] = "box_orange"

mat_tray = verianim.make_material("tray_blue", base_color=(0.1, 0.2, 0.8, 1.0))
mat_tray["verianim_id"] = "tray_blue"

mat_light = verianim.make_material("light_green", base_color=(0.0, 1.0, 0.0, 1.0))
mat_light["verianim_id"] = "light_green"
# Make light emissive
bsdf = verianim.find_node_by_type(mat_light.node_tree, "BSDF_PRINCIPLED")
if bsdf:
    bsdf.inputs["Emission Color"].default_value = (0.0, 1.0, 0.0, 1.0)
    bsdf.inputs["Emission Strength"].default_value = 5.0

# -------------------------------------------------------------------
# Objects
# -------------------------------------------------------------------
# Ground: cube scaled to 10x10x0.05, bottom at z=0
ground = verianim_safe_add_cube("ground", size=1.0, collection=col, material=mat_floor,
                       location=(0.0, 0.0, 0.025))
ground.scale = (10.0, 10.0, 0.05)
verianim.set_verianim_properties(ground, verianim_id="ground", verianim_role="support")

# Conveyor belt: cube scaled 3x0.6x0.1, bottom at z=0
belt = verianim_safe_add_cube("belt", size=1.0, collection=col, material=mat_belt,
                     location=(0.0, 0.0, 0.05))
belt.scale = (3.0, 0.6, 0.1)
verianim.set_verianim_properties(belt, verianim_id="belt", verianim_role="primary")

# Box: cube 0.2 size, placed on belt top (z=0.1)
box = verianim_safe_add_cube("box", size=0.2, collection=col, material=mat_box,
                    location=(-1.4, 0.0, 0.2))
verianim.set_verianim_properties(box, verianim_id="box", verianim_role="primary")

# Left finger: block 0.1x0.1x0.3
finger_left = verianim_safe_add_cube("finger_left", size=1.0, collection=col,
                            material=mat_belt, location=(0.5, 0.3, 0.6))
finger_left.scale = (0.1, 0.1, 0.3)
verianim.set_verianim_properties(finger_left, verianim_id="finger_left", verianim_role="primary")

# Right finger
finger_right = verianim_safe_add_cube("finger_right", size=1.0, collection=col,
                             material=mat_belt, location=(0.5, -0.3, 0.6))
finger_right.scale = (0.1, 0.1, 0.3)
verianim.set_verianim_properties(finger_right, verianim_id="finger_right", verianim_role="primary")

# Tray: 0.5x0.5x0.05, bottom at z=0
tray = verianim_safe_add_cube("tray", size=1.0, collection=col, material=mat_tray,
                     location=(1.5, 0.0, 0.025))
tray.scale = (0.5, 0.5, 0.05)
verianim.set_verianim_properties(tray, verianim_id="tray", verianim_role="primary")

# Status light: small green sphere
light_obj = verianim.add_uv_sphere("light", radius=0.025, segments=16, rings=8,
                               collection=col, material=mat_light,
                               location=(1.5, 0.3, 0.025))
verianim.set_verianim_properties(light_obj, verianim_id="light", verianim_role="decoration")
# Make visible for static scene (no animation constraints apply)
light_obj.hide_viewport = False
light_obj.hide_render = False

# -------------------------------------------------------------------
# Cameras
# -------------------------------------------------------------------
cam_main = verianim.add_camera("cam_main", location=(3.0, -2.5, 2.5),
                           look_at_target=(0.0, 0.0, 0.3),
                           lens=24.0, collection=col, make_active=True)
cam_side = verianim.add_camera("cam_side", location=(0.0, -3.0, 1.5),
                           look_at_target=(0.0, 0.0, 0.2),
                           lens=24.0, collection=col, make_active=False)
cam_top = verianim.add_camera("cam_top", location=(0.0, 0.0, 5.0),
                          look_at_target=(0.0, 0.0, 0.1),
                          lens=24.0, collection=col, make_active=False)
cam_close = verianim.add_camera("cam_close", location=(-1.4, -0.5, 0.3),
                            look_at_target=(-1.4, 0.0, 0.2),
                            lens=50.0, collection=col, make_active=False)
bpy.context.scene.camera = cam_main

# -------------------------------------------------------------------
# Lights
# -------------------------------------------------------------------
key_light = verianim.add_light("key_light", light_type="AREA",
                           location=(2.0, -3.0, 4.0),
                           rotation=(0, 0, 0), energy=500.0, size=4.0,
                           color=None, collection=col)
fill_light = verianim.add_light("fill_light", light_type="AREA",
                            location=(-1.0, 3.0, 3.0),
                            rotation=(0, 0, 0), energy=300.0, size=3.0,
                            color=None, collection=col)
rim_light = verianim.add_light("rim_light", light_type="AREA",
                           location=(0.0, 0.0, 5.0),
                           rotation=(0, 0, 0), energy=200.0, size=3.0,
                           color=None, collection=col)

# -------------------------------------------------------------------
# Render settings
# -------------------------------------------------------------------
verianim.configure_render(scene, engine="workbench")

# -------------------------------------------------------------------
# Metadata
# -------------------------------------------------------------------
VERIANIM_METADATA = {
    "scene": "StaticBaseline",
    "objects": {
        "ground": ground.name,
        "belt": belt.name,
        "box": box.name,
        "finger_left": finger_left.name,
        "finger_right": finger_right.name,
        "tray": tray.name,
        "light": light_obj.name,
    },
    "cameras": [
        cam_main.name,
        cam_side.name,
        cam_top.name,
        cam_close.name,
    ],
    "materials": [
        mat_floor.name,
        mat_belt.name,
        mat_box.name,
        mat_tray.name,
        mat_light.name,
    ],
    "note": "Static scene baseline with all required objects, spatial relations, and neutral pose. No animation applied.",
}

if bpy.context.scene.camera is None:
    for _verianim_camera in bpy.data.objects:
        if _verianim_camera.type == "CAMERA":
            bpy.context.scene.camera = _verianim_camera
            break
