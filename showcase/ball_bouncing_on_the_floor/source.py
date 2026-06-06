import bpy
from blender import verianim_utils as verianim

# -------------------------------------------------------------------
# Safe helper wrappers (avoiding recursion)
# -------------------------------------------------------------------
def _verianim_safe_utils():
    utils = globals().get("verianim") or globals().get("llm")
    if utils is None:
        raise RuntimeError("VeriAnim helper alias verianim/llm is not available")
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


# -------------------------------------------------------------------
# Clear scene and basic setup
# -------------------------------------------------------------------
scene = verianim.clear_scene()
verianim.configure_render(scene, engine="workbench")   # BLENDER_WORKBENCH in 4.5

# Neutral world background
world = bpy.data.worlds.new(name="StudioWorld")
world.use_nodes = True
bg_node = world.node_tree.nodes.new(type="ShaderNodeBackground")
bg_node.inputs["Color"].default_value = (0.9, 0.9, 0.9, 1.0)
bg_node.inputs["Strength"].default_value = 1.0
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.9, 0.9, 0.9, 1.0)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0
scene.world = world

# -------------------------------------------------------------------
# Collections
# -------------------------------------------------------------------
main_coll = verianim.ensure_collection("BouncingBall")

# -------------------------------------------------------------------
# Materials
# -------------------------------------------------------------------
ball_mat = verianim.make_material(
    "ball_material",
    base_color=(0.9, 0.1, 0.1, 1.0),
    roughness=0.65,
    metallic=0.0,
)
ground_mat = verianim.make_material(
    "ground_material",
    base_color=(0.7, 0.7, 0.7, 1.0),
    roughness=0.8,
    metallic=0.0,
)

# -------------------------------------------------------------------
# Objects
# -------------------------------------------------------------------
# Ball: sphere, radius 0.2 (size [0.4,0.4,0.4] -> diameter 0.4)
ball = verianim.add_uv_sphere(
    name="Ball",
    radius=0.2,
    segments=32,
    rings=16,
    location=(0.0, 0.0, 2.0),
    collection=main_coll,
    material=ball_mat,
    verianim_id="ball",
    verianim_role="primary",
)

# Ground: large flat plane (uses the helper defined earlier)
ground = verianim_safe_add_plane(
    name="Ground",
    size=10.0,
    location=(0.0, 0.0, 0.0),
    collection=main_coll,
    material=ground_mat,
    verianim_id="ground",
    verianim_role="support",
)

# -------------------------------------------------------------------
# Lights
# -------------------------------------------------------------------
key_light = verianim.add_light(
    name="key_light",
    light_type="AREA",
    location=(2.0, -3.0, 4.0),
    rotation=(0.0, 0.0, 0.0),
    energy=500.0,
    size=4.0,
    collection=main_coll,
)

# -------------------------------------------------------------------
# Cameras
# -------------------------------------------------------------------
cam_main = verianim.add_camera(
    name="camera_main",
    location=(5.0, -5.0, 4.0),
    look_at_target=(0.0, 0.0, 1.5),
    lens=35,
    collection=main_coll,
    make_active=True,
)

cam_side = verianim.add_camera(
    name="camera_side",
    location=(4.0, 0.0, 1.5),
    look_at_target=(0.0, 0.0, 1.5),
    lens=35,
    collection=main_coll,
    make_active=False,
)

cam_closeup = verianim.add_camera(
    name="camera_closeup",
    location=(0.0, -1.5, 1.0),
    look_at_target=(0.0, 0.0, 1.0),
    lens=50,
    collection=main_coll,
    make_active=False,
)

# Explicitly set scene camera
scene.camera = cam_main

# -------------------------------------------------------------------
# Metadata for downstream stages
# -------------------------------------------------------------------
VERIANIM_METADATA = {
    "scene": scene.name,
    "objects": {
        "ball": ball.name,
        "ground": ground.name,
    },
    "cameras": {
        "camera_main": cam_main.name,
        "camera_side": cam_side.name,
        "camera_closeup": cam_closeup.name,
    },
    "materials": {
        "ball_material": ball_mat.name,
        "ground_material": ground_mat.name,
    },
}

# Fallback: make sure an active camera exists
if bpy.context.scene.camera is None:
    for _verianim_camera in bpy.data.objects:
        if _verianim_camera.type == "CAMERA":
            bpy.context.scene.camera = _verianim_camera
            break