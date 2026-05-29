"""Deterministic static repair for simple horizontal support relations."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .animation_repair import BBox, _scene_bboxes
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
        support_bbox = bboxes.get(relation.object_id)
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
    exact = _ll3m_static_repair_bpy.data.objects.get(marker)
    if exact:
        matches.append(exact)
    for obj in _ll3m_static_repair_bpy.data.objects:
        obj_id = str(obj.get("ll3m_id", ""))
        if obj not in matches and (obj_id == marker or obj_id.startswith(marker + "_")):
            matches.append(obj)
    for obj in _ll3m_static_repair_bpy.data.objects:
        if obj not in matches and obj.name.startswith(marker):
            matches.append(obj)
    return matches

def _ll3m_static_repair_apply_delta(subject_id, delta):
    objects = _ll3m_static_repair_find_objects(subject_id)
    if not objects:
        return
    exact_roots = [
        obj for obj in objects
        if str(obj.get("ll3m_id", "")) == str(subject_id) and obj.parent is None
    ]
    exact = [obj for obj in objects if str(obj.get("ll3m_id", "")) == str(subject_id)]
    targets = exact_roots or exact[:1] or objects
    vector = _ll3m_static_repair_Vector(tuple(float(value) for value in delta))
    for obj in targets:
        obj.location = obj.location + vector

for _ll3m_static_repair_adjustment in _LL3M_STATIC_SUPPORT_REPAIR_PLAN.get("adjustments", []):
    _ll3m_static_repair_apply_delta(
        _ll3m_static_repair_adjustment.get("subject_id"),
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
