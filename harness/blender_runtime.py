"""Blender execution, deterministic validation, and render helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from blender.client import BlenderClient

from .config import HarnessConfig
from .ir import GenerationIR, Severity, ValidationIssue, ValidationReport, VerificationMode


REPORT_MARKER = "LL3M_VALIDATION_REPORT:"
SCREENSHOT_MARKER = "LL3M_SCREENSHOTS:"
ANIMATION_MARKER = "LL3M_ANIMATION_REPORT:"


@dataclass(slots=True)
class BlenderRunResult:
    ok: bool
    message: str | None
    stdout: str
    raw: dict[str, Any] | Any


class BlenderRuntime:
    def __init__(self, config: HarnessConfig):
        self.config = config

    def execute_code(self, code: str, *, expects_render: bool = False) -> BlenderRunResult:
        result = BlenderClient.execute_code(
            code,
            host=self.config.blender_host,
            port=self.config.blender_port,
            expects_render=expects_render,
            headless_enabled=self.config.headless_rendering,
            fallback_to_socket=True,
        )
        stdout = _stdout(result)
        ok, message = _infer_ok(result, stdout)
        return BlenderRunResult(ok=ok, message=message, stdout=stdout, raw=result)

    def get_scene_graph(self) -> dict[str, Any]:
        result = BlenderClient.get_scene_graph(
            include_hidden=True,
            evaluated=True,
            host=self.config.blender_host,
            port=self.config.blender_port,
        )
        if _command_ok(result):
            return _command_result(result)
        return {}

    def validate_scene(self, ir: GenerationIR) -> ValidationReport:
        # Use an injected validation script so changes to validator logic take
        # effect immediately even when Blender has an older addon instance
        # already running.
        script = _scene_validation_script(ir)
        result = self.execute_code(script)
        if not result.ok:
            return ValidationReport.failed(
                VerificationMode.DETERMINISTIC,
                [ValidationIssue(code="BLENDER_VALIDATION_EXEC_FAILED", message=result.message or result.stdout)],
            )
        return _parse_report(result.stdout, REPORT_MARKER)

    def render_screenshots(self, ir: GenerationIR, output_dir: Path) -> list[Path]:
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        views = _view_dicts(ir)
        structured = BlenderClient.render_view_plan(
            views,
            str(output_dir),
            width=self.config.render_width,
            height=self.config.render_height,
            host=self.config.blender_host,
            port=self.config.blender_port,
        )
        if _command_ok(structured):
            payload = _command_result(structured)
            paths = [Path(path) for path in payload.get("paths", []) if Path(path).exists()]
            if paths:
                return paths
        script = _screenshot_script(ir, output_dir, self.config.render_width, self.config.render_height)
        result = self.execute_code(script, expects_render=True)
        if not result.ok:
            return []
        payload = _parse_marker_json(result.stdout, SCREENSHOT_MARKER, default={"paths": []})
        return [Path(path) for path in payload.get("paths", []) if Path(path).exists()]

    def validate_animation(self, ir: GenerationIR) -> tuple[ValidationReport, dict[str, Any]]:
        if not ir.animation:
            return ValidationReport.ok(VerificationMode.DETERMINISTIC, "No animation requested."), {}
        structured = BlenderClient.run_validation(
            ir.to_dict(),
            include_scene=False,
            include_animation=True,
            host=self.config.blender_host,
            port=self.config.blender_port,
        )
        if _command_ok(structured):
            payload = _command_result(structured)
            return _report_from_payload(payload, VerificationMode.DETERMINISTIC), payload.get("trace", {})
        result = self.execute_code(_animation_validation_script(ir))
        if not result.ok:
            return (
                ValidationReport.failed(
                    VerificationMode.DETERMINISTIC,
                    [ValidationIssue(code="ANIMATION_VALIDATION_EXEC_FAILED", message=result.message or result.stdout)],
                ),
                {},
            )
        payload = _parse_marker_json(result.stdout, ANIMATION_MARKER, default={"report": {}, "trace": {}})
        return _report_from_payload(payload.get("report", {}), VerificationMode.DETERMINISTIC), payload.get("trace", {})

    def render_animation_samples(self, ir: GenerationIR, output_dir: Path) -> tuple[list[Path], Path | None]:
        if not ir.animation:
            return [], None
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        frames = ir.animation.verifier.sampled_frames
        if not frames:
            duration = ir.animation.duration_frames
            frames = sorted(set([1, max(1, duration // 2), duration]))
        structured = BlenderClient.render_animation_preview(
            str(output_dir),
            frames=frames,
            width=self.config.render_width,
            height=self.config.render_height,
            render_video=ir.animation.verifier.require_preview_video,
            host=self.config.blender_host,
            port=self.config.blender_port,
        )
        if _command_ok(structured):
            payload = _command_result(structured)
            return [Path(path) for path in payload.get("paths", []) if Path(path).exists()], (
                Path(payload["video"]) if payload.get("video") and Path(payload["video"]).exists() else None
            )
        script = _animation_render_script(ir, frames, output_dir, self.config.render_width, self.config.render_height)
        result = self.execute_code(script, expects_render=True)
        if not result.ok:
            return [], None
        payload = _parse_marker_json(result.stdout, SCREENSHOT_MARKER, default={"paths": [], "video": None})
        return [Path(path) for path in payload.get("paths", []) if Path(path).exists()], (
            Path(payload["video"]) if payload.get("video") and Path(payload["video"]).exists() else None
        )


def _stdout(result: dict[str, Any] | Any) -> str:
    if isinstance(result, dict):
        inner = result.get("result")
        if isinstance(inner, dict):
            value = inner.get("result")
            return value if isinstance(value, str) else json.dumps(value, default=str)
        if isinstance(inner, str):
            return inner
        if result.get("message"):
            return str(result.get("message"))
    return str(result)


def _infer_ok(result: dict[str, Any] | Any, stdout: str) -> tuple[bool, str | None]:
    if isinstance(result, dict) and str(result.get("status", "")).lower() == "error":
        return False, str(result.get("message") or stdout)
    if REPORT_MARKER in stdout or ANIMATION_MARKER in stdout or SCREENSHOT_MARKER in stdout:
        return True, None
    lowered = stdout.lower()
    if any(token in lowered for token in ("traceback", "exception", "error:", "failed")):
        return False, stdout
    return True, None


def _command_ok(result: dict[str, Any] | Any) -> bool:
    if not isinstance(result, dict):
        return False
    if str(result.get("status", "")).lower() != "success":
        return False
    if isinstance(result.get("result"), dict) and "Unknown command type" in str(result.get("result")):
        return False
    return True


def _command_result(result: dict[str, Any]) -> dict[str, Any]:
    payload = result.get("result")
    return payload if isinstance(payload, dict) else {}


def _parse_report(stdout: str, marker: str) -> ValidationReport:
    payload = _parse_marker_json(stdout, marker, default={"passed": False, "issues": []})
    return _report_from_payload(payload, VerificationMode.DETERMINISTIC)


def _parse_marker_json(stdout: str, marker: str, *, default: dict[str, Any]) -> dict[str, Any]:
    for line in stdout.splitlines():
        if marker in line:
            raw = line.split(marker, 1)[1].strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return default
    return default


def _report_from_payload(payload: dict[str, Any], mode: VerificationMode) -> ValidationReport:
    issues = [
        ValidationIssue(
            code=str(item.get("code", "VALIDATION_ISSUE")),
            message=str(item.get("message", "Validation issue.")),
            severity=_severity(item.get("severity", "major")),
            target_id=item.get("target_id"),
            relation_id=item.get("relation_id"),
            frame=item.get("frame"),
            suggested_fix=item.get("suggested_fix"),
            evidence=item.get("evidence") or {},
        )
        for item in payload.get("issues", []) or []
    ]
    return ValidationReport(mode=mode, passed=bool(payload.get("passed", not issues)), issues=issues, summary=payload.get("summary"))


def _severity(value: Any) -> Severity:
    if isinstance(value, Severity):
        return value
    try:
        return Severity(str(value))
    except ValueError:
        return Severity.MAJOR


def _json(ir: GenerationIR) -> str:
    return json.dumps(ir.to_dict(), ensure_ascii=False)


def _view_dicts(ir: GenerationIR) -> list[dict[str, Any]]:
    views = ir.scene.verifier.screenshot_plan.views
    if not views:
        from .ir import CameraViewType, ScreenshotViewSpec

        views = [
            ScreenshotViewSpec(id="front", view_type=CameraViewType.FRONT, description="Front view"),
            ScreenshotViewSpec(id="side", view_type=CameraViewType.RIGHT, description="Side view"),
            ScreenshotViewSpec(id="three_quarter", view_type=CameraViewType.THREE_QUARTER, description="Three-quarter view"),
        ]
    return [
        {
            "id": view.id,
            "view_type": view.view_type.value,
            "description": view.description,
            "camera_id": view.camera_id,
            "target_object_ids": view.target_object_ids,
            "relation_ids": view.relation_ids,
            "frame": view.frame,
            "crop_hint": view.crop_hint,
            "required": view.required,
        }
        for view in views
    ]


def _scene_validation_script(ir: GenerationIR) -> str:
    return f"""
import json, math
import bpy
from mathutils import Vector

IR = json.loads({_json(ir)!r})
issues = []

def issue(code, message, severity="major", target_id=None, relation_id=None, evidence=None):
    issues.append({{"code": code, "message": message, "severity": severity, "target_id": target_id, "relation_id": relation_id, "evidence": evidence or {{}}}})

def find_obj(ll3m_id):
    for obj in bpy.data.objects:
        if obj.get("ll3m_id") == ll3m_id or obj.name == ll3m_id or obj.name.startswith(ll3m_id):
            return obj
    return None

def find_objects(ll3m_id):
    matches = []
    exact = bpy.data.objects.get(ll3m_id)
    if exact:
        matches.append(exact)
    for obj in bpy.data.objects:
        if obj not in matches and obj.get("ll3m_id") == ll3m_id:
            matches.append(obj)
    for obj in bpy.data.objects:
        if obj not in matches and obj.name.startswith(str(ll3m_id)):
            matches.append(obj)
    return matches

def world_bbox(obj):
    if not obj or not getattr(obj, "bound_box", None):
        return []
    return [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]

def bbox_minmax(obj):
    corners = world_bbox(obj)
    if not corners:
        return None
    return (
        Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners))),
        Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners))),
    )

def aggregate_minmax(objs):
    corners = []
    for obj in objs:
        if obj.type not in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}}:
            continue
        corners.extend(world_bbox(obj))
    if not corners:
        return None
    return (
        Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners))),
        Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners))),
    )

def center(mm):
    return (mm[0] + mm[1]) * 0.5

objects = {{}}
for spec in IR["scene"].get("objects", []):
    matches = find_objects(spec["id"])
    if not matches:
        issue("MISSING_OBJECT", f"Object '{{spec['id']}}' was not created.", "critical", spec["id"])
        continue
    objects[spec["id"]] = matches
    mesh_parts = [obj for obj in matches if obj.type == "MESH"]
    if mesh_parts:
        if all(len(obj.data.vertices) == 0 for obj in mesh_parts):
            issue("EMPTY_MESH", f"Object '{{spec['id']}}' has no mesh vertices.", "critical", spec["id"])
        if not any(obj.data.materials for obj in mesh_parts) and spec.get("material_ids"):
            issue("MISSING_MATERIAL", f"Object '{{spec['id']}}' has no material assigned.", "major", spec["id"])

for relation in IR["scene"].get("relations", []):
    sid = relation["subject_id"]
    oid = relation["object_id"]
    subj = objects.get(sid)
    obj = objects.get(oid)
    if not subj or not obj:
        continue
    sb = aggregate_minmax(subj)
    ob = aggregate_minmax(obj)
    if not sb or not ob:
        issue("MISSING_BBOX", "Could not compute relation bounding boxes.", "major", sid, relation["id"])
        continue
    sc = center(sb)
    oc = center(ob)
    tol = float(relation.get("tolerance", 0.05))
    rtype = relation["relation_type"]
    if rtype == "on_top_of":
        overlap_x = min(sb[1].x, ob[1].x) - max(sb[0].x, ob[0].x)
        overlap_y = min(sb[1].y, ob[1].y) - max(sb[0].y, ob[0].y)
        z_gap = abs(sb[0].z - ob[1].z)
        if overlap_x <= 0 or overlap_y <= 0 or z_gap > max(tol, 0.12):
            issue("RELATION_ON_TOP_OF_FAILED", f"'{{sid}}' is not clearly on top of '{{oid}}'.", "major", sid, relation["id"], {{"z_gap": z_gap, "overlap_x": overlap_x, "overlap_y": overlap_y}})
    elif rtype == "left_of" and not (sc.x < oc.x - tol):
        issue("RELATION_LEFT_OF_FAILED", f"'{{sid}}' is not left of '{{oid}}'.", "major", sid, relation["id"], {{"subject_x": sc.x, "object_x": oc.x}})
    elif rtype == "right_of" and not (sc.x > oc.x + tol):
        issue("RELATION_RIGHT_OF_FAILED", f"'{{sid}}' is not right of '{{oid}}'.", "major", sid, relation["id"], {{"subject_x": sc.x, "object_x": oc.x}})
    elif rtype == "near":
        max_dist = relation.get("max_distance") or 2.0
        dist = (sc - oc).length
        if dist > max_dist:
            issue("RELATION_NEAR_FAILED", f"'{{sid}}' is too far from '{{oid}}'.", "major", sid, relation["id"], {{"distance": dist, "max_distance": max_dist}})
    elif rtype == "not_intersecting":
        overlap = (min(sb[1].x, ob[1].x) - max(sb[0].x, ob[0].x), min(sb[1].y, ob[1].y) - max(sb[0].y, ob[0].y), min(sb[1].z, ob[1].z) - max(sb[0].z, ob[0].z))
        if all(v > tol for v in overlap):
            issue("RELATION_INTERSECTION_FAILED", f"'{{sid}}' appears to intersect '{{oid}}'.", "major", sid, relation["id"], {{"overlap": overlap}})

if IR["scene"].get("cameras") and not bpy.context.scene.camera:
    issue("MISSING_ACTIVE_CAMERA", "Scene has camera specs but no active camera.", "major")

report = {{"passed": not issues, "issues": issues, "summary": "Scene deterministic validation passed." if not issues else "Scene deterministic validation found issues."}}
print("{REPORT_MARKER}" + json.dumps(report))
"""


def _screenshot_script(ir: GenerationIR, output_dir: Path, width: int, height: int) -> str:
    views = ir.scene.verifier.screenshot_plan.views
    if not views:
        from .ir import CameraViewType, ScreenshotViewSpec

        views = [
            ScreenshotViewSpec(id="front", view_type=CameraViewType.FRONT, description="Front view"),
            ScreenshotViewSpec(id="side", view_type=CameraViewType.RIGHT, description="Side view"),
            ScreenshotViewSpec(id="three_quarter", view_type=CameraViewType.THREE_QUARTER, description="Three-quarter view"),
        ]
    view_dicts = [
        {
            "id": view.id,
            "view_type": view.view_type.value,
            "target_object_ids": view.target_object_ids,
            "relation_ids": view.relation_ids,
            "frame": view.frame,
        }
        for view in views
    ]
    return _render_views_script(view_dicts, output_dir, width, height, frame_default=None)


def _animation_validation_script(ir: GenerationIR) -> str:
    return f"""
import json
import bpy

IR = json.loads({_json(ir)!r})
issues = []
trace = {{}}

def issue(code, message, severity="major", target_id=None, frame=None, evidence=None):
    issues.append({{"code": code, "message": message, "severity": severity, "target_id": target_id, "frame": frame, "evidence": evidence or {{}}}})

def find_obj(ll3m_id):
    for obj in bpy.data.objects:
        if obj.get("ll3m_id") == ll3m_id or obj.name == ll3m_id or obj.name.startswith(ll3m_id):
            return obj
    return None

anim = IR.get("animation") or {{}}
duration = int(anim.get("duration_frames") or 0)
if duration > 0 and bpy.context.scene.frame_end < duration:
    issue("FRAME_END_TOO_SHORT", "Scene frame_end is shorter than AnimationSpec duration.", "major", evidence={{"frame_end": bpy.context.scene.frame_end, "duration": duration}})

events = list(anim.get("events", [])) + list(anim.get("camera_events", []))
for event in events:
    for sid in event.get("subject_ids", []):
        obj = find_obj(sid)
        if not obj:
            issue("MISSING_ANIMATED_OBJECT", f"Animated object '{{sid}}' was not found.", "critical", sid)
            continue
        if event.get("action") not in ("camera_move", "camera_orbit") and not obj.animation_data:
            issue("MISSING_ANIMATION_DATA", f"Animated object '{{sid}}' has no animation_data.", "major", sid)
        frames = sorted(set([int(event.get("start_frame", 1)), int((event.get("start_frame", 1) + event.get("end_frame", 1)) / 2), int(event.get("end_frame", 1))]))
        trace[sid] = []
        for frame in frames:
            bpy.context.scene.frame_set(frame)
            trace[sid].append({{"frame": frame, "location": list(obj.matrix_world.translation), "rotation": list(obj.rotation_euler), "scale": list(obj.scale)}})

report = {{"passed": not issues, "issues": issues, "summary": "Animation deterministic validation passed." if not issues else "Animation deterministic validation found issues."}}
print("{ANIMATION_MARKER}" + json.dumps({{"report": report, "trace": trace}}))
"""


def _animation_render_script(ir: GenerationIR, frames: list[int], output_dir: Path, width: int, height: int) -> str:
    views = [
        {"id": f"frame_{frame:04d}", "view_type": "three_quarter", "target_object_ids": [], "relation_ids": [], "frame": frame}
        for frame in frames
    ]
    return _render_views_script(views, output_dir, width, height, frame_default=1)


def _render_views_script(view_dicts: list[dict[str, Any]], output_dir: Path, width: int, height: int, frame_default: int | None) -> str:
    return f"""
import json, math, os
import bpy
from mathutils import Vector

VIEWS = json.loads({json.dumps(view_dicts, ensure_ascii=False)!r})
OUT_DIR = {str(output_dir).replace(chr(92), "/")!r}
WIDTH = {int(width)}
HEIGHT = {int(height)}
FRAME_DEFAULT = {repr(frame_default)}
os.makedirs(OUT_DIR, exist_ok=True)

def find_obj(ll3m_id):
    for obj in bpy.data.objects:
        if obj.get("ll3m_id") == ll3m_id or obj.name == ll3m_id or obj.name.startswith(ll3m_id):
            return obj
    return None

def bbox_for_objects(objs):
    points = []
    for obj in objs:
        if getattr(obj, "bound_box", None):
            points.extend([obj.matrix_world @ Vector(corner) for corner in obj.bound_box])
    if not points:
        points = [Vector((0, 0, 0))]
    mn = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    mx = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return mn, mx

def look_at(camera, target):
    direction = target - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

def camera_offset(view_type, radius):
    if view_type == "front":
        return Vector((0, -radius, radius * 0.35))
    if view_type == "back":
        return Vector((0, radius, radius * 0.35))
    if view_type == "left":
        return Vector((-radius, 0, radius * 0.35))
    if view_type == "right":
        return Vector((radius, 0, radius * 0.35))
    if view_type == "top":
        return Vector((0, 0, radius))
    return Vector((radius * 0.8, -radius * 0.8, radius * 0.55))

scene = bpy.context.scene
scene.render.resolution_x = WIDTH
scene.render.resolution_y = HEIGHT
scene.render.resolution_percentage = 100
engines = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys()
if "BLENDER_EEVEE_NEXT" in engines:
    scene.render.engine = "BLENDER_EEVEE_NEXT"
elif "BLENDER_EEVEE" in engines:
    scene.render.engine = "BLENDER_EEVEE"

all_objs = [obj for obj in bpy.data.objects if obj.type in {{"MESH", "CURVE", "EMPTY"}} and not obj.name.startswith("ll3m_render_camera")]
paths = []

for view in VIEWS:
    frame = view.get("frame")
    if frame is None:
        frame = FRAME_DEFAULT
    if frame is not None:
        scene.frame_set(int(frame))
    targets = [find_obj(item) for item in view.get("target_object_ids", [])]
    targets = [obj for obj in targets if obj]
    if not targets:
        targets = all_objs
    mn, mx = bbox_for_objects(targets)
    center = (mn + mx) * 0.5
    radius = max((mx - mn).length * 1.4, 3.0)
    cam_data = bpy.data.cameras.new("ll3m_render_camera_" + view["id"] + "_data")
    cam = bpy.data.objects.new("ll3m_render_camera_" + view["id"], cam_data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = center + camera_offset(view.get("view_type", "three_quarter"), radius)
    look_at(cam, center)
    cam_data.lens = 35
    scene.camera = cam
    path = os.path.join(OUT_DIR, view["id"] + ".png").replace("\\\\", "/")
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    paths.append(path)

print("{SCREENSHOT_MARKER}" + json.dumps({{"paths": paths, "video": None}}))
"""
