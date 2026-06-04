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

# ------------------------------------------------------------
# Clear scene and start fresh
# ------------------------------------------------------------
scene = verianim.clear_scene()

# ------------------------------------------------------------
# Collections
# ------------------------------------------------------------
main_coll = verianim.create_collection("Scene")

# ------------------------------------------------------------
# Materials
# ------------------------------------------------------------
mat_ground = verianim.make_material("mat_ground", base_color=(0.3, 0.3, 0.3, 1.0), metallic=0.0, roughness=0.5)
mat_ground["verianim_id"] = "mat_ground"

mat_gray = verianim.make_material("mat_gray", base_color=(0.5, 0.5, 0.5, 1.0), metallic=0.0, roughness=0.5)
mat_gray["verianim_id"] = "mat_gray"

mat_blue = verianim.make_material("mat_blue", base_color=(0.0, 0.0, 0.8, 1.0), metallic=0.0, roughness=0.5)
mat_blue["verianim_id"] = "mat_blue"

mat_yellow = verianim.make_material("mat_yellow", base_color=(1.0, 0.8, 0.0, 1.0), metallic=0.0, roughness=0.5)
mat_yellow["verianim_id"] = "mat_yellow"

# ------------------------------------------------------------
# Ground plane (top at z=0)
# ------------------------------------------------------------
ground = verianim.add_plane(
    "ground",
    size=2.0,
    collection=main_coll,
    material=mat_ground,
    location=(0.0, 0.0, 0.0)  # Plane at z=0, top is exactly 0
)
verianim.set_verianim_properties(ground, verianim_id="ground", verianim_role="support")

# ------------------------------------------------------------
# Table (box, bottom at z=0, top at z=0.1)
# ------------------------------------------------------------
table = verianim.add_cube(
    "table",
    size=1.0,  # we will scale
    collection=main_coll,
    material=mat_gray,
    location=(0.0, 0.0, 0.0)  # temporary; we'll move after scaling
)
# dimensions: 0.5 x 0.3 x 0.1
table.scale = (0.5, 0.3, 0.1)
# Anchor: bottom_center. Half-height = 0.05. To have bottom at z=0, z = 0.05.
table.location = (-0.15, 0.0, 0.05)
verianim.set_verianim_properties(table, verianim_id="table", verianim_role="support")

# ------------------------------------------------------------
# Platform (box, bottom at z=0, top at z=0.03)
# ------------------------------------------------------------
platform = verianim.add_cube(
    "platform",
    size=1.0,
    collection=main_coll,
    material=mat_blue,
    location=(0.0, 0.0, 0.0)
)
platform.scale = (0.3, 0.3, 0.03)
platform.location = (0.644, 0.0, 0.015)  # bottom at 0, half-height 0.015
verianim.set_verianim_properties(platform, verianim_id="platform", verianim_role="support")

# ------------------------------------------------------------
# Ramp (slanted box)
# ------------------------------------------------------------
ramp = verianim.add_cube(
    "ramp",
    size=1.0,
    collection=main_coll,
    material=mat_gray,
    location=(0.0, 0.0, 0.0)
)
ramp.scale = (0.4, 0.3, 0.02)
# rotation as given: euler y=0.173 rad
import mathutils
ramp.rotation_euler = (0.0, 0.173, 0.0)
ramp.location = (0.297, 0.0, 0.065)
verianim.set_verianim_properties(ramp, verianim_id="ramp", verianim_role="support")

# ------------------------------------------------------------
# Puck (cylinder, flat, yellow)
# ------------------------------------------------------------
puck = verianim.add_cylinder(
    "puck",
    radius=0.1,       # diameter 0.2
    depth=0.02,       # height 0.02
    vertices_count=32,
    collection=main_coll,
    material=mat_yellow,
    location=(0.0, 0.0, 0.0)  # temporary
)
# Cylinder is centered at origin. Bottom at table top z=0.1 => center z = 0.11.
puck.location = (-0.4, 0.0, 0.11)
verianim.set_verianim_properties(puck, verianim_id="puck")

# ------------------------------------------------------------
# Cameras
# ------------------------------------------------------------
# Overall three-quarter view
cam_overall = verianim.add_camera(
    name="cam_overall",
    location=(1.8, -2.0, 1.3),
    look_at_target=(0.2, 0.0, 0.08),
    lens=35,
    collection=main_coll,
    make_active=True
)
cam_overall["verianim_id"] = "cam_overall"

# Close-up of ramp contact area
cam_closeup = verianim.add_camera(
    name="cam_closeup",
    location=(0.35, -0.6, 0.3),
    look_at_target=(0.35, 0.0, 0.06),
    lens=50,
    collection=main_coll,
    make_active=False
)
cam_closeup["verianim_id"] = "cam_closeup"

# Side view (right side)
cam_side = verianim.add_camera(
    name="cam_side",
    location=(0.2, 2.0, 0.15),
    look_at_target=(0.2, 0.0, 0.06),
    lens=35,
    collection=main_coll,
    make_active=False
)
cam_side["verianim_id"] = "cam_side"

# Set active camera to overall
scene.camera = cam_overall

# ------------------------------------------------------------
# Lighting
# ------------------------------------------------------------
key_light = verianim.create_area_light(
    name="key_light",
    location=(-1.5, -1.5, 2.0),
    energy=500,
    size=3.0,
    color=(1.0, 1.0, 1.0)
)
verianim.link_object(key_light, collection=main_coll)
key_light["verianim_id"] = "key_light"

# ------------------------------------------------------------
# Render settings (Workbench)
# ------------------------------------------------------------
verianim.configure_render(scene, engine="workbench", fps=24)

# ------------------------------------------------------------
# Animation Setup
# ------------------------------------------------------------
scene.frame_start = 1
scene.frame_end = 120

# --- Puck slide animation ---
# Frame 1: Start on table center
scene.frame_set(1)
puck.location = (-0.15, 0.0, 0.11)
puck.rotation_euler = (0.0, 0.0, 0.0)
puck.keyframe_insert(data_path="location", frame=1)
puck.keyframe_insert(data_path="rotation_euler", frame=1)

# Frame 20: Still on table before transition
puck.location = (-0.15, 0.0, 0.11)
puck.rotation_euler = (0.0, 0.0, 0.0)
puck.keyframe_insert(data_path="location", frame=20)
puck.keyframe_insert(data_path="rotation_euler", frame=20)

# Frame 25: On ramp (tilted to match ramp slope)
puck.location = (0.297, 0.0, 0.12028)
puck.rotation_euler = (0.0, 0.173, 0.0)
puck.keyframe_insert(data_path="location", frame=25)
puck.keyframe_insert(data_path="rotation_euler", frame=25)

# Frame 60: Still on ramp before platform transition
puck.location = (0.297, 0.0, 0.12028)
puck.rotation_euler = (0.0, 0.173, 0.0)
puck.keyframe_insert(data_path="location", frame=60)
puck.keyframe_insert(data_path="rotation_euler", frame=60)

# Frame 65: On platform (flat)
puck.location = (0.644, 0.0, 0.041)
puck.rotation_euler = (0.0, 0.0, 0.0)
puck.keyframe_insert(data_path="location", frame=65)
puck.keyframe_insert(data_path="rotation_euler", frame=65)

# Frame 120: Final rest on platform
puck.location = (0.644, 0.0, 0.041)
puck.rotation_euler = (0.0, 0.0, 0.0)
puck.keyframe_insert(data_path="location", frame=120)
puck.keyframe_insert(data_path="rotation_euler", frame=120)

# Set linear interpolation for all puck keyframes
if puck.animation_data and puck.animation_data.action:
    for fcurve in verianim_iter_action_fcurves(puck.animation_data.action):
        for kf in fcurve.keyframe_points:
            kf.interpolation = 'LINEAR'

# Return to frame 1 for static verification
scene.frame_set(1)

# ------------------------------------------------------------
# Metadata
# ------------------------------------------------------------
VERIANIM_METADATA = {
    "objects": {
        "ground": ground.name,
        "table": table.name,
        "platform": platform.name,
        "ramp": ramp.name,
        "puck": puck.name
    },
    "materials": {
        "mat_ground": mat_ground.name,
        "mat_gray": mat_gray.name,
        "mat_blue": mat_blue.name,
        "mat_yellow": mat_yellow.name
    },
    "cameras": {
        "cam_overall": cam_overall.name,
        "cam_closeup": cam_closeup.name,
        "cam_side": cam_side.name
    },
    "light": key_light.name
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

_VERIANIM_ANIMATION_REPAIR_PLAN = _verianim_repair_json.loads('{"applied": true, "plans": [{"end_frame": 120, "event_id": "puck_slide", "keyframes": [{"frame": 1, "label": "centered on support table", "location": [-0.15000000596046448, 0.0, 0.11099999940395355]}, {"frame": 20, "label": "centered on support table", "location": [-0.15000000596046448, 0.0, 0.11099999940395355]}, {"frame": 25, "label": "centered on support ramp", "location": [0.2969999983906746, 0.0, 0.12027839368581772]}, {"frame": 60, "label": "centered on support ramp", "location": [0.2969999983906746, 0.0, 0.12027839368581772]}, {"frame": 65, "label": "centered on support platform", "location": [0.6440000087022781, 0.0, 0.04099999724328518]}, {"frame": 120, "label": "centered on support platform", "location": [0.6440000087022781, 0.0, 0.04099999724328518]}], "lane_axis": "y", "notes": ["deterministic support sequence repair from scene graph support centers"], "start_frame": 1, "subject_id": "puck", "support_end_frame": 60, "support_id": "ramp", "support_start_frame": 25, "travel_axis": "x"}], "skipped": []}')

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
_verianim_repair_objects = _verianim_repair_find_objects("puck")
_verianim_repair_roots = [obj for obj in _verianim_repair_objects if str(obj.get("verianim_id", "")) == "puck"]
_verianim_repair_obj = (_verianim_repair_roots or _verianim_repair_objects or [None])[0]
if _verianim_repair_obj is not None:
    for _verianim_repair_clear_obj in _verianim_repair_objects:
        _verianim_repair_clear_location_animation(_verianim_repair_clear_obj)
    _verianim_repair_normalize_child_offsets(
        _verianim_repair_obj,
        _verianim_repair_objects,
        (-0.15000000596046448, 0.0, 0.11099999940395355),
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

_VERIANIM_ANIMATION_CONTACT_REPAIRS = _verianim_contact_repair_json.loads('{"constant_deltas": {}, "support_pairs": {"puck": "ramp"}}')

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
