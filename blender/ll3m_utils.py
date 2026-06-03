"""Reusable Blender helpers for LL3M-generated scene scripts.

The functions in this module intentionally import ``bpy`` lazily so the module
can be imported by normal Python tests outside Blender.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence


DEFAULT_RENDER_ENGINE = "BLENDER_WORKBENCH"


def _bpy() -> Any:
    import bpy  # type: ignore[import-not-found]

    return bpy


def _vector(values: Sequence[float], length: int = 3, default: float = 0.0) -> tuple[float, ...]:
    items = [float(item) for item in values[:length]]
    while len(items) < length:
        items.append(default)
    return tuple(items)


def _rgba(color: Sequence[float] | None, alpha: float | None = None) -> tuple[float, float, float, float]:
    if color is None:
        color = (0.8, 0.8, 0.8, 1.0)
    values = list(float(item) for item in color[:4])
    while len(values) < 4:
        values.append(1.0)
    if alpha is not None:
        values[3] = float(alpha)
    return tuple(max(0.0, min(1.0, value)) for value in values)  # type: ignore[return-value]


def _object_sequence(obj_or_objs: Any | Iterable[Any]) -> list[Any]:
    if obj_or_objs is None:
        return []
    if isinstance(obj_or_objs, (str, bytes)) or hasattr(obj_or_objs, "bound_box"):
        return [obj_or_objs]
    try:
        return [item for item in obj_or_objs if item is not None]
    except TypeError:
        return [obj_or_objs]


def _iter_bbox_objects(obj_or_objs: Any | Iterable[Any], *, include_children: bool = True) -> list[Any]:
    objects: list[Any] = []
    pending = _object_sequence(obj_or_objs)
    while pending:
        obj = pending.pop(0)
        if obj in objects:
            continue
        if getattr(obj, "bound_box", None) is not None:
            objects.append(obj)
        if include_children:
            pending.extend(getattr(obj, "children", []) or [])
    return objects


def normalize_engine_name(engine: str | None) -> str:
    """Return a Blender render engine enum value, preferring Workbench by default."""

    if not engine:
        return DEFAULT_RENDER_ENGINE
    key = str(engine).strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "WORKBENCH": "BLENDER_WORKBENCH",
        "BLENDER_WORKBENCH": "BLENDER_WORKBENCH",
        "EEVEE": "BLENDER_EEVEE_NEXT",
        "EEVEE_NEXT": "BLENDER_EEVEE_NEXT",
        "BLENDER_EEVEE": "BLENDER_EEVEE",
        "BLENDER_EEVEE_NEXT": "BLENDER_EEVEE_NEXT",
        "CYCLE": "CYCLES",
        "CYCLES": "CYCLES",
    }
    return aliases.get(key, key)


def available_render_engines(scene: Any | None = None) -> list[str]:
    """List render engine enum values available in the active Blender build."""

    bpy = _bpy()
    render = (scene or bpy.context.scene).render
    try:
        return list(render.bl_rna.properties["engine"].enum_items.keys())
    except Exception:
        return list(bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys())


def set_render_engine(
    scene: Any | None = None,
    engine: str | None = DEFAULT_RENDER_ENGINE,
    fallbacks: Iterable[str] = (DEFAULT_RENDER_ENGINE, "BLENDER_EEVEE_NEXT", "CYCLES", "BLENDER_EEVEE"),
) -> str:
    """Set ``scene.render.engine`` with safe fallbacks and return the value used."""

    bpy = _bpy()
    scene = scene or bpy.context.scene
    available = set(available_render_engines(scene))
    candidates = [normalize_engine_name(engine)]
    candidates.extend(normalize_engine_name(item) for item in fallbacks)
    for candidate in dict.fromkeys(candidates):
        if candidate in available:
            scene.render.engine = candidate
            return candidate
    raise RuntimeError(f"No supported render engine found. Available engines: {sorted(available)}")


def configure_render(
    scene: Any | None = None,
    *,
    width: int | None = None,
    height: int | None = None,
    fps: int | None = None,
    engine: str | None = DEFAULT_RENDER_ENGINE,
    transparent_background: bool | None = None,
    resolution_percentage: int = 100,
) -> str:
    """Apply common render settings and return the selected render engine."""

    bpy = _bpy()
    scene = scene or bpy.context.scene
    if width is not None:
        scene.render.resolution_x = int(width)
    if height is not None:
        scene.render.resolution_y = int(height)
    scene.render.resolution_percentage = int(resolution_percentage)
    if fps is not None:
        scene.render.fps = int(fps)
    if transparent_background is not None:
        scene.render.film_transparent = bool(transparent_background)
    return set_render_engine(scene, engine)


def clear_scene(*, remove_orphans: bool = True) -> Any:
    """Delete scene objects and optionally remove unused common datablocks."""

    bpy = _bpy()
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    if remove_orphans:
        for collection_name in ("meshes", "curves", "materials", "cameras", "lights", "images"):
            collection = getattr(bpy.data, collection_name, None)
            if not collection:
                continue
            for datablock in list(collection):
                if not getattr(datablock, "users", 0):
                    try:
                        collection.remove(datablock)
                    except Exception:
                        pass
        for collection in list(bpy.data.collections):
            if not getattr(collection, "users", 0):
                try:
                    bpy.data.collections.remove(collection)
                except Exception:
                    pass
    return bpy.context.scene


def create_collection(name: str, parent: Any | None = None) -> Any:
    """Create or return a named collection linked under ``parent``."""

    bpy = _bpy()
    collection = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    parent = parent or bpy.context.scene.collection
    if collection.name not in {child.name for child in parent.children}:
        parent.children.link(collection)
    return collection


def link_object(obj: Any, collection: Any | None = None) -> Any:
    """Link an object to a collection if needed."""

    bpy = _bpy()
    collection = collection or bpy.context.scene.collection
    if obj.name not in {item.name for item in collection.objects}:
        collection.objects.link(obj)
    return obj


def set_ll3m_properties(
    obj: Any,
    *,
    ll3m_id: str | None = None,
    ll3m_part: str | None = None,
    ll3m_role: str | None = None,
) -> Any:
    """Attach standard LL3M custom properties to an object."""

    if ll3m_id:
        obj["ll3m_id"] = ll3m_id
    if ll3m_part:
        obj["ll3m_part"] = ll3m_part
    if ll3m_role:
        obj["ll3m_role"] = ll3m_role
    return obj


def find_node_by_type(node_tree: Any, node_type: str) -> Any | None:
    """Find a shader/world node by stable ``node.type`` instead of UI name."""

    for node in getattr(node_tree, "nodes", []) or []:
        if getattr(node, "type", None) == node_type:
            return node
    return None


def make_material(
    name: str | Mapping[str, Any],
    base_color: Sequence[float] | None = None,
    *,
    metallic: float | None = None,
    roughness: float | None = None,
    alpha: float | None = None,
    texture_path: str | None = None,
) -> Any:
    """Create or update a Principled material with stable LL3M metadata."""

    bpy = _bpy()
    if isinstance(name, Mapping):
        spec = name
        name = str(spec.get("id") or spec.get("name") or "material")
        base_color = spec.get("base_color", base_color)
        metallic = spec.get("metallic", metallic)
        roughness = spec.get("roughness", roughness)
        alpha = spec.get("alpha", alpha)
        texture_source = spec.get("texture_source")
        if isinstance(texture_source, Mapping) and texture_source.get("approved_by_vision") and texture_source.get("local_path"):
            texture_path = str(texture_source["local_path"])
    color = _rgba(base_color, alpha)
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat["ll3m_id"] = name
    mat.diffuse_color = color
    mat.use_nodes = True
    principled = find_node_by_type(mat.node_tree, "BSDF_PRINCIPLED") if mat.node_tree else None
    if principled:
        if "Base Color" in principled.inputs:
            principled.inputs["Base Color"].default_value = color
        if "Alpha" in principled.inputs:
            principled.inputs["Alpha"].default_value = color[3]
        if metallic is not None and "Metallic" in principled.inputs:
            principled.inputs["Metallic"].default_value = float(metallic)
        if roughness is not None and "Roughness" in principled.inputs:
            principled.inputs["Roughness"].default_value = float(roughness)
        if texture_path:
            image = bpy.data.images.load(texture_path, check_existing=True)
            try:
                image.colorspace_settings.name = "sRGB"
            except Exception:
                pass
            tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
            tex.image = image
            mat.node_tree.links.new(tex.outputs["Color"], principled.inputs["Base Color"])
    return mat


def create_material(
    name: str | Mapping[str, Any],
    base_color: Sequence[float] | None = None,
    *,
    metallic: float | None = None,
    roughness: float | None = None,
    alpha: float | None = None,
    texture_path: str | None = None,
) -> Any:
    """Compatibility alias for generated code that asks to create a material."""

    return make_material(name, base_color, metallic=metallic, roughness=roughness, alpha=alpha, texture_path=texture_path)


def create_mesh_object(
    name: str,
    vertices: Sequence[Sequence[float]],
    faces: Sequence[Sequence[int]],
    *,
    collection: Any | None = None,
    material: Any | None = None,
    location: Sequence[float] = (0.0, 0.0, 0.0),
    rotation: Sequence[float] = (0.0, 0.0, 0.0),
    scale: Sequence[float] = (1.0, 1.0, 1.0),
    ll3m_id: str | None = None,
    ll3m_part: str | None = None,
    ll3m_role: str | None = None,
) -> Any:
    """Create a mesh object using Blender's data API."""

    bpy = _bpy()
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata([tuple(vertex) for vertex in vertices], [], [tuple(face) for face in faces])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = _vector(location)
    obj.rotation_euler = _vector(rotation)
    obj.scale = _vector(scale, default=1.0)
    if material is not None:
        obj.data.materials.append(material)
    set_ll3m_properties(obj, ll3m_id=ll3m_id, ll3m_part=ll3m_part, ll3m_role=ll3m_role)
    return link_object(obj, collection)


def get_or_create_collection(name: str, parent: Any | None = None) -> Any:
    """Compatibility alias for generated code."""

    return create_collection(name, parent)


def ensure_collection(name: str, parent: Any | None = None) -> Any:
    """Compatibility alias for generated code."""

    return create_collection(name, parent)


def link_to_collection(obj: Any, collection: Any | None = None) -> Any:
    """Compatibility alias for generated code."""

    return link_object(obj, collection)


def add_cube(
    name: str,
    *,
    size: float | Sequence[float] = 1.0,
    collection: Any | None = None,
    material: Any | None = None,
    location: Sequence[float] = (0.0, 0.0, 0.0),
    rotation: Sequence[float] = (0.0, 0.0, 0.0),
    ll3m_id: str | None = None,
    ll3m_part: str | None = None,
    ll3m_role: str | None = None,
) -> Any:
    """Create a box mesh centered on its origin."""

    if isinstance(size, (int, float)):
        sx = sy = sz = float(size)
    else:
        sx, sy, sz = _vector(size, default=1.0)
    x, y, z = sx / 2.0, sy / 2.0, sz / 2.0
    vertices = [(-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z), (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z)]
    faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    return create_mesh_object(
        name,
        vertices,
        faces,
        collection=collection,
        material=material,
        location=location,
        rotation=rotation,
        ll3m_id=ll3m_id,
        ll3m_part=ll3m_part,
        ll3m_role=ll3m_role,
    )


def create_box(
    name: str,
    *,
    size: float | Sequence[float] = 1.0,
    collection: Any | None = None,
    material: Any | None = None,
    location: Sequence[float] = (0.0, 0.0, 0.0),
    rotation: Sequence[float] = (0.0, 0.0, 0.0),
    ll3m_id: str | None = None,
    ll3m_part: str | None = None,
    ll3m_role: str | None = None,
) -> Any:
    """Compatibility alias for generated code."""

    return add_cube(
        name,
        size=size,
        collection=collection,
        material=material,
        location=location,
        rotation=rotation,
        ll3m_id=ll3m_id,
        ll3m_part=ll3m_part,
        ll3m_role=ll3m_role,
    )


def make_box(
    name: str,
    size: float | Sequence[float] = 1.0,
    *,
    collection: Any | None = None,
    material: Any | None = None,
    location: Sequence[float] = (0.0, 0.0, 0.0),
    rotation: Sequence[float] = (0.0, 0.0, 0.0),
    ll3m_id: str | None = None,
    ll3m_part: str | None = None,
    ll3m_role: str | None = None,
) -> Any:
    """Compatibility alias that accepts size as a positional argument."""

    return add_cube(
        name,
        size=size,
        collection=collection,
        material=material,
        location=location,
        rotation=rotation,
        ll3m_id=ll3m_id,
        ll3m_part=ll3m_part,
        ll3m_role=ll3m_role,
    )


def add_plane(
    name: str,
    *,
    size: float | Sequence[float] = 1.0,
    collection: Any | None = None,
    material: Any | None = None,
    location: Sequence[float] = (0.0, 0.0, 0.0),
    ll3m_id: str | None = None,
    ll3m_part: str | None = None,
    ll3m_role: str | None = None,
) -> Any:
    """Create a flat XY plane centered on its origin."""

    if isinstance(size, (int, float)):
        sx = sy = float(size)
    else:
        sx, sy = _vector(size, length=2, default=1.0)
    x, y = sx / 2.0, sy / 2.0
    return create_mesh_object(
        name,
        [(-x, -y, 0.0), (x, -y, 0.0), (x, y, 0.0), (-x, y, 0.0)],
        [(0, 1, 2, 3)],
        collection=collection,
        material=material,
        location=location,
        ll3m_id=ll3m_id,
        ll3m_part=ll3m_part,
        ll3m_role=ll3m_role,
    )


def add_cylinder(
    name: str,
    *,
    radius: float = 0.5,
    depth: float = 1.0,
    vertices_count: int = 32,
    collection: Any | None = None,
    material: Any | None = None,
    location: Sequence[float] = (0.0, 0.0, 0.0),
    rotation: Sequence[float] = (0.0, 0.0, 0.0),
    ll3m_id: str | None = None,
    ll3m_part: str | None = None,
    ll3m_role: str | None = None,
) -> Any:
    """Create a vertical cylinder centered on its origin."""

    count = max(3, int(vertices_count))
    half = float(depth) / 2.0
    verts: list[tuple[float, float, float]] = []
    for z in (-half, half):
        for index in range(count):
            angle = 2.0 * math.pi * index / count
            verts.append((float(radius) * math.cos(angle), float(radius) * math.sin(angle), z))
    faces: list[tuple[int, ...]] = [tuple(range(count - 1, -1, -1)), tuple(range(count, count * 2))]
    for index in range(count):
        nxt = (index + 1) % count
        faces.append((index, nxt, count + nxt, count + index))
    return create_mesh_object(
        name,
        verts,
        faces,
        collection=collection,
        material=material,
        location=location,
        rotation=rotation,
        ll3m_id=ll3m_id,
        ll3m_part=ll3m_part,
        ll3m_role=ll3m_role,
    )


def add_uv_sphere(
    name: str,
    *,
    radius: float = 0.5,
    segments: int = 32,
    rings: int = 16,
    collection: Any | None = None,
    material: Any | None = None,
    location: Sequence[float] = (0.0, 0.0, 0.0),
    ll3m_id: str | None = None,
    ll3m_part: str | None = None,
    ll3m_role: str | None = None,
) -> Any:
    """Create a simple UV sphere mesh centered on its origin."""

    segments = max(8, int(segments))
    rings = max(4, int(rings))
    verts: list[tuple[float, float, float]] = [(0.0, 0.0, float(radius))]
    for ring in range(1, rings):
        phi = math.pi * ring / rings
        z = float(radius) * math.cos(phi)
        ring_radius = float(radius) * math.sin(phi)
        for segment in range(segments):
            theta = 2.0 * math.pi * segment / segments
            verts.append((ring_radius * math.cos(theta), ring_radius * math.sin(theta), z))
    verts.append((0.0, 0.0, -float(radius)))
    bottom_index = len(verts) - 1
    faces: list[tuple[int, ...]] = []
    for segment in range(segments):
        nxt = (segment + 1) % segments
        faces.append((0, 1 + segment, 1 + nxt))
    for ring in range(rings - 2):
        start = 1 + ring * segments
        next_start = start + segments
        for segment in range(segments):
            nxt = (segment + 1) % segments
            faces.append((start + segment, start + nxt, next_start + nxt, next_start + segment))
    last_ring = 1 + (rings - 2) * segments
    for segment in range(segments):
        nxt = (segment + 1) % segments
        faces.append((last_ring + nxt, last_ring + segment, bottom_index))
    return create_mesh_object(
        name,
        verts,
        faces,
        collection=collection,
        material=material,
        location=location,
        ll3m_id=ll3m_id,
        ll3m_part=ll3m_part,
        ll3m_role=ll3m_role,
    )


def set_frame_range(scene: Any | None = None, start: int = 1, end: int = 120, fps: int | None = None) -> Any:
    """Set the active animation frame range and optional fps."""

    bpy = _bpy()
    scene = scene or bpy.context.scene
    scene.frame_start = int(start)
    scene.frame_end = int(end)
    if fps is not None:
        scene.render.fps = int(fps)
    return scene


def world_bbox(obj_or_objs: Any | Iterable[Any], *, include_children: bool = True) -> dict[str, tuple[float, float, float] | float]:
    """Return an aggregate world-space bounding box for one object or object group."""

    from mathutils import Vector  # type: ignore[import-not-found]

    try:
        _bpy().context.view_layer.update()
    except Exception:
        pass
    points: list[Vector] = []
    for obj in _iter_bbox_objects(obj_or_objs, include_children=include_children):
        matrix = getattr(obj, "matrix_world", None)
        for corner in getattr(obj, "bound_box", []) or []:
            point = Vector(tuple(float(value) for value in corner[:3]))
            points.append(matrix @ point if matrix is not None else point)
    if not points:
        loc = getattr(obj_or_objs, "location", (0.0, 0.0, 0.0))
        x, y, z = _vector(loc)
        return {
            "min": (x, y, z),
            "max": (x, y, z),
            "center": (x, y, z),
            "size": (0.0, 0.0, 0.0),
            "top": z,
            "bottom": z,
        }
    min_xyz = tuple(min(point[index] for point in points) for index in range(3))
    max_xyz = tuple(max(point[index] for point in points) for index in range(3))
    center = tuple((min_xyz[index] + max_xyz[index]) / 2.0 for index in range(3))
    size = tuple(max_xyz[index] - min_xyz[index] for index in range(3))
    return {"min": min_xyz, "max": max_xyz, "center": center, "size": size, "top": max_xyz[2], "bottom": min_xyz[2]}


def bbox_center(obj_or_objs: Any | Iterable[Any], *, include_children: bool = True) -> tuple[float, float, float]:
    return world_bbox(obj_or_objs, include_children=include_children)["center"]  # type: ignore[return-value]


def bbox_size(obj_or_objs: Any | Iterable[Any], *, include_children: bool = True) -> tuple[float, float, float]:
    return world_bbox(obj_or_objs, include_children=include_children)["size"]  # type: ignore[return-value]


def bbox_top(obj_or_objs: Any | Iterable[Any], *, include_children: bool = True) -> float:
    return float(world_bbox(obj_or_objs, include_children=include_children)["top"])


def bbox_bottom(obj_or_objs: Any | Iterable[Any], *, include_children: bool = True) -> float:
    return float(world_bbox(obj_or_objs, include_children=include_children)["bottom"])


def move_bottom_to_z(obj: Any, z: float, *, margin: float = 0.001, include_children: bool = True) -> Any:
    """Move an object root so its aggregate bbox bottom sits at ``z + margin``."""

    delta = float(z) + float(margin) - bbox_bottom(obj, include_children=include_children)
    obj.location.z += delta
    return obj


def align_bottom_to_top(subject: Any, support: Any | Iterable[Any], *, margin: float = 0.001, include_children: bool = True) -> Any:
    """Place ``subject`` on top of ``support`` using actual world bboxes."""

    return move_bottom_to_z(subject, bbox_top(support, include_children=include_children), margin=margin, include_children=include_children)


def space_gripper_fingers_around_subject(
    gripper: Any,
    subject: Any,
    *,
    axis: str = "X",
    fingers: Sequence[Any] | None = None,
    gap: float = 0.02,
    align_z: str | None = "center",
) -> list[Any]:
    """Move two gripper finger children outside a subject bbox to avoid embedding."""

    axis_index = {"X": 0, "Y": 1, "Z": 2}[str(axis).upper()]
    if fingers is None:
        fingers = [
            child
            for child in getattr(gripper, "children", []) or []
            if "finger" in str(getattr(child, "name", "")).lower() or str(child.get("ll3m_part", "")).lower() == "finger"
        ]
    selected = list(fingers)[:2]
    if len(selected) < 2:
        return selected
    subject_size = bbox_size(subject)[axis_index]
    for sign, finger in zip((-1.0, 1.0), selected):
        if getattr(finger, "parent", None) is gripper and hasattr(finger, "matrix_parent_inverse"):
            finger.matrix_parent_inverse.identity()
        finger_size = bbox_size(finger, include_children=False)[axis_index]
        finger.location[axis_index] = sign * (subject_size / 2.0 + finger_size / 2.0 + float(gap))
        if align_z == "center":
            if getattr(finger, "parent", None) is gripper:
                finger.location.z = 0.0
            else:
                subject_center_z = bbox_center(subject)[2]
                finger_center_z = bbox_center(finger, include_children=False)[2]
                finger.location.z += subject_center_z - finger_center_z
        elif align_z == "top":
            finger.location.z += bbox_top(subject) - bbox_top(finger, include_children=False)
    return selected


def create_parallel_gripper(
    name: str,
    *,
    carried: Any | None = None,
    location: Sequence[float] = (0.0, 0.0, 1.0),
    collection: Any = None,
    material: Any = None,
    ll3m_id: str | None = None,
    axis: str = "Y",
    finger_length: float | None = None,
    finger_thickness: float = 0.06,
    palm_size: Sequence[float] | None = None,
    stem_height: float = 0.35,
    open_gap: float = 0.06,
) -> dict[str, Any]:
    """Create a visible two-finger gripper rooted at a palm object.

    The returned ``root`` owns ``ll3m_id`` and is the object to animate. The
    palm, stem, and fingers are children, so a single root path keeps the whole
    gripper coherent.
    """

    axis_index = {"X": 0, "Y": 1, "Z": 2}[str(axis).upper()]
    if axis_index == 2:
        raise ValueError("create_parallel_gripper finger axis must be X or Y")
    bpy = _bpy()
    root = bpy.data.objects.new(str(name), None)
    root.empty_display_type = "CUBE"
    root.empty_display_size = 0.15
    root.location = _vector(location)
    link_object(root, collection)
    set_ll3m_properties(root, ll3m_id=ll3m_id or name, ll3m_role="active")

    carried_size = bbox_size(carried) if carried is not None else (0.35, 0.35, 0.25)
    grip_span = carried_size[axis_index] if carried is not None else 0.35
    vertical_size = carried_size[2] if carried is not None else 0.25
    length = float(finger_length if finger_length is not None else max(0.22, vertical_size * 1.15))
    thickness = float(finger_thickness)
    if palm_size is None:
        palm_size = (
            max(0.24, carried_size[0] + 2.0 * thickness),
            max(0.24, carried_size[1] + 2.0 * thickness),
            thickness,
        )
    palm = add_cube(f"{name}_palm", size=1.0, collection=collection, material=material, ll3m_part="palm")
    palm.scale = _vector(palm_size)
    palm.parent = root
    palm_z = vertical_size / 2.0 + thickness / 2.0 + 0.02
    palm.location = (0.0, 0.0, palm_z)

    stem = add_cube(f"{name}_stem", size=1.0, collection=collection, material=material, ll3m_part="stem")
    stem.scale = (thickness, thickness, float(stem_height))
    stem.parent = root
    stem.location = (0.0, 0.0, palm_z + float(stem_height) / 2.0 + float(palm_size[2]) / 2.0)

    finger_scale = [thickness, thickness, length]
    finger_offset = grip_span / 2.0 + thickness / 2.0 + float(open_gap)
    finger_z = 0.0
    left = add_cube(f"{name}_left_finger", size=1.0, collection=collection, material=material, ll3m_part="finger")
    right = add_cube(f"{name}_right_finger", size=1.0, collection=collection, material=material, ll3m_part="finger")
    for sign, finger in ((-1.0, left), (1.0, right)):
        finger.scale = tuple(finger_scale)
        finger.parent = root
        loc = [0.0, 0.0, finger_z]
        loc[axis_index] = sign * finger_offset
        finger.location = tuple(loc)
    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    return {"root": root, "palm": palm, "stem": stem, "left_finger": left, "right_finger": right, "fingers": [left, right]}


def _iter_action_fcurves(action: Any) -> Iterable[Any]:
    for fcurve in getattr(action, "fcurves", []) or []:
        yield fcurve
    for layer in getattr(action, "layers", []) or []:
        for strip in getattr(layer, "strips", []) or []:
            channelbags = getattr(strip, "channelbags", []) or getattr(strip, "channel_bags", []) or []
            for bag in channelbags:
                for fcurve in getattr(bag, "fcurves", []) or []:
                    yield fcurve


def set_keyframe_interpolation(obj: Any, interpolation: str = "LINEAR", easing: str | None = None) -> Any:
    """Set interpolation/easing on all keyframes in an object's action."""

    action = getattr(getattr(obj, "animation_data", None), "action", None)
    if action is None:
        return obj
    for fcurve in _iter_action_fcurves(action):
        for key in getattr(fcurve, "keyframe_points", []) or []:
            key.interpolation = str(interpolation).upper()
            if easing is not None:
                key.easing = str(easing).upper()
    return obj


def set_linear_interpolation(obj: Any) -> Any:
    return set_keyframe_interpolation(obj, "LINEAR")


def insert_location_keyframe(obj: Any, frame: int, location: Sequence[float], interpolation: str = "LINEAR") -> Any:
    obj.location = _vector(location)
    obj.keyframe_insert(data_path="location", frame=int(frame))
    return set_keyframe_interpolation(obj, interpolation)


def insert_rotation_keyframe(obj: Any, frame: int, rotation: Sequence[float], interpolation: str = "LINEAR") -> Any:
    obj.rotation_euler = _vector(rotation)
    obj.keyframe_insert(data_path="rotation_euler", frame=int(frame))
    return set_keyframe_interpolation(obj, interpolation)


def insert_scale_keyframe(obj: Any, frame: int, scale: Sequence[float], interpolation: str = "LINEAR") -> Any:
    obj.scale = _vector(scale, default=1.0)
    obj.keyframe_insert(data_path="scale", frame=int(frame))
    return set_keyframe_interpolation(obj, interpolation)


def animate_translate(obj: Any, keyframes: Sequence[Mapping[str, Any] | Sequence[Any]], interpolation: str = "LINEAR") -> Any:
    """Animate rigid translation from ``[(frame, location), ...]`` or dict keyframes."""

    for keyframe in keyframes:
        if isinstance(keyframe, Mapping):
            frame = int(keyframe["frame"])
            location = keyframe["location"]
        else:
            frame = int(keyframe[0])
            location = keyframe[1]
        insert_location_keyframe(obj, frame, location, interpolation)
    return obj


def animate_follow_path(obj: Any, points: Sequence[Sequence[float]], start_frame: int, end_frame: int, interpolation: str = "LINEAR") -> Any:
    """Animate translation along evenly timed path points."""

    if len(points) < 2:
        raise ValueError("animate_follow_path requires at least two points")
    span = int(end_frame) - int(start_frame)
    keyframes = []
    for index, point in enumerate(points):
        frame = int(round(int(start_frame) + span * index / (len(points) - 1)))
        keyframes.append((frame, point))
    return animate_translate(obj, keyframes, interpolation)


def _location_on_support(subject: Any, support: Any, xy: Sequence[float], *, margin: float = 0.001) -> tuple[float, float, float]:
    bottom = bbox_bottom(subject)
    z = getattr(subject.location, "z", _vector(subject.location)[2]) + bbox_top(support) + float(margin) - bottom
    x, y = _vector(xy, length=2)
    return (x, y, z)


def _safe_xy_on_support(
    subject: Any,
    support: Any,
    desired_xy: Sequence[float],
    *,
    avoid_supports: Sequence[Any] = (),
    margin: float = 0.02,
) -> tuple[float, float]:
    sx, sy = _vector(desired_xy, length=2)
    subject_size = bbox_size(subject)
    half = (subject_size[0] / 2.0 + float(margin), subject_size[1] / 2.0 + float(margin))
    support_box = world_bbox(support)
    min_xy = (support_box["min"][0] + half[0], support_box["min"][1] + half[1])
    max_xy = (support_box["max"][0] - half[0], support_box["max"][1] - half[1])
    if min_xy[0] <= max_xy[0]:
        sx = min(max(sx, min_xy[0]), max_xy[0])
    if min_xy[1] <= max_xy[1]:
        sy = min(max(sy, min_xy[1]), max_xy[1])

    def footprint_overlaps(x: float, y: float, avoid_box: Mapping[str, Any]) -> bool:
        return (
            x - half[0] < avoid_box["max"][0]
            and x + half[0] > avoid_box["min"][0]
            and y - half[1] < avoid_box["max"][1]
            and y + half[1] > avoid_box["min"][1]
        )

    for avoid in avoid_supports:
        if avoid is None or avoid is support:
            continue
        avoid_box = world_bbox(avoid)
        if not footprint_overlaps(sx, sy, avoid_box):
            continue
        candidates: list[tuple[float, float, float]] = []
        for axis_index in (0, 1):
            current = sx if axis_index == 0 else sy
            low = min_xy[axis_index]
            high = max_xy[axis_index]
            if low > high:
                continue
            avoid_center = (avoid_box["min"][axis_index] + avoid_box["max"][axis_index]) / 2.0
            support_center = (support_box["min"][axis_index] + support_box["max"][axis_index]) / 2.0
            if support_center >= avoid_center:
                target = avoid_box["max"][axis_index] + half[axis_index]
                clamped = min(max(target, low), high)
            else:
                target = avoid_box["min"][axis_index] - half[axis_index]
                clamped = min(max(target, low), high)
            test_x, test_y = (clamped, sy) if axis_index == 0 else (sx, clamped)
            if not footprint_overlaps(test_x, test_y, avoid_box):
                candidates.append((abs(clamped - current), test_x, test_y))
        if candidates:
            _, sx, sy = min(candidates, key=lambda item: item[0])
    return (sx, sy)


def animate_support_slide(
    subject: Any,
    support: Any,
    start_xy: Sequence[float],
    end_xy: Sequence[float],
    start_frame: int,
    end_frame: int,
    *,
    margin: float = 0.001,
    interpolation: str = "LINEAR",
) -> Any:
    """Slide a rigid object across a horizontal support while preserving contact."""

    start = _location_on_support(subject, support, start_xy, margin=margin)
    end = _location_on_support(subject, support, end_xy, margin=margin)
    return animate_translate(subject, [(start_frame, start), (end_frame, end)], interpolation)


def animate_support_sequence(
    subject: Any,
    supports_with_windows: Sequence[Mapping[str, Any] | Sequence[Any]],
    *,
    margin: float = 0.001,
    interpolation: str = "LINEAR",
) -> Any:
    """Animate support-to-support motion from windows containing support, frame, and xy."""

    keyframes = []
    for item in supports_with_windows:
        if isinstance(item, Mapping):
            support = item["support"]
            frame = int(item["frame"])
            xy = item["xy"]
        else:
            support, frame, xy = item[0], int(item[1]), item[2]
        keyframes.append((frame, _location_on_support(subject, support, xy, margin=margin)))
    return animate_translate(subject, keyframes, interpolation)


def animate_attached_carry(
    driver: Any,
    carried: Any,
    frame_locations: Sequence[Mapping[str, Any] | Sequence[Any]],
    offset: Sequence[float] = (0.0, 0.0, -0.5),
    *,
    interpolation: str = "LINEAR",
) -> tuple[Any, Any]:
    """Animate a driver and a carried object with a fixed world-space offset."""

    ox, oy, oz = _vector(offset)
    driver_keys = []
    carried_keys = []
    for item in frame_locations:
        if isinstance(item, Mapping):
            frame = int(item["frame"])
            location = _vector(item["location"])
        else:
            frame = int(item[0])
            location = _vector(item[1])
        driver_keys.append((frame, location))
        carried_keys.append((frame, (location[0] + ox, location[1] + oy, location[2] + oz)))
    animate_translate(driver, driver_keys, interpolation)
    animate_translate(carried, carried_keys, interpolation)
    return driver, carried


def animate_pick_place(
    gripper: Any,
    carried: Any,
    source_support: Any,
    dest_support: Any,
    *,
    source_xy: Sequence[float] | None = None,
    dest_xy: Sequence[float] | None = None,
    frames: Sequence[int] = (1, 25, 45, 80, 100, 120),
    carry_height: float = 1.0,
    clearance: float = 0.05,
    gripper_offset: Sequence[float] | None = None,
    avoid_dest_supports: Sequence[Any] | None = None,
    margin: float = 0.001,
    interpolation: str = "LINEAR",
) -> tuple[Any, Any]:
    """Kinematic pick, carry, and place motion that avoids support penetration."""

    if len(frames) != 6:
        raise ValueError("animate_pick_place frames must contain six frames: approach, grasp, lift, carry, lower, release")
    source_center = bbox_center(source_support)
    dest_center = bbox_center(dest_support)
    sx, sy = _vector(source_xy or source_center[:2], length=2)
    dx, dy = _vector(dest_xy or dest_center[:2], length=2)
    avoid_supports = tuple(avoid_dest_supports) if avoid_dest_supports is not None else ((source_support,) if source_support is not dest_support else ())
    if avoid_supports:
        dx, dy = _safe_xy_on_support(carried, dest_support, (dx, dy), avoid_supports=avoid_supports, margin=max(float(clearance), 0.02))
    carried_height = bbox_size(carried)[2]
    source_z = bbox_top(source_support) + carried_height / 2.0 + float(margin)
    dest_z = bbox_top(dest_support) + carried_height / 2.0 + float(margin)
    lift_z = max(source_z, dest_z) + float(carry_height)
    if gripper_offset is None:
        gripper_offset = (0.0, 0.0, carried_height / 2.0 + float(clearance))
    gx, gy, gz = _vector(gripper_offset)
    carried_keys = [
        (frames[0], (sx, sy, source_z)),
        (frames[1], (sx, sy, source_z)),
        (frames[2], (sx, sy, lift_z)),
        (frames[3], (dx, dy, lift_z)),
        (frames[4], (dx, dy, dest_z)),
        (frames[5], (dx, dy, dest_z)),
    ]
    gripper_keys = [(frame, (loc[0] + gx, loc[1] + gy, loc[2] + gz)) for frame, loc in carried_keys]
    animate_translate(carried, carried_keys, interpolation)
    animate_translate(gripper, gripper_keys, interpolation)
    return gripper, carried


def animate_parallel_gripper_pick_place(
    gripper: Any,
    carried: Any,
    source_support: Any,
    dest_support: Any,
    *,
    fingers: Sequence[Any] | None = None,
    axis: str = "Y",
    source_xy: Sequence[float] | None = None,
    dest_xy: Sequence[float] | None = None,
    frames: Sequence[int] = (1, 25, 45, 80, 100, 120),
    carry_height: float = 1.0,
    clearance: float = 0.04,
    open_gap: float = 0.08,
    closed_gap: float = 0.005,
    gripper_offset: Sequence[float] | None = None,
    avoid_dest_supports: Sequence[Any] | None = None,
    margin: float = 0.001,
    interpolation: str = "LINEAR",
) -> tuple[Any, Any]:
    """Pick-place with visible finger close/hold/open keyframes."""

    if len(frames) != 6:
        raise ValueError("animate_parallel_gripper_pick_place frames must contain six frames")
    if fingers is None:
        fingers = [
            child
            for child in getattr(gripper, "children", []) or []
            if "finger" in str(getattr(child, "name", "")).lower() or str(child.get("ll3m_part", "")).lower() == "finger"
        ]
    selected = list(fingers)[:2]
    if len(selected) >= 2:
        space_gripper_fingers_around_subject(gripper, carried, axis=axis, fingers=selected, gap=open_gap, align_z="center")
    if gripper_offset is None:
        gripper_offset = (0.0, 0.0, 0.0)
    animate_pick_place(
        gripper,
        carried,
        source_support,
        dest_support,
        source_xy=source_xy,
        dest_xy=dest_xy,
        frames=frames,
        carry_height=carry_height,
        clearance=clearance,
        gripper_offset=gripper_offset,
        avoid_dest_supports=avoid_dest_supports,
        margin=margin,
        interpolation=interpolation,
    )
    if len(selected) >= 2:
        axis_index = {"X": 0, "Y": 1, "Z": 2}[str(axis).upper()]
        subject_size = bbox_size(carried)[axis_index]
        open_positions = []
        closed_positions = []
        for finger in selected:
            loc = list(_vector(finger.location))
            sign = -1.0 if loc[axis_index] < 0 else 1.0
            finger_size = bbox_size(finger, include_children=False)[axis_index]
            open_loc = list(loc)
            closed_loc = list(loc)
            open_loc[axis_index] = sign * (subject_size / 2.0 + finger_size / 2.0 + float(open_gap))
            closed_loc[axis_index] = sign * (subject_size / 2.0 + finger_size / 2.0 + float(closed_gap))
            open_positions.append(tuple(open_loc))
            closed_positions.append(tuple(closed_loc))
        for finger, open_loc, closed_loc in zip(selected, open_positions, closed_positions):
            animate_translate(
                finger,
                [
                    (frames[0], open_loc),
                    (frames[1], closed_loc),
                    (frames[2], closed_loc),
                    (frames[3], closed_loc),
                    (frames[4], closed_loc),
                    (frames[5], open_loc),
                ],
                interpolation,
            )
    return gripper, carried


def animate_push(
    pusher: Any,
    pushed: Any,
    support: Any,
    start_xy: Sequence[float],
    end_xy: Sequence[float],
    start_frame: int,
    end_frame: int,
    *,
    pusher_offset: Sequence[float] = (-0.8, 0.0, 0.0),
    margin: float = 0.001,
    interpolation: str = "LINEAR",
) -> tuple[Any, Any]:
    """Animate one rigid object pushing another across a support."""

    pushed_start = _location_on_support(pushed, support, start_xy, margin=margin)
    pushed_end = _location_on_support(pushed, support, end_xy, margin=margin)
    ox, oy, oz = _vector(pusher_offset)
    pusher_start = (pushed_start[0] + ox, pushed_start[1] + oy, pushed_start[2] + oz)
    pusher_end = (pushed_end[0] + ox, pushed_end[1] + oy, pushed_end[2] + oz)
    animate_translate(pushed, [(start_frame, pushed_start), (end_frame, pushed_end)], interpolation)
    animate_translate(pusher, [(start_frame, pusher_start), (end_frame, pusher_end)], interpolation)
    return pusher, pushed


def animate_drop_to_support(
    subject: Any,
    support: Any,
    start_location: Sequence[float],
    start_frame: int,
    end_frame: int,
    *,
    end_xy: Sequence[float] | None = None,
    margin: float = 0.001,
    interpolation: str = "QUAD",
) -> Any:
    """Kinematic drop ending with the subject resting on a support."""

    sx, sy, _ = _vector(start_location)
    ex, ey = _vector(end_xy or (sx, sy), length=2)
    end_location = _location_on_support(subject, support, (ex, ey), margin=margin)
    return animate_translate(subject, [(start_frame, start_location), (end_frame, end_location)], interpolation)


def animate_rotate_about_axis(
    obj: Any,
    axis: str | Sequence[float],
    angle: float,
    start_frame: int,
    end_frame: int,
    *,
    start_rotation: Sequence[float] | None = None,
    interpolation: str = "LINEAR",
) -> Any:
    """Animate Euler rotation around a principal axis or vector."""

    start = _vector(start_rotation or getattr(obj, "rotation_euler", (0.0, 0.0, 0.0)))
    end = list(start)
    if isinstance(axis, str):
        index = {"X": 0, "Y": 1, "Z": 2}[axis.upper()]
        end[index] += float(angle)
    else:
        ax = _vector(axis)
        dominant = max(range(3), key=lambda idx: abs(ax[idx]))
        end[dominant] += math.copysign(float(angle), ax[dominant] or 1.0)
    insert_rotation_keyframe(obj, start_frame, start, interpolation)
    insert_rotation_keyframe(obj, end_frame, end, interpolation)
    return obj


def create_rotor_root(
    name: str,
    *,
    location: Sequence[float] = (0.0, 0.0, 0.0),
    children: Sequence[Any] | None = None,
    collection: Any | None = None,
    ll3m_id: str | None = None,
    ll3m_role: str | None = "kinematic",
) -> Any:
    """Create an empty pivot root for propellers, rotors, wheels, and fans."""

    bpy = _bpy()
    root = bpy.data.objects.new(name, None)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 0.25
    root.location = _vector(location)
    link_to_collection(root, collection)
    set_ll3m_properties(root, ll3m_id=ll3m_id or name, ll3m_role=ll3m_role)
    for child in children or []:
        child.parent = root
        if hasattr(child, "matrix_parent_inverse"):
            child.matrix_parent_inverse.identity()
    return root


def animate_rotor(
    rotor_root: Any,
    *,
    axis: str | Sequence[float] = "X",
    turns: float = 1.0,
    start_frame: int = 1,
    end_frame: int = 120,
    start_rotation: Sequence[float] | None = None,
    interpolation: str = "LINEAR",
) -> Any:
    """Animate a rotor pivot root by ``turns`` full rotations around ``axis``."""

    return animate_rotate_about_axis(
        rotor_root,
        axis,
        float(turns) * math.tau,
        start_frame,
        end_frame,
        start_rotation=start_rotation,
        interpolation=interpolation,
    )


def animate_hinge(
    obj: Any,
    hinge_origin: Sequence[float],
    axis: str | Sequence[float],
    angle: float,
    start_frame: int,
    end_frame: int,
    *,
    interpolation: str = "LINEAR",
) -> Any:
    """Animate a hinged rigid object. Set the origin to ``hinge_origin`` first."""

    bpy = _bpy()
    cursor_location = tuple(bpy.context.scene.cursor.location)
    bpy.context.scene.cursor.location = _vector(hinge_origin)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    try:
        bpy.ops.object.origin_set(type="ORIGIN_CURSOR", center="MEDIAN")
    finally:
        bpy.context.scene.cursor.location = cursor_location
    return animate_rotate_about_axis(obj, axis, angle, start_frame, end_frame, interpolation=interpolation)


def look_at(obj: Any, target: Sequence[float] | Any) -> Any:
    """Rotate an object so its local -Z axis points at ``target``."""

    from mathutils import Vector  # type: ignore[import-not-found]

    target_vec = target if hasattr(target, "x") else Vector(_vector(target))
    direction = target_vec - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return obj


def add_camera(
    name: str = "camera_main",
    *,
    location: Sequence[float] = (3.0, -4.0, 2.5),
    look_at_target: Sequence[float] = (0.0, 0.0, 0.0),
    lens: float = 35.0,
    collection: Any | None = None,
    make_active: bool = True,
) -> Any:
    """Create a camera and optionally make it the active scene camera."""

    bpy = _bpy()
    camera_data = bpy.data.cameras.new(f"{name}_data")
    camera_data.lens = float(lens)
    camera = bpy.data.objects.new(name, camera_data)
    camera.location = _vector(location)
    look_at(camera, look_at_target)
    link_object(camera, collection)
    if make_active:
        bpy.context.scene.camera = camera
    return camera


def create_camera(
    name: str = "camera_main",
    *,
    location: Sequence[float] = (3.0, -4.0, 2.5),
    look_at: Sequence[float] = (0.0, 0.0, 0.0),
    lens: float = 35.0,
    collection: Any | None = None,
    make_active: bool = True,
) -> Any:
    """Compatibility alias using the common ``look_at`` keyword."""

    return add_camera(name, location=location, look_at_target=look_at, lens=lens, collection=collection, make_active=make_active)


def make_camera(
    name: str = "camera_main",
    *,
    location: Sequence[float] = (3.0, -4.0, 2.5),
    look_at: Sequence[float] = (0.0, 0.0, 0.0),
    lens: float = 35.0,
    collection: Any | None = None,
    make_active: bool = True,
) -> Any:
    """Compatibility alias for generated code."""

    return add_camera(name, location=location, look_at_target=look_at, lens=lens, collection=collection, make_active=make_active)


def add_light(
    name: str,
    *,
    light_type: str = "AREA",
    location: Sequence[float] = (0.0, 0.0, 4.0),
    rotation: Sequence[float] = (0.0, 0.0, 0.0),
    energy: float = 500.0,
    size: float | None = None,
    color: Sequence[float] | None = None,
    collection: Any | None = None,
) -> Any:
    """Create a light with common properties."""

    bpy = _bpy()
    light_data = bpy.data.lights.new(f"{name}_data", type=str(light_type).upper())
    light_data.energy = float(energy)
    if size is not None and hasattr(light_data, "size"):
        light_data.size = float(size)
    if color is not None:
        light_data.color = _vector(color, length=3, default=1.0)
    light = bpy.data.objects.new(name, light_data)
    light.location = _vector(location)
    light.rotation_euler = _vector(rotation)
    return link_object(light, collection)


def create_light(
    name: str,
    *,
    light_type: str = "AREA",
    location: Sequence[float] = (0.0, 0.0, 4.0),
    rotation: Sequence[float] = (0.0, 0.0, 0.0),
    energy: float = 500.0,
    size: float | None = None,
    color: Sequence[float] | None = None,
    collection: Any | None = None,
) -> Any:
    """Compatibility alias for generated code."""

    return add_light(
        name,
        light_type=light_type,
        location=location,
        rotation=rotation,
        energy=energy,
        size=size,
        color=color,
        collection=collection,
    )


def make_light(
    name: str,
    light_type: str = "AREA",
    *,
    location: Sequence[float] = (0.0, 0.0, 4.0),
    rotation: Sequence[float] = (0.0, 0.0, 0.0),
    energy: float = 500.0,
    size: float | None = None,
    color: Sequence[float] | None = None,
    collection: Any | None = None,
) -> Any:
    """Compatibility alias that accepts light type as a positional argument."""

    return add_light(
        name,
        light_type=light_type,
        location=location,
        rotation=rotation,
        energy=energy,
        size=size,
        color=color,
        collection=collection,
    )


def create_area_light(
    name: str,
    *,
    location: Sequence[float] = (0.0, 0.0, 4.0),
    rotation: Sequence[float] = (0.0, 0.0, 0.0),
    energy: float = 500.0,
    size: float | None = None,
    color: Sequence[float] | None = None,
    collection: Any | None = None,
) -> Any:
    """Compatibility alias for generated area lights."""

    return add_light(
        name,
        light_type="AREA",
        location=location,
        rotation=rotation,
        energy=energy,
        size=size,
        color=color,
        collection=collection,
    )


__all__ = [
    "DEFAULT_RENDER_ENGINE",
    "add_camera",
    "add_cube",
    "add_cylinder",
    "add_light",
    "add_plane",
    "add_uv_sphere",
    "available_render_engines",
    "clear_scene",
    "configure_render",
    "create_collection",
    "create_box",
    "create_camera",
    "create_area_light",
    "create_light",
    "create_material",
    "create_mesh_object",
    "create_rotor_root",
    "ensure_collection",
    "find_node_by_type",
    "get_or_create_collection",
    "link_object",
    "link_to_collection",
    "look_at",
    "make_box",
    "make_camera",
    "make_light",
    "make_material",
    "normalize_engine_name",
    "set_ll3m_properties",
    "set_render_engine",
    "animate_rotor",
]
