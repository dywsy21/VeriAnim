"""Deterministic static repair for simple horizontal support relations."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .animation_repair import BBox, MESH_BBOX_TYPES, _bbox_from_payload, _candidate_object_ids, _scene_bboxes
from .ir import GenerationIR, RelationType, ValidationReport


@dataclass(frozen=True, slots=True)
class StaticSupportAdjustment:
    relation_id: str
    subject_id: str
    support_id: str
    delta: tuple[float, float, float]
    subject_bottom_before: float
    subject_bottom_after: float
    support_top: float
    overlap_before: tuple[float, float]
    overlap_after: tuple[float, float]
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "subject_id": self.subject_id,
            "support_id": self.support_id,
            "delta": list(self.delta),
            "subject_bottom_before": self.subject_bottom_before,
            "subject_bottom_after": self.subject_bottom_after,
            "support_top": self.support_top,
            "overlap_before": list(self.overlap_before),
            "overlap_after": list(self.overlap_after),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class StaticSupportRepairPlan:
    applied: bool
    adjustments: tuple[StaticSupportAdjustment, ...]
    skipped: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "adjustments": [adjustment.to_dict() for adjustment in self.adjustments],
            "skipped": list(self.skipped),
        }


def repair_static_support(
    ir: GenerationIR,
    scene_graph: dict[str, Any],
    deterministic_report: ValidationReport,
    *,
    margin: float = 0.02,
    epsilon: float = 1e-5,
) -> StaticSupportRepairPlan:
    """Plan root-object translations for failed horizontal ``on_top_of`` checks."""

    issues = [
        issue
        for issue in deterministic_report.issues
        if issue.code == "RELATION_ON_TOP_OF_FAILED" and issue.relation_id
    ]
    if not issues:
        return StaticSupportRepairPlan(applied=False, adjustments=(), skipped=("No RELATION_ON_TOP_OF_FAILED issue.",))

    relation_by_id = {relation.id: relation for relation in ir.scene.relations}
    object_ids = {obj.id for obj in ir.scene.objects}
    bboxes = _scene_bboxes(scene_graph, object_ids)
    adjustments: list[StaticSupportAdjustment] = []
    skipped: list[str] = []

    for issue in issues:
        relation = relation_by_id.get(str(issue.relation_id))
        if relation is None:
            skipped.append(f"{issue.relation_id}: relation not found.")
            continue
        if relation.relation_type != RelationType.ON_TOP_OF:
            skipped.append(f"{relation.id}: relation is {relation.relation_type.value}, not on_top_of.")
            continue
        subject_bbox = bboxes.get(relation.subject_id)
        support_bbox = _support_bbox_for_repair(scene_graph, relation.object_id, object_ids, subject_bbox) if subject_bbox else None
        if subject_bbox is None or support_bbox is None:
            skipped.append(f"{relation.id}: missing bbox for {relation.subject_id} or {relation.object_id}.")
            continue

        support_top = support_bbox.max[2]
        dx = _axis_delta_for_overlap(subject_bbox, support_bbox, axis=0, margin=margin)
        dy = _axis_delta_for_overlap(subject_bbox, support_bbox, axis=1, margin=margin)
        dz = support_top - subject_bbox.min[2]
        if max(abs(dx), abs(dy), abs(dz)) <= epsilon:
            skipped.append(f"{relation.id}: subject already satisfies support contact.")
            continue

        delta = (dx, dy, dz)
        repaired_bbox = _translate_bbox(subject_bbox, delta)
        adjustment = StaticSupportAdjustment(
            relation_id=relation.id,
            subject_id=relation.subject_id,
            support_id=relation.object_id,
            delta=delta,
            subject_bottom_before=subject_bbox.min[2],
            subject_bottom_after=repaired_bbox.min[2],
            support_top=support_top,
            overlap_before=_xy_overlap(subject_bbox, support_bbox),
            overlap_after=_xy_overlap(repaired_bbox, support_bbox),
            notes=_repair_notes(issue.evidence, support_top),
        )
        adjustments.append(adjustment)

    return StaticSupportRepairPlan(applied=bool(adjustments), adjustments=tuple(adjustments), skipped=tuple(skipped))


def blender_static_support_repair_script(plan: StaticSupportRepairPlan) -> str:
    """Create a Blender snippet that translates repaired subject roots."""

    if not plan.applied:
        return ""
    payload = json.dumps(plan.to_dict(), ensure_ascii=True, sort_keys=True)
    return f"""
# LL3M deterministic static support repair
import json as _ll3m_static_repair_json
from mathutils import Vector as _ll3m_static_repair_Vector
import bpy as _ll3m_static_repair_bpy

_LL3M_STATIC_SUPPORT_REPAIR_PLAN = _ll3m_static_repair_json.loads({payload!r})

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
    before_locations = {{obj: obj.matrix_world.translation.copy() for obj in objects}}
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
        if obj.type not in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}} or not getattr(obj, "bound_box", None):
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
    support_bbox = _ll3m_static_repair_select_support_bbox(subject_bbox, _ll3m_static_repair_find_objects(support_id))
    if not subject_bbox or not support_bbox:
        return vector
    subject_min, subject_max = subject_bbox
    support_min, support_max = support_bbox
    return _ll3m_static_repair_Vector((
        _ll3m_static_repair_axis_delta(subject_min, subject_max, support_min, support_max, 0),
        _ll3m_static_repair_axis_delta(subject_min, subject_max, support_min, support_max, 1),
        float(support_max.z - subject_min.z),
    ))

def _ll3m_static_repair_select_support_bbox(subject_bbox, support_objects):
    if not subject_bbox:
        return _ll3m_static_repair_world_bbox(support_objects)
    candidates = []
    for obj in support_objects:
        bbox = _ll3m_static_repair_world_bbox([obj])
        if not bbox:
            continue
        bmin, bmax = bbox
        size = bmax - bmin
        if max(abs(float(size.x)), abs(float(size.y)), abs(float(size.z))) <= 1e-6:
            continue
        candidates.append(bbox)
    if not candidates:
        return _ll3m_static_repair_world_bbox(support_objects)
    subject_min, subject_max = subject_bbox
    def axis_gap(bmin, bmax, axis):
        if subject_max[axis] < bmin[axis]:
            return bmin[axis] - subject_max[axis]
        if bmax[axis] < subject_min[axis]:
            return subject_min[axis] - bmax[axis]
        return 0.0
    def score(bbox):
        bmin, bmax = bbox
        size = bmax - bmin
        xy_gap = axis_gap(bmin, bmax, 0) + axis_gap(bmin, bmax, 1)
        z_gap = abs(float(subject_min.z - bmax.z))
        top_above_penalty = max(0.0, float(bmax.z - subject_min.z))
        return (z_gap + top_above_penalty * 2.0, xy_gap, abs(float(size.z)))
    return min(candidates, key=score)

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
""".strip()


def _axis_delta_for_overlap(subject_bbox: BBox, support_bbox: BBox, *, axis: int, margin: float) -> float:
    overlap = min(subject_bbox.max[axis], support_bbox.max[axis]) - max(subject_bbox.min[axis], support_bbox.min[axis])
    if overlap > 0:
        return 0.0
    subject_half = subject_bbox.size[axis] * 0.5
    low = support_bbox.min[axis] + subject_half + margin
    high = support_bbox.max[axis] - subject_half - margin
    if low <= high:
        target_center = min(max(subject_bbox.center[axis], low), high)
    else:
        target_center = support_bbox.center[axis]
    return target_center - subject_bbox.center[axis]


def _support_bbox_for_repair(
    scene_graph: dict[str, Any],
    support_id: str,
    object_ids: set[str],
    subject_bbox: BBox,
) -> BBox | None:
    """Choose the most plausible support surface for a subject.

    Compound supports such as multi-level shelves have an aggregate bbox whose
    top is often not the intended contact surface. Prefer the child/part bbox
    whose top is closest to the subject bottom, with x/y proximity as a
    secondary signal.
    """

    candidates: list[BBox] = []
    for obj in scene_graph.get("objects", []) if isinstance(scene_graph, dict) else []:
        if not isinstance(obj, dict):
            continue
        obj_type = obj.get("type")
        if isinstance(obj_type, str) and obj_type not in MESH_BBOX_TYPES:
            continue
        if support_id not in _candidate_object_ids(obj, object_ids):
            continue
        bbox = _bbox_from_payload(obj.get("bbox"))
        if bbox is not None and max(bbox.size) > 1e-6:
            candidates.append(bbox)
    if not candidates:
        return _scene_bboxes(scene_graph, {support_id}).get(support_id)
    return min(candidates, key=lambda bbox: _support_surface_score(subject_bbox, bbox))


def _support_surface_score(subject_bbox: BBox, support_bbox: BBox) -> tuple[float, float, float]:
    xy_gap = _axis_gap(subject_bbox, support_bbox, 0) + _axis_gap(subject_bbox, support_bbox, 1)
    z_gap = abs(subject_bbox.min[2] - support_bbox.max[2])
    top_above_penalty = max(0.0, support_bbox.max[2] - subject_bbox.min[2])
    return (z_gap + top_above_penalty * 2.0, xy_gap, support_bbox.size[2])


def _axis_gap(subject_bbox: BBox, support_bbox: BBox, axis: int) -> float:
    if subject_bbox.max[axis] < support_bbox.min[axis]:
        return support_bbox.min[axis] - subject_bbox.max[axis]
    if support_bbox.max[axis] < subject_bbox.min[axis]:
        return subject_bbox.min[axis] - support_bbox.max[axis]
    return 0.0


def _translate_bbox(bbox: BBox, delta: tuple[float, float, float]) -> BBox:
    return BBox(
        min=tuple(bbox.min[index] + delta[index] for index in range(3)),  # type: ignore[arg-type]
        max=tuple(bbox.max[index] + delta[index] for index in range(3)),  # type: ignore[arg-type]
    )


def _xy_overlap(subject_bbox: BBox, support_bbox: BBox) -> tuple[float, float]:
    return (
        min(subject_bbox.max[0], support_bbox.max[0]) - max(subject_bbox.min[0], support_bbox.min[0]),
        min(subject_bbox.max[1], support_bbox.max[1]) - max(subject_bbox.min[1], support_bbox.min[1]),
    )


def _repair_notes(evidence: dict[str, Any], support_top: float) -> tuple[str, ...]:
    notes = ["deterministic static support repair", "support top from scene graph bbox"]
    try:
        reported_support_z = float(evidence.get("support_z"))
    except (TypeError, ValueError):
        reported_support_z = None
    if reported_support_z is not None and abs(reported_support_z - support_top) > 1e-5:
        notes.append(f"ignored report support_z {reported_support_z}")
    return tuple(notes)
