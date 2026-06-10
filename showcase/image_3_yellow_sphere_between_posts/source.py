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

from blender import verianim_utils as verianim
import bpy
import math

# -------------------------------------------------------------------
# Helper to set linear interpolation on an Action object (all layers)
# -------------------------------------------------------------------
def set_action_linear_interpolation(action):
    """Set all keyframe points to LINEAR interpolation on a given Action."""
    if hasattr(action, "fcurves"):
        for fcurve in verianim_iter_action_fcurves(action):
            for kf in fcurve.keyframe_points:
                kf.interpolation = 'LINEAR'
            fcurve.update()
    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            for bag in getattr(strip, "channelbags", []):
                for fcurve in verianim_iter_action_fcurves(bag):
                    for kf in fcurve.keyframe_points:
                        kf.interpolation = 'LINEAR'
                    fcurve.update()

def set_object_linear_interpolation(obj):
    """Set all keyframe points to LINEAR interpolation on object and shape keys."""
    if not hasattr(obj, 'animation_data') or not obj.animation_data:
        return
    action = obj.animation_data.action
    if action:
        set_action_linear_interpolation(action)

# -------------------------------------------------------------------
# Clear scene
# -------------------------------------------------------------------
scene = verianim.clear_scene()

# -------------------------------------------------------------------
# Collections
# -------------------------------------------------------------------
main_coll = verianim.create_collection("Scene")
cloth_coll = verianim.create_collection("Cloth", parent=main_coll)
post_coll = verianim.create_collection("Posts", parent=main_coll)
env_coll = verianim.create_collection("Environment", parent=main_coll)
light_coll = verianim.create_collection("Lights", parent=main_coll)
camera_coll = verianim.create_collection("Cameras", parent=main_coll)

# -------------------------------------------------------------------
# Materials (with grid texture for deformation visibility)
# -------------------------------------------------------------------
mat_cloth = verianim.make_material({
    "id": "cloth_yellow",
    "base_color": (0.95, 0.72, 0.12, 1.0),
    "roughness": 0.8
})
# Add a procedural grid texture to the cloth material using nodes
cloth_mat = bpy.data.materials.get("cloth_yellow")
if cloth_mat and cloth_mat.node_tree:
    nodes = cloth_mat.node_tree.nodes
    links = cloth_mat.node_tree.links
    bsdf = None
    for node in nodes:
        if node.type == 'BSDF_PRINCIPLED':
            bsdf = node
            break
    if bsdf:
        tex_coord = nodes.new('ShaderNodeTexCoord')
        mapping = nodes.new('ShaderNodeMapping')
        checker = nodes.new('ShaderNodeTexChecker')
        checker.inputs['Scale'].default_value = 6.0
        checker.inputs['Color2'].default_value = (0.95, 0.72, 0.12, 1.0)
        checker.inputs['Color1'].default_value = (0.8, 0.6, 0.08, 1.0)
        links.new(tex_coord.outputs['UV'], mapping.inputs['Vector'])
        links.new(mapping.outputs['Vector'], checker.inputs['Vector'])
        links.new(checker.outputs['Color'], bsdf.inputs['Base Color'])
        bsdf.inputs['Roughness'].default_value = 0.8

mat_post = verianim.make_material({
    "id": "post_gray",
    "base_color": (0.22, 0.22, 0.24, 1.0),
    "roughness": 0.65
})

mat_floor = verianim.make_material("floor_gray",
    base_color=(0.3, 0.3, 0.3, 1.0),
    roughness=0.7)

# -------------------------------------------------------------------
# Posts – bottom_center at z=0, dimensions 0.08 x 0.08 x 1.4
# -------------------------------------------------------------------
post_size = (0.08, 0.08, 1.4)
post_centers = [(-0.9, 0.0, 0.7), (0.9, 0.0, 0.7)]
post_ids = ["left_post", "right_post"]
for loc, pid in zip(post_centers, post_ids):
    post = verianim_safe_add_cube(pid, size=1.0,
        collection=post_coll,
        material=mat_post,
        location=(loc[0], loc[1], loc[2]),
        verianim_id=pid,
        verianim_role="support")
    post.scale = post_size

# -------------------------------------------------------------------
# Cloth Patch – subdivided grid with solidify, shape keys for ripple
# Dimensions: 1.6 x 0.05 x 0.7 (width, thickness, height)
# -------------------------------------------------------------------
# Create a grid in XY plane, then rotate to vertical (XZ plane)
bpy.ops.mesh.primitive_grid_add(
    x_subdivisions=20, y_subdivisions=10,
    size=1.6,
    location=(0.0, 0.0, 1.2))
cloth_obj = bpy.context.view_layer.objects.active
cloth_obj.name = "cloth_patch"
# Rotate around X to make it vertical (plane lies in XZ)
cloth_obj.rotation_euler = (math.radians(90), 0.0, 0.0)
# Scale Y (local) to get height 0.7 (grid initially 1.6 x 1.6)
cloth_obj.scale.y = 0.7 / 1.6  # 0.4375
bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
# Now mesh local coords: X width (-0.8 to 0.8), Y height (-0.35 to 0.35), Z thin (0)
# Add solidify for thickness
solidify = cloth_obj.modifiers.new(name="Solidify", type='SOLIDIFY')
solidify.thickness = 0.05
solidify.offset = 0.0
# Add subdivision surface for smooth deformation
subdiv = cloth_obj.modifiers.new(name="Subdivision", type='SUBSURF')
subdiv.levels = 2
subdiv.render_levels = 2
# Add material
if cloth_obj.data.materials:
    cloth_obj.data.materials[0] = mat_cloth
else:
    cloth_obj.data.materials.append(mat_cloth)

# Set verianim properties
verianim.set_verianim_properties(cloth_obj, verianim_id="cloth_patch", verianim_role="kinematic")
# Link to collection (already linked via primitive, but ensure)
verianim.link_to_collection(cloth_obj, cloth_coll)

# -------------------------------------------------------------------
# Shape keys for ripple deformation
# -------------------------------------------------------------------
mesh = cloth_obj.data
cloth_obj.shape_key_add(name="Basis", from_mix=False)
ripple_key = cloth_obj.shape_key_add(name="Ripple", from_mix=False)

# Displace vertices along local Z (normal to plane) based on X and Y
for i, v in enumerate(mesh.vertices):
    x = v.co.x  # -0.8 .. 0.8
    y = v.co.y  # -0.35 .. 0.35
    # 2.5 periods across width, half period across height
    displacement = 0.08 * math.sin(2 * math.pi * (x / 1.6) * 2.5) * math.cos(2 * math.pi * (y / 0.7) * 0.5)
    new_co = (v.co.x, v.co.y, v.co.z + displacement)
    ripple_key.data[i].co = new_co

# Keyframe shape key value
ripple_key.value = 0.0
cloth_obj.data.shape_keys.keyframe_insert(
    data_path=f'key_blocks["{ripple_key.name}"].value', frame=1)

ripple_key.value = 1.0
cloth_obj.data.shape_keys.keyframe_insert(
    data_path=f'key_blocks["{ripple_key.name}"].value', frame=60)

ripple_key.value = 0.0
cloth_obj.data.shape_keys.keyframe_insert(
    data_path=f'key_blocks["{ripple_key.name}"].value', frame=120)

# Set linear interpolation on shape key action
if cloth_obj.data.shape_keys and cloth_obj.data.shape_keys.animation_data:
    shape_key_action = cloth_obj.data.shape_keys.animation_data.action
    if shape_key_action:
        set_action_linear_interpolation(shape_key_action)

# -------------------------------------------------------------------
# Scale keyframes (as per AnimationSpec)
# -------------------------------------------------------------------
cloth_obj.scale = (1.0, 1.0, 1.0)
verianim.insert_scale_keyframe(cloth_obj, 1, (1.0, 1.0, 1.0))

cloth_obj.scale = (0.92, 1.0, 1.12)
verianim.insert_scale_keyframe(cloth_obj, 60, (0.92, 1.0, 1.12))

cloth_obj.scale = (1.18, 1.0, 0.86)
verianim.insert_scale_keyframe(cloth_obj, 120, (1.18, 1.0, 0.86))

set_object_linear_interpolation(cloth_obj)

# -------------------------------------------------------------------
# Floor
# -------------------------------------------------------------------
floor = verianim_safe_add_plane("Floor", size=6.0,
    collection=env_coll,
    material=mat_floor,
    location=(0, 0, 0),
    verianim_id="floor",
    verianim_role="support")

# -------------------------------------------------------------------
# Lights
# -------------------------------------------------------------------
key_light = verianim.create_area_light("key_light",
    location=(0.0, -3.0, 4.0),
    rotation=(0.0, 0.0, 0.0),
    energy=500.0,
    size=5.0,
    collection=light_coll)

fill_light = verianim.create_area_light("fill_light",
    location=(1.5, 2.0, 3.0),
    rotation=(0.0, 0.0, 0.0),
    energy=200.0,
    size=3.0,
    color=(0.9, 0.9, 1.0, 1.0),
    collection=light_coll)

# -------------------------------------------------------------------
# Cameras – front (main), side, top, and an angled view
# -------------------------------------------------------------------
cam_main = verianim.add_camera("camera_main",
    location=(0.0, -4.0, 1.4),
    look_at_target=(0.0, 0.0, 1.1),
    lens=35,
    collection=camera_coll,
    make_active=True)

cam_persp = verianim.add_camera("camera_persp",
    location=(2.5, -2.5, 1.8),
    look_at_target=(0.0, 0.0, 1.0),
    lens=35,
    collection=camera_coll,
    make_active=False)

cam_side = verianim.add_camera("camera_side",
    location=(4.0, 0.0, 1.4),
    look_at_target=(0.0, 0.0, 1.1),
    lens=35,
    collection=camera_coll,
    make_active=False)

cam_top = verianim.add_camera("camera_top",
    location=(0.0, 0.0, 4.5),
    look_at_target=(0.0, 0.0, 1.0),
    lens=35,
    collection=camera_coll,
    make_active=False)

for cam in [cam_main, cam_persp, cam_side, cam_top]:
    cam.data.clip_end = 20.0

# -------------------------------------------------------------------
# Render setup
# -------------------------------------------------------------------
verianim.configure_render(scene, engine='workbench')
scene.render.resolution_x = 1280
scene.render.resolution_y = 720
scene.frame_start = 1
scene.frame_end = 120
scene.render.fps = 24

# -------------------------------------------------------------------
# Ensure active camera
# -------------------------------------------------------------------
if bpy.context.scene.camera is None:
    for _cam in bpy.data.objects:
        if _cam.type == "CAMERA":
            bpy.context.scene.camera = _cam
            break

# Ensure posts remain static
left_post = bpy.data.objects.get("left_post")
right_post = bpy.data.objects.get("right_post")
for post in [left_post, right_post]:
    if post:
        post.animation_data_clear()

# -------------------------------------------------------------------
# Metadata
# -------------------------------------------------------------------
VERIANIM_METADATA = {
    "objects": {
        "cloth_patch": "cloth_patch",
        "left_post": "left_post",
        "right_post": "right_post"
    },
    "materials": {
        "cloth_yellow": "cloth_yellow",
        "post_gray": "post_gray"
    },
    "cameras": {
        "camera_main": "camera_main"
    },
    "verianim_version": "0.2"
}