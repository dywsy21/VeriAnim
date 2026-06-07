import bpy
import math
import numpy as np
from mathutils import Vector
from blender import verianim_utils as verianim

# ------------------------------------------------------------
# Safe helpers (preserve existing)
# ------------------------------------------------------------
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

# ------------------------------------------------------------
# Scene and collections
# ------------------------------------------------------------
scene = verianim.clear_scene()
col_main = verianim.get_or_create_collection("Main")
col_floor = verianim.get_or_create_collection("Floor", parent=col_main)
col_pedestal = verianim.get_or_create_collection("Pedestal", parent=col_main)
col_sculpture = verianim.get_or_create_collection("Sculpture", parent=col_main)
col_lights = verianim.get_or_create_collection("Lights", parent=col_main)
col_cameras = verianim.get_or_create_collection("Cameras", parent=col_main)

# ------------------------------------------------------------
# Materials
# ------------------------------------------------------------
bronze_spec = {
    "id": "bronze",
    "base_color": (0.55, 0.32, 0.12, 1.0),
    "metallic": 0.9,
    "roughness": 0.35,
}
stone_spec = {
    "id": "stone",
    "base_color": (0.58, 0.58, 0.55, 1.0),
    "roughness": 0.8,
}
floor_spec = {
    "id": "floor_material",
    "base_color": (0.15, 0.15, 0.15, 1.0),
    "roughness": 0.9,
}
mat_bronze = verianim.make_material(bronze_spec)
mat_stone = verianim.make_material(stone_spec)
mat_floor = verianim.make_material(floor_spec)

# ------------------------------------------------------------
# Floor
# ------------------------------------------------------------
floor = verianim_safe_add_plane(
    name="Floor",
    size=20,
    collection=col_floor,
    material=mat_floor,
    location=(0, 0, 0),
)
floor["verianim_id"] = "floor"

# ------------------------------------------------------------
# Pedestal (cube centered at half height)
# Pedestal bottom at z=0, top at z=0.6
# ------------------------------------------------------------
pedestal = verianim_safe_add_cube(
    name="Pedestal",
    size=1.0,
    collection=col_pedestal,
    material=mat_stone,
    location=(0, 0, 0.3),
)
pedestal.scale = (1.2, 1.2, 0.6)
verianim.set_verianim_properties(pedestal, verianim_id="pedestal")

pedestal_top_z = 0.6

# ------------------------------------------------------------
# Sculpture – three abstract curved vertical forms (mesh tubes)
# Use a parent empty at pedestal top with verianim_id="sculpture"
# Children are mesh-based curved tubes with bronze material.
# ------------------------------------------------------------
sculpture_root = bpy.data.objects.new("sculpture_root", None)
sculpture_root.empty_display_type = 'PLAIN_AXES'
sculpture_root.location = (0.0, 0.0, pedestal_top_z)
verianim.set_verianim_properties(sculpture_root, verianim_id="sculpture")
bpy.context.collection.objects.link(sculpture_root)
col_sculpture.objects.link(sculpture_root)
if "Collection" in bpy.data.collections and sculpture_root.name in bpy.data.collections["Collection"].objects:
    bpy.data.collections["Collection"].objects.unlink(sculpture_root)

def create_curved_tube_mesh(name, base_offset_x, base_offset_y, height=1.6,
                            tube_radius=0.07, num_segments=12, num_rings=20):
    """Create a curved tube mesh using vertex data directly.
    
    The tube follows a quadratic bezier from (0,0,0) at the bottom
    to (base_offset_x, base_offset_y, height) at the top, with a
    mid control point at (base_offset_x*0.4, base_offset_y*0.4, height*0.5).
    """
    mesh = bpy.data.meshes.new(name + "_mesh")
    
    # Control points
    start = Vector((0.0, 0.0, 0.0))
    mid = Vector((base_offset_x * 0.4, base_offset_y * 0.4, height * 0.5))
    end = Vector((base_offset_x, base_offset_y, height))
    
    # Generate path points
    path_verts = []
    for i in range(num_rings + 1):
        t = i / num_rings
        # Quadratic bezier: B(t) = (1-t)^2 * P0 + 2*(1-t)*t * P1 + t^2 * P2
        p = (1 - t) ** 2 * start + 2 * (1 - t) * t * mid + t ** 2 * end
        path_verts.append(p)
    
    # Build tube vertices: for each ring, place a circle of vertices
    verts = []
    for p in path_verts:
        for j in range(num_segments):
            angle = 2 * math.pi * j / num_segments
            x = tube_radius * math.cos(angle)
            y = tube_radius * math.sin(angle)
            verts.append((p.x + x, p.y + y, p.z))
    
    # Build quad faces between consecutive rings
    faces = []
    for i in range(num_rings):
        for j in range(num_segments):
            next_j = (j + 1) % num_segments
            v1 = i * num_segments + j
            v2 = i * num_segments + next_j
            v3 = (i + 1) * num_segments + next_j
            v4 = (i + 1) * num_segments + j
            faces.append((v1, v2, v3, v4))
    
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    
    obj = bpy.data.objects.new(name, mesh)
    obj.location = (0.0, 0.0, 0.0)  # relative to parent
    obj.parent = sculpture_root
    bpy.context.collection.objects.link(obj)
    col_sculpture.objects.link(obj)
    if "Collection" in bpy.data.collections and obj.name in bpy.data.collections["Collection"].objects:
        bpy.data.collections["Collection"].objects.unlink(obj)
    
    # Assign bronze material
    if mesh.materials:
        mesh.materials[0] = mat_bronze
    else:
        mesh.materials.append(mat_bronze)
    
    verianim.set_verianim_properties(obj, verianim_part="bronze_forms")
    return obj

# Three curved forms radiating outward at 0°, 120°, 240° with different curvatures
radius_spread = 0.35  # how far the top leans outward
angles = [0, 2 * math.pi / 3, 4 * math.pi / 3]
heights = [1.5, 1.7, 1.6]        # slightly different heights for organic feel
spreads = [0.35, 0.30, 0.40]     # different spreads for variety
radii = [0.065, 0.075, 0.060]    # different thicknesses

for i, angle in enumerate(angles):
    dx = spreads[i] * math.cos(angle)
    dy = spreads[i] * math.sin(angle)
    name = f"bronze_form_{i}"
    create_curved_tube_mesh(
        name,
        base_offset_x=dx,
        base_offset_y=dy,
        height=heights[i],
        tube_radius=radii[i],
        num_segments=14,
        num_rings=24,
    )

# Add a small bronze base disc to unify the forms at the bottom
base_disc = verianim.add_uv_sphere(
    name="sculpture_base",
    radius=0.18,
    segments=16,
    rings=8,
    collection=col_sculpture,
    material=mat_bronze,
    location=(0, 0, 0.05),
)
base_disc.scale = (1.0, 1.0, 0.25)
base_disc.parent = sculpture_root
verianim.set_verianim_properties(base_disc, verianim_part="bronze_forms")

# ------------------------------------------------------------
# Camera look-at target (placed at center of sculpture volume)
# ------------------------------------------------------------
camera_target = bpy.data.objects.new("camera_target", None)
camera_target.empty_display_type = 'PLAIN_AXES'
camera_target.location = (0.0, 0.0, 1.5)  # roughly halfway up the sculpture
bpy.context.collection.objects.link(camera_target)
col_cameras.objects.link(camera_target)
if "Collection" in bpy.data.collections and camera_target.name in bpy.data.collections["Collection"].objects:
    bpy.data.collections["Collection"].objects.unlink(camera_target)

# ------------------------------------------------------------
# Camera – start position matches spec exactly
# ------------------------------------------------------------
start_loc = (3.2, -3.2, 1.9)
camera = verianim.add_camera(
    name="camera_main",
    location=start_loc,
    look_at_target=(0.0, 0.0, 0.6),  # initial orientation, will be overridden by constraint
    lens=28,  # wider lens for better coverage
    collection=col_cameras,
    make_active=True,
)
verianim.set_verianim_properties(camera, verianim_id="camera_main")

# ------------------------------------------------------------
# Lighting – dramatic gallery style
# ------------------------------------------------------------
key_light = verianim.add_light(
    name="key_light",
    light_type="AREA",
    location=(2.5, -3.5, 5.0),
    energy=650,
    size=5.0,
    color=(1.0, 0.95, 0.88),
    collection=col_lights,
)
verianim.set_verianim_properties(key_light, verianim_id="key_light")

fill_light = verianim.add_light(
    name="fill_light",
    light_type="AREA",
    location=(-3.0, 2.0, 3.0),
    energy=250,
    size=3.0,
    color=(0.9, 0.92, 1.0),
    collection=col_lights,
)
verianim.set_verianim_properties(fill_light, verianim_id="fill_light")

rim_light = verianim.add_light(
    name="rim_light",
    light_type="AREA",
    location=(-1.0, 4.0, 2.5),
    energy=300,
    size=2.0,
    color=(1.0, 0.95, 0.85),
    collection=col_lights,
)
verianim.set_verianim_properties(rim_light, verianim_id="rim_light")

# ------------------------------------------------------------
# Render setup (Workbench)
# ------------------------------------------------------------
verianim.configure_render(scene, engine="workbench")

# ------------------------------------------------------------
# Metadata for verification
# ------------------------------------------------------------
VERIANIM_METADATA = {
    "sculpture": "sculpture_root",
    "pedestal": "Pedestal",
    "camera_main": "camera_main",
    "key_light": "key_light",
}

# Ensure camera is active
if bpy.context.scene.camera is None:
    for cam_obj in bpy.data.objects:
        if cam_obj.type == "CAMERA":
            bpy.context.scene.camera = cam_obj
            break

# ------------------------------------------------------------
# VeriAnim deterministic animation with Track To constraint
# ------------------------------------------------------------
import json as _verianim_det_json
import bpy as _verianim_det_bpy
from mathutils import Vector as _verianim_det_Vector

# Fixed keyframes to match exact spec locations
_VERIANIM_DETERMINISTIC_ANIMATION = _verianim_det_json.loads('{"duration_frames": 96, "events": [{"action": "camera_orbit", "camera_look_at": "sculpture", "data_path": "location", "id": "camera_orbit_sculpture", "interpolation": "BEZIER", "keyframes": [{"frame": 1, "value": [3.2, -3.2, 1.9]}, {"frame": 48, "value": [4.2, 0.0, 1.9]}, {"frame": 96, "value": [-3.2, 3.2, 1.9]}], "subject_ids": ["camera_main"], "target_ids": ["sculpture"]}], "fps": 24}')

def _verianim_det_descendants(root):
    yield root
    for child in list(getattr(root, "children", [])):
        yield from _verianim_det_descendants(child)

def _verianim_det_find_root(verianim_id):
    marker = str(verianim_id)
    exact = _verianim_det_bpy.data.objects.get(marker)
    if exact is not None:
        return exact
    matches = []
    for obj in _verianim_det_bpy.data.objects:
        if str(obj.get("verianim_id", "")) == marker:
            matches.append(obj)
    if matches:
        matches.sort(key=lambda obj: (0 if getattr(obj, "type", "") == "EMPTY" else 1, obj.name))
        return matches[0]
    for obj in _verianim_det_bpy.data.objects:
        clean = obj.name.rsplit(".", 1)[0] if obj.name.rsplit(".", 1)[-1].isdigit() else obj.name
        if clean == marker or clean.startswith(marker + "_"):
            return obj
    return None

def _verianim_det_iter_action_fcurves(action):
    if not action:
        return
    seen = set()
    for fcurve in getattr(action, "fcurves", []) or []:
        marker = fcurve.as_pointer() if hasattr(fcurve, "as_pointer") else id(fcurve)
        if marker not in seen:
            seen.add(marker)
            yield fcurve
    for layer in getattr(action, "layers", []) or []:
        for strip in getattr(layer, "strips", []) or []:
            channelbags = getattr(strip, "channelbags", []) or getattr(strip, "channel_bags", []) or []
            for bag in channelbags:
                for fcurve in getattr(bag, "fcurves", []) or []:
                    marker = fcurve.as_pointer() if hasattr(fcurve, "as_pointer") else id(fcurve)
                    if marker not in seen:
                        seen.add(marker)
                        yield fcurve

def _verianim_det_set_interpolation(obj, data_path, interpolation):
    action = obj.animation_data.action if obj.animation_data else None
    for fcurve in _verianim_det_iter_action_fcurves(action):
        if fcurve.data_path != data_path:
            continue
        for point in fcurve.keyframe_points:
            point.interpolation = interpolation
        fcurve.update()

def _verianim_det_insert_keyframe(obj, data_path, frame, value, interpolation):
    values = [float(item) for item in value]
    if data_path == "location":
        obj.location = tuple(values)
    elif data_path == "rotation_euler":
        obj.rotation_euler = tuple(values)
    elif data_path == "scale":
        obj.scale = tuple(values)
    obj.keyframe_insert(data_path=data_path, frame=int(frame))
    _verianim_det_set_interpolation(obj, data_path, interpolation)

def _verianim_det_world_center(root):
    points = []
    for obj in _verianim_det_descendants(root):
        if getattr(obj, "type", "") not in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
            continue
        if not getattr(obj, "bound_box", None):
            continue
        points.extend(obj.matrix_world @ _verianim_det_Vector(corner) for corner in obj.bound_box)
    if not points:
        return _verianim_det_Vector(root.matrix_world.translation)
    return _verianim_det_Vector((
        (min(point.x for point in points) + max(point.x for point in points)) * 0.5,
        (min(point.y for point in points) + max(point.y for point in points)) * 0.5,
        (min(point.z for point in points) + max(point.z for point in points)) * 0.5,
    ))

def _verianim_det_look_at(camera, target_id):
    target = _verianim_det_find_root(target_id)
    if target is None:
        return
    direction = _verianim_det_world_center(target) - camera.location
    if direction.length <= 1e-6:
        return
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

def _verianim_det_mark_rotor_direction(root, subject_id):
    text = " ".join(
        [str(subject_id), root.name]
        + [child.name for child in _verianim_det_descendants(root)]
        + [str(child.get("verianim_part", "")) for child in _verianim_det_descendants(root)]
    ).lower()
    if not any(token in text for token in ("blade", "rotor", "propeller", "fan", "windmill")):
        return
    material = _verianim_det_bpy.data.materials.get("verianim_motion_marker_red")
    if material is None:
        material = _verianim_det_bpy.data.materials.new("verianim_motion_marker_red")
        material.diffuse_color = (0.95, 0.08, 0.04, 1.0)
    candidates = [
        obj
        for obj in _verianim_det_descendants(root)
        if getattr(obj, "type", "") == "MESH"
        and ("blade" in obj.name.lower() or "blade" in str(obj.get("verianim_part", "")).lower())
    ]
    if not candidates:
        return
    target = sorted(candidates, key=lambda obj: obj.name)[0]
    if target.data.materials:
        target.data.materials[0] = material
    else:
        target.data.materials.append(material)

# ------------------------------------------------------------
# Setup scene animation range
# ------------------------------------------------------------
_verianim_det_scene = _verianim_det_bpy.context.scene
_verianim_det_scene.frame_start = 1
_verianim_det_scene.frame_end = int(_VERIANIM_DETERMINISTIC_ANIMATION["duration_frames"])
_verianim_det_scene.render.fps = int(_VERIANIM_DETERMINISTIC_ANIMATION["fps"])

# ------------------------------------------------------------
# Find camera and apply Track To constraint + location keyframes
# ------------------------------------------------------------
camera_obj = _verianim_det_find_root("camera_main")
if camera_obj is not None:
    # Clear any existing animation data on the camera
    if camera_obj.animation_data:
        camera_obj.animation_data_clear()
    
    # Remove any old constraints
    camera_obj.constraints.clear()
    
    # Add Track To constraint so camera always points at the camera_target empty
    track_constraint = camera_obj.constraints.new(type='TRACK_TO')
    track_constraint.target = camera_target
    track_constraint.track_axis = 'TRACK_NEGATIVE_Z'
    track_constraint.up_axis = 'UP_Y'
    
    # Set location keyframes matching exact spec
    _verianim_det_insert_keyframe(camera_obj, "location", 1, [3.2, -3.2, 1.9], "BEZIER")
    _verianim_det_insert_keyframe(camera_obj, "location", 48, [4.2, 0.0, 1.9], "BEZIER")
    _verianim_det_insert_keyframe(camera_obj, "location", 96, [-3.2, 3.2, 1.9], "BEZIER")
    
    # Update initial frame to ensure constraint works
    _verianim_det_scene.frame_set(1)
    _verianim_det_bpy.context.view_layer.update()

_verianim_det_scene.frame_set(_verianim_det_scene.frame_start)
_verianim_det_bpy.context.view_layer.update()

# ------------------------------------------------------------
# Ensure camera is active
# ------------------------------------------------------------
if bpy.context.scene.camera is None:
    for cam_obj in bpy.data.objects:
        if cam_obj.type == "CAMERA":
            bpy.context.scene.camera = cam_obj
            break
else:
    bpy.context.scene.camera = camera_obj  # explicitly set

# End of script