"""Deterministic animation path repair for simple support crossings."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import json
from typing import Any

from .ir import (
    AnimationAction,
    ContactConstraintType,
    GenerationIR,
    Interpolation,
    KeyframeSpec,
    MotionPathSpec,
    TransformSpec,
)


@dataclass(frozen=True, slots=True)
class BBox:
    min: tuple[float, float, float]
    max: tuple[float, float, float]

    @property
    def center(self) -> tuple[float, float, float]:
        return tuple((self.min[index] + self.max[index]) * 0.5 for index in range(3))  # type: ignore[return-value]

    @property
    def size(self) -> tuple[float, float, float]:
        return tuple(self.max[index] - self.min[index] for index in range(3))  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class RepairKeyframe:
    frame: int
    location: tuple[float, float, float]
    label: str


@dataclass(frozen=True, slots=True)
class RepairedEventPlan:
    event_id: str
    subject_id: str
    support_id: str
    travel_axis: str
    lane_axis: str
    start_frame: int
    end_frame: int
    support_start_frame: int
    support_end_frame: int
    keyframes: tuple[RepairKeyframe, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnimationRepairPlan:
    applied: bool
    plans: tuple[RepairedEventPlan, ...]
    skipped: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "plans": [
                {
                    "event_id": plan.event_id,
                    "subject_id": plan.subject_id,
                    "support_id": plan.support_id,
                    "travel_axis": plan.travel_axis,
                    "lane_axis": plan.lane_axis,
                    "start_frame": plan.start_frame,
                    "end_frame": plan.end_frame,
                    "support_start_frame": plan.support_start_frame,
                    "support_end_frame": plan.support_end_frame,
                    "keyframes": [
                        {"frame": item.frame, "location": list(item.location), "label": item.label}
                        for item in plan.keyframes
                    ],
                    "notes": list(plan.notes),
                }
                for plan in self.plans
            ],
            "skipped": list(self.skipped),
        }


AXIS_INDEX = {"x": 0, "y": 1, "z": 2}
INDEX_AXIS = ("x", "y", "z")
SUPPORT_TOKENS = ("bridge", "deck", "platform", "ramp", "road", "table", "shelf")
CROSSING_SUPPORT_TOKENS = ("bridge", "deck", "platform", "ramp")
SINGLE_RIDE_SUPPORT_TOKENS = ("belt", "conveyor", "shelf", "table")
MESH_BBOX_TYPES = {"MESH", "CURVE", "SURFACE", "FONT", "META"}


def repair_animation_ir(
    ir: GenerationIR,
    scene_graph: dict[str, Any],
    *,
    margin: float = 0.08,
) -> tuple[GenerationIR, AnimationRepairPlan]:
    """Return a repaired IR copy plus a deterministic repair plan.

    The first pass is deliberately narrow: horizontal support crossings where a
    translated subject moves over a deck/platform-like support.
    """

    repaired = copy.deepcopy(ir)
    if not repaired.animation:
        return repaired, AnimationRepairPlan(applied=False, plans=(), skipped=("No animation spec.",))

    object_ids = {obj.id for obj in repaired.scene.objects}
    bboxes = _scene_bboxes(scene_graph, object_ids)
    plans: list[RepairedEventPlan] = []
    skipped: list[str] = []

    for event in repaired.animation.events:
        if event.action not in {AnimationAction.TRANSLATE, AnimationAction.FOLLOW_PATH}:
            skipped.append(f"{event.id}: unsupported action {event.action.value}.")
            continue
        if not event.subject_ids:
            skipped.append(f"{event.id}: no animated subject.")
            continue
        subject_id = event.subject_ids[0]
        constraints = _event_constraints(repaired, event, subject_id)
        support_constraint = _event_support_constraint(constraints, repaired, event=event)
        if support_constraint is None:
            skipped.append(f"{event.id}: no deck/platform support constraint.")
            continue
        support_id = support_constraint.object_id
        subject_bbox = bboxes.get(subject_id)
        support_bbox = bboxes.get(support_id)
        if subject_bbox is None or support_bbox is None:
            skipped.append(f"{event.id}: missing bbox for {subject_id} or {support_id}.")
            continue
        start_location = _location(event.start_transform) or _first_path_location(event)
        end_location = _location(event.end_transform) or _last_path_location(event)
        if start_location is None or end_location is None:
            skipped.append(f"{event.id}: missing start/end location.")
            continue

        subject_root_to_bottom = _scene_root_to_bottom(scene_graph, subject_id, subject_bbox, start_location)
        distinct_support_ids = {
            constraint.object_id
            for constraint in constraints
            if constraint.constraint_type == ContactConstraintType.SUPPORT and constraint.object_id in bboxes
        }
        support_tokens = _support_tokens(repaired, support_id)
        if len(distinct_support_ids) < 2 and (
            any(token in support_tokens for token in SINGLE_RIDE_SUPPORT_TOKENS)
            or not any(token in support_tokens for token in CROSSING_SUPPORT_TOKENS)
        ):
            skipped.append(f"{event.id}: single support ride on {support_id} does not need crossing repair.")
            continue
        plan = None
        if len(distinct_support_ids) >= 3:
            plan = _build_support_sequence_plan(
                event_id=event.id,
                subject_id=subject_id,
                primary_support_id=support_id,
                subject_bbox=subject_bbox,
                subject_root_to_bottom=subject_root_to_bottom,
                bboxes=bboxes,
                constraints=constraints,
                start_frame=int(event.start_frame),
                end_frame=int(event.end_frame),
            )
        if plan is None:
            plan = _build_support_crossing_plan(
                event_id=event.id,
                subject_id=subject_id,
                support_id=support_id,
                subject_bbox=subject_bbox,
                support_bbox=support_bbox,
                subject_root_to_bottom=subject_root_to_bottom,
                bboxes=bboxes,
                constraints=constraints,
                start_location=start_location,
                end_location=end_location,
                start_frame=int(event.start_frame),
                end_frame=int(event.end_frame),
                margin=margin,
            )
        if plan is None:
            plan = _build_support_sequence_plan(
                event_id=event.id,
                subject_id=subject_id,
                primary_support_id=support_id,
                subject_bbox=subject_bbox,
                subject_root_to_bottom=subject_root_to_bottom,
                bboxes=bboxes,
                constraints=constraints,
                start_frame=int(event.start_frame),
                end_frame=int(event.end_frame),
            )
        if plan is None:
            skipped.append(f"{event.id}: could not build support crossing plan.")
            continue

        event.path = MotionPathSpec(
            keyframes=[
                KeyframeSpec(
                    frame=item.frame,
                    transform=TransformSpec(location=item.location),
                    interpolation=Interpolation.LINEAR,
                    description=item.label,
                )
                for item in plan.keyframes
            ],
            follow_orientation=bool(event.path.follow_orientation) if event.path else False,
        )
        event.start_transform = TransformSpec(location=plan.keyframes[0].location)
        event.end_transform = TransformSpec(location=plan.keyframes[-1].location)
        event.interpolation = Interpolation.LINEAR
        support_constraint.start_frame = plan.support_start_frame
        support_constraint.end_frame = plan.support_end_frame
        support_constraint.description = _append_note(
            support_constraint.description,
            "Deterministically repaired to cover only deck-overlap frames.",
        )
        _normalize_nonpenetration_windows(constraints, plan)
        _normalize_terminal_support_windows(constraints, plan)
        _ensure_sampled_frames(repaired, plan)
        plans.append(plan)

    return repaired, AnimationRepairPlan(applied=bool(plans), plans=tuple(plans), skipped=tuple(skipped))


def blender_repair_script(plan: AnimationRepairPlan) -> str:
    """Create a Blender Python snippet that rewrites repaired subject keyframes."""

    if not plan.applied:
        return ""
    payload = json.dumps(plan.to_dict(), ensure_ascii=True, sort_keys=True)
    blocks = [_blender_repair_event_block(index, event_plan) for index, event_plan in enumerate(plan.plans)]
    body = "\n\n".join(blocks)
    return f"""

# LL3M deterministic animation path repair
import json as _ll3m_repair_json
import bpy as _ll3m_repair_bpy
from mathutils import Vector as _ll3m_repair_Vector

_LL3M_ANIMATION_REPAIR_PLAN = _ll3m_repair_json.loads({payload!r})

def _ll3m_repair_descendants(obj):
    found = []
    stack = list(getattr(obj, "children", []))
    while stack:
        child = stack.pop(0)
        if child in found:
            continue
        found.append(child)
        stack.extend(list(getattr(child, "children", [])))
    return found

def _ll3m_repair_add_match(matches, obj):
    if obj not in matches:
        matches.append(obj)
    for child in _ll3m_repair_descendants(obj):
        if child not in matches:
            matches.append(child)

def _ll3m_repair_add_parent_roots(matches, marker):
    for obj in list(matches):
        parent = getattr(obj, "parent", None)
        while parent is not None:
            matching_children = [
                child
                for child in getattr(parent, "children", [])
                if child in matches
                or str(child.get("ll3m_id", "")) == marker
                or child.name.startswith(marker)
            ]
            if len(matching_children) >= 2 or parent.name.startswith(marker) or "root" in parent.name.lower():
                if parent not in matches:
                    matches.insert(0, parent)
                _ll3m_repair_add_match(matches, parent)
                break
            parent = getattr(parent, "parent", None)

def _ll3m_repair_find_objects(ll3m_id):
    marker = str(ll3m_id)
    matches = []
    exact = _ll3m_repair_bpy.data.objects.get(marker)
    if exact:
        _ll3m_repair_add_match(matches, exact)
    for obj in _ll3m_repair_bpy.data.objects:
        obj_id = str(obj.get("ll3m_id", ""))
        if obj not in matches and (obj_id == marker or obj_id.startswith(marker + "_")):
            _ll3m_repair_add_match(matches, obj)
    for obj in _ll3m_repair_bpy.data.objects:
        if obj not in matches and obj.name.startswith(marker):
            _ll3m_repair_add_match(matches, obj)
    _ll3m_repair_add_parent_roots(matches, marker)
    return matches

def _ll3m_repair_iter_action_fcurves(action):
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

def _ll3m_repair_remove_fcurve(collection, fcurve):
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

def _ll3m_repair_clear_location_animation(obj):
    if obj.animation_data and obj.animation_data.action:
        for collection, fcurve in list(_ll3m_repair_iter_action_fcurves(obj.animation_data.action)):
            if fcurve.data_path == "location":
                _ll3m_repair_remove_fcurve(collection, fcurve)

def _ll3m_repair_normalize_child_offsets(root, objects, reference_location):
    direct_children = [obj for obj in objects if obj is not root and obj.parent == root]
    if not direct_children:
        return
    center = _ll3m_repair_Vector((0.0, 0.0, 0.0))
    for child in direct_children:
        center += child.location
    center /= len(direct_children)
    reference = _ll3m_repair_Vector(tuple(float(value) for value in reference_location))
    root_extent = max(float(value) for value in getattr(root, "dimensions", (1.0, 1.0, 1.0)) if float(value) >= 0.0)
    threshold = max(root_extent * 2.0, 10.0)
    if center.length <= max(root_extent * 0.75, 0.25):
        return
    if getattr(root, "type", "") != "EMPTY" and (center - reference).length > threshold:
        return
    try:
        basis = root.matrix_world.to_3x3().inverted()
    except Exception:
        basis = None
    for child in direct_children:
        offset = child.location - center
        child.location = basis @ offset if basis is not None else offset

def _ll3m_repair_select_anchor(ll3m_id, objects):
    marker = str(ll3m_id)
    exact = _ll3m_repair_bpy.data.objects.get(marker)
    if exact in objects:
        return exact
    candidates = [obj for obj in objects if str(obj.get("ll3m_id", "")) == marker]
    named_roots = [
        obj
        for obj in objects
        if (obj.name == marker or "root" in obj.name.lower())
        and any(child in objects for child in _ll3m_repair_descendants(obj))
    ]
    if named_roots:
        named_roots.sort(key=lambda obj: (0 if obj.name == marker else 1, 0 if "root" in obj.name.lower() else 1, obj.name))
        return named_roots[0]
    parent_roots = [obj for obj in objects if getattr(obj, "parent", None) not in objects]
    with_children = [
        obj
        for obj in parent_roots
        if any(child in objects for child in _ll3m_repair_descendants(obj))
    ]
    if with_children:
        return with_children[0]
    empty_roots = [obj for obj in parent_roots if getattr(obj, "type", "") == "EMPTY"]
    if empty_roots:
        return empty_roots[0]
    return (parent_roots or candidates or objects or [None])[0]

def _ll3m_repair_uses_flat_group(anchor, objects):
    if anchor is None:
        return False
    if any(child in objects for child in _ll3m_repair_descendants(anchor)):
        return False
    parent_roots = [obj for obj in objects if getattr(obj, "parent", None) not in objects]
    return len(parent_roots) > 1

def _ll3m_repair_apply_flat_group_keyframes(anchor, objects, keyframes):
    anchor_location = _ll3m_repair_Vector(anchor.location)
    offsets = [(obj, _ll3m_repair_Vector(obj.location) - anchor_location) for obj in objects]
    for obj, offset in offsets:
        for keyframe in keyframes:
            location = _ll3m_repair_Vector(tuple(float(value) for value in keyframe.get("location", [0.0, 0.0, 0.0]))) + offset
            _ll3m_repair_insert_location(obj, list(location), keyframe.get("frame", 1))
        _ll3m_repair_set_linear_location(obj)

def _ll3m_repair_world_bbox(objects):
    _ll3m_repair_bpy.context.view_layer.update()
    points = []
    for obj in objects:
        if obj.type not in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}} or not getattr(obj, "bound_box", None):
            continue
        points.extend(obj.matrix_world @ _ll3m_repair_Vector(corner) for corner in obj.bound_box)
    if not points:
        return None
    return (
        _ll3m_repair_Vector((min(point.x for point in points), min(point.y for point in points), min(point.z for point in points))),
        _ll3m_repair_Vector((max(point.x for point in points), max(point.y for point in points), max(point.z for point in points))),
    )

def _ll3m_repair_root_to_bottom(root, objects):
    bbox = _ll3m_repair_world_bbox(objects)
    if not bbox:
        return 0.0
    return float(root.matrix_world.translation.z - bbox[0].z)

def _ll3m_repair_support_top(support_id):
    support_objects = _ll3m_repair_find_objects(support_id)
    bbox = _ll3m_repair_world_bbox(support_objects)
    if not bbox:
        return None
    return float(bbox[1].z)

def _ll3m_repair_recalibrate_keyframes(plan, root, objects):
    keyframes = list(plan.get("keyframes", []))
    support_top = _ll3m_repair_support_top(plan.get("support_id"))
    root_to_bottom = _ll3m_repair_root_to_bottom(root, objects)
    support_z = support_top + root_to_bottom + 0.001 if support_top is not None else None
    terminal_z = None
    for terminal_id in ("ground", "floor", "terrain"):
        terminal_top = _ll3m_repair_support_top(terminal_id)
        if terminal_top is not None:
            terminal_z = terminal_top + root_to_bottom + 0.001
            break
    support_start = int(plan.get("support_start_frame", 0))
    support_end = int(plan.get("support_end_frame", 0))
    for keyframe in keyframes:
        frame = int(keyframe.get("frame", 0))
        location = list(keyframe.get("location", [0.0, 0.0, 0.0]))
        label = str(keyframe.get("label", "")).lower()
        label_prefix = "centered on support "
        if label.startswith(label_prefix):
            label_support_top = _ll3m_repair_support_top(label[len(label_prefix):].strip())
            if label_support_top is not None:
                location[2] = label_support_top + root_to_bottom + 0.001
                keyframe["location"] = location
                continue
        if label == "ground outside support footprint" and terminal_z is not None:
            location[2] = terminal_z
            keyframe["location"] = location
        elif support_z is not None and (
            support_start <= frame <= support_end
            or "lift outside support footprint" in label
            or "support height" in label
        ):
            location[2] = support_z
            keyframe["location"] = location
    return keyframes

def _ll3m_repair_insert_location(obj, location, frame):
    obj.location = tuple(location)
    obj.keyframe_insert(data_path="location", frame=int(frame))

def _ll3m_repair_set_linear_location(obj):
    if obj.animation_data and obj.animation_data.action:
        for _ll3m_repair_collection, _ll3m_repair_fcurve in _ll3m_repair_iter_action_fcurves(obj.animation_data.action):
            if _ll3m_repair_fcurve.data_path == "location":
                for _ll3m_repair_key in _ll3m_repair_fcurve.keyframe_points:
                    _ll3m_repair_key.interpolation = "LINEAR"

{body}
""".rstrip()


def _scene_bboxes(scene_graph: dict[str, Any], object_ids: set[str]) -> dict[str, BBox]:
    bboxes: dict[str, BBox] = {}
    for obj in scene_graph.get("objects", []) if isinstance(scene_graph, dict) else []:
        if not isinstance(obj, dict):
            continue
        obj_type = obj.get("type")
        if isinstance(obj_type, str) and obj_type not in MESH_BBOX_TYPES:
            continue
        bbox = _bbox_from_payload(obj.get("bbox"))
        if bbox is None:
            continue
        for obj_id in _candidate_object_ids(obj, object_ids):
            current = bboxes.get(obj_id)
            bboxes[obj_id] = bbox if current is None else _merge_bbox(current, bbox)
    return bboxes


def _candidate_object_ids(obj: dict[str, Any], object_ids: set[str]) -> set[str]:
    candidates: set[str] = set()
    for key in ("ll3m_id", "name", "parent"):
        value = obj.get(key)
        if isinstance(value, str) and value:
            candidates.update(_matching_object_ids(value, object_ids))
    for child in obj.get("children", []) or []:
        if isinstance(child, str) and child:
            candidates.update(_matching_object_ids(child, object_ids))
    return candidates


def _matching_object_ids(value: str, object_ids: set[str]) -> set[str]:
    clean = value.rsplit(".", 1)[0] if value.rsplit(".", 1)[-1].isdigit() else value
    return {object_id for object_id in object_ids if clean == object_id or clean.startswith(object_id + "_")}


def _scene_root_to_bottom(
    scene_graph: dict[str, Any],
    subject_id: str,
    subject_bbox: BBox,
    fallback_location: tuple[float, float, float],
) -> float:
    best = None
    for obj in scene_graph.get("objects", []) if isinstance(scene_graph, dict) else []:
        if not isinstance(obj, dict):
            continue
        if str(obj.get("ll3m_id", "")) != subject_id:
            continue
        location = obj.get("location")
        bbox = _bbox_from_payload(obj.get("bbox"))
        if _is_vec3(location) and bbox is not None:
            return float(location[2]) - bbox.min[2]
        best = obj
    if isinstance(best, dict):
        location = best.get("location")
        if _is_vec3(location):
            return float(location[2]) - subject_bbox.min[2]
    return fallback_location[2] - subject_bbox.min[2]


def _merge_bbox(left: BBox, right: BBox) -> BBox:
    return BBox(
        min=tuple(min(left.min[index], right.min[index]) for index in range(3)),  # type: ignore[arg-type]
        max=tuple(max(left.max[index], right.max[index]) for index in range(3)),  # type: ignore[arg-type]
    )


def _blender_repair_event_block(plan_index: int, plan: RepairedEventPlan) -> str:
    subject_literal = json.dumps(plan.subject_id, ensure_ascii=False)
    return f"""
_ll3m_repair_plan = _LL3M_ANIMATION_REPAIR_PLAN["plans"][{plan_index}]
_ll3m_repair_objects = _ll3m_repair_find_objects({subject_literal})
_ll3m_repair_obj = _ll3m_repair_select_anchor({subject_literal}, _ll3m_repair_objects)
if _ll3m_repair_obj is not None:
    _ll3m_repair_obj["ll3m_id"] = {subject_literal}
    for _ll3m_repair_clear_obj in _ll3m_repair_objects:
        _ll3m_repair_clear_location_animation(_ll3m_repair_clear_obj)
    if _ll3m_repair_uses_flat_group(_ll3m_repair_obj, _ll3m_repair_objects):
        _ll3m_repair_keyframes = _ll3m_repair_recalibrate_keyframes(_ll3m_repair_plan, _ll3m_repair_obj, _ll3m_repair_objects)
        _ll3m_repair_apply_flat_group_keyframes(_ll3m_repair_obj, _ll3m_repair_objects, _ll3m_repair_keyframes)
    else:
        _ll3m_repair_normalize_child_offsets(
            _ll3m_repair_obj,
            _ll3m_repair_objects,
            ({plan.keyframes[0].location[0]!r}, {plan.keyframes[0].location[1]!r}, {plan.keyframes[0].location[2]!r}),
        )
        for _ll3m_repair_keyframe in _ll3m_repair_recalibrate_keyframes(_ll3m_repair_plan, _ll3m_repair_obj, _ll3m_repair_objects):
            _ll3m_repair_insert_location(
                _ll3m_repair_obj,
                _ll3m_repair_keyframe.get("location", [0.0, 0.0, 0.0]),
                _ll3m_repair_keyframe.get("frame", 1),
            )
        _ll3m_repair_set_linear_location(_ll3m_repair_obj)
    _ll3m_repair_bpy.context.view_layer.update()
""".strip()


def _event_constraints(ir: GenerationIR, event: Any, subject_id: str) -> list[Any]:
    constraints: list[Any] = []
    constraints.extend(
        constraint
        for constraint in event.contact_constraints
        if constraint.subject_id == subject_id or constraint.subject_id in event.subject_ids
    )
    if ir.animation:
        constraints.extend(
            constraint
            for constraint in ir.animation.contact_constraints
            if constraint.subject_id == subject_id or constraint.subject_id in event.subject_ids
        )
    return constraints


def _bbox_from_payload(payload: Any) -> BBox | None:
    if isinstance(payload, list):
        points = [point for point in payload if _is_vec3(point)]
        if not points:
            return None
        return BBox(
            min=tuple(min(float(point[index]) for point in points) for index in range(3)),  # type: ignore[arg-type]
            max=tuple(max(float(point[index]) for point in points) for index in range(3)),  # type: ignore[arg-type]
        )
    if not isinstance(payload, dict):
        return None
    min_value = payload.get("min")
    max_value = payload.get("max")
    if not _is_vec3(min_value) or not _is_vec3(max_value):
        corners = payload.get("corners")
        if isinstance(corners, list):
            return _bbox_from_payload(corners)
        return None
    return BBox(min=tuple(float(v) for v in min_value), max=tuple(float(v) for v in max_value))  # type: ignore[arg-type]


def _is_vec3(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 3 and all(isinstance(v, (int, float)) for v in value)


def _event_support_constraint(constraints: list[Any], ir: GenerationIR, *, event: Any | None = None) -> Any | None:
    candidates: list[tuple[int, Any]] = []
    for constraint in constraints:
        if constraint.constraint_type != ContactConstraintType.SUPPORT:
            continue
        tokens = _support_tokens(ir, constraint.object_id)
        if any(token in tokens for token in SUPPORT_TOKENS):
            score = 1
            if any(token in tokens for token in ("ramp", "bridge", "deck", "platform")):
                score += 4
            if any(token in tokens for token in ("road", "ground")):
                score -= 2
            if event is not None:
                event_start = int(getattr(event, "start_frame", 0))
                event_end = int(getattr(event, "end_frame", 0))
                if int(constraint.start_frame) > event_start and int(constraint.end_frame) < event_end:
                    score += 3
            candidates.append((score, constraint))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _support_tokens(ir: GenerationIR, object_id: str) -> set[str]:
    objects_by_id = {obj.id: obj for obj in ir.scene.objects}
    support = objects_by_id.get(object_id)
    text = " ".join(
        [
            str(object_id),
            str(getattr(support, "label", "") or ""),
            str(getattr(support, "description", "") or ""),
        ]
    ).lower()
    return set(text.replace("_", " ").replace("-", " ").split())


def _location(transform: TransformSpec | None) -> tuple[float, float, float] | None:
    if transform and transform.location:
        return tuple(float(value) for value in transform.location)  # type: ignore[return-value]
    return None


def _first_path_location(event: Any) -> tuple[float, float, float] | None:
    if not event.path:
        return None
    for keyframe in event.path.keyframes:
        location = _location(keyframe.transform)
        if location is not None:
            return location
    if event.path.points:
        return tuple(float(value) for value in event.path.points[0])  # type: ignore[return-value]
    return None


def _last_path_location(event: Any) -> tuple[float, float, float] | None:
    if not event.path:
        return None
    for keyframe in reversed(event.path.keyframes):
        location = _location(keyframe.transform)
        if location is not None:
            return location
    if event.path.points:
        return tuple(float(value) for value in event.path.points[-1])  # type: ignore[return-value]
    return None


def _build_support_crossing_plan(
    *,
    event_id: str,
    subject_id: str,
    support_id: str,
    subject_bbox: BBox,
    support_bbox: BBox,
    subject_root_to_bottom: float,
    bboxes: dict[str, BBox],
    constraints: list[Any],
    start_location: tuple[float, float, float],
    end_location: tuple[float, float, float],
    start_frame: int,
    end_frame: int,
    margin: float,
) -> RepairedEventPlan | None:
    dx = abs(end_location[0] - start_location[0])
    dy = abs(end_location[1] - start_location[1])
    travel_axis = "x" if dx >= dy else "y"
    lane_axis = "y" if travel_axis == "x" else "x"
    t_index = AXIS_INDEX[travel_axis]
    l_index = AXIS_INDEX[lane_axis]
    subject_size = subject_bbox.size
    subject_half_travel = subject_size[t_index] * 0.5
    outside_half_travel = subject_half_travel * 1.5
    support_top_z = support_bbox.max[2]
    deck_root_z = support_top_z + subject_root_to_bottom + 0.001

    support_min = support_bbox.min[t_index]
    support_max = support_bbox.max[t_index]
    start_side = -1.0 if start_location[t_index] <= support_bbox.center[t_index] else 1.0
    end_side = -1.0 if end_location[t_index] <= support_bbox.center[t_index] else 1.0
    clearance = max(margin, 0.02)
    outside_start_t = support_min - outside_half_travel - clearance if start_side < 0 else support_max + outside_half_travel + clearance
    outside_end_t = support_min - outside_half_travel - clearance if end_side < 0 else support_max + outside_half_travel + clearance
    inside_start_t = support_min + subject_half_travel + clearance if start_side < 0 else support_max - subject_half_travel - clearance
    inside_end_t = support_min + subject_half_travel + clearance if end_side < 0 else support_max - subject_half_travel - clearance
    if start_side == end_side:
        return None
    if inside_start_t > inside_end_t and start_side < 0 < end_side:
        return None
    if inside_end_t > inside_start_t and start_side > 0 > end_side:
        return None

    lane_value = _clamp(start_location[l_index], support_bbox.min[l_index] + subject_size[l_index] * 0.5, support_bbox.max[l_index] - subject_size[l_index] * 0.5)
    frames = _phase_frames(start_frame, end_frame)
    start_ground = list(start_location)
    start_ground[t_index] = outside_start_t
    start_ground[l_index] = lane_value
    start_terminal_z = _terminal_support_root_z(
        constraints,
        bboxes,
        frame=start_frame,
        subject_root_to_bottom=subject_root_to_bottom,
        crossing_support_id=support_id,
    )
    if start_terminal_z is not None:
        start_ground[2] = start_terminal_z
    lift = list(start_ground)
    lift[2] = deck_root_z
    enter = list(lift)
    enter[t_index] = inside_start_t
    middle = list(enter)
    middle[t_index] = support_bbox.center[t_index]
    exit_support = list(lift)
    exit_support[t_index] = inside_end_t
    outside_high = list(exit_support)
    outside_high[t_index] = outside_end_t
    end_ground = list(end_location)
    end_ground[t_index] = outside_end_t
    end_ground[l_index] = lane_value
    end_terminal_z = _terminal_support_root_z(
        constraints,
        bboxes,
        frame=end_frame,
        subject_root_to_bottom=subject_root_to_bottom,
        crossing_support_id=support_id,
    )
    if end_terminal_z is not None:
        end_ground[2] = end_terminal_z

    keyframes = (
        RepairKeyframe(frames[0], _vec3(start_ground), "ground outside support footprint"),
        RepairKeyframe(frames[1], _vec3(lift), "lift outside support footprint"),
        RepairKeyframe(frames[2], _vec3(enter), "enter support footprint at support height"),
        RepairKeyframe(frames[3], _vec3(middle), "middle on support"),
        RepairKeyframe(frames[4], _vec3(exit_support), "exit support footprint at support height"),
        RepairKeyframe(frames[5], _vec3(outside_high), "outside support footprint at support height"),
        RepairKeyframe(frames[6], _vec3(end_ground), "ground outside support footprint"),
    )
    return RepairedEventPlan(
        event_id=event_id,
        subject_id=subject_id,
        support_id=support_id,
        travel_axis=travel_axis,
        lane_axis=lane_axis,
        start_frame=start_frame,
        end_frame=end_frame,
        support_start_frame=frames[2],
        support_end_frame=frames[4],
        keyframes=keyframes,
        notes=("deterministic horizontal support crossing repair",),
    )


def _build_support_sequence_plan(
    *,
    event_id: str,
    subject_id: str,
    primary_support_id: str,
    subject_bbox: BBox,
    subject_root_to_bottom: float,
    bboxes: dict[str, BBox],
    constraints: list[Any],
    start_frame: int,
    end_frame: int,
) -> RepairedEventPlan | None:
    support_constraints = [
        constraint
        for constraint in constraints
        if constraint.constraint_type == ContactConstraintType.SUPPORT and constraint.object_id in bboxes
    ]
    if len(support_constraints) < 2:
        return None
    support_constraints.sort(key=lambda constraint: (int(constraint.start_frame), int(constraint.end_frame)))
    ordered = support_constraints
    if len({constraint.object_id for constraint in ordered}) < 2:
        return None
    centers = [bboxes[constraint.object_id].center for constraint in ordered]
    dx = abs(centers[-1][0] - centers[0][0])
    dy = abs(centers[-1][1] - centers[0][1])
    travel_axis = "x" if dx >= dy else "y"
    lane_axis = "y" if travel_axis == "x" else "x"
    t_index = AXIS_INDEX[travel_axis]
    if abs(centers[-1][t_index] - centers[0][t_index]) <= 1e-6:
        return None

    phase_windows: list[tuple[Any, int, int]] = []
    for index, constraint in enumerate(ordered):
        phase_start = min(max(int(constraint.start_frame), start_frame), end_frame)
        phase_end = min(max(int(constraint.end_frame), start_frame), end_frame)
        if index + 1 < len(ordered):
            next_start = min(max(int(ordered[index + 1].start_frame), start_frame), end_frame)
            if phase_end >= next_start:
                phase_end = max(phase_start, next_start - 1)
        if index > 0 and phase_windows:
            previous_end = phase_windows[-1][2]
            phase_start = max(phase_start, min(end_frame, previous_end + 1))
            phase_end = max(phase_start, phase_end)
        constraint.start_frame = phase_start
        constraint.end_frame = phase_end
        phase_windows.append((constraint, phase_start, phase_end))

    keyframes: list[RepairKeyframe] = []
    for constraint, phase_start, phase_end in phase_windows:
        bbox = bboxes[constraint.object_id]
        location = list(bbox.center)
        location[2] = bbox.max[2] + subject_root_to_bottom + 0.001
        for frame in (phase_start, phase_end):
            keyframes.append(RepairKeyframe(frame, _vec3(location), f"centered on support {constraint.object_id}"))
    keyframes = sorted(keyframes, key=lambda item: item.frame)
    unique_keyframes: list[RepairKeyframe] = []
    for keyframe in keyframes:
        if unique_keyframes and keyframe.frame == unique_keyframes[-1].frame:
            unique_keyframes[-1] = keyframe
            continue
        if unique_keyframes and keyframe.frame < unique_keyframes[-1].frame:
            continue
        unique_keyframes.append(keyframe)
    unique_keyframes[-1] = RepairKeyframe(end_frame, unique_keyframes[-1].location, unique_keyframes[-1].label)
    primary_constraints = [constraint for constraint in ordered if constraint.object_id == primary_support_id]
    support_start_frame = min(int(constraint.start_frame) for constraint in primary_constraints) if primary_constraints else unique_keyframes[0].frame
    support_end_frame = max(int(constraint.end_frame) for constraint in primary_constraints) if primary_constraints else unique_keyframes[-1].frame
    return RepairedEventPlan(
        event_id=event_id,
        subject_id=subject_id,
        support_id=primary_support_id,
        travel_axis=travel_axis,
        lane_axis=lane_axis,
        start_frame=start_frame,
        end_frame=end_frame,
        support_start_frame=support_start_frame,
        support_end_frame=support_end_frame,
        keyframes=tuple(unique_keyframes),
        notes=("deterministic support sequence repair from scene graph support centers",),
    )


def _phase_frames(start_frame: int, end_frame: int) -> tuple[int, int, int, int, int, int, int]:
    span = max(6, end_frame - start_frame)
    offsets = [0.0, 0.18, 0.30, 0.50, 0.70, 0.82, 1.0]
    frames = [int(round(start_frame + span * offset)) for offset in offsets]
    for index in range(1, len(frames)):
        frames[index] = max(frames[index], frames[index - 1] + 1)
    frames[-1] = end_frame
    return tuple(frames)  # type: ignore[return-value]


def _vec3(value: list[float]) -> tuple[float, float, float]:
    return (float(value[0]), float(value[1]), float(value[2]))


def _clamp(value: float, low: float, high: float) -> float:
    if low > high:
        return value
    return min(max(value, low), high)


def _terminal_support_root_z(
    constraints: list[Any],
    bboxes: dict[str, BBox],
    *,
    frame: int,
    subject_root_to_bottom: float,
    crossing_support_id: str,
) -> float | None:
    for constraint in constraints:
        if constraint.constraint_type != ContactConstraintType.SUPPORT:
            continue
        if constraint.object_id == crossing_support_id:
            continue
        if not (int(constraint.start_frame) <= frame <= int(constraint.end_frame)):
            continue
        support_bbox = bboxes.get(constraint.object_id)
        if support_bbox is None:
            continue
        return support_bbox.max[2] + subject_root_to_bottom + 0.001
    return None


def _append_note(description: str | None, note: str) -> str:
    if not description:
        return note
    if note in description:
        return description
    return f"{description} {note}"


def _normalize_nonpenetration_windows(constraints: list[Any], plan: RepairedEventPlan) -> None:
    for constraint in constraints:
        if constraint.constraint_type == ContactConstraintType.NONPENETRATION and constraint.object_id == plan.support_id:
            constraint.start_frame = plan.start_frame
            constraint.end_frame = plan.end_frame


def _normalize_terminal_support_windows(constraints: list[Any], plan: RepairedEventPlan) -> None:
    for constraint in constraints:
        if constraint.constraint_type != ContactConstraintType.SUPPORT:
            continue
        if constraint.object_id == plan.support_id:
            continue
        if not _is_terminal_support_id(str(constraint.object_id)):
            continue
        start_frame = int(constraint.start_frame)
        end_frame = int(constraint.end_frame)
        if start_frame <= plan.start_frame <= end_frame and end_frame < plan.support_start_frame:
            constraint.start_frame = plan.start_frame
            constraint.end_frame = plan.start_frame
            constraint.description = _append_note(
                constraint.description,
                "Deterministically narrowed to the repaired ground start frame.",
            )
        elif start_frame > plan.support_end_frame and start_frame <= plan.end_frame <= end_frame:
            constraint.start_frame = plan.end_frame
            constraint.end_frame = plan.end_frame
            constraint.description = _append_note(
                constraint.description,
                "Deterministically narrowed to the repaired ground end frame.",
            )


def _is_terminal_support_id(object_id: str) -> bool:
    tokens = set(object_id.lower().replace("_", " ").replace("-", " ").split())
    return bool(tokens & {"ground", "floor", "terrain"})


def _ensure_sampled_frames(ir: GenerationIR, plan: RepairedEventPlan) -> None:
    if not ir.animation:
        return
    frames = set(ir.animation.verifier.sampled_frames or [])
    middle_keyframe = plan.keyframes[len(plan.keyframes) // 2]
    frames.update({plan.start_frame, plan.support_start_frame, middle_keyframe.frame, plan.support_end_frame, plan.end_frame})
    frames = {frame for frame in frames if 1 <= frame <= ir.animation.duration_frames}
    ir.animation.verifier.sampled_frames = sorted(frames)
