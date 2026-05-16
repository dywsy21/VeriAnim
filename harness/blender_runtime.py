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
        script = _screenshot_script(ir, output_dir, self.config.render_width, self.config.render_height)
        result = self.execute_code(script, expects_render=True)
        if result.ok:
            payload = _parse_marker_json(result.stdout, SCREENSHOT_MARKER, default={"paths": []})
            paths = [Path(path) for path in payload.get("paths", []) if Path(path).exists()]
            if paths:
                return paths
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
        return []

    def validate_animation(self, ir: GenerationIR) -> tuple[ValidationReport, dict[str, Any]]:
        if not ir.animation:
            return ValidationReport.ok(VerificationMode.DETERMINISTIC, "No animation requested."), {}
        result = self.execute_code(_animation_validation_script(ir))
        if result.ok:
            payload = _parse_marker_json(result.stdout, ANIMATION_MARKER, default={"report": {}, "trace": {}})
            return _report_from_payload(payload.get("report", {}), VerificationMode.DETERMINISTIC), payload.get("trace", {})
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
        else:
            return (
                ValidationReport.failed(
                    VerificationMode.DETERMINISTIC,
                    [ValidationIssue(code="ANIMATION_VALIDATION_EXEC_FAILED", message=result.message or result.stdout)],
                ),
                {},
            )

    def render_animation_samples(
        self,
        ir: GenerationIR,
        output_dir: Path,
        *,
        render_gif: bool = False,
    ) -> tuple[list[Path], Path | None]:
        if not ir.animation:
            return [], None
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        frames = ir.animation.verifier.sampled_frames
        if not frames:
            duration = ir.animation.duration_frames
            frames = sorted(set([1, max(1, duration // 2), duration]))
        gif_frames = list(range(1, max(1, int(ir.animation.duration_frames)) + 1)) if render_gif else []
        script = _animation_render_script(
            ir,
            sample_frames=frames,
            gif_frames=gif_frames,
            output_dir=output_dir,
            width=self.config.render_width,
            height=self.config.render_height,
        )
        result = self.execute_code(script, expects_render=True)
        if result.ok:
            payload = _parse_marker_json(result.stdout, SCREENSHOT_MARKER, default={"paths": [], "gif_frames": [], "video": None})
            paths = [Path(path) for path in payload.get("paths", []) if Path(path).exists()]
            gif_frame_paths = [Path(path) for path in payload.get("gif_frames", []) if Path(path).exists()]
            gif_path = (
                _write_animation_gif(
                    gif_frame_paths,
                    output_dir / "animation.gif",
                    fps=max(1, int(ir.animation.fps)),
                )
                if render_gif
                else None
            )
            if paths:
                return paths, gif_path
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
        return [], None


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


def _write_animation_gif(frame_paths: list[Path], output_path: Path, *, fps: int) -> Path | None:
    if not frame_paths:
        return None
    try:
        from PIL import Image
    except Exception as exc:
        raise RuntimeError("Pillow is required to write animation GIFs. Install requirements.txt.") from exc

    frames = []
    for path in frame_paths:
        with Image.open(path) as image:
            frames.append(image.convert("P", palette=Image.Palette.ADAPTIVE))
    if not frames:
        return None
    duration_ms = max(1, int(1000 / max(1, fps)))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=False,
    )
    return output_path if output_path.exists() else None


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
    views = _normalized_screenshot_views(ir)
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


def _normalized_screenshot_views(ir: GenerationIR):
    from .ir import CameraViewType, ScreenshotViewSpec

    plan = ir.scene.verifier.screenshot_plan
    views = list(plan.views)
    existing_ids = {view.id for view in views}
    primary_targets = [obj.id for obj in ir.scene.objects if obj.importance.value == "required"] or [
        obj.id for obj in ir.scene.objects[:3]
    ]
    relation_ids = [relation.id for relation in ir.scene.relations if relation.required]
    defaults = [
        ScreenshotViewSpec(
            id="three_quarter",
            view_type=CameraViewType.THREE_QUARTER,
            description="Overall inspection view",
            target_object_ids=primary_targets,
            relation_ids=relation_ids,
        ),
        ScreenshotViewSpec(
            id="relation_closeup",
            view_type=CameraViewType.RELATION_CLOSE_UP,
            description="Close view for required support, contact, and attachment relations",
            target_object_ids=[],
            relation_ids=relation_ids,
        ),
        ScreenshotViewSpec(
            id="side_support",
            view_type=CameraViewType.RIGHT,
            description="Side view for support, contact, and floating part checks",
            target_object_ids=primary_targets,
            relation_ids=relation_ids,
        ),
        ScreenshotViewSpec(
            id="top_layout",
            view_type=CameraViewType.TOP,
            description="Top view for layout and intersection checks",
            target_object_ids=primary_targets,
            relation_ids=relation_ids,
        ),
        ScreenshotViewSpec(
            id="front_support",
            view_type=CameraViewType.FRONT,
            description="Front view for object proportions and horizontal alignment",
            target_object_ids=primary_targets,
            relation_ids=relation_ids,
        ),
        ScreenshotViewSpec(
            id="left_support",
            view_type=CameraViewType.LEFT,
            description="Opposite side view for occlusion and attachment checks",
            target_object_ids=primary_targets,
            relation_ids=relation_ids,
        ),
    ]
    for default in defaults:
        if len(views) >= max(1, plan.min_required_views):
            break
        if default.id not in existing_ids:
            views.append(default)
            existing_ids.add(default.id)
    relation_targets = {
        relation.id: [relation.subject_id, relation.object_id]
        for relation in ir.scene.relations
    }
    for view in views:
        if view.target_object_ids:
            continue
        targets: list[str] = []
        for relation_id in view.relation_ids:
            targets.extend(relation_targets.get(relation_id, []))
        view.target_object_ids.extend(dict.fromkeys(targets or primary_targets))
    return views


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
    matches = []
    exact = bpy.data.objects.get(str(ll3m_id))
    if exact:
        matches.append(exact)
    for obj in bpy.data.objects:
        if obj not in matches and (obj.get("ll3m_id") == ll3m_id or obj.name.startswith(str(ll3m_id))):
            matches.append(obj)
    for obj in matches:
        if obj.animation_data and obj.animation_data.action:
            return obj
    return matches[0] if matches else None

def has_fcurve(obj, path_prefix):
    action = obj.animation_data.action if obj.animation_data else None
    if not action:
        return False
    fcurves = []
    if hasattr(action, "fcurves"):
        fcurves.extend(list(action.fcurves))
    if hasattr(action, "layers"):
        for layer in action.layers:
            for strip in getattr(layer, "strips", []):
                for bag in getattr(strip, "channelbags", []):
                    fcurves.extend(list(getattr(bag, "fcurves", [])))
    return any(fc.data_path.startswith(path_prefix) for fc in fcurves)

def distance(a, b):
    return sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)) ** 0.5

def expected_path(action):
    if action == "translate":
        return "location"
    if action == "rotate":
        return "rotation_euler"
    if action == "scale":
        return "scale"
    return None

def find_objects(ll3m_id):
    marker = str(ll3m_id)
    matches = []
    exact = bpy.data.objects.get(marker)
    if exact:
        matches.append(exact)
    for obj in bpy.data.objects:
        obj_id = str(obj.get("ll3m_id", ""))
        if obj not in matches and (obj_id == marker or obj_id.startswith(marker + "_")):
            matches.append(obj)
    for obj in bpy.data.objects:
        if obj not in matches and obj.name.startswith(marker):
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

def collect_mesh_hierarchy(objs):
    result = set()
    queue = list(objs)
    while queue:
        obj = queue.pop()
        if obj in result:
            continue
        result.add(obj)
        for child in obj.children:
            queue.append(child)
    return [o for o in result if o.type in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}}]

def aggregate_minmax(objs):
    mesh_objs = collect_mesh_hierarchy(objs)
    corners = []
    for obj in mesh_objs:
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
material_specs = {{item.get("id"): item for item in IR["scene"].get("materials", []) if item.get("id")}}

def find_material(material_id):
    mat = bpy.data.materials.get(str(material_id))
    if mat:
        return mat
    for candidate in bpy.data.materials:
        if candidate.get("ll3m_id") == material_id:
            return candidate
    return None

def color_distance(a, b):
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))

def material_candidate_colors(mat):
    colors = [mat.diffuse_color]
    if mat.use_nodes and mat.node_tree:
        for node in mat.node_tree.nodes:
            if getattr(node, "type", None) == "BSDF_PRINCIPLED" and "Base Color" in node.inputs:
                colors.append(node.inputs["Base Color"].default_value)
    return colors

for material_id, spec in material_specs.items():
    mat = find_material(material_id)
    if not mat:
        expected = spec.get("base_color")
        if expected:
            best = None
            best_distance = 999.0
            for candidate in bpy.data.materials:
                for color in material_candidate_colors(candidate):
                    dist = color_distance(color, expected)
                    if dist < best_distance:
                        best = candidate
                        best_distance = dist
            if best and best_distance <= 0.2:
                mat = best
            else:
                issue("MISSING_MATERIAL_SPEC", f"Material '{{material_id}}' was not created.", "major")
                continue
        else:
            issue("MISSING_MATERIAL_SPEC", f"Material '{{material_id}}' was not created.", "major")
            continue
    expected = spec.get("base_color")
    if expected and all(color_distance(color, expected) > 0.35 for color in material_candidate_colors(mat)):
        issue(
            "MATERIAL_COLOR_MISMATCH",
            f"Material '{{material_id}}' diffuse color does not match IR base_color. This often means shader nodes were found by localized display name instead of node.type.",
            "major",
            evidence={{"actual": [list(color) for color in material_candidate_colors(mat)], "expected": expected}},
        )

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
    views = _normalized_screenshot_views(ir)
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
from mathutils import Vector

IR = json.loads({_json(ir)!r})
issues = []
trace = {{}}

def issue(code, message, severity="major", target_id=None, frame=None, evidence=None):
    issues.append({{"code": code, "message": message, "severity": severity, "target_id": target_id, "frame": frame, "evidence": evidence or {{}}}})

def find_obj(ll3m_id):
    matches = find_objects(ll3m_id)
    return matches[0] if matches else None

def descendants(obj):
    found = []
    stack = list(getattr(obj, "children", []))
    while stack:
        child = stack.pop(0)
        found.append(child)
        stack.extend(list(getattr(child, "children", [])))
    return found

def find_objects(ll3m_id):
    marker = str(ll3m_id)
    matches = []
    exact = bpy.data.objects.get(marker)
    if exact:
        matches.append(exact)
        matches.extend(descendants(exact))
    for obj in bpy.data.objects:
        obj_id = str(obj.get("ll3m_id", ""))
        if obj not in matches and (obj_id == marker or obj_id.startswith(marker + "_") or obj.name.startswith(marker)):
            matches.append(obj)
            matches.extend([child for child in descendants(obj) if child not in matches])
    return list(dict.fromkeys(matches))

def has_fcurve(obj, path_prefix):
    action = obj.animation_data.action if obj.animation_data else None
    if not action:
        return False
    if any(getattr(fc, "data_path", "").startswith(path_prefix) for fc in getattr(action, "fcurves", [])):
        return True
    for layer in getattr(action, "layers", []) or []:
        for strip in getattr(layer, "strips", []) or []:
            for bag in getattr(strip, "channelbags", []) or []:
                if any(getattr(fc, "data_path", "").startswith(path_prefix) for fc in getattr(bag, "fcurves", [])):
                    return True
    return False

def any_has_fcurve(objs, path_prefix):
    return any(has_fcurve(obj, path_prefix) for obj in objs)

def representative_obj(objs, path_prefix):
    for obj in objs:
        if path_prefix and has_fcurve(obj, path_prefix):
            return obj
    for obj in objs:
        if obj.type in {{"MESH", "EMPTY", "CURVE", "SURFACE", "FONT", "META"}}:
            return obj
    return objs[0] if objs else None

def value_for_path(obj, path_prefix):
    if path_prefix == "rotation_euler":
        return list(obj.rotation_euler)
    if path_prefix == "scale":
        return list(obj.scale)
    return list(obj.matrix_world.translation)

def moving_representative(objs, path_prefix, frames):
    if not objs or not path_prefix or not frames:
        return representative_obj(objs, path_prefix)
    start_frame = int(frames[0])
    end_frame = int(frames[-1])
    best = None
    best_delta = -1.0
    for candidate in objs:
        bpy.context.scene.frame_set(start_frame)
        start_value = value_for_path(candidate, path_prefix)
        bpy.context.scene.frame_set(end_frame)
        end_value = value_for_path(candidate, path_prefix)
        delta = distance(start_value, end_value)
        if delta > best_delta:
            best = candidate
            best_delta = delta
    if best and best_delta > 0.001:
        return best
    return representative_obj(objs, path_prefix)

def distance(a, b):
    return sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)) ** 0.5

def expected_path(action):
    if action in ("translate", "follow_path", "camera_move", "camera_orbit"):
        return "location"
    if action == "rotate":
        return "rotation_euler"
    if action == "scale":
        return "scale"
    return None

def world_bbox(obj):
    if not obj or obj.type not in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}} or not getattr(obj, "bound_box", None):
        return []
    return [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]

def aggregate_minmax(objs):
    points = []
    for obj in objs:
        points.extend(world_bbox(obj))
    if not points:
        return None
    return (
        Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points))),
        Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points))),
    )

def bbox_gap(a, b):
    gaps = []
    for index in range(3):
        if a[1][index] < b[0][index]:
            gaps.append(b[0][index] - a[1][index])
        elif b[1][index] < a[0][index]:
            gaps.append(a[0][index] - b[1][index])
        else:
            gaps.append(0.0)
    return (gaps[0] ** 2 + gaps[1] ** 2 + gaps[2] ** 2) ** 0.5

def gripper_subset(objs):
    grippers = [
        obj for obj in objs
        if obj.type in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}}
        and ("gripper" in str(obj.get("ll3m_part", "")).lower() or "gripper" in obj.name.lower())
    ]
    return grippers or [obj for obj in objs if obj.type in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}}]

def interaction_targets(event):
    if event.get("action") in ("appear", "disappear"):
        return []
    text = " ".join([
        str(event.get("id", "")),
        str(event.get("description", "")),
        str(event.get("expected_visual_result", "")),
        " ".join(event.get("constraints", []) or []),
    ]).lower()
    if any(token in text for token in ("light", "status", "signal")):
        return []
    if not any(token in text for token in ("grasp", "gripper", "lift", "carry", "pick", "place", "transfer")):
        return []
    targets = list(event.get("target_ids", []) or [])
    if not targets and any(token in text for token in ("gripper", "lift", "carry", "pick", "place", "transfer")):
        for obj_spec in IR.get("scene", {{}}).get("objects", []):
            haystack = f"{{obj_spec.get('id', '')}} {{obj_spec.get('description', '')}} {{obj_spec.get('label', '')}}".lower()
            if any(token in haystack for token in ("gripper", "robotic_arm", "robotic arm", "end effector", "end-effector")):
                targets.append(obj_spec.get("id"))
    return [target for target in targets if target]

anim = IR.get("animation") or {{}}
duration = int(anim.get("duration_frames") or 0)
if duration > 0 and bpy.context.scene.frame_end < duration:
    issue("FRAME_END_TOO_SHORT", "Scene frame_end is shorter than AnimationSpec duration.", "major", evidence={{"frame_end": bpy.context.scene.frame_end, "duration": duration}})

events = list(anim.get("events", [])) + list(anim.get("camera_events", []))
for event in events:
    for sid in event.get("subject_ids", []):
        objs = find_objects(sid)
        if not objs:
            issue("MISSING_ANIMATED_OBJECT", f"Animated object '{{sid}}' was not found.", "critical", sid)
            continue
        action = event.get("action")
        path = expected_path(action)
        if action in ("camera_move", "camera_orbit"):
            path = "location"
        if event.get("action") not in ("camera_move", "camera_orbit") and not any(getattr(item, "animation_data", None) for item in objs):
            issue("MISSING_ANIMATION_DATA", f"Animated object '{{sid}}' and its child parts have no animation_data.", "major", sid)
        if path and not any_has_fcurve(objs, path):
            issue("MISSING_ANIMATION_FCURVE", f"Animated object '{{sid}}' has no '{{path}}' F-Curve for event '{{event.get('id')}}'.", "major", sid)
        frames = sorted(set([int(event.get("start_frame", 1)), int((event.get("start_frame", 1) + event.get("end_frame", 1)) / 2), int(event.get("end_frame", 1))]))
        obj = moving_representative(objs, path, frames)
        trace[sid] = []
        for frame in frames:
            bpy.context.scene.frame_set(frame)
            trace[sid].append({{"frame": frame, "location": list(obj.matrix_world.translation), "rotation": list(obj.rotation_euler), "scale": list(obj.scale)}})
        if path and len(trace[sid]) >= 2:
            start_sample = trace[sid][0]
            end_sample = trace[sid][-1]
            key = "rotation" if path == "rotation_euler" else path
            if distance(start_sample[key], end_sample[key]) < 0.01:
                issue("ANIMATION_NO_VISIBLE_CHANGE", f"Event '{{event.get('id')}}' does not visibly change '{{sid}}' {{path}} between sampled frames.", "major", sid, int(event.get("end_frame", 1)), {{"start": start_sample[key], "end": end_sample[key], "path": path}})

        end_transform = event.get("end_transform") or {{}}
        if path == "location" and end_transform.get("location") and trace[sid]:
            actual = trace[sid][-1]["location"]
            expected = end_transform["location"]
            if distance(actual, expected) > 0.25:
                issue("ANIMATION_END_LOCATION_MISMATCH", f"Event '{{event.get('id')}}' end location does not match AnimationSpec.", "major", sid, int(event.get("end_frame", 1)), {{"actual": actual, "expected": expected}})
        if path == "scale" and end_transform.get("scale") and trace[sid]:
            actual = trace[sid][-1]["scale"]
            expected = end_transform["scale"]
            if distance(actual, expected) > 0.25:
                issue("ANIMATION_END_SCALE_MISMATCH", f"Event '{{event.get('id')}}' end scale does not match AnimationSpec.", "major", sid, int(event.get("end_frame", 1)), {{"actual": actual, "expected": expected}})
        if path == "rotation_euler" and end_transform.get("rotation_euler") and trace[sid]:
            actual = trace[sid][-1]["rotation"]
            expected = end_transform["rotation_euler"]
            if distance(actual, expected) > 0.25:
                issue("ANIMATION_END_ROTATION_MISMATCH", f"Event '{{event.get('id')}}' end rotation does not match AnimationSpec.", "major", sid, int(event.get("end_frame", 1)), {{"actual": actual, "expected": expected}})

    targets = interaction_targets(event)
    if targets:
        frames = sorted(set([int(event.get("start_frame", 1)), int((event.get("start_frame", 1) + event.get("end_frame", 1)) / 2), int(event.get("end_frame", 1))]))
        for sid in event.get("subject_ids", []):
            subj_objs = find_objects(sid)
            if not subj_objs:
                continue
            for target_id in targets:
                target_objs = find_objects(target_id)
                if "gripper" in str(event.get("description", "")).lower() or "gripper" in str(event.get("expected_visual_result", "")).lower():
                    target_objs = gripper_subset(target_objs)
                if not target_objs:
                    continue
                for frame in frames:
                    bpy.context.scene.frame_set(frame)
                    sb = aggregate_minmax(subj_objs)
                    tb = aggregate_minmax(target_objs)
                    if not sb or not tb:
                        continue
                    gap = bbox_gap(sb, tb)
                    if gap > 0.18:
                        issue(
                            "ANIMATION_INTERACTION_GAP",
                            f"Event '{{event.get('id')}}' requires '{{sid}}' to stay visually connected to '{{target_id}}', but their bounding boxes are separated.",
                            "major",
                            sid,
                            frame,
                            {{"target_id": target_id, "gap": gap}},
                        )

report = {{"passed": not issues, "issues": issues, "summary": "Animation deterministic validation passed." if not issues else "Animation deterministic validation found issues."}}
print("{ANIMATION_MARKER}" + json.dumps({{"report": report, "trace": trace}}))
"""


def _animation_render_script(
    ir: GenerationIR,
    *,
    sample_frames: list[int],
    gif_frames: list[int],
    output_dir: Path,
    width: int,
    height: int,
) -> str:
    target_ids = [obj.id for obj in ir.scene.objects]
    return f"""
import json, math, os
import bpy
from mathutils import Vector

SAMPLE_FRAMES = json.loads({json.dumps(sample_frames)!r})
GIF_FRAMES = json.loads({json.dumps(gif_frames)!r})
TARGET_IDS = json.loads({json.dumps(target_ids)!r})
OUT_DIR = {str(output_dir).replace(chr(92), "/")!r}
GIF_DIR = os.path.join(OUT_DIR, "gif_frames").replace("\\\\", "/")
WIDTH = {int(width)}
HEIGHT = {int(height)}
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(GIF_DIR, exist_ok=True)

def find_objects(ll3m_id):
    marker = str(ll3m_id)
    matches = []
    exact = bpy.data.objects.get(marker)
    if exact:
        matches.append(exact)
    for obj in bpy.data.objects:
        obj_id = str(obj.get("ll3m_id", ""))
        if obj not in matches and (obj_id == marker or obj_id.startswith(marker + "_")):
            matches.append(obj)
    for obj in bpy.data.objects:
        if obj not in matches and obj.name.startswith(marker):
            matches.append(obj)
    return matches

def target_objects():
    objs = []
    for item in TARGET_IDS:
        objs.extend(find_objects(item))
    objs = [obj for obj in dict.fromkeys(objs) if obj.type in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}}]
    foreground = [obj for obj in objs if obj.get("ll3m_role") != "background"]
    if foreground:
        objs = foreground
    if not objs:
        objs = [obj for obj in bpy.data.objects if obj.type in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}} and not obj.name.startswith("ll3m_render_camera")]
    return objs

def bbox_points(objs):
    points = []
    for obj in objs:
        if getattr(obj, "bound_box", None):
            points.extend([obj.matrix_world @ Vector(corner) for corner in obj.bound_box])
    return points

def look_at(camera, target):
    direction = target - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

scene = bpy.context.scene
scene.render.resolution_x = WIDTH
scene.render.resolution_y = HEIGHT
scene.render.resolution_percentage = 100
original_engine = scene.render.engine
engines = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys()
if "BLENDER_EEVEE_NEXT" in engines:
    scene.render.engine = "BLENDER_EEVEE_NEXT"
elif "BLENDER_WORKBENCH" in engines:
    scene.render.engine = "BLENDER_WORKBENCH"

try:
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "Medium High Contrast"
    scene.view_settings.exposure = -1.0
    scene.view_settings.gamma = 1.0
except Exception:
    pass

all_points = []
objs = target_objects()
for frame in sorted(set(SAMPLE_FRAMES + GIF_FRAMES)):
    scene.frame_set(int(frame))
    all_points.extend(bbox_points(objs))
if not all_points:
    all_points = [Vector((0, 0, 0))]
mn = Vector((min(p.x for p in all_points), min(p.y for p in all_points), min(p.z for p in all_points)))
mx = Vector((max(p.x for p in all_points), max(p.y for p in all_points), max(p.z for p in all_points)))
center = (mn + mx) * 0.5
radius = max((mx - mn).length * 1.35, 2.0)

cam_data = bpy.data.cameras.new("ll3m_animation_sample_camera_data")
cam = bpy.data.objects.new("ll3m_animation_sample_camera", cam_data)
bpy.context.scene.collection.objects.link(cam)
cam.location = center + Vector((radius * 0.85, -radius * 0.85, radius * 0.55))
look_at(cam, center)
cam_data.lens = 35
scene.camera = cam

paths = []
gif_paths = []
try:
    for frame in SAMPLE_FRAMES:
        scene.frame_set(int(frame))
        path = os.path.join(OUT_DIR, f"frame_{{int(frame):04d}}.png").replace("\\\\", "/")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        paths.append(path)
    for frame in GIF_FRAMES:
        scene.frame_set(int(frame))
        path = os.path.join(GIF_DIR, f"frame_{{int(frame):04d}}.png").replace("\\\\", "/")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        gif_paths.append(path)
finally:
    try:
        scene.render.engine = original_engine
    except Exception:
        pass

print("{SCREENSHOT_MARKER}" + json.dumps({{"paths": paths, "gif_frames": gif_paths, "video": os.path.join(OUT_DIR, "animation.gif").replace("\\\\", "/")}}))
"""


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

def find_objects(ll3m_id):
    marker = str(ll3m_id)
    matches = []
    exact = bpy.data.objects.get(marker)
    if exact:
        matches.append(exact)
    for obj in bpy.data.objects:
        obj_id = str(obj.get("ll3m_id", ""))
        if obj not in matches and (obj_id == marker or obj_id.startswith(marker + "_")):
            matches.append(obj)
    for obj in bpy.data.objects:
        if obj not in matches and obj.name.startswith(marker):
            matches.append(obj)
    return matches

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
    if view_type in {{"close_up", "relation_close_up"}}:
        radius = max(radius * 0.55, 1.5)
        return Vector((radius * 0.75, -radius * 0.75, radius * 0.45))
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
original_engine = scene.render.engine
engines = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys()
if "BLENDER_EEVEE_NEXT" in engines:
    scene.render.engine = "BLENDER_EEVEE_NEXT"
elif "BLENDER_WORKBENCH" in engines:
    scene.render.engine = "BLENDER_WORKBENCH"

original_view_settings = {{
    "view_transform": getattr(scene.view_settings, "view_transform", None),
    "look": getattr(scene.view_settings, "look", None),
    "exposure": getattr(scene.view_settings, "exposure", None),
    "gamma": getattr(scene.view_settings, "gamma", None),
}}
original_light_energy = {{obj.name: obj.data.energy for obj in bpy.data.objects if obj.type == "LIGHT" and hasattr(obj.data, "energy")}}
original_world_strength = None
world = scene.world
if world and world.use_nodes:
    for _node in world.node_tree.nodes:
        if _node.type == "BACKGROUND":
            original_world_strength = _node.inputs[1].default_value
            break

def apply_inspection_render_settings():
    # Verification screenshots must be legible even when generated code creates
    # poor lights/exposure. Keep this temporary and restore after rendering.
    try:
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
    except Exception:
        pass
    # For Workbench engine, configure studio lighting via scene.display
    try:
        scene.display.shading.light = "STUDIO"
        scene.display.shading.studio_light = "studio.exr"
        scene.display.shading.color_type = "MATERIAL"
        scene.display.shading.show_shadows = True
        scene.display.shading.show_cavity = True
        scene.display.shading.studiolight_intensity = 1.0
    except Exception:
        pass
    # Ensure at least one adequate light exists for EEVEE/Cycles fallback
    has_light = any(obj.type == "LIGHT" for obj in bpy.data.objects if not obj.name.startswith("ll3m_"))
    if not has_light:
        light_data = bpy.data.lights.new("ll3m_inspection_light", type="SUN")
        light_data.energy = 3.0
        light_obj = bpy.data.objects.new("ll3m_inspection_light", light_data)
        light_obj.location = (0, 0, 10)
        light_obj.rotation_euler = (0.5, 0.2, -0.3)
        bpy.context.scene.collection.objects.link(light_obj)
    # Clamp extreme light values that would blow out the render
    for obj in bpy.data.objects:
        if obj.type != "LIGHT" or not hasattr(obj.data, "energy"):
            continue
        if obj.name.startswith("ll3m_"):
            continue
        if obj.data.type == "SUN":
            obj.data.energy = max(min(float(obj.data.energy), 8.0), 1.0)
        elif obj.data.type == "AREA":
            obj.data.energy = max(min(float(obj.data.energy), 800.0), 50.0)
        elif obj.data.type == "POINT":
            obj.data.energy = max(min(float(obj.data.energy), 1000.0), 50.0)
        else:
            obj.data.energy = max(min(float(obj.data.energy), 1000.0), 50.0)
    # Ensure world background is not pitch black
    if world and world.use_nodes:
        bg = None
        for node in world.node_tree.nodes:
            if node.type == "BACKGROUND":
                bg = node
                break
        if bg:
            strength = float(bg.inputs[1].default_value)
            bg.inputs[1].default_value = max(min(strength, 1.0), 0.1)

def restore_render_settings():
    try:
        scene.render.engine = original_engine
    except Exception:
        pass
    for attr, value in original_view_settings.items():
        if value is not None:
            try:
                setattr(scene.view_settings, attr, value)
            except Exception:
                pass
    for obj in bpy.data.objects:
        if obj.name in original_light_energy and hasattr(obj.data, "energy"):
            obj.data.energy = original_light_energy[obj.name]
    if original_world_strength is not None and world and world.use_nodes:
        bg = None
        for node in world.node_tree.nodes:
            if node.type == "BACKGROUND":
                bg = node
                break
        if bg:
            bg.inputs[1].default_value = original_world_strength
    # Remove inspection light if we added one
    inspection_light = bpy.data.objects.get("ll3m_inspection_light")
    if inspection_light:
        bpy.data.objects.remove(inspection_light, do_unlink=True)

apply_inspection_render_settings()

all_objs = [obj for obj in bpy.data.objects if obj.type in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}} and not obj.name.startswith("ll3m_render_camera")]
paths = []

try:
    for view in VIEWS:
        frame = view.get("frame")
        if frame is None:
            frame = FRAME_DEFAULT
        if frame is not None:
            scene.frame_set(int(frame))
        targets = []
        for item in view.get("target_object_ids", []):
            targets.extend(find_objects(item))
        targets = [obj for obj in dict.fromkeys(targets) if obj.type in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}}]
        foreground_targets = [obj for obj in targets if obj.get("ll3m_role") != "background"]
        if foreground_targets:
            targets = foreground_targets
        if not targets:
            targets = all_objs
        mn, mx = bbox_for_objects(targets)
        center = (mn + mx) * 0.5
        radius = max((mx - mn).length * 1.25, 1.15)
        cam_data = bpy.data.cameras.new("ll3m_render_camera_" + view["id"] + "_data")
        cam = bpy.data.objects.new("ll3m_render_camera_" + view["id"], cam_data)
        bpy.context.scene.collection.objects.link(cam)
        view_type = view.get("view_type", "three_quarter")
        cam.location = center + camera_offset(view_type, radius)
        look_at(cam, center)
        cam_data.lens = 70 if view_type in {{"close_up", "relation_close_up"}} else 35
        scene.camera = cam
        path = os.path.join(OUT_DIR, view["id"] + ".png").replace("\\\\", "/")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        paths.append(path)
finally:
    restore_render_settings()

print("{SCREENSHOT_MARKER}" + json.dumps({{"paths": paths, "video": None}}))
"""
