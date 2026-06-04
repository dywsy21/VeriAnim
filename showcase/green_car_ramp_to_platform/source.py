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

import blender.verianim_utils as verianim
import bpy
import math

# ── Clear and prepare ──────────────────────────────────────────────
scene = verianim.clear_scene()
main_col = verianim.create_collection("Scene_Main")

# ── Materials ──────────────────────────────────────────────────────
floor_mat    = verianim.make_material("floor_mat",    base_color=(0.5, 0.5, 0.5, 1.0))
road_mat     = verianim.make_material("road_mat",     base_color=(0.2, 0.2, 0.2, 1.0))
platform_mat = verianim.make_material("platform_mat", base_color=(0.0, 0.2, 0.8, 1.0))
car_mat      = verianim.make_material("car_mat",      base_color=(0.0, 0.8, 0.2, 1.0))
wheel_mat    = verianim.make_material("wheel_mat",    base_color=(0.1, 0.1, 0.1, 1.0))

# ── Floor ──────────────────────────────────────────────────────────
floor = verianim.add_plane("floor", size=12.0, collection=main_col,
                       material=floor_mat, location=(0, 0, 0))
verianim.set_verianim_properties(floor, verianim_id="floor", verianim_role="support")

# ── Road ───────────────────────────────────────────────────────────
road_length   = 4.0   # x from -4 to 0
road_width    = 2.0
road_thickness = 0.1  # top at z=0 → center z = -thickness/2
road = verianim.add_cube("road", size=1.0, collection=main_col, material=road_mat)
road.scale = (road_length, road_width, road_thickness)
road.location = (-2.0, 0, -road_thickness / 2)
verianim.set_verianim_properties(road, verianim_id="road", verianim_role="support")

# ── Platform ───────────────────────────────────────────────────────
plat_length = 3.0
plat_width  = 2.0
plat_height = 1.0   # top at z=1.0 → center z = 0.5
platform = verianim.add_cube("platform", size=1.0, collection=main_col,
                         material=platform_mat)
platform.scale = (plat_length, plat_width, plat_height)
# ramp ends at x=2, so platform starts at x=2; center x = 2 + 1.5 = 3.5
platform.location = (3.5, 0, plat_height / 2)
verianim.set_verianim_properties(platform, verianim_id="platform", verianim_role="support")

# ── Ramp ───────────────────────────────────────────────────────────
ramp_horiz  = 2.0
ramp_rise   = 1.0
ramp_length = math.sqrt(ramp_horiz ** 2 + ramp_rise ** 2)  # ≈ 2.236 m
ramp_width  = 2.0
ramp_thick  = 0.1
ramp_angle  = math.atan2(ramp_rise, ramp_horiz)            # ≈ 26.565°

ramp = verianim.add_cube("ramp", size=1.0, collection=main_col, material=road_mat)
ramp.scale = (ramp_length, ramp_width, ramp_thick)
ramp.location = (ramp_horiz / 2, 0, ramp_rise / 2)        # (1, 0, 0.5)
# *** FIX ORIENTATION: the ramp must ascend in +x ***
ramp.rotation_euler = (0, -ramp_angle, 0)   # negative angle makes it ascend
verianim.set_verianim_properties(ramp, verianim_id="ramp", verianim_role="support")

# ── Car ────────────────────────────────────────────────────────────
car_len   = 0.4
car_wid   = 0.2
car_ht    = 0.15
body_ht   = 0.08
whl_rad   = 0.035
whl_depth = 0.03

# parent empty – will be placed exactly at the wheel contact point (bottom)
car_root = bpy.data.objects.new("car", None)
car_root.location = (-2.0, 0, 0.001)        # root at bottom + 0.001 gap
car_root.empty_display_type = 'PLAIN_AXES'
main_col.objects.link(car_root)
verianim.set_verianim_properties(car_root, verianim_id="car", verianim_role="primary")

# body – shifted up so the original shape is preserved
car_body = verianim.add_cube("car_body", size=1.0, collection=main_col,
                         material=car_mat)
car_body.scale = (car_len, car_wid * 0.75, body_ht)
body_offset_z = (body_ht / 2 + whl_rad) - 0.001   # original root height minus new root z
car_body.location = (0, 0, body_offset_z)
car_body.parent = car_root
verianim.set_verianim_properties(car_body, verianim_part="body")

# four wheels – adjust local z so they sit on the road with the new root
whl_local_z = -body_ht / 2 + body_offset_z   # -0.04 + 0.074 = 0.034
whl_positions = [
    ( car_len / 2 - whl_rad,  car_wid / 2 - 0.01, whl_local_z),
    ( car_len / 2 - whl_rad, -car_wid / 2 + 0.01, whl_local_z),
    (-car_len / 2 + whl_rad,  car_wid / 2 - 0.01, whl_local_z),
    (-car_len / 2 + whl_rad, -car_wid / 2 + 0.01, whl_local_z),
]

for i, (wx, wy, wz) in enumerate(whl_positions):
    whl = verianim.add_cylinder(f"car_wheel_{i}", radius=whl_rad, depth=whl_depth,
                            collection=main_col, material=wheel_mat)
    whl.location = (wx, wy, wz)
    whl.rotation_euler = (math.pi / 2, 0, 0)   # axle along Y
    whl.parent = car_root
    verianim.set_verianim_properties(whl, verianim_part="wheel")

# ── Camera ─────────────────────────────────────────────────────────
camera = verianim.add_camera(
    name="camera_main",
    location=(0, -7, 3.5),
    look_at_target=(0.5, 0, 0.6),
    lens=35,
    collection=main_col,
    make_active=True,
)
bpy.context.scene.camera = camera

# ── Light ──────────────────────────────────────────────────────────
key_light = verianim.add_light(
    name="key_light",
    light_type="AREA",
    location=(2, -5, 4),
    energy=500,
    size=5.0,
    color=(1, 1, 1),
    collection=main_col,
)

# ── Render + animation setup ───────────────────────────────────────
verianim.configure_render(scene, engine="workbench")
scene.frame_start = 1
scene.frame_end   = 120
scene.render.fps  = 24

# ── Animation keyframes (car_root) ─────────────────────────────────
# helper for ramp top z at world x (ascending ramp, top face)
def ramp_top_z(x):
    # plane equation: -sin*(x-1.02236) + cos*(z-0.54472) = 0  with angle negative
    sin = math.sin(-ramp_angle)   # ≈ -0.4472
    cos = math.cos(-ramp_angle)   # ≈ 0.8944
    # centre of +Z face: (1.02236, 0, 0.54472)
    return 0.54472 - (sin/cos)*(x - 1.02236)   # sin/cos = tan(-a) = -0.5
# actually simplify: sin/cos = tan(-26.565°) ≈ -0.5
# So z ≈ 0.54472 + 0.5*(x - 1.02236) = 0.54472 + 0.5x - 0.51118 = 0.5x + 0.03354

# start frame (on road)
car_root.location = (-2, 0, 0.001)
car_root.rotation_euler = (0, 0, 0)
car_root.keyframe_insert(data_path="location", frame=1)
car_root.keyframe_insert(data_path="rotation_euler", frame=1)

# move to edge of road (still on road)
car_root.location = (-0.2, 0, 0.001)
car_root.rotation_euler = (0, 0, 0)
car_root.keyframe_insert(data_path="location", frame=27)
car_root.keyframe_insert(data_path="rotation_euler", frame=27)

# onto ramp (frame 28, sampled)
x_r = 0.2   # fully on ramp
z_r = ramp_top_z(x_r) + 0.001   # contact + gap
car_root.location = (x_r, 0, z_r)
car_root.rotation_euler = (0, ramp_angle, 0)   # tilt up to match slope
car_root.keyframe_insert(data_path="location", frame=28)
car_root.keyframe_insert(data_path="rotation_euler", frame=28)

# intermediate on ramp (frame 60, sampled)
x_r = 1.0
z_r = ramp_top_z(x_r) + 0.001
car_root.location = (x_r, 0, z_r)
car_root.rotation_euler = (0, ramp_angle, 0)
car_root.keyframe_insert(data_path="location", frame=60)
car_root.keyframe_insert(data_path="rotation_euler", frame=60)

# near top of ramp (frame 87, sampled – still on ramp)
x_r = 1.8
z_r = ramp_top_z(x_r) + 0.001
car_root.location = (x_r, 0, z_r)
car_root.rotation_euler = (0, ramp_angle, 0)
car_root.keyframe_insert(data_path="location", frame=87)
car_root.keyframe_insert(data_path="rotation_euler", frame=87)

# onto platform (frame 88, sampled)
car_root.location = (3.5, 0, 1.001)   # exact final root coordinate per AnimationSpec
car_root.rotation_euler = (0, 0, 0)   # level
car_root.keyframe_insert(data_path="location", frame=88)
car_root.keyframe_insert(data_path="rotation_euler", frame=88)

# final rest (frame 120)
car_root.location = (3.5, 0, 1.001)
car_root.rotation_euler = (0, 0, 0)
car_root.keyframe_insert(data_path="location", frame=120)
car_root.keyframe_insert(data_path="rotation_euler", frame=120)

# set interpolation for all new f-curves (linear looks mechanical, which fits toy car)
for fc in verianim_iter_action_fcurves(car_root.animation_data.action):
    for kp in fc.keyframe_points:
        kp.interpolation = 'LINEAR'

# ── Metadata ───────────────────────────────────────────────────────
VERIANIM_METADATA = {
    "objects": {
        "floor":    floor.name,
        "road":     road.name,
        "platform": platform.name,
        "ramp":     ramp.name,
        "car":      car_root.name,
    },
    "materials": {
        "floor_mat":    floor_mat.name,
        "road_mat":     road_mat.name,
        "platform_mat": platform_mat.name,
        "car_mat":      car_mat.name,
        "wheel_mat":    wheel_mat.name,
    },
    "cameras": {
        "camera_main": camera.name,
    },
    "lights": {
        "key_light": key_light.name,
    },
}

if bpy.context.scene.camera is None:
    for _verianim_camera in bpy.data.objects:
        if _verianim_camera.type == "CAMERA":
            bpy.context.scene.camera = _verianim_camera
            break



# VERIANIM deterministic animation path repair
import json as _verianim_repair_json
import bpy as _verianim_repair_bpy
from mathutils import Vector as _verianim_repair_Vector

_VERIANIM_ANIMATION_REPAIR_PLAN = _verianim_repair_json.loads('{"applied": true, "plans": [{"end_frame": 120, "event_id": "car_drive", "keyframes": [{"frame": 1, "label": "centered on support road", "location": [-2.0, 0.0, 0.001]}, {"frame": 27, "label": "centered on support road", "location": [-2.0, 0.0, 0.001]}, {"frame": 28, "label": "centered on support ramp", "location": [0.9999999413266778, 0.0, 1.0457213649749755]}, {"frame": 87, "label": "centered on support ramp", "location": [0.9999999413266778, 0.0, 1.0457213649749755]}, {"frame": 88, "label": "centered on support platform", "location": [3.5, 0.0, 1.001]}, {"frame": 120, "label": "centered on support platform", "location": [3.5, 0.0, 1.001]}], "lane_axis": "y", "notes": ["deterministic support sequence repair from scene graph support centers"], "start_frame": 1, "subject_id": "car", "support_end_frame": 87, "support_id": "ramp", "support_start_frame": 28, "travel_axis": "x"}], "skipped": []}')

def _verianim_repair_descendants(obj):
    found = []
    stack = list(getattr(obj, "children", []))
    while stack:
        child = stack.pop(0)
        if child in found:
            continue
        found.append(child)
        stack.extend(list(getattr(child, "children", [])))
    return found

def _verianim_repair_add_match(matches, obj):
    if obj not in matches:
        matches.append(obj)
    for child in _verianim_repair_descendants(obj):
        if child not in matches:
            matches.append(child)

def _verianim_repair_find_objects(verianim_id):
    marker = str(verianim_id)
    matches = []
    exact = _verianim_repair_bpy.data.objects.get(marker)
    if exact:
        _verianim_repair_add_match(matches, exact)
    for obj in _verianim_repair_bpy.data.objects:
        obj_id = str(obj.get("verianim_id", ""))
        if obj not in matches and (obj_id == marker or obj_id.startswith(marker + "_")):
            _verianim_repair_add_match(matches, obj)
    for obj in _verianim_repair_bpy.data.objects:
        if obj not in matches and obj.name.startswith(marker):
            _verianim_repair_add_match(matches, obj)
    return matches

def _verianim_repair_iter_action_fcurves(action):
    if not action:
        return
    seen = set()
    if hasattr(action, "fcurves"):
        for fcurve in action.fcurves:
            marker = fcurve.as_pointer() if hasattr(fcurve, "as_pointer") else id(fcurve)
            if marker not in seen:
                seen.add(marker)
                yield action.fcurves, fcurve
    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            for bag in getattr(strip, "channelbags", []):
                collection = getattr(bag, "fcurves", None)
                if collection:
                    for fcurve in collection:
                        marker = fcurve.as_pointer() if hasattr(fcurve, "as_pointer") else id(fcurve)
                        if marker not in seen:
                            seen.add(marker)
                            yield collection, fcurve

def _verianim_repair_remove_fcurve(collection, fcurve):
    try:
        collection.remove(fcurve)
        return
    except Exception:
        pass
    try:
        while fcurve.keyframe_points:
            fcurve.keyframe_points.remove(fcurve.keyframe_points[0], fast=True)
        fcurve.update()
    except Exception:
        pass

def _verianim_repair_clear_location_animation(obj):
    if obj.animation_data and obj.animation_data.action:
        for collection, fcurve in list(_verianim_repair_iter_action_fcurves(obj.animation_data.action)):
            if fcurve.data_path == "location":
                _verianim_repair_remove_fcurve(collection, fcurve)

def _verianim_repair_normalize_child_offsets(root, objects, reference_location):
    direct_children = [obj for obj in objects if obj is not root and obj.parent == root]
    if not direct_children:
        return
    center = _verianim_repair_Vector((0.0, 0.0, 0.0))
    for child in direct_children:
        center += child.location
    center /= len(direct_children)
    reference = _verianim_repair_Vector(tuple(float(value) for value in reference_location))
    root_extent = max(float(value) for value in getattr(root, "dimensions", (1.0, 1.0, 1.0)) if float(value) >= 0.0)
    threshold = max(root_extent * 2.0, 10.0)
    if center.length <= max(root_extent * 0.75, 0.25):
        return
    if (center - reference).length > threshold:
        return
    try:
        basis = root.matrix_world.to_3x3().inverted()
    except Exception:
        basis = None
    for child in direct_children:
        offset = child.location - center
        child.location = basis @ offset if basis is not None else offset

def _verianim_repair_world_bbox(objects):
    _verianim_repair_bpy.context.view_layer.update()
    points = []
    for obj in objects:
        if obj.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META"} or not getattr(obj, "bound_box", None):
            continue
        points.extend(obj.matrix_world @ _verianim_repair_Vector(corner) for corner in obj.bound_box)
    if not points:
        return None
    return (
        _verianim_repair_Vector((min(point.x for point in points), min(point.y for point in points), min(point.z for point in points))),
        _verianim_repair_Vector((max(point.x for point in points), max(point.y for point in points), max(point.z for point in points))),
    )

def _verianim_repair_root_to_bottom(root, objects):
    bbox = _verianim_repair_world_bbox(objects)
    if not bbox:
        return 0.0
    return float(root.matrix_world.translation.z - bbox[0].z)

def _verianim_repair_support_top(support_id):
    support_objects = _verianim_repair_find_objects(support_id)
    bbox = _verianim_repair_world_bbox(support_objects)
    if not bbox:
        return None
    return float(bbox[1].z)

def _verianim_repair_recalibrate_keyframes(plan, root, objects):
    keyframes = list(plan.get("keyframes", []))
    support_top = _verianim_repair_support_top(plan.get("support_id"))
    root_to_bottom = _verianim_repair_root_to_bottom(root, objects)
    support_z = support_top + root_to_bottom + 0.001 if support_top is not None else None
    support_start = int(plan.get("support_start_frame", 0))
    support_end = int(plan.get("support_end_frame", 0))
    for keyframe in keyframes:
        frame = int(keyframe.get("frame", 0))
        location = list(keyframe.get("location", [0.0, 0.0, 0.0]))
        label = str(keyframe.get("label", ""))
        label_prefix = "centered on support "
        if label.startswith(label_prefix):
            label_support_top = _verianim_repair_support_top(label[len(label_prefix):].strip())
            if label_support_top is not None:
                location[2] = label_support_top + root_to_bottom + 0.001
                keyframe["location"] = location
                continue
        if support_z is not None and support_start <= frame <= support_end:
            location[2] = support_z
            keyframe["location"] = location
    return keyframes

def _verianim_repair_insert_location(obj, location, frame):
    obj.location = tuple(location)
    obj.keyframe_insert(data_path="location", frame=int(frame))

def _verianim_repair_set_linear_location(obj):
    if obj.animation_data and obj.animation_data.action:
        for _verianim_repair_collection, _verianim_repair_fcurve in _verianim_repair_iter_action_fcurves(obj.animation_data.action):
            if _verianim_repair_fcurve.data_path == "location":
                for _verianim_repair_key in _verianim_repair_fcurve.keyframe_points:
                    _verianim_repair_key.interpolation = "LINEAR"

_verianim_repair_plan = _VERIANIM_ANIMATION_REPAIR_PLAN["plans"][0]
_verianim_repair_objects = _verianim_repair_find_objects("car")
_verianim_repair_roots = [obj for obj in _verianim_repair_objects if str(obj.get("verianim_id", "")) == "car"]
_verianim_repair_obj = (_verianim_repair_roots or _verianim_repair_objects or [None])[0]
if _verianim_repair_obj is not None:
    for _verianim_repair_clear_obj in _verianim_repair_objects:
        _verianim_repair_clear_location_animation(_verianim_repair_clear_obj)
    _verianim_repair_normalize_child_offsets(
        _verianim_repair_obj,
        _verianim_repair_objects,
        (-2.0, 0.0, 0.001),
    )
    for _verianim_repair_keyframe in _verianim_repair_recalibrate_keyframes(_verianim_repair_plan, _verianim_repair_obj, _verianim_repair_objects):
        _verianim_repair_insert_location(
            _verianim_repair_obj,
            _verianim_repair_keyframe.get("location", [0.0, 0.0, 0.0]),
            _verianim_repair_keyframe.get("frame", 1),
        )
    _verianim_repair_set_linear_location(_verianim_repair_obj)
    _verianim_repair_bpy.context.view_layer.update()

# VERIANIM deterministic animation contact repair
import json as _verianim_contact_repair_json
import bpy as _verianim_contact_repair_bpy
from mathutils import Vector as _verianim_contact_repair_Vector

_VERIANIM_ANIMATION_CONTACT_REPAIRS = _verianim_contact_repair_json.loads('{"constant_deltas": {}, "support_pairs": {"car": "ramp"}}')

def _verianim_contact_repair_find_root(verianim_id):
    marker = str(verianim_id)
    exact = _verianim_contact_repair_bpy.data.objects.get(marker)
    if exact is not None:
        return exact
    for obj in _verianim_contact_repair_bpy.data.objects:
        if str(obj.get("verianim_id", "")) == marker:
            return obj
    for obj in _verianim_contact_repair_bpy.data.objects:
        if obj.name.startswith(marker):
            return obj
    return None

def _verianim_contact_repair_iter_fcurves(action):
    if not action:
        return
    seen = set()
    if hasattr(action, "fcurves"):
        for fcurve in action.fcurves:
            marker = fcurve.as_pointer() if hasattr(fcurve, "as_pointer") else id(fcurve)
            if marker not in seen:
                seen.add(marker)
                yield fcurve
    for layer in getattr(action, "layers", []):
        for strip in getattr(layer, "strips", []):
            for bag in getattr(strip, "channelbags", []):
                for fcurve in getattr(bag, "fcurves", []):
                    marker = fcurve.as_pointer() if hasattr(fcurve, "as_pointer") else id(fcurve)
                    if marker not in seen:
                        seen.add(marker)
                        yield fcurve

def _verianim_contact_repair_shift_z(obj, dz):
    obj.location.z += float(dz)
    action = obj.animation_data.action if obj.animation_data else None
    for fcurve in _verianim_contact_repair_iter_fcurves(action):
        if fcurve.data_path != "location" or fcurve.array_index != 2:
            continue
        for point in fcurve.keyframe_points:
            point.co.y += float(dz)
            point.handle_left.y += float(dz)
            point.handle_right.y += float(dz)
        fcurve.update()

def _verianim_contact_repair_descendants(root):
    yield root
    for child in root.children:
        yield from _verianim_contact_repair_descendants(child)

def _verianim_contact_repair_bbox(root):
    corners = []
    for obj in _verianim_contact_repair_descendants(root):
        if not hasattr(obj, "bound_box") or not obj.bound_box:
            continue
        corners.extend(obj.matrix_world @ _verianim_contact_repair_Vector(corner) for corner in obj.bound_box)
    if not corners:
        loc = root.matrix_world.translation
        return (loc.x, loc.y, loc.z, loc.x, loc.y, loc.z)
    return (
        min(corner.x for corner in corners),
        min(corner.y for corner in corners),
        min(corner.z for corner in corners),
        max(corner.x for corner in corners),
        max(corner.y for corner in corners),
        max(corner.z for corner in corners),
    )

def _verianim_contact_repair_xy_overlap(a, b):
    return min(a[3], b[3]) > max(a[0], b[0]) and min(a[4], b[4]) > max(a[1], b[1])

def _verianim_contact_repair_z_fcurves(obj):
    action = obj.animation_data.action if obj.animation_data else None
    for fcurve in _verianim_contact_repair_iter_fcurves(action):
        if fcurve.data_path == "location" and fcurve.array_index == 2:
            yield fcurve

def _verianim_contact_repair_align_keyed_support(subject, support):
    scene = _verianim_contact_repair_bpy.context.scene
    fcurves = list(_verianim_contact_repair_z_fcurves(subject))
    if not fcurves:
        _verianim_contact_repair_bpy.context.view_layer.update()
        subject_box = _verianim_contact_repair_bbox(subject)
        support_box = _verianim_contact_repair_bbox(support)
        if _verianim_contact_repair_xy_overlap(subject_box, support_box):
            dz = support_box[5] - subject_box[2] + 0.001
            if abs(dz) <= 0.25:
                subject.location.z += dz
        return
    original_frame = scene.frame_current
    for fcurve in fcurves:
        for point in fcurve.keyframe_points:
            frame = int(round(point.co.x))
            scene.frame_set(frame)
            _verianim_contact_repair_bpy.context.view_layer.update()
            subject_box = _verianim_contact_repair_bbox(subject)
            support_box = _verianim_contact_repair_bbox(support)
            if not _verianim_contact_repair_xy_overlap(subject_box, support_box):
                continue
            dz = support_box[5] - subject_box[2] + 0.001
            if abs(dz) > 0.25 or abs(dz) <= 1e-5:
                continue
            point.co.y += dz
            point.handle_left.y += dz
            point.handle_right.y += dz
        fcurve.update()
    scene.frame_set(original_frame)

for _verianim_contact_repair_id, _verianim_contact_repair_support_id in _VERIANIM_ANIMATION_CONTACT_REPAIRS.get("support_pairs", {}).items():
    _verianim_contact_repair_obj = _verianim_contact_repair_find_root(_verianim_contact_repair_id)
    _verianim_contact_repair_support = _verianim_contact_repair_find_root(_verianim_contact_repair_support_id)
    if _verianim_contact_repair_obj is not None and _verianim_contact_repair_support is not None:
        _verianim_contact_repair_align_keyed_support(_verianim_contact_repair_obj, _verianim_contact_repair_support)

for _verianim_contact_repair_id, _verianim_contact_repair_dz in _VERIANIM_ANIMATION_CONTACT_REPAIRS.get("constant_deltas", {}).items():
    _verianim_contact_repair_obj = _verianim_contact_repair_find_root(_verianim_contact_repair_id)
    if _verianim_contact_repair_obj is not None:
        _verianim_contact_repair_shift_z(_verianim_contact_repair_obj, _verianim_contact_repair_dz)

_verianim_contact_repair_bpy.context.view_layer.update()
