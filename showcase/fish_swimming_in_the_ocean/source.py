import bpy
import math
from mathutils import Vector
from blender import ll3m_utils as ll3m

# -------------------------------------------------------------------
# 1. Scene, collections, render setup
# -------------------------------------------------------------------
scene = ll3m.clear_scene()
ll3m.configure_render(scene, width=1024, height=1024, fps=24, engine="workbench")
scene.frame_start = 1
scene.frame_end = 100

main_col = bpy.data.collections.new("MainScene")
scene.collection.children.link(main_col)

# -------------------------------------------------------------------
# 2. World background – deep underwater blue
# -------------------------------------------------------------------
world = scene.world
if world is None:
    world = bpy.data.worlds.new("World")
    scene.world = world
world.color = (0.03, 0.12, 0.3)
world.use_nodes = True

# -------------------------------------------------------------------
# 3. Materials
# -------------------------------------------------------------------
# Fish material with procedural scales
mat_fish = ll3m.make_material("mat_fish", base_color=(0.8, 0.2, 0.2, 1.0), roughness=0.3, metallic=0.1)
mat_fish["ll3m_id"] = "mat_fish"
nodes_f = mat_fish.node_tree.nodes
links_f = mat_fish.node_tree.links
bsdf_fish = ll3m.find_node_by_type(mat_fish.node_tree, "BSDF_PRINCIPLED")
if bsdf_fish:
    texcoord = nodes_f.new("ShaderNodeTexCoord")
    texcoord.location = (-600, 300)
    wave = nodes_f.new("ShaderNodeTexWave")
    wave.location = (-400, 300)
    wave.wave_type = "BANDS"
    wave.bands_direction = "DIAGONAL"
    wave.inputs["Scale"].default_value = 15.0
    wave.inputs["Distortion"].default_value = 0.5
    bump = nodes_f.new("ShaderNodeBump")
    bump.location = (-200, 300)
    bump.inputs["Strength"].default_value = 0.15
    links_f.new(texcoord.outputs["UV"], wave.inputs["Vector"])
    links_f.new(wave.outputs["Color"], bump.inputs["Height"])
    links_f.new(bump.outputs["Normal"], bsdf_fish.inputs["Normal"])

# Eye material
mat_eye = ll3m.make_material("mat_eye", base_color=(0.0, 0.0, 0.0, 1.0), roughness=0.1)
mat_eye["ll3m_id"] = "mat_eye"

# Floor material with procedural sand
mat_floor = ll3m.make_material("mat_floor", base_color=(0.9, 0.9, 0.7, 1.0), roughness=0.9, metallic=0.0)
mat_floor["ll3m_id"] = "mat_floor"
nodes_fl = mat_floor.node_tree.nodes
links_fl = mat_floor.node_tree.links
bsdf_floor = ll3m.find_node_by_type(mat_floor.node_tree, "BSDF_PRINCIPLED")
if bsdf_floor:
    tcoord = nodes_fl.new("ShaderNodeTexCoord"); tcoord.location = (-800, 300)
    noise_tx = nodes_fl.new("ShaderNodeTexNoise"); noise_tx.location = (-600, 300)
    noise_tx.inputs["Scale"].default_value = 10.0
    noise_tx.inputs["Detail"].default_value = 8.0
    ramp = nodes_fl.new("ShaderNodeValToRGB"); ramp.location = (-400, 300)
    ramp.color_ramp.elements[0].position = 0.25
    ramp.color_ramp.elements[0].color = (0.86, 0.86, 0.62, 1.0)
    ramp.color_ramp.elements[1].position = 0.75
    ramp.color_ramp.elements[1].color = (0.94, 0.94, 0.40, 1.0)
    mix_color = nodes_fl.new("ShaderNodeMix"); mix_color.location = (-200, 300)
    mix_color.data_type = "RGBA"
    mix_color.blend_type = "MIX"
    mix_color.inputs["Factor"].default_value = 0.7
    base_color_node = nodes_fl.new("ShaderNodeRGB"); base_color_node.location = (-400, 100)
    base_color_node.outputs[0].default_value = (0.9, 0.9, 0.7, 1.0)
    links_fl.new(tcoord.outputs["UV"], noise_tx.inputs["Vector"])
    links_fl.new(noise_tx.outputs["Color"], ramp.inputs["Fac"])
    links_fl.new(ramp.outputs["Color"], mix_color.inputs["A"])
    links_fl.new(base_color_node.outputs["Color"], mix_color.inputs["B"])
    links_fl.new(mix_color.outputs["Result"], bsdf_floor.inputs["Base Color"])
    bump_sand = nodes_fl.new("ShaderNodeBump"); bump_sand.location = (0, 300)
    bump_sand.inputs["Strength"].default_value = 0.4
    links_fl.new(noise_tx.outputs["Color"], bump_sand.inputs["Height"])
    links_fl.new(bump_sand.outputs["Normal"], bsdf_floor.inputs["Normal"])

# Water volume material
mat_water = ll3m.make_material("mat_water", base_color=(0.15, 0.40, 0.70, 0.10), roughness=0.0, metallic=0.0)
mat_water["ll3m_id"] = "mat_water"
mat_water.diffuse_color = (0.15, 0.40, 0.70, 0.10)
mat_water.blend_method = "BLEND"
shader_water = ll3m.find_node_by_type(mat_water.node_tree, "BSDF_PRINCIPLED")
if shader_water:
    shader_water.inputs["Alpha"].default_value = 0.10

# Seaweed material
mat_seaweed = ll3m.make_material("mat_seaweed", base_color=(0.1, 0.6, 0.1, 1.0), roughness=0.5, metallic=0.0)
mat_seaweed["ll3m_id"] = "mat_seaweed"

# Rock material with texture
mat_rock = ll3m.make_material("mat_rock", base_color=(0.4, 0.4, 0.4, 1.0), roughness=0.8, metallic=0.1)
mat_rock["ll3m_id"] = "mat_rock"
rock_tex_path = "/Users/mac/Projects/ll3m-animation/runs/run_20260531_165731/textures/mat_rock/granite_slab_rock.jpg"
rock_img = bpy.data.images.load(rock_tex_path)
rock_img.colorspace_settings.name = "sRGB"
nodes_r = mat_rock.node_tree.nodes
links_r = mat_rock.node_tree.links
principled_rock = ll3m.find_node_by_type(mat_rock.node_tree, "BSDF_PRINCIPLED")
if principled_rock:
    texcoord_r = nodes_r.new("ShaderNodeTexCoord"); texcoord_r.location = (-800, 300)
    img_node = nodes_r.new("ShaderNodeTexImage"); img_node.location = (-400, 300)
    img_node.image = rock_img
    links_r.new(texcoord_r.outputs["UV"], img_node.inputs["Vector"])
    links_r.new(img_node.outputs["Color"], principled_rock.inputs["Base Color"])

# Coral material
mat_coral = ll3m.make_material("mat_coral", base_color=(0.9, 0.5, 0.5, 1.0), roughness=0.4, metallic=0.0)
mat_coral["ll3m_id"] = "mat_coral"

# -------------------------------------------------------------------
# 4. Environment light
# -------------------------------------------------------------------
light = ll3m.add_light("sun_light", light_type="AREA", location=(0.0, 0.0, 6.0),
                       energy=700.0, size=4.0, color=(0.55, 0.65, 0.95), collection=main_col)

# -------------------------------------------------------------------
# 5. Sea floor
# -------------------------------------------------------------------
floor_size = (5.0, 5.0, 0.1)
floor_obj = ll3m.add_cube("sea_floor", size=1.0, collection=main_col, material=mat_floor)
floor_obj.scale = floor_size
floor_obj.location = (0.0, 0.0, -floor_size[2] / 2)   # top at z = 0
ll3m.set_ll3m_properties(floor_obj, ll3m_id="sea_floor", ll3m_role="support")

# -------------------------------------------------------------------
# 6. Water volume
# -------------------------------------------------------------------
vol_obj = ll3m.add_cube("sea_volume", size=1.0, collection=main_col, material=mat_water)
vol_obj.scale = (5.0, 5.0, 5.0)
vol_obj.location = (0.0, 0.0, 2.5)
ll3m.set_ll3m_properties(vol_obj, ll3m_id="sea_volume", ll3m_role="background")

# -------------------------------------------------------------------
# 7. Seaweed – green fronds planted on sea floor
# -------------------------------------------------------------------
seaweed_root = bpy.data.objects.new("seaweed", None)
main_col.objects.link(seaweed_root)
seaweed_root.location = (1.0, -1.0, 0.5)   # IR placement center
ll3m.set_ll3m_properties(seaweed_root, ll3m_id="seaweed", ll3m_role="decoration")

frond_data = [
    ((0.0, 0.0, 0.8/2 - 0.5), 0.8, 0.04, 0.0),
    ((-0.04, 0.025, 0.75/2 - 0.5), 0.75, 0.04, 0.25),
    ((0.04, -0.02, 0.78/2 - 0.5), 0.78, 0.04, -0.2),
    ((-0.05, -0.03, 0.72/2 - 0.5), 0.72, 0.04, -0.15),
    ((0.06, 0.015, 0.74/2 - 0.5), 0.74, 0.04, 0.1),
    ((-0.02, -0.04, 0.77/2 - 0.5), 0.77, 0.04, 0.05),
    ((0.025, -0.025, 0.7/2 - 0.5), 0.7, 0.04, -0.1),
    ((0.0, 0.03, 0.73/2 - 0.5), 0.73, 0.04, 0.3),
]

for i, (offset, height, scale_y, rot_y) in enumerate(frond_data):
    frond = ll3m.add_cylinder(
        f"seaweed_frond_{i}",
        radius=0.025,
        depth=height,
        vertices_count=10,
        collection=main_col,
        material=mat_seaweed,
    )
    frond.parent = seaweed_root
    frond.location = Vector(offset)
    frond.scale = (1.0, scale_y, 1.0)
    frond.rotation_euler = (0.0, rot_y, 0.0)

# -------------------------------------------------------------------
# 8. Rock
# -------------------------------------------------------------------
rock = ll3m.add_uv_sphere("rock", radius=0.2, segments=16, rings=16,
                          collection=main_col, material=mat_rock)
rock.scale = (1.25, 1.25, 0.75)   # approx 0.5 x 0.5 x 0.3
rock.location = (-1.0, 1.0, 0.15)   # bottom at 0
ll3m.set_ll3m_properties(rock, ll3m_id="rock", ll3m_role="decoration")

# -------------------------------------------------------------------
# 9. Coral – branching pink colony
# -------------------------------------------------------------------
coral_root = bpy.data.objects.new("coral", None)
main_col.objects.link(coral_root)
coral_root.location = (0.5, 0.5, 0.0)   # bottom placed on floor at z=0
ll3m.set_ll3m_properties(coral_root, ll3m_id="coral", ll3m_role="decoration")

trunk = ll3m.add_cylinder("coral_trunk", radius=0.045, depth=0.45, vertices_count=16,
                          collection=main_col, material=mat_coral)
trunk.parent = coral_root
trunk.location = (0.0, 0.0, 0.25)
ll3m.set_ll3m_properties(trunk, ll3m_part="trunk")

branch_defs = [
    ((0.0, 0.0, 0.42), (0.15, 0.0, 0.0), 0.018, 0.25),
    ((0.0, 0.0, 0.42), (-0.15, 0.0, 0.0), 0.018, 0.25),
    ((0.0, 0.0, 0.43), (0.0, 0.2, 0.0), 0.017, 0.23),
    ((0.0, 0.0, 0.43), (0.0, -0.2, 0.0), 0.017, 0.23),
    ((0.03, 0.0, 0.30), (0.1, 0.5, 0.1), 0.015, 0.22),
    ((-0.03, 0.0, 0.30), (-0.1, 0.8, -0.1), 0.015, 0.22),
    ((0.0, 0.03, 0.30), (0.0, 1.2, 0.0), 0.015, 0.2),
    ((0.0, -0.03, 0.30), (0.0, -1.5, 0.0), 0.015, 0.2),
    ((0.02, 0.02, 0.38), (0.4, 0.9, 0.3), 0.012, 0.18),
    ((-0.02, -0.02, 0.38), (-0.4, -1.0, -0.3), 0.012, 0.18),
    ((-0.02, 0.02, 0.38), (-0.3, 1.1, 0.0), 0.012, 0.18),
    ((0.02, -0.02, 0.38), (0.3, -1.2, 0.0), 0.012, 0.18),
]
for i, (loc, rot_euler, radius, depth) in enumerate(branch_defs):
    b = ll3m.add_cylinder(f"coral_branch_{i}", radius=radius, depth=depth,
                          vertices_count=10, collection=main_col, material=mat_coral)
    b.parent = coral_root
    b.location = Vector(loc)
    b.rotation_euler = rot_euler
    ll3m.set_ll3m_properties(b, ll3m_part="branch")

# -------------------------------------------------------------------
# 10. Fish – detailed tropical fish with scales, fins, tail, eyes, mouth
# -------------------------------------------------------------------
fish_root = bpy.data.objects.new("fish", None)
main_col.objects.link(fish_root)
fish_root.location = (0.0, -2.0, 1.5)
fish_root.rotation_euler = (0.0, 0.0, 0.0)
ll3m.set_ll3m_properties(fish_root, ll3m_id="fish", ll3m_role="primary")

# Body – elongated sphere matching dimensions (0.5 x 0.15 x 0.15)
body = ll3m.add_uv_sphere("fish_body", radius=0.2, segments=20, rings=20,
                          collection=main_col, material=mat_fish)
body.parent = fish_root
body.location = (0.0, 0.0, 0.0)
body.scale = (1.25, 0.375, 0.375)
ll3m.set_ll3m_properties(body, ll3m_part="body")

# Eyes – two small black spheres
eye_left = ll3m.add_uv_sphere("fish_eye_L", radius=0.018, segments=12, rings=12,
                               collection=main_col, material=mat_eye)
eye_left.parent = fish_root
eye_left.location = (0.15, 0.045, 0.06)

eye_right = ll3m.add_uv_sphere("fish_eye_R", radius=0.018, segments=12, rings=12,
                                collection=main_col, material=mat_eye)
eye_right.parent = fish_root
eye_right.location = (0.15, -0.045, 0.06)

# Mouth – small dark cylinder
mouth = ll3m.add_cylinder("fish_mouth", radius=0.015, depth=0.02, vertices_count=8,
                          collection=main_col, material=mat_eye)
mouth.parent = fish_root
mouth.location = (0.24, 0.0, 0.02)
mouth.rotation_euler = (0.0, 1.5708, 0.0)

# Tail fin – forked shape
tail_verts = [
    (0.0, 0.0, 0.0),
    (-0.18, 0.0, 0.14),
    (-0.18, 0.0, -0.14),
    (-0.06, 0.0, 0.0),
]
tail_faces = [(0, 1, 3), (0, 3, 2)]
tail_obj = ll3m.create_mesh_object("fish_tail", tail_verts, tail_faces,
                                   collection=main_col, material=mat_fish)
tail_obj.parent = fish_root
tail_obj.location = (-0.25, 0.0, 0.0)
ll3m.set_ll3m_properties(tail_obj, ll3m_part="tail")

# Dorsal fin
dorsal_verts = [
    (0.0, 0.0, 0.0),
    (-0.08, 0.0, 0.12),
    (-0.14, 0.0, 0.04),
    (-0.16, 0.0, 0.0),
]
dorsal_faces = [(0, 1, 3), (1, 2, 3)]
dorsal = ll3m.create_mesh_object("fish_dorsal", dorsal_verts, dorsal_faces,
                                 collection=main_col, material=mat_fish)
dorsal.parent = fish_root
dorsal.location = (0.05, 0.0, 0.075)
ll3m.set_ll3m_properties(dorsal, ll3m_part="dorsal_fin")

# Pectoral fins
for y_sign, name in [(0.065, "fish_pectoral_left"), (-0.065, "fish_pectoral_right")]:
    p_verts = [(0, 0, 0), (0.0, 0.09 * (1 if y_sign > 0 else -1), 0.0), (0.0, 0.04 * (1 if y_sign > 0 else -1), 0.07)]
    p_faces = [(0, 1, 2)]
    pfin = ll3m.create_mesh_object(name, p_verts, p_faces,
                                   collection=main_col, material=mat_fish)
    pfin.parent = fish_root
    pfin.location = (0.0, 0.0, -0.02)
    pfin.rotation_euler = (0, 0, 0) if y_sign > 0 else (0, 0, 3.14159)
    ll3m.set_ll3m_properties(pfin, ll3m_part="pectoral_fin")

# Pelvic fins
for y_sign, name in [(0.06, "fish_pelvic_left"), (-0.06, "fish_pelvic_right")]:
    pel_verts = [(0, 0, 0), (0.0, 0.08 * (1 if y_sign > 0 else -1), 0.0), (0.0, 0.03 * (1 if y_sign > 0 else -1), 0.05)]
    pel_faces = [(0, 1, 2)]
    pelvin = ll3m.create_mesh_object(name, pel_verts, pel_faces,
                                     collection=main_col, material=mat_fish)
    pelvin.parent = fish_root
    pelvin.location = (0.02, 0.0, -0.06)
    pelvin.rotation_euler = (0, 0, 0) if y_sign > 0 else (0, 0, 3.14159)
    ll3m.set_ll3m_properties(pelvin, ll3m_part="pelvic_fin")

# -------------------------------------------------------------------
# 11. Cameras
# -------------------------------------------------------------------
cam_main = ll3m.add_camera("cam_three_quarter", location=(4, -4, 4),
                           look_at_target=(0.0, 0.0, 1.5), lens=24,
                           collection=main_col, make_active=True)
ll3m.add_camera("cam_side", location=(0, -5, 2), look_at_target=(0, 0, 1.5),
                lens=28, collection=main_col, make_active=False)
ll3m.add_camera("cam_top", location=(0, 0, 7), look_at_target=(0, 0, 1.5),
                lens=35, collection=main_col, make_active=False)
ll3m.add_camera("cam_fish_close", location=(0, -2.8, 2.5),
                look_at_target=(0, -2.0, 1.5), lens=50,
                collection=main_col, make_active=False)

# -------------------------------------------------------------------
# 12. Animation – fish swimming along path
# -------------------------------------------------------------------
def set_fish_transform(frame, loc, rot_euler=(0, 0, 0)):
    fish_root.location = loc
    fish_root.rotation_euler = rot_euler
    fish_root.keyframe_insert(data_path="location", index=-1, frame=frame)
    fish_root.keyframe_insert(data_path="rotation_euler", index=-1, frame=frame)

# Path keyframes (from IR)
kf1 = Vector((0.0, -2.0, 1.5))
kf2 = Vector((1.0, 0.0, 2.0))
kf3 = Vector((0.0, 2.0, 1.5))

# Compute yaw angles from direction vectors (2D signed angle)
dir12 = kf2 - kf1
dir23 = kf3 - kf2

rot1_z = math.atan2(dir12.y, dir12.x)
rot2_z = math.atan2(dir23.y, dir23.x)
rot_mid_z = (rot1_z + rot2_z) / 2.0

set_fish_transform(1, kf1, (0, 0, rot1_z))
set_fish_transform(50, kf2, (0, 0, rot_mid_z))
set_fish_transform(100, kf3, (0, 0, rot2_z))

# -------------------------------------------------------------------
# 13. Metadata
# -------------------------------------------------------------------
LL3M_METADATA = {
    "objects": {
        "fish": "fish",
        "sea_floor": "sea_floor",
        "sea_volume": "sea_volume",
        "seaweed": "seaweed",
        "rock": "rock",
        "coral": "coral",
    },
}

# Ensure active camera is set
if bpy.context.scene.camera is None:
    for cam_obj in bpy.data.objects:
        if cam_obj.type == "CAMERA":
            bpy.context.scene.camera = cam_obj
            break

# LL3M deterministic static support repair
import json as _ll3m_static_repair_json
from mathutils import Vector as _ll3m_static_repair_Vector
import bpy as _ll3m_static_repair_bpy

_LL3M_STATIC_SUPPORT_REPAIR_PLAN = _ll3m_static_repair_json.loads('{"adjustments": [{"delta": [0.0, 0.0, -0.30000001192092896], "notes": ["deterministic static support repair", "support top from scene graph bbox"], "overlap_after": [0.1637355089187622, 0.10000002384185791], "overlap_before": [0.1637355089187622, 0.10000002384185791], "relation_id": "rel_coral_on_floor", "subject_bottom_after": 0.0, "subject_bottom_before": 0.30000001192092896, "subject_id": "coral", "support_id": "sea_floor", "support_top": 0.0}], "applied": true, "skipped": []}')

def _ll3m_static_repair_find_objects(ll3m_id):
    marker = str(ll3m_id)
    matches = []
    def add_with_descendants(obj):
        if obj not in matches:
            matches.append(obj)
        stack = list(getattr(obj, "children", []))
        while stack:
            child = stack.pop(0)
            if child not in matches:
                matches.append(child)
            stack.extend(list(getattr(child, "children", [])))
    exact = _ll3m_static_repair_bpy.data.objects.get(marker)
    if exact:
        add_with_descendants(exact)
    for obj in _ll3m_static_repair_bpy.data.objects:
        obj_id = str(obj.get("ll3m_id", ""))
        if obj not in matches and (obj_id == marker or obj_id.startswith(marker + "_")):
            add_with_descendants(obj)
    for obj in _ll3m_static_repair_bpy.data.objects:
        if obj not in matches and obj.name.startswith(marker):
            add_with_descendants(obj)
    return matches

def _ll3m_static_repair_apply_delta(subject_id, support_id, delta):
    objects = _ll3m_static_repair_find_objects(subject_id)
    if not objects:
        return
    _ll3m_static_repair_normalize_child_offsets(objects)
    before_locations = {obj: obj.matrix_world.translation.copy() for obj in objects}
    exact_roots = [
        obj for obj in objects
        if str(obj.get("ll3m_id", "")) == str(subject_id) and obj.parent is None
    ]
    exact = [obj for obj in objects if str(obj.get("ll3m_id", "")) == str(subject_id)]
    targets = exact_roots or exact[:1] or objects
    vector = _ll3m_static_repair_current_delta(
        subject_id,
        delta,
        support_id,
    )
    frame = int(_ll3m_static_repair_bpy.context.scene.frame_current)
    for obj in targets:
        obj.location = obj.location + vector
        _ll3m_static_repair_shift_location_keyframes(obj, vector, frame=frame)
    _ll3m_static_repair_bpy.context.view_layer.update()
    for obj in objects:
        before = before_locations.get(obj)
        if before is None:
            continue
        observed = obj.matrix_world.translation - before
        remainder = vector - observed
        if max(abs(float(remainder.x)), abs(float(remainder.y)), abs(float(remainder.z))) <= 1e-6:
            continue
        obj.matrix_world.translation = obj.matrix_world.translation + remainder
        _ll3m_static_repair_shift_location_keyframes(obj, remainder, frame=frame)

def _ll3m_static_repair_normalize_child_offsets(objects):
    roots = [obj for obj in objects if str(obj.get("ll3m_id", "")) and obj.parent is None]
    root = roots[0] if roots else (objects[0] if objects else None)
    if root is None:
        return
    direct_children = [obj for obj in objects if obj is not root and obj.parent == root]
    if not direct_children:
        return
    center = _ll3m_static_repair_Vector((0.0, 0.0, 0.0))
    for child in direct_children:
        center += child.location
    center /= len(direct_children)
    root_extent = max([float(value) for value in getattr(root, "dimensions", (0.0, 0.0, 0.0)) if float(value) >= 0.0] or [0.0])
    if center.length <= max(root_extent * 0.75, 0.25):
        return
    bbox = _ll3m_static_repair_world_bbox(objects)
    reference = (bbox[0] + bbox[1]) * 0.5 if bbox else root.matrix_world.translation
    threshold = max(root_extent * 2.0, 1.0)
    if (center - reference).length > threshold:
        return
    try:
        basis = root.matrix_world.to_3x3().inverted()
    except Exception:
        basis = None
    for child in direct_children:
        offset = child.location - center
        child.location = basis @ offset if basis is not None else offset
    _ll3m_static_repair_bpy.context.view_layer.update()

def _ll3m_static_repair_world_bbox(objects):
    _ll3m_static_repair_bpy.context.view_layer.update()
    points = []
    depsgraph = _ll3m_static_repair_bpy.context.evaluated_depsgraph_get()
    for obj in objects:
        if obj.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META"} or not getattr(obj, "bound_box", None):
            continue
        evaluated = obj.evaluated_get(depsgraph)
        points.extend(evaluated.matrix_world @ _ll3m_static_repair_Vector(corner) for corner in evaluated.bound_box)
    if not points:
        return None
    return (
        _ll3m_static_repair_Vector((min(point.x for point in points), min(point.y for point in points), min(point.z for point in points))),
        _ll3m_static_repair_Vector((max(point.x for point in points), max(point.y for point in points), max(point.z for point in points))),
    )

def _ll3m_static_repair_axis_delta(subject_min, subject_max, support_min, support_max, axis, margin=0.02):
    overlap = min(subject_max[axis], support_max[axis]) - max(subject_min[axis], support_min[axis])
    if overlap > 0:
        return 0.0
    subject_size = subject_max[axis] - subject_min[axis]
    subject_half = subject_size * 0.5
    subject_center = (subject_min[axis] + subject_max[axis]) * 0.5
    support_center = (support_min[axis] + support_max[axis]) * 0.5
    low = support_min[axis] + subject_half + margin
    high = support_max[axis] - subject_half - margin
    target_center = min(max(subject_center, low), high) if low <= high else support_center
    return target_center - subject_center

def _ll3m_static_repair_current_delta(subject_id, fallback_delta, support_id):
    vector = _ll3m_static_repair_Vector(tuple(float(value) for value in fallback_delta))
    if not support_id:
        return vector
    subject_bbox = _ll3m_static_repair_world_bbox(_ll3m_static_repair_find_objects(subject_id))
    support_bbox = _ll3m_static_repair_world_bbox(_ll3m_static_repair_find_objects(support_id))
    if not subject_bbox or not support_bbox:
        return vector
    subject_min, subject_max = subject_bbox
    support_min, support_max = support_bbox
    return _ll3m_static_repair_Vector((
        _ll3m_static_repair_axis_delta(subject_min, subject_max, support_min, support_max, 0),
        _ll3m_static_repair_axis_delta(subject_min, subject_max, support_min, support_max, 1),
        float(support_max.z - subject_min.z),
    ))

def _ll3m_static_repair_shift_location_keyframes(obj, vector, frame=None):
    action = obj.animation_data.action if obj.animation_data else None
    if not action:
        return
    fcurves = []
    seen_fcurves = set()
    if hasattr(action, "fcurves"):
        for fcurve in action.fcurves:
            marker = fcurve.as_pointer() if hasattr(fcurve, "as_pointer") else id(fcurve)
            if marker not in seen_fcurves:
                seen_fcurves.add(marker)
                fcurves.append(fcurve)
    if hasattr(action, "layers"):
        for layer in action.layers:
            for strip in getattr(layer, "strips", []):
                for bag in getattr(strip, "channelbags", []):
                    for fcurve in getattr(bag, "fcurves", []):
                        marker = fcurve.as_pointer() if hasattr(fcurve, "as_pointer") else id(fcurve)
                        if marker not in seen_fcurves:
                            seen_fcurves.add(marker)
                            fcurves.append(fcurve)
    for fcurve in fcurves:
        if fcurve.data_path != "location" or fcurve.array_index not in (0, 1, 2):
            continue
        offset = float(vector[fcurve.array_index])
        if abs(offset) <= 1e-12:
            continue
        for point in fcurve.keyframe_points:
            if frame is not None and abs(float(point.co.x) - float(frame)) > 0.5:
                continue
            point.co.y += offset
            point.handle_left.y += offset
            point.handle_right.y += offset
        fcurve.update()

for _ll3m_static_repair_adjustment in _LL3M_STATIC_SUPPORT_REPAIR_PLAN.get("adjustments", []):
    _ll3m_static_repair_apply_delta(
        _ll3m_static_repair_adjustment.get("subject_id"),
        _ll3m_static_repair_adjustment.get("support_id"),
        _ll3m_static_repair_adjustment.get("delta", [0.0, 0.0, 0.0]),
    )

_ll3m_static_repair_bpy.context.view_layer.update()
