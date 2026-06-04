def verianim_iter_action_fcurves(action):
    """Yield fcurves from both legacy and Blender 5 layered actions."""
    if not action:
        return
    if hasattr(action, "fcurves"):
        for fcurve in action.fcurves:
            yield fcurve
    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            for bag in getattr(strip, "channelbags", []):
                for fcurve in getattr(bag, "fcurves", []):
                    yield fcurve

import bpy
import blender.verianim_utils as verianim

scene = verianim.clear_scene()

# Collections
col_main = verianim.create_collection("conveyor_scene")
col_env = verianim.create_collection("environment", parent=col_main)

# Materials
belt_mat = verianim.make_material(
    "belt_material",
    base_color=(0.3, 0.3, 0.32, 1.0),
    metallic=0.0,
    roughness=0.8,
)
belt_mat["verianim_id"] = "belt_material"

box_mat = verianim.make_material(
    "box_material",
    base_color=(0.85, 0.45, 0.12, 1.0),
    roughness=0.7,
    metallic=0.0,
)
box_mat["verianim_id"] = "box_material"

# Ensure texture wired with mapping for visibility
def ensure_texture(mat, path):
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = verianim.find_node_by_type(nt, "BSDF_PRINCIPLED")
    if bsdf is None:
        return
    try:
        img = bpy.data.images.load(path, check_existing=True)
        try:
            img.colorspace_settings.name = "sRGB"
        except Exception:
            pass
    except Exception:
        return
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    coord = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])

# Floor
floor_mat = verianim.make_material("studio_floor", base_color=(0.7, 0.7, 0.72, 1.0), roughness=0.9)
floor = verianim.add_plane("studio_floor", size=30.0, collection=col_env,
                       material=floor_mat, location=(0, 0, 0))

# Conveyor belt: elongated along x, top surface at z=0.7
belt_length = 5.0
belt_width = 1.0
belt_height = 0.7
belt = verianim.add_cube("conveyor_belt", size=1.0, collection=col_main,
                     material=belt_mat, location=(0, 0, belt_height / 2.0),
                     verianim_id="conveyor_belt", verianim_role="support")
belt.scale = (belt_length, belt_width, belt_height)
bpy.context.view_layer.update()

belt_top_z = belt.location.z + belt_height / 2.0

# Cardboard box on belt top
box_size = 0.4
box = verianim.add_cube("cardboard_box", size=1.0, collection=col_main,
                    material=box_mat, location=(0, 0, 0),
                    verianim_id="cardboard_box", verianim_role="primary")
box.scale = (box_size, box_size, box_size)
bpy.context.view_layer.update()

box.location = (0.0, 0.0, belt_top_z + box_size / 2.0 + 0.001)
bpy.context.view_layer.update()

# Lights
verianim.add_light("key_light", light_type="AREA", location=(3.0, -4.0, 5.0),
               energy=600.0, size=5.0, collection=col_env)
verianim.add_light("fill_light", light_type="AREA", location=(-3.0, -2.0, 3.0),
               energy=300.0, size=4.0, collection=col_env)

# Camera
camera = verianim.add_camera(name="camera_main", location=(4.0, -5.0, 3.0),
                         look_at_target=(0.0, 0.0, 0.6), lens=35,
                         collection=col_env, make_active=True)
scene.camera = camera

# Render
verianim.configure_render(scene, width=1280, height=720, fps=24, engine="workbench")

# -------------------- Animation --------------------
scene.frame_start = 1
scene.frame_end = 120
scene.render.fps = 24

# Belt top from actual world bbox; box rests with its bottom on belt top
bpy.context.view_layer.update()
belt_top_world = max(c[2] for c in (belt.matrix_world @ __import__("mathutils").Vector(v) for v in belt.bound_box))
box_half_z = box_size / 2.0
ride_z = belt_top_world + box_half_z + 0.001  # box center keeps bottom on belt top

# Travel range kept within belt footprint (belt spans x in [-2.5, 2.5])
start_x = -2.2
mid_x = 0.0
end_x = 2.2

# Clear any existing animation
box.animation_data_clear()

# Start keyframe (frame 1): left end, resting on belt
box.location = (start_x, 0.0, ride_z)
box.keyframe_insert(data_path="location", frame=1)

# Middle keyframe (frame 60): belt center, resting on belt
box.location = (mid_x, 0.0, ride_z)
box.keyframe_insert(data_path="location", frame=60)

# End keyframe (frame 120): right end, resting on belt
box.location = (end_x, 0.0, ride_z)
box.keyframe_insert(data_path="location", frame=120)

# Linear interpolation for constant-speed ride
if box.animation_data and box.animation_data.action:
    for fc in verianim_iter_action_fcurves(box.animation_data.action):
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"

bpy.context.view_layer.update()

VERIANIM_METADATA = {
    "objects": {
        "conveyor_belt": belt.name,
        "cardboard_box": box.name,
    },
    "materials": ["belt_material", "box_material", "studio_floor"],
    "cameras": [camera.name],
    "lights": ["key_light", "fill_light"],
    "collections": [col_main.name, col_env.name],
    "animation": {
        "frame_start": 1,
        "frame_end": 120,
        "fps": 24,
        "events": {
            "box_ride_belt": {
                "subject": box.name,
                "keyframes": [1, 60, 120],
                "start_location": [start_x, 0.0, ride_z],
                "mid_location": [mid_x, 0.0, ride_z],
                "end_location": [end_x, 0.0, ride_z],
                "interpolation": "LINEAR",
            }
        },
    },
}

if bpy.context.scene.camera is None:
    for _verianim_camera in bpy.data.objects:
        if _verianim_camera.type == "CAMERA":
            bpy.context.scene.camera = _verianim_camera
            break
