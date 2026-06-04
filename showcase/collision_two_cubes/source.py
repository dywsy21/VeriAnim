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

import bpy
from blender import verianim_utils as verianim

scene = verianim.clear_scene()
col = verianim.create_collection("elastic_collision")

# --- Materials ---
blue_mat = verianim.make_material("blue_mat", base_color=(0.2, 0.4, 0.9, 1.0), metallic=0.0, roughness=0.5)
blue_mat.diffuse_color = (0.2, 0.4, 0.9, 1.0)
blue_mat['verianim_id'] = 'blue_mat'

red_mat = verianim.make_material("red_mat", base_color=(0.9, 0.2, 0.2, 1.0), metallic=0.0, roughness=0.5)
red_mat.diffuse_color = (0.9, 0.2, 0.2, 1.0)
red_mat['verianim_id'] = 'red_mat'

floor_mat = verianim.make_material("floor_mat", base_color=(0.5, 0.5, 0.5, 1.0), metallic=0.0, roughness=0.7)
floor_mat.diffuse_color = (0.5, 0.5, 0.5, 1.0)
floor_mat['verianim_id'] = 'floor_mat'

white_mat = verianim.make_material("white_mat", base_color=(1.0, 1.0, 1.0, 1.0), metallic=0.0, roughness=0.3)
white_mat.diffuse_color = (1.0, 1.0, 1.0, 1.0)
white_mat['verianim_id'] = 'white_mat'

# --- Floor ---
floor = verianim_safe_add_cube("floor", size=1.0, collection=col, material=floor_mat, location=(0, 0, 0))
floor.scale = (10.0, 6.0, 0.05)
verianim.set_verianim_properties(floor, verianim_id="floor", verianim_role="support")

# --- Moving Square ---
moving_square = verianim_safe_add_cube("moving_square", size=1.0, collection=col, material=blue_mat, location=(-3.0, 0.0, 0.5))
verianim.set_verianim_properties(moving_square, verianim_id="moving_square", verianim_role="active")

# --- Stationary Square ---
stationary_square = verianim_safe_add_cube("stationary_square", size=1.0, collection=col, material=red_mat, location=(0.0, 0.0, 0.5))
verianim.set_verianim_properties(stationary_square, verianim_id="stationary_square", verianim_role="passive")

# --- Stripe Marker on moving square front face (+X direction) ---
stripe = verianim_safe_add_cube("stripe_marker", size=1.0, collection=col, material=white_mat, location=(0, 0, 0))
stripe.scale = (0.02, 0.2, 0.8)
stripe.parent = moving_square
stripe.location = (0.51, 0.0, 0.0)
verianim.set_verianim_properties(stripe, verianim_id="moving_square", verianim_part="stripe_marker", verianim_role="decorative")

# --- Align squares on floor for visible on_top_of contact ---
verianim.align_bottom_to_top(moving_square, floor, margin=0.001)
verianim.align_bottom_to_top(stationary_square, floor, margin=0.001)

# --- Lights ---
key_light = verianim.create_area_light("key_light", location=(2.0, -4.0, 5.0), rotation=(0.5, 0.3, 0), energy=600.0, size=4.0, color=(1.0, 1.0, 1.0))
fill_light = verianim.create_area_light("fill_light", location=(-2.0, 2.0, 3.0), rotation=(0.5, -0.2, 0), energy=200.0, size=3.0, color=(0.9, 0.9, 1.0))

# --- Cameras ---
camera_main = verianim.add_camera("camera_main", location=(0.0, -5.0, 3.0), look_at_target=(0.0, 0.0, 0.5), lens=35, collection=col, make_active=True)
camera_side = verianim.add_camera("camera_side", location=(0.0, -6.0, 1.5), look_at_target=(0.0, 0.0, 0.5), lens=50, collection=col)

# --- Render ---
verianim.configure_render(scene, engine='workbench')

# ============================================================
# ANIMATION SETUP
# ============================================================

verianim.set_frame_range(scene, start=1, end=120, fps=24)
bpy.context.scene.camera = camera_main
verianim.configure_render(scene, width=1280, height=720, fps=24, engine='workbench')

# Capture aligned z from actual object locations after align_bottom_to_top
moving_z = moving_square.location.z
stationary_z = stationary_square.location.z

# --- Moving Square: approach then stop (elastic collision) ---
# Frame 1: start position x=-3
verianim.insert_location_keyframe(moving_square, 1, (-3.0, 0.0, moving_z), interpolation="LINEAR")
# Frame 30: midpoint approach x=-2
verianim.insert_location_keyframe(moving_square, 30, (-2.0, 0.0, moving_z), interpolation="LINEAR")
# Frame 60: contact position x=-1 (right face touches stationary left face)
verianim.insert_location_keyframe(moving_square, 60, (-1.0, 0.0, moving_z), interpolation="LINEAR")
# Frame 120: stays stopped at x=-1 after collision
verianim.insert_location_keyframe(moving_square, 120, (-1.0, 0.0, moving_z), interpolation="LINEAR")

# --- Stationary Square: rest then depart (elastic collision) ---
# Frame 1: at rest at x=0
verianim.insert_location_keyframe(stationary_square, 1, (0.0, 0.0, stationary_z), interpolation="LINEAR")
# Frame 60: still at rest at x=0 at collision moment
verianim.insert_location_keyframe(stationary_square, 60, (0.0, 0.0, stationary_z), interpolation="LINEAR")
# Frame 90: midpoint departure x=1.5
verianim.insert_location_keyframe(stationary_square, 90, (1.5, 0.0, stationary_z), interpolation="LINEAR")
# Frame 120: final position x=3
verianim.insert_location_keyframe(stationary_square, 120, (3.0, 0.0, stationary_z), interpolation="LINEAR")

# Ensure linear interpolation on all fcurves
verianim.set_linear_interpolation(moving_square)
verianim.set_linear_interpolation(stationary_square)

VERIANIM_METADATA = {
    "object_ids": {
        "floor": "floor",
        "moving_square": "moving_square",
        "stationary_square": "stationary_square",
        "stripe_marker": "stripe_marker"
    },
    "object_names": {
        "floor": floor.name,
        "moving_square": moving_square.name,
        "stationary_square": stationary_square.name,
        "stripe_marker": stripe.name
    },
    "material_ids": {
        "blue_mat": "blue_mat",
        "red_mat": "red_mat",
        "floor_mat": "floor_mat",
        "white_mat": "white_mat"
    },
    "camera_ids": {
        "camera_main": "camera_main",
        "camera_side": "camera_side"
    },
    "animation": {
        "duration_frames": 120,
        "fps": 24,
        "loop": False,
        "events": [
            {
                "id": "moving_approach",
                "subject_id": "moving_square",
                "action": "translate",
                "start_frame": 1,
                "end_frame": 60,
                "start_transform": {"location": [-3.0, 0.0, moving_z]},
                "end_transform": {"location": [-1.0, 0.0, moving_z]},
                "interpolation": "LINEAR",
                "description": "Blue square moves right toward red square until contact"
            },
            {
                "id": "moving_stopped",
                "subject_id": "moving_square",
                "action": "translate",
                "start_frame": 60,
                "end_frame": 120,
                "start_transform": {"location": [-1.0, 0.0, moving_z]},
                "end_transform": {"location": [-1.0, 0.0, moving_z]},
                "interpolation": "LINEAR",
                "description": "Blue square stays stopped after elastic collision"
            },
            {
                "id": "stationary_rest",
                "subject_id": "stationary_square",
                "action": "translate",
                "start_frame": 1,
                "end_frame": 60,
                "start_transform": {"location": [0.0, 0.0, stationary_z]},
                "end_transform": {"location": [0.0, 0.0, stationary_z]},
                "interpolation": "LINEAR",
                "description": "Red square stays at rest before collision"
            },
            {
                "id": "stationary_depart",
                "subject_id": "stationary_square",
                "action": "translate",
                "start_frame": 60,
                "end_frame": 120,
                "start_transform": {"location": [0.0, 0.0, stationary_z]},
                "end_transform": {"location": [3.0, 0.0, stationary_z]},
                "interpolation": "LINEAR",
                "description": "Red square departs rightward after elastic collision"
            }
        ],
        "contact_constraints": [
            {
                "id": "floor_support_moving",
                "constraint_type": "support",
                "subject_id": "moving_square",
                "object_id": "floor",
                "start_frame": 1,
                "end_frame": 120
            },
            {
                "id": "floor_support_stationary",
                "constraint_type": "support",
                "subject_id": "stationary_square",
                "object_id": "floor",
                "start_frame": 1,
                "end_frame": 120
            },
            {
                "id": "approach_nonpen",
                "constraint_type": "nonpenetration",
                "subject_id": "moving_square",
                "object_id": "stationary_square",
                "start_frame": 1,
                "end_frame": 59
            },
            {
                "id": "depart_nonpen",
                "constraint_type": "nonpenetration",
                "subject_id": "stationary_square",
                "object_id": "moving_square",
                "start_frame": 61,
                "end_frame": 120
            }
        ],
        "render": {
            "resolution": [1280, 720],
            "engine": "workbench"
        },
        "verifier": {
            "sampled_frames": [1, 30, 60, 90, 120],
            "pass_criteria": [
                "Blue square approaches and stops at contact by frame 60.",
                "Red square departs rightward from frame 60 to 120.",
                "Blue square remains stationary after frame 60.",
                "No floating or penetration between squares or floor."
            ]
        }
    }
}

if bpy.context.scene.camera is None:
    for _verianim_camera in bpy.data.objects:
        if _verianim_camera.type == "CAMERA":
            bpy.context.scene.camera = _verianim_camera
            break