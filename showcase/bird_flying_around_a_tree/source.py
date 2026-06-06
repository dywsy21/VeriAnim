from blender import verianim_utils as verianim
import bpy
import math

# -------------------------------------------------------------------
# Helper overrides (preserve existing safety wrappers)
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
# Scene and collection
# -------------------------------------------------------------------
scene = verianim.clear_scene()
main_col = verianim.get_or_create_collection("Main")

# -------------------------------------------------------------------
# Materials
# -------------------------------------------------------------------
grass_mat = verianim.make_material(
    "grass",
    base_color=(0.2, 0.6, 0.2, 1.0),
    roughness=0.8,
    metallic=0.0,
    texture_path="/Users/mac/Projects/ll3m-animation-update/ll3m-animation/runs/run_20260606_231935/textures/grass/grass_lawn_green.jpg",
)
# Brighter brown without the texture path to avoid black appearance when image
# is missing. The visual fallback must show a brown bark-like surface.
bark_mat = verianim.make_material(
    "bark",
    base_color=(0.6, 0.4, 0.2, 1.0),
    roughness=0.9,
    metallic=0.0,
)
leaves_mat = verianim.make_material(
    "leaves",
    base_color=(0.2, 0.6, 0.1, 1.0),
    roughness=0.7,
    metallic=0.0,
    texture_path="/Users/mac/Projects/ll3m-animation-update/ll3m-animation/runs/run_20260606_231935/textures/leaves/lime_green_leaf_closeup.jpg",
)
bird_mat = verianim.make_material(
    "bird_body",
    base_color=(0.9, 0.2, 0.2, 1.0),
    roughness=0.5,
    metallic=0.0,
)

# -------------------------------------------------------------------
# Ground – large flat grassy plane
# -------------------------------------------------------------------
ground = verianim_safe_add_plane(
    "ground", size=10.0, collection=main_col, material=grass_mat, location=(0, 0, 0)
)
verianim.set_verianim_properties(ground, verianim_id="ground", verianim_role="support")

# -------------------------------------------------------------------
# Tree trunk – cylinder, bottom at z=0, top at z=3.0
# -------------------------------------------------------------------
trunk_radius = 0.15
trunk_height = 3.0
trunk = verianim.add_cylinder(
    "tree_trunk",
    radius=trunk_radius,
    depth=trunk_height,
    vertices_count=32,
    collection=main_col,
    material=bark_mat,
    location=(0, 0, trunk_height / 2),
)
# Explicitly re-assign material to ensure it is used during rendering
trunk.data.materials.clear()
trunk.data.materials.append(bark_mat)
verianim.set_verianim_properties(trunk, verianim_id="tree_trunk", verianim_role="passive")

# -------------------------------------------------------------------
# Tree foliage – sphere sitting on trunk top (z=3.0)
# -------------------------------------------------------------------
foliage_radius = 1.5
foliage_bottom_z = trunk_height  # 3.0
foliage_center_z = foliage_bottom_z + foliage_radius  # 4.5
foliage = verianim.add_uv_sphere(
    "tree_foliage",
    radius=foliage_radius,
    segments=32,
    rings=16,
    collection=main_col,
    material=leaves_mat,
    location=(0, 0, foliage_center_z),
)
foliage.data.materials.clear()
foliage.data.materials.append(leaves_mat)
verianim.set_verianim_properties(foliage, verianim_id="tree_foliage", verianim_role="passive")

# -------------------------------------------------------------------
# Bird – static neutral flying pose, wings visibly spread, beak visible
# The bird stays at its intended flight altitude (z=2.0) to remain clearly
# visible in all camera views.
# -------------------------------------------------------------------
bird_root = bpy.data.objects.new("bird", None)
bird_root.location = (3.0, 0.0, 2.0)   # flying height, NOT on the ground
bird_root.empty_display_type = "PLAIN_AXES"
main_col.objects.link(bird_root)
verianim.set_verianim_properties(bird_root, verianim_id="bird", verianim_role="kinematic")

# Body: elongated along Y (head-tail direction)
bird_body = verianim.add_uv_sphere(
    "bird_body",
    radius=1.0,
    collection=main_col,
    material=bird_mat,
    location=(0, 0, 0),
)
bird_body.parent = bird_root
bird_body.scale = (0.2, 0.35, 0.2)  # wider left-right, longer front-back

# Wings: flat plates, clearly separated from body
wing_length = 0.45
wing_width = 0.12
wing_thickness = 0.04
body_half_x = 0.2  # after scale
gap = 0.02
wing_x_center = body_half_x + gap + wing_length / 2

left_wing = verianim_safe_add_cube(
    "bird_left_wing",
    size=1.0,
    collection=main_col,
    material=bird_mat,
    location=(-wing_x_center, 0, 0),
)
left_wing.scale = (wing_length, wing_thickness, wing_width)
left_wing.parent = bird_root

right_wing = verianim_safe_add_cube(
    "bird_right_wing",
    size=1.0,
    collection=main_col,
    material=bird_mat,
    location=(wing_x_center, 0, 0),
)
right_wing.scale = (wing_length, wing_thickness, wing_width)
right_wing.parent = bird_root

# Beak: small flat triangle-like shape pointing forward
beak_length = 0.15
beak_width = 0.06
beak_height = 0.06
body_half_y = 0.35  # from body scale
beak_y = body_half_y + beak_length / 2
beak = verianim_safe_add_cube(
    "bird_beak",
    size=1.0,
    collection=main_col,
    material=bird_mat,
    location=(0, beak_y, 0.02),  # slightly raised for visual separation
)
beak.scale = (beak_width, beak_length, beak_height)
beak.parent = bird_root

# Ensure display layer is updated
bpy.context.view_layer.update()

# -------------------------------------------------------------------
# Cameras – four required views
# -------------------------------------------------------------------
cam_main = verianim.add_camera(
    name="cam_three_quarter",
    location=(8, -8, 6),
    look_at_target=(0, 0, 3),
    lens=35,
    collection=main_col,
    make_active=True,
)
verianim.add_camera(
    name="cam_side",
    location=(0, 8, 3),
    look_at_target=(0, 0, 3),
    lens=50,
    collection=main_col,
)
verianim.add_camera(
    name="cam_top",
    location=(0, 0, 10),
    look_at_target=(0, 0, 2),
    lens=40,
    collection=main_col,
)
verianim.add_camera(
    name="cam_bird_front",
    location=(6, 0, 2),
    look_at_target=(2.5, 0, 2),
    lens=50,
    collection=main_col,
)

# -------------------------------------------------------------------
# Lights – outdoor sunlight + soft fill
# -------------------------------------------------------------------
verianim.add_light(
    "sun",
    light_type="SUN",
    location=(5, -5, 8),
    energy=3.0,
    color=(1, 1, 1),
    size=0.1,
    collection=main_col,
)
verianim.add_light(
    "fill",
    light_type="AREA",
    location=(-3, 2, 5),
    energy=200,
    size=4.0,
    color=(0.8, 0.9, 1),
    collection=main_col,
)

# -------------------------------------------------------------------
# Render settings – Workbench for consistent textured viewport renders
# -------------------------------------------------------------------
verianim.configure_render(scene, engine="workbench")

# -------------------------------------------------------------------
# Animation: bird flying one complete circular loop around the tree
# -------------------------------------------------------------------
verianim.set_frame_range(scene, start=1, end=240, fps=24)

bird_anim_root = bpy.data.objects.get("bird")
if bird_anim_root:
    # Linear placement along circular path at constant height (z=2.0)
    verianim.insert_location_keyframe(bird_anim_root, 1,  (3.0, 0.0, 2.0), interpolation="LINEAR")
    verianim.insert_location_keyframe(bird_anim_root, 60, (0.0, 3.0, 2.0), interpolation="LINEAR")
    verianim.insert_location_keyframe(bird_anim_root, 120, (-3.0, 0.0, 2.0), interpolation="LINEAR")
    verianim.insert_location_keyframe(bird_anim_root, 180, (0.0, -3.0, 2.0), interpolation="LINEAR")
    verianim.insert_location_keyframe(bird_anim_root, 240, (3.0, 0.0, 2.0), interpolation="LINEAR")

    # Face the direction of motion (tangent) at each keyframe
    r1 = math.atan2(3.0, 3.0)   # 45°  – heading NW from (3,0) to (0,3)
    verianim.insert_rotation_keyframe(bird_anim_root, 1, (0, 0, r1), interpolation="LINEAR")
    r2 = math.atan2(3.0, -3.0)  # 135° – heading SW from (0,3) to (-3,0)
    verianim.insert_rotation_keyframe(bird_anim_root, 60, (0, 0, r2), interpolation="LINEAR")
    r3 = math.atan2(-3.0, -3.0) # -135° – heading SE from (-3,0) to (0,-3)
    verianim.insert_rotation_keyframe(bird_anim_root, 120, (0, 0, r3), interpolation="LINEAR")
    r4 = math.atan2(-3.0, 3.0)  # -45°  – heading NE from (0,-3) to (3,0)
    verianim.insert_rotation_keyframe(bird_anim_root, 180, (0, 0, r4), interpolation="LINEAR")
    # Loop seamlessly: ending orientation matches start orientation
    verianim.insert_rotation_keyframe(bird_anim_root, 240, (0, 0, r1), interpolation="LINEAR")

# -------------------------------------------------------------------
# Metadata – stable object ids for later animation stages
# -------------------------------------------------------------------
VERIANIM_METADATA = {
    "objects": {
        "ground": "ground",
        "tree_trunk": "tree_trunk",
        "tree_foliage": "tree_foliage",
        "bird": "bird",
    }
}

# Fallback: ensure a camera is active if not already set
if bpy.context.scene.camera is None:
    for _verianim_camera in bpy.data.objects:
        if _verianim_camera.type == "CAMERA":
            bpy.context.scene.camera = _verianim_camera
            break