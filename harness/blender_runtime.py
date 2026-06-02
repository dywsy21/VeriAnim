"""Blender execution, deterministic validation, and render helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from blender.client import BlenderClient

from .config import HarnessConfig
from .ir import GenerationIR, Severity, ValidationIssue, ValidationReport, VerificationMode


REPORT_MARKER = "LL3M_VALIDATION_REPORT:"
SCREENSHOT_MARKER = "LL3M_SCREENSHOTS:"
ANIMATION_MARKER = "LL3M_ANIMATION_REPORT:"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _clean_scene_prefix() -> str:
    return f"""
import importlib, sys
if {str(PROJECT_ROOT)!r} not in sys.path:
    sys.path.insert(0, {str(PROJECT_ROOT)!r})
try:
    import blender.ll3m_utils as _ll3m_utils
    importlib.reload(_ll3m_utils)
except Exception:
    pass
import bpy
for _ll3m_obj in list(bpy.context.scene.objects):
    _ll3m_obj.select_set(True)
bpy.ops.object.delete()
for _ll3m_collection in list(bpy.data.collections):
    if not _ll3m_collection.users:
        bpy.data.collections.remove(_ll3m_collection)
for _ll3m_mesh in list(bpy.data.meshes):
    if not _ll3m_mesh.users:
        bpy.data.meshes.remove(_ll3m_mesh)
for _ll3m_material in list(bpy.data.materials):
    if not _ll3m_material.users:
        bpy.data.materials.remove(_ll3m_material)
""".strip()


@dataclass(slots=True)
class BlenderRunResult:
    ok: bool
    message: str | None
    stdout: str
    raw: dict[str, Any] | Any
    stderr: str = ""
    traceback: str | None = None

    def diagnostic_text(self) -> str:
        parts: list[str] = []
        if self.stdout:
            parts.append(self.stdout.rstrip())
        if self.stderr:
            parts.append("[stderr]\n" + self.stderr.rstrip())
        if self.traceback:
            parts.append("[traceback]\n" + self.traceback.rstrip())
        if self.message and self.message not in "\n".join(parts):
            parts.append("[message]\n" + self.message)
        return "\n\n".join(part for part in parts if part).rstrip() + ("\n" if parts else "")


class BlenderRuntime:
    def __init__(self, config: HarnessConfig):
        self.config = config

    def execute_scene_code(self, code: str) -> BlenderRunResult:
        """Execute generated scene code from a clean Blender scene.

        Validation and render helper scripts intentionally run against the
        current scene, but generated scene scripts must not be allowed to pass
        by accidentally reusing geometry from a previous failed round.
        """

        return self.execute_code(_clean_scene_prefix() + "\n" + code)

    def execute_code(self, code: str, *, expects_render: bool = False) -> BlenderRunResult:
        result = BlenderClient.execute_code(
            code,
            host=self.config.blender_host,
            port=self.config.blender_port,
            expects_render=expects_render,
            headless_enabled=self.config.headless_rendering,
            fallback_to_socket=True,
        )
        execution = _execution_payload(result)
        stdout = _stdout(result)
        stderr = _payload_text(execution, "stderr")
        traceback_text = _payload_text(execution, "traceback") or None
        ok, message = _infer_ok(result, execution)
        return BlenderRunResult(ok=ok, message=message, stdout=stdout, stderr=stderr, traceback=traceback_text, raw=result)

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
                _normalize_verification_renders(paths)
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
                _normalize_verification_renders(paths)
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
            _normalize_verification_renders([*paths, *gif_frame_paths])
            gif_path = (
                _write_animation_gif(
                    gif_frame_paths,
                    output_dir / "animation.gif",
                    fps=max(1, int(ir.animation.fps)),
                )
                if render_gif
                else None
            )
            mp4_path = (
                _write_animation_mp4(
                    gif_frame_paths,
                    output_dir / "animation.mp4",
                    fps=max(1, int(ir.animation.fps)),
                )
                if render_gif
                else None
            )
            if paths:
                return paths, mp4_path or gif_path
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
            paths = [Path(path) for path in payload.get("paths", []) if Path(path).exists()]
            _normalize_verification_renders(paths)
            return paths, (
                Path(payload["video"]) if payload.get("video") and Path(payload["video"]).exists() else None
            )
        return [], None


def _execution_payload(result: dict[str, Any] | Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    inner = result.get("result")
    if isinstance(inner, dict):
        return inner
    return result


def _stdout(result: dict[str, Any] | Any) -> str:
    if isinstance(result, dict):
        inner = _execution_payload(result)
        if inner:
            if isinstance(inner.get("stdout"), str):
                return inner["stdout"]
            value = inner.get("result")
            if value is not None:
                return value if isinstance(value, str) else json.dumps(value, default=str)
        if isinstance(inner, str):
            return inner
        if result.get("message"):
            return str(result.get("message"))
    return str(result)


def _payload_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _infer_ok(result: dict[str, Any] | Any, execution: dict[str, Any]) -> tuple[bool, str | None]:
    if isinstance(result, dict) and str(result.get("status", "")).lower() == "error":
        return False, str(result.get("message") or _stdout(result))
    if "ok" in execution:
        ok = bool(execution.get("ok"))
        if ok:
            return True, None
        message = execution.get("message") or execution.get("traceback") or execution.get("stderr") or execution.get("stdout")
        return False, str(message or "Blender execution failed.")
    if "executed" in execution:
        ok = bool(execution.get("executed"))
        if ok:
            return True, None
        message = execution.get("message") or execution.get("result")
        return False, str(message or "Blender execution failed.")
    stdout = _stdout(result)
    if REPORT_MARKER in stdout or ANIMATION_MARKER in stdout or SCREENSHOT_MARKER in stdout:
        return True, None
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


def _write_animation_mp4(frame_paths: list[Path], output_path: Path, *, fps: int) -> Path | None:
    if not frame_paths or not shutil.which("ffmpeg"):
        return None
    frame_dir = frame_paths[0].parent
    if not all(path.parent == frame_dir for path in frame_paths):
        return None
    first = frame_paths[0].name
    if not first.startswith("frame_") or not first.endswith(".png"):
        return None
    pattern = str(frame_dir / "frame_%04d.png")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-framerate",
        str(max(1, fps)),
        "-i",
        pattern,
        "-vf",
        "scale=640:-2,format=yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True, timeout=120)
    except Exception:
        return None
    return output_path if output_path.exists() else None


def _normalize_verification_renders(paths: list[Path]) -> None:
    """Normalize inspection renders so verification is not gated by exposure.

    Generated Blender scripts often create extreme lights, near-white materials,
    or enclosed rooms that make screenshots unreadably bright or dark. The
    verifier should judge geometry and scene semantics, so we keep a conservative
    post-render luminance target similar to Blender's layout inspection view.
    """

    if not paths:
        return
    try:
        from PIL import Image, ImageEnhance, ImageOps, ImageStat
    except Exception:
        return

    target_mean = 128.0
    for path in paths:
        try:
            with Image.open(path) as image:
                rgba = image.convert("RGBA")
                rgb = rgba.convert("RGB")
                luminance = ImageOps.grayscale(rgb)
                mean = float(ImageStat.Stat(luminance).mean[0])
                if mean <= 1.0:
                    continue
                hist = luminance.histogram()
                total = sum(hist)
                threshold = total * 0.95
                cumulative = 0
                p95 = 255
                for value, count in enumerate(hist):
                    cumulative += count
                    if cumulative >= threshold:
                        p95 = value
                        break
                mean_factor = target_mean / mean
                highlight_factor = 210.0 / max(float(p95), 1.0)
                factor = max(0.45, min(1.85, mean_factor, highlight_factor))
                adjusted = ImageEnhance.Brightness(rgb).enhance(factor)
                if p95 > 235:
                    adjusted = ImageEnhance.Contrast(adjusted).enhance(0.9)
                elif mean > 170.0:
                    adjusted = ImageEnhance.Contrast(adjusted).enhance(1.08)
                elif mean < 80.0:
                    adjusted = ImageEnhance.Contrast(adjusted).enhance(1.04)
                if "A" in image.getbands():
                    adjusted.putalpha(rgba.getchannel("A"))
                adjusted.save(path)
        except Exception:
            continue


def _severity(value: Any) -> Severity:
    if isinstance(value, Severity):
        return value
    try:
        return Severity(str(value))
    except ValueError:
        return Severity.MAJOR


def _json(ir: GenerationIR) -> str:
    return json.dumps(ir.to_dict(), ensure_ascii=False)


def build_deformation_statistics(
    samples_by_target: dict[str, list[dict[str, Any]]],
    *,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Summarize sampled bbox/deformation evidence for extension prototypes."""

    thresholds = thresholds or {}
    targets: list[dict[str, Any]] = []
    for target_id, samples in sorted(samples_by_target.items()):
        ordered = sorted(samples, key=lambda item: int(item.get("frame", 0)))
        if not ordered:
            continue
        bbox_sizes = [
            [float(component) for component in sample.get("bbox_size", [])[:3]]
            for sample in ordered
            if isinstance(sample.get("bbox_size"), list) and len(sample.get("bbox_size", [])) >= 3
        ]
        displacement_values = [
            float(sample.get("displacement_spread", 0.0))
            for sample in ordered
            if sample.get("displacement_spread") is not None
        ]
        bbox_delta = 0.0
        if len(bbox_sizes) >= 2:
            first = bbox_sizes[0]
            bbox_delta = max(
                max(abs(sample[index] - first[index]) for index in range(3))
                for sample in bbox_sizes[1:]
            )
        displacement_spread = max(displacement_values) if displacement_values else 0.0
        threshold = float(thresholds.get(target_id, 0.05))
        targets.append(
            {
                "target_id": target_id,
                "frame_range": [int(ordered[0].get("frame", 0)), int(ordered[-1].get("frame", 0))],
                "sampled_frames": [int(sample.get("frame", 0)) for sample in ordered],
                "bbox_delta": bbox_delta,
                "displacement_spread": displacement_spread,
                "threshold": threshold,
                "passed": max(bbox_delta, displacement_spread) >= threshold,
            }
        )
    passed = bool(targets) and all(target["passed"] for target in targets)
    return {
        "frame_range": [
            min((target["frame_range"][0] for target in targets), default=0),
            max((target["frame_range"][1] for target in targets), default=0),
        ],
        "target_ids": [target["target_id"] for target in targets],
        "targets": targets,
        "threshold": min((target["threshold"] for target in targets), default=0.0),
        "passed": passed,
        "review_required": False,
    }


def _view_dicts(ir: GenerationIR) -> list[dict[str, Any]]:
    views = _normalized_screenshot_views(ir)
    relation_frames = _relation_frame_overrides(ir)
    return [
        {
            "id": view.id,
            "view_type": view.view_type.value,
            "description": view.description,
            "camera_id": view.camera_id,
            "target_object_ids": view.target_object_ids,
            "relation_ids": view.relation_ids,
            "frame": view.frame if view.frame is not None else _view_relation_frame(view.relation_ids, relation_frames),
            "crop_hint": view.crop_hint,
            "required": view.required,
            "min_subject_pixel_fraction": view.min_subject_pixel_fraction,
            "must_show_full_targets": view.must_show_full_targets,
            "purpose": view.purpose,
        }
        for view in views
    ]


def _relation_frame_overrides(ir: GenerationIR) -> dict[str, int]:
    if not ir.animation:
        return {}
    start_frame = 1
    end_frame = int(ir.animation.duration_frames)
    events = [*ir.animation.events, *ir.animation.camera_events]
    if events:
        start_frame = min(int(event.start_frame) for event in events)
        end_frame = max(int(event.end_frame) for event in events)

    overrides: dict[str, int] = {}
    subject_visibility_frames: dict[str, int] = {}
    for event in events:
        if event.action.value == "appear":
            visible_frame = int(event.end_frame)
        elif event.action.value == "disappear":
            visible_frame = int(event.start_frame)
        else:
            continue
        for subject_id in event.subject_ids:
            previous = subject_visibility_frames.get(subject_id)
            subject_visibility_frames[subject_id] = visible_frame if previous is None else max(previous, visible_frame)

    for relation in ir.scene.relations:
        if relation.frame is not None:
            overrides[relation.id] = int(relation.frame)
            continue
        relation_subjects = (relation.subject_id, relation.object_id)
        visible_frames = [
            subject_visibility_frames[subject_id]
            for subject_id in relation_subjects
            if subject_id in subject_visibility_frames
        ]
        if visible_frames:
            overrides[relation.id] = max(visible_frames)
            continue
        text = " ".join(
            part
            for part in (
                relation.id,
                relation.description or "",
            )
            if part
        ).lower()
        tokens = text.replace("_", " ").replace("-", " ").split()
        if any(token in tokens for token in ("final", "end", "ending", "ended", "last", "stop", "stopped", "stops")):
            overrides[relation.id] = end_frame
        elif any(token in tokens for token in ("initial", "start", "starts", "starting", "started", "first", "begin", "begins", "beginning", "resting")):
            overrides[relation.id] = start_frame
    return overrides


def _view_relation_frame(relation_ids: list[str], relation_frames: dict[str, int]) -> int | None:
    frames = {relation_frames[relation_id] for relation_id in relation_ids if relation_id in relation_frames}
    return frames.pop() if len(frames) == 1 else None


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
    relation_frames = _relation_frame_overrides(ir)
    return f"""
import json, math
import bpy
from mathutils import Vector

IR = json.loads({_json(ir)!r})
RELATION_FRAMES = json.loads({json.dumps(relation_frames)!r})
issues = []
bpy.context.view_layer.update()

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
    if obj.type == "MESH" and getattr(obj, "data", None) and len(obj.data.vertices) > 0:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        try:
            if mesh and len(mesh.vertices) > 0:
                return [eval_obj.matrix_world @ vertex.co for vertex in mesh.vertices]
        finally:
            eval_obj.to_mesh_clear()
    return [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]

def bbox_minmax(obj):
    corners = world_bbox(obj)
    if not corners:
        return None
    return (
        Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners))),
        Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners))),
    )

def is_physical_bbox_object(obj):
    text = f"{{obj.name}} {{obj.get('ll3m_part', '')}} {{obj.get('ll3m_role', '')}}".lower()
    visual_tokens = ("grain", "detail", "decal", "label", "marking", "stripe", "line", "arrow", "text", "annotation")
    if not obj.get("ll3m_id") and any(token in text for token in visual_tokens):
        return False
    return True

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
    return [o for o in result if o.type in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}} and is_physical_bbox_object(o)]

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

def bbox_overlaps(a, b):
    return (
        min(a[1].x, b[1].x) - max(a[0].x, b[0].x),
        min(a[1].y, b[1].y) - max(a[0].y, b[0].y),
        min(a[1].z, b[1].z) - max(a[0].z, b[0].z),
    )

def penetration_depth(a, b):
    overlaps = bbox_overlaps(a, b)
    if overlaps[0] <= 0 or overlaps[1] <= 0 or overlaps[2] <= 0:
        return 0.0, overlaps, None
    depths = [float(overlaps[0]), float(overlaps[1]), float(overlaps[2])]
    axis_index = min(range(3), key=lambda index: depths[index])
    return depths[axis_index], overlaps, ("x", "y", "z")[axis_index]

def pairwise_mesh_penetration(subject_objs, object_objs):
    subject_meshes = collect_mesh_hierarchy(subject_objs)
    object_meshes = collect_mesh_hierarchy(object_objs)
    if not subject_meshes or not object_meshes:
        sb = aggregate_minmax(subject_objs)
        ob = aggregate_minmax(object_objs)
        if not sb or not ob:
            return 0.0, (0.0, 0.0, 0.0), None, None
        depth, overlaps, axis = penetration_depth(sb, ob)
        return depth, overlaps, axis, None
    worst = (0.0, (0.0, 0.0, 0.0), None, None)
    for subject_mesh in subject_meshes:
        sb = bbox_minmax(subject_mesh)
        if not sb:
            continue
        for object_mesh in object_meshes:
            ob = bbox_minmax(object_mesh)
            if not ob:
                continue
            depth, overlaps, axis = penetration_depth(sb, ob)
            if depth > worst[0]:
                worst = (depth, overlaps, axis, [subject_mesh.name, object_mesh.name])
    return worst

def center(mm):
    return (mm[0] + mm[1]) * 0.5

def aabb_axis_gap(a, b, axis):
    index = {{"x": 0, "y": 1, "z": 2}}[axis]
    if a[1][index] < b[0][index]:
        return b[0][index] - a[1][index]
    if b[1][index] < a[0][index]:
        return a[0][index] - b[1][index]
    return 0.0

def aabb_distance(a, b):
    dx = aabb_axis_gap(a, b, "x")
    dy = aabb_axis_gap(a, b, "y")
    dz = aabb_axis_gap(a, b, "z")
    return math.sqrt(dx * dx + dy * dy + dz * dz)

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
    method = relation.get("verification_method") or "auto"
    if method == "visual_only":
        continue
    relation_frame = RELATION_FRAMES.get(str(relation["id"]))
    original_frame = bpy.context.scene.frame_current
    if relation_frame is not None:
        bpy.context.scene.frame_set(int(relation_frame))
        sb = aggregate_minmax(subj)
        ob = aggregate_minmax(obj)
        if not sb or not ob:
            issue("MISSING_BBOX", "Could not compute relation bounding boxes.", "major", sid, relation["id"], {{"frame": relation_frame}})
            bpy.context.scene.frame_set(original_frame)
            continue
        sc = center(sb)
        oc = center(ob)
    if method == "distance":
        max_dist = relation.get("max_distance") or 2.0
        dist = (sc - oc).length
        if dist > max_dist:
            issue("RELATION_DISTANCE_FAILED", f"'{{sid}}' is too far from '{{oid}}'.", "major", sid, relation["id"], {{"distance": dist, "max_distance": max_dist, "frame": relation_frame}})
        if relation_frame is not None:
            bpy.context.scene.frame_set(original_frame)
        continue
    if method == "attachment" or rtype in {{"attached_to", "touching"}}:
        dist = aabb_distance(sb, ob)
        if dist > max(tol, 0.18):
            issue("RELATION_ATTACHMENT_FAILED", f"'{{sid}}' is not visibly attached to or touching '{{oid}}'.", "major", sid, relation["id"], {{"bbox_distance": dist, "frame": relation_frame}})
        if relation_frame is not None:
            bpy.context.scene.frame_set(original_frame)
        continue
    if rtype == "on_top_of":
        overlap_x = min(sb[1].x, ob[1].x) - max(sb[0].x, ob[0].x)
        overlap_y = min(sb[1].y, ob[1].y) - max(sb[0].y, ob[0].y)
        relation_text = " ".join([
            str(relation.get("id", "")),
            str(relation.get("description", "")),
            str(sid),
            str(oid),
        ]).lower()
        if any(token in relation_text for token in ("above", "overhead", "hang", "hanging", "suspend", "suspended")):
            z_gap = sb[0].z - ob[1].z
            if overlap_x <= 0 or overlap_y <= 0 or sc.z <= oc.z + max(tol, 0.12):
                issue("RELATION_ON_TOP_OF_FAILED", f"'{{sid}}' is not clearly above '{{oid}}'.", "major", sid, relation["id"], {{"z_gap": z_gap, "overlap_x": overlap_x, "overlap_y": overlap_y, "frame": relation_frame}})
            if relation_frame is not None:
                bpy.context.scene.frame_set(original_frame)
            continue
        support_z = ob[1].z
        if "floor" in relation_text and any(token in relation_text for token in ("room", "greenhouse", "enclosure", "interior")):
            support_z = ob[0].z
        z_gap = abs(sb[0].z - support_z)
        if overlap_x <= 0 or overlap_y <= 0 or z_gap > max(tol, 0.12):
            issue("RELATION_ON_TOP_OF_FAILED", f"'{{sid}}' is not clearly on top of '{{oid}}'.", "major", sid, relation["id"], {{"z_gap": z_gap, "overlap_x": overlap_x, "overlap_y": overlap_y, "support_z": support_z, "frame": relation_frame}})
    elif rtype == "left_of" and not (sc.x < oc.x - tol):
        issue("RELATION_LEFT_OF_FAILED", f"'{{sid}}' is not left of '{{oid}}'.", "major", sid, relation["id"], {{"subject_x": sc.x, "object_x": oc.x, "frame": relation_frame}})
    elif rtype == "right_of" and not (sc.x > oc.x + tol):
        issue("RELATION_RIGHT_OF_FAILED", f"'{{sid}}' is not right of '{{oid}}'.", "major", sid, relation["id"], {{"subject_x": sc.x, "object_x": oc.x, "frame": relation_frame}})
    elif rtype == "near":
        max_dist = relation.get("max_distance") or 2.0
        dist = (sc - oc).length
        if dist > max_dist:
            issue("RELATION_NEAR_FAILED", f"'{{sid}}' is too far from '{{oid}}'.", "major", sid, relation["id"], {{"distance": dist, "max_distance": max_dist, "frame": relation_frame}})
    elif rtype == "not_intersecting":
        depth, overlap, axis, mesh_pair = pairwise_mesh_penetration(subj, obj)
        if depth > tol:
            issue("RELATION_INTERSECTION_FAILED", f"'{{sid}}' appears to intersect '{{oid}}'.", "major", sid, relation["id"], {{"overlap": overlap, "penetration_depth": depth, "axis": axis, "mesh_pair": mesh_pair, "frame": relation_frame}})
    if relation_frame is not None:
        bpy.context.scene.frame_set(original_frame)

if IR["scene"].get("cameras") and not bpy.context.scene.camera:
    issue("MISSING_ACTIVE_CAMERA", "Scene has camera specs but no active camera.", "major")

report = {{"passed": not issues, "issues": issues, "summary": "Scene deterministic validation passed." if not issues else "Scene deterministic validation found issues."}}
print("{REPORT_MARKER}" + json.dumps(report))
"""


def _screenshot_script(ir: GenerationIR, output_dir: Path, width: int, height: int) -> str:
    views = _normalized_screenshot_views(ir)
    relation_frames = _relation_frame_overrides(ir)
    view_dicts = [
        {
            "id": view.id,
            "view_type": view.view_type.value,
            "target_object_ids": view.target_object_ids,
            "relation_ids": view.relation_ids,
            "frame": view.frame if view.frame is not None else _view_relation_frame(view.relation_ids, relation_frames),
        }
        for view in views
    ]
    return _render_views_script(view_dicts, output_dir, width, height, frame_default=None)


def _animation_validation_script(ir: GenerationIR) -> str:
    prototype = ir.extension.prototype if ir.extension else None
    deformable_subject_ids = prototype.subject_ids if prototype else []
    deformable_thresholds = {
        statistic.target_id: statistic.threshold
        for statistic in (prototype.statistics if prototype else [])
    }
    if prototype:
        for subject_id in prototype.subject_ids:
            deformable_thresholds.setdefault(subject_id, prototype.statistic_threshold)
    return f"""
import json
import bpy
from mathutils import Vector

IR = json.loads({_json(ir)!r})
DEFORMABLE_SUBJECT_IDS = json.loads({json.dumps(deformable_subject_ids)!r})
DEFORMABLE_THRESHOLDS = json.loads({json.dumps(deformable_thresholds)!r})
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
    keyed = [candidate for candidate in objs if has_fcurve(candidate, path_prefix)]
    if keyed:
        return representative_obj(keyed, path_prefix)
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
    if obj.type == "MESH" and getattr(obj, "data", None) and len(obj.data.vertices) > 0:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        eval_obj = obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        try:
            if mesh and len(mesh.vertices) > 0:
                return [eval_obj.matrix_world @ vertex.co for vertex in mesh.vertices]
        finally:
            eval_obj.to_mesh_clear()
    return [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]

def aggregate_minmax(objs):
    points = []
    for obj in objs:
        if not is_physical_bbox_object(obj):
            continue
        points.extend(world_bbox(obj))
    if not points:
        return None
    return (
        Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points))),
        Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points))),
    )

def bbox_size(mm):
    if not mm:
        return [0.0, 0.0, 0.0]
    return [float(mm[1].x - mm[0].x), float(mm[1].y - mm[0].y), float(mm[1].z - mm[0].z)]

def deformation_sample(objs, frame):
    bpy.context.scene.frame_set(frame)
    mm = aggregate_minmax(objs)
    size = bbox_size(mm)
    centers = []
    for obj in objs:
        if obj.type not in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}}:
            continue
        child_mm = aggregate_minmax([obj])
        if child_mm:
            centers.append((child_mm[0] + child_mm[1]) * 0.5)
    spread = 0.0
    if centers:
        base = centers[0]
        spread = max((center - base).length for center in centers)
    return {{"frame": int(frame), "bbox_size": size, "displacement_spread": float(spread)}}

def build_deformation_statistics(samples_by_target):
    targets = []
    for target_id, samples in samples_by_target.items():
        samples = sorted(samples, key=lambda item: int(item.get("frame", 0)))
        if not samples:
            continue
        first_size = samples[0].get("bbox_size") or [0.0, 0.0, 0.0]
        bbox_delta = 0.0
        for sample in samples[1:]:
            size = sample.get("bbox_size") or [0.0, 0.0, 0.0]
            bbox_delta = max(bbox_delta, max(abs(float(size[index]) - float(first_size[index])) for index in range(3)))
        displacement_spread = max(float(sample.get("displacement_spread", 0.0)) for sample in samples)
        threshold = float(DEFORMABLE_THRESHOLDS.get(target_id, 0.05))
        targets.append({{
            "target_id": target_id,
            "frame_range": [int(samples[0].get("frame", 0)), int(samples[-1].get("frame", 0))],
            "sampled_frames": [int(sample.get("frame", 0)) for sample in samples],
            "bbox_delta": bbox_delta,
            "displacement_spread": displacement_spread,
            "threshold": threshold,
            "passed": max(bbox_delta, displacement_spread) >= threshold,
        }})
    return {{
        "frame_range": [min([target["frame_range"][0] for target in targets], default=0), max([target["frame_range"][1] for target in targets], default=0)],
        "target_ids": [target["target_id"] for target in targets],
        "targets": targets,
        "threshold": min([target["threshold"] for target in targets], default=0.0),
        "passed": bool(targets) and all(target["passed"] for target in targets),
        "review_required": False,
    }}

def is_physical_bbox_object(obj):
    text = f"{{obj.name}} {{obj.get('ll3m_part', '')}} {{obj.get('ll3m_role', '')}}".lower()
    visual_tokens = ("grain", "detail", "decal", "label", "marking", "stripe", "line", "arrow", "text", "annotation")
    if not obj.get("ll3m_id") and any(token in text for token in visual_tokens):
        return False
    return True

def mesh_like_descendants(objs):
    result = []
    queue = list(objs)
    seen = set()
    while queue:
        obj = queue.pop(0)
        if obj in seen:
            continue
        seen.add(obj)
        if obj.type in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}} and is_physical_bbox_object(obj):
            result.append(obj)
        queue.extend(list(getattr(obj, "children", [])))
    return result

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

def bbox_overlaps(a, b):
    return (
        min(a[1].x, b[1].x) - max(a[0].x, b[0].x),
        min(a[1].y, b[1].y) - max(a[0].y, b[0].y),
        min(a[1].z, b[1].z) - max(a[0].z, b[0].z),
    )

def penetration_depth(a, b):
    overlaps = bbox_overlaps(a, b)
    if overlaps[0] <= 0 or overlaps[1] <= 0 or overlaps[2] <= 0:
        return 0.0, overlaps, None
    depths = [float(overlaps[0]), float(overlaps[1]), float(overlaps[2])]
    axis_index = min(range(3), key=lambda index: depths[index])
    return depths[axis_index], overlaps, ("x", "y", "z")[axis_index]

def pairwise_mesh_penetration(subject_objs, object_objs):
    subject_meshes = mesh_like_descendants(subject_objs)
    object_meshes = mesh_like_descendants(object_objs)
    if not subject_meshes or not object_meshes:
        sb = aggregate_minmax(subject_objs)
        ob = aggregate_minmax(object_objs)
        if not sb or not ob:
            return 0.0, (0.0, 0.0, 0.0), None, None
        depth, overlaps, axis = penetration_depth(sb, ob)
        return depth, overlaps, axis, None
    worst = (0.0, (0.0, 0.0, 0.0), None, None)
    for subject_mesh in subject_meshes:
        sb = aggregate_minmax([subject_mesh])
        if not sb:
            continue
        for object_mesh in object_meshes:
            ob = aggregate_minmax([object_mesh])
            if not ob:
                continue
            depth, overlaps, axis = penetration_depth(sb, ob)
            if depth > worst[0]:
                worst = (depth, overlaps, axis, [subject_mesh.name, object_mesh.name])
    return worst

def bbox_center(box):
    return (box[0] + box[1]) * 0.5

def point_inside_bbox(point, box):
    return (
        box[0].x <= point.x <= box[1].x
        and box[0].y <= point.y <= box[1].y
        and box[0].z <= point.z <= box[1].z
    )

def pairwise_embedded_contact(subject_objs, object_objs):
    subject_meshes = mesh_like_descendants(subject_objs) or subject_objs
    object_meshes = mesh_like_descendants(object_objs) or object_objs
    for subject_mesh in subject_meshes:
        sb = aggregate_minmax([subject_mesh])
        if not sb:
            continue
        for object_mesh in object_meshes:
            ob = aggregate_minmax([object_mesh])
            if not ob:
                continue
            overlaps = bbox_overlaps(sb, ob)
            if overlaps[0] <= 0 or overlaps[1] <= 0 or overlaps[2] <= 0:
                continue
            subject_center_inside = point_inside_bbox(bbox_center(sb), ob)
            object_center_inside = point_inside_bbox(bbox_center(ob), sb)
            if subject_center_inside or object_center_inside:
                return True, overlaps, [getattr(subject_mesh, "name", None), getattr(object_mesh, "name", None)], {{
                    "subject_center_inside_object": subject_center_inside,
                    "object_center_inside_subject": object_center_inside,
                }}
    return False, (0.0, 0.0, 0.0), None, {{}}

def xy_overlap(a, b):
    return (
        min(a[1].x, b[1].x) - max(a[0].x, b[0].x),
        min(a[1].y, b[1].y) - max(a[0].y, b[0].y),
    )

def gripper_subset(objs):
    grippers = [
        obj for obj in objs
        if obj.type in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}}
        and ("gripper" in str(obj.get("ll3m_part", "")).lower() or "gripper" in obj.name.lower())
    ]
    return grippers or [obj for obj in objs if obj.type in {{"MESH", "CURVE", "SURFACE", "FONT", "META"}}]

def mentions_signal_or_light(text):
    words = text.lower().replace("_", " ").replace("-", " ").split()
    return any(word in ("light", "status", "signal") for word in words)

def target_haystack(target_id):
    for obj_spec in IR.get("scene", {{}}).get("objects", []):
        if obj_spec.get("id") == target_id:
            return f"{{obj_spec.get('id', '')}} {{obj_spec.get('description', '')}} {{obj_spec.get('label', '')}}".lower()
    return str(target_id).lower()

def is_static_destination_target(target_id):
    haystack = target_haystack(target_id)
    return any(token in haystack for token in ("platform", "table", "conveyor", "belt", "surface", "floor", "output", "marker", "landing", "bridge", "ramp"))

def motion_support_targets(event):
    text = " ".join([
        str(event.get("id", "")),
        str(event.get("description", "")),
        str(event.get("expected_visual_result", "")),
        " ".join(event.get("constraints", []) or []),
    ]).lower()
    if any(token in text for token in ("pick", "gripper", "grasp", "carry", "carried", "transfer")):
        return []
    if not any(token in text for token in ("drive", "drives", "cross", "roll", "rolls", "slide", "slides", "move", "moves", "travel")):
        return []
    return [target for target in (event.get("target_ids", []) or []) if target and is_static_destination_target(target)]

def frames_for_motion_support(event, target_id, frames):
    constraint_windows = []
    for constraint in [*(event.get("contact_constraints", []) or []), *(IR.get("animation", {{}}).get("contact_constraints", []) or [])]:
        if constraint.get("constraint_type") != "support" or constraint.get("object_id") != target_id:
            continue
        constraint_windows.append((int(constraint.get("start_frame", 1)), int(constraint.get("end_frame", 1))))
    if constraint_windows:
        selected = [
            frame
            for frame in frames
            if any(start <= int(frame) <= end for start, end in constraint_windows)
        ]
        if selected:
            return selected
        return [max(start for start, _end in constraint_windows)]
    haystack = target_haystack(target_id)
    end_frame = int(event.get("end_frame", frames[-1] if frames else 1))
    if any(token in haystack for token in ("marker", "landing", "output", "destination", "right platform")):
        return [end_frame]
    if any(token in haystack for token in ("bridge", "drawbridge", "ramp", "belt", "conveyor")):
        return [frame for frame in frames if int(event.get("start_frame", 1)) < frame < end_frame]
    return [end_frame]

def check_supported_by(subject_id, subject_objs, target_id, target_objs, frame, event_id):
    sb = aggregate_minmax(subject_objs)
    tb = aggregate_minmax(target_objs)
    if not sb or not tb:
        return
    overlap_x, overlap_y = xy_overlap(sb, tb)
    z_gap = sb[0].z - tb[1].z
    if overlap_x <= 0 or overlap_y <= 0:
        issue(
            "ANIMATION_SUPPORT_OVERLAP_FAILED",
            f"Event '{{event_id}}' expects '{{subject_id}}' to be supported by '{{target_id}}', but their x/y footprints do not overlap.",
            "major",
            subject_id,
            frame,
            {{"target_id": target_id, "overlap_x": overlap_x, "overlap_y": overlap_y, "z_gap": z_gap}},
        )
        return
    if z_gap > 0.18:
        issue(
            "ANIMATION_FLOATING_OVER_SUPPORT",
            f"Event '{{event_id}}' has '{{subject_id}}' floating above '{{target_id}}'.",
            "major",
            subject_id,
            frame,
            {{"target_id": target_id, "z_gap": z_gap}},
        )
    elif z_gap < -0.05:
        issue(
            "ANIMATION_PENETRATES_SUPPORT",
            f"Event '{{event_id}}' has '{{subject_id}}' visibly penetrating '{{target_id}}'.",
            "major",
            subject_id,
            frame,
            {{"target_id": target_id, "z_gap": z_gap}},
        )

def collision_spec(object_id):
    for obj_spec in IR.get("scene", {{}}).get("objects", []):
        if obj_spec.get("id") == object_id:
            collision = obj_spec.get("collision") or {{}}
            if collision.get("enabled", True) is False:
                return None
            return collision
    return {{}}

def collision_object_ids():
    ids = []
    for obj_spec in IR.get("scene", {{}}).get("objects", []):
        object_id = obj_spec.get("id")
        if not object_id:
            continue
        collision = collision_spec(object_id)
        if collision is None:
            continue
        if str(collision.get("role", "")).lower() == "trigger":
            continue
        ids.append(object_id)
    return ids

def animated_collision_object_ids():
    ids = set()
    for event in events:
        if event.get("action") in ("camera_move", "camera_orbit"):
            continue
        for subject_id in event.get("subject_ids", []) or []:
            if collision_spec(subject_id) is not None:
                ids.add(subject_id)
    return ids

def pair_key(a, b):
    return tuple(sorted((str(a), str(b))))

def globally_allowed_overlap_pairs(frame):
    allowed = set()
    for relation in IR.get("scene", {{}}).get("relations", []):
        rtype = str(relation.get("relation_type", ""))
        method = str(relation.get("verification_method", ""))
        if rtype in ("attached_to", "touching", "inside", "contains") or method == "attachment":
            allowed.add(pair_key(relation.get("subject_id"), relation.get("object_id")))
    for constraint in anim.get("contact_constraints", []) or []:
        start = int(constraint.get("start_frame", 1))
        end = int(constraint.get("end_frame", start))
        if start <= frame <= end:
            allowed.add(pair_key(constraint.get("subject_id"), constraint.get("object_id")))
    for event in events:
        for constraint in event.get("contact_constraints", []) or []:
            start = int(constraint.get("start_frame", event.get("start_frame", 1)))
            end = int(constraint.get("end_frame", event.get("end_frame", start)))
            if start <= frame <= end:
                allowed.add(pair_key(constraint.get("subject_id"), constraint.get("object_id")))
    return allowed

def contact_constraint_frames(constraint):
    start = int(constraint.get("start_frame", 1))
    end = int(constraint.get("end_frame", start))
    frames = {{start, end, int((start + end) / 2)}}
    ctype = str(constraint.get("constraint_type", ""))
    if ctype in ("touching", "attachment", "carry_contact", "nonpenetration"):
        step = 1 if duration <= 180 else max(1, int(duration / 180))
        frames.update(range(max(1, start), min(duration, end) + 1, step))
        frames.add(min(duration, end))
    for frame in (anim.get("verifier", {{}}).get("sampled_frames", []) or []):
        frame = int(frame)
        if start <= frame <= end:
            frames.add(frame)
    return sorted(frame for frame in frames if 1 <= frame <= duration)

def check_contact_constraint(constraint):
    severity = "major" if constraint.get("required", True) else "minor"
    subject_id = constraint.get("subject_id")
    object_id = constraint.get("object_id")
    subject_objs = find_objects(subject_id)
    object_objs = find_objects(object_id)
    if not subject_objs or not object_objs:
        return
    ctype = str(constraint.get("constraint_type", "nonpenetration"))
    max_penetration = float(constraint.get("max_penetration", 0.02))
    max_gap = constraint.get("max_gap")
    if max_gap is None:
        max_gap = 0.10 if ctype in ("touching", "attachment", "carry_contact") else 0.08
    max_gap = float(max_gap)
    embedded_contact_reported = False
    for frame in contact_constraint_frames(constraint):
        bpy.context.scene.frame_set(frame)
        sb = aggregate_minmax(subject_objs)
        ob = aggregate_minmax(object_objs)
        if not sb or not ob:
            continue
        depth, overlaps, axis = penetration_depth(sb, ob)
        gap = bbox_gap(sb, ob)
        overlap_x, overlap_y = xy_overlap(sb, ob)
        z_gap = sb[0].z - ob[1].z
        if ctype == "nonpenetration":
            depth, overlaps, axis, mesh_pair = pairwise_mesh_penetration(subject_objs, object_objs)
            if depth > max_penetration:
                issue(
                    "CONTACT_CONSTRAINT_PENETRATION",
                    f"Contact constraint '{{constraint.get('id')}}' has '{{subject_id}}' penetrating '{{object_id}}'.",
                    severity,
                    subject_id,
                    frame,
                    {{"object_id": object_id, "penetration_depth": depth, "axis": axis, "overlaps": list(overlaps), "mesh_pair": mesh_pair, "max_penetration": max_penetration}},
                )
        elif ctype == "support":
            if overlap_x <= 0 or overlap_y <= 0:
                issue(
                    "CONTACT_CONSTRAINT_SUPPORT_OVERLAP_FAILED",
                    f"Contact constraint '{{constraint.get('id')}}' expects '{{subject_id}}' supported by '{{object_id}}', but x/y footprints do not overlap.",
                    severity,
                    subject_id,
                    frame,
                    {{"object_id": object_id, "overlap_x": overlap_x, "overlap_y": overlap_y, "z_gap": z_gap}},
                )
            elif z_gap > max_gap:
                issue(
                    "CONTACT_CONSTRAINT_FLOATING",
                    f"Contact constraint '{{constraint.get('id')}}' has '{{subject_id}}' floating above '{{object_id}}'.",
                    severity,
                    subject_id,
                    frame,
                    {{"object_id": object_id, "z_gap": z_gap, "max_gap": max_gap}},
                )
            elif z_gap < -max_penetration:
                issue(
                    "CONTACT_CONSTRAINT_SUPPORT_PENETRATION",
                    f"Contact constraint '{{constraint.get('id')}}' has '{{subject_id}}' penetrating its support '{{object_id}}'.",
                    severity,
                    subject_id,
                    frame,
                    {{"object_id": object_id, "z_gap": z_gap, "max_penetration": max_penetration}},
                )
        elif ctype in ("touching", "attachment", "carry_contact"):
            if gap > max_gap:
                issue(
                    "CONTACT_CONSTRAINT_GAP",
                    f"Contact constraint '{{constraint.get('id')}}' expects '{{subject_id}}' to stay connected to '{{object_id}}', but the objects are separated.",
                    severity,
                    subject_id,
                    frame,
                    {{"object_id": object_id, "gap": gap, "max_gap": max_gap}},
                )
            depth, overlaps, axis, mesh_pair = pairwise_mesh_penetration(subject_objs, object_objs)
            if depth > max_penetration:
                issue(
                    "CONTACT_CONSTRAINT_CONTACT_PENETRATION",
                    f"Contact constraint '{{constraint.get('id')}}' has excessive penetration between '{{subject_id}}' and '{{object_id}}'.",
                    severity,
                    subject_id,
                    frame,
                    {{"object_id": object_id, "penetration_depth": depth, "axis": axis, "overlaps": list(overlaps), "mesh_pair": mesh_pair, "max_penetration": max_penetration}},
                )
            embedded, embedded_overlaps, embedded_pair, embedded_flags = pairwise_embedded_contact(subject_objs, object_objs)
            if embedded and not embedded_contact_reported:
                embedded_contact_reported = True
                issue(
                    "CONTACT_CONSTRAINT_EMBEDDED_CONTACT",
                    f"Contact constraint '{{constraint.get('id')}}' has '{{subject_id}}' embedded inside '{{object_id}}'.",
                    severity,
                    subject_id,
                    frame,
                    {{"object_id": object_id, "overlaps": list(embedded_overlaps), "mesh_pair": embedded_pair, **embedded_flags}},
                )
        elif ctype == "inside":
            max_escape = float(constraint.get("max_gap", 0.02) or 0.02)
            escapes = {{
                "min_x": ob[0].x - sb[0].x,
                "max_x": sb[1].x - ob[1].x,
                "min_y": ob[0].y - sb[0].y,
                "max_y": sb[1].y - ob[1].y,
                "min_z": ob[0].z - sb[0].z,
                "max_z": sb[1].z - ob[1].z,
            }}
            escaped = {{axis_name: value for axis_name, value in escapes.items() if value > max_escape}}
            if escaped:
                issue(
                    "CONTACT_CONSTRAINT_INSIDE_FAILED",
                    f"Contact constraint '{{constraint.get('id')}}' expects '{{subject_id}}' to remain inside '{{object_id}}'.",
                    severity,
                    subject_id,
                    frame,
                    {{"object_id": object_id, "escaped": escaped, "max_escape": max_escape}},
                )

def animation_audit_frames():
    frames = set(anim.get("verifier", {{}}).get("sampled_frames", []) or [])
    for event in events:
        start = int(event.get("start_frame", 1))
        end = int(event.get("end_frame", start))
        frames.update([start, end, int((start + end) / 2)])
    if duration:
        step = 1 if duration <= 180 else max(1, int(duration / 180))
        frames.update(range(1, duration + 1, step))
        frames.add(duration)
    return sorted(int(frame) for frame in frames if 1 <= int(frame) <= duration)

def audit_global_nonpenetration():
    object_ids = collision_object_ids()
    moving_ids = animated_collision_object_ids()
    if len(object_ids) < 2 or not moving_ids:
        return
    violations = {{}}
    for frame in animation_audit_frames():
        bpy.context.scene.frame_set(frame)
        allowed = globally_allowed_overlap_pairs(frame)
        object_map = {{}}
        for object_id in object_ids:
            objs = find_objects(object_id)
            if objs:
                object_map[object_id] = objs
        for index, subject_id in enumerate(object_ids):
            for object_id in object_ids[index + 1:]:
                if subject_id not in moving_ids and object_id not in moving_ids:
                    continue
                if pair_key(subject_id, object_id) in allowed:
                    continue
                if subject_id not in object_map or object_id not in object_map:
                    continue
                max_penetration = max(
                    float((collision_spec(subject_id) or {{}}).get("margin", 0.02)),
                    float((collision_spec(object_id) or {{}}).get("margin", 0.02)),
                    0.02,
                )
                depth, overlaps, axis, mesh_pair = pairwise_mesh_penetration(object_map[subject_id], object_map[object_id])
                if depth > max_penetration:
                    key = pair_key(subject_id, object_id)
                    existing = violations.get(key)
                    if existing is None:
                        violations[key] = {{
                            "subject_id": subject_id,
                            "object_id": object_id,
                            "frames": [],
                            "worst_frame": frame,
                            "penetration_depth": depth,
                            "axis": axis,
                            "overlaps": list(overlaps),
                            "mesh_pair": mesh_pair,
                            "max_penetration": max_penetration,
                        }}
                    existing = violations[key]
                    existing["frames"].append(frame)
                    if depth > existing["penetration_depth"]:
                        existing["worst_frame"] = frame
                        existing["penetration_depth"] = depth
                        existing["axis"] = axis
                        existing["overlaps"] = list(overlaps)
                        existing["mesh_pair"] = mesh_pair
    for data in violations.values():
        frames = sorted(set(int(frame) for frame in data.pop("frames", [])))
        data["frames"] = frames[:24]
        data["frame_count"] = len(frames)
        issue(
            "ANIMATION_GLOBAL_PENETRATION",
            f"Objects '{{data['subject_id']}}' and '{{data['object_id']}}' penetrate during animation.",
            "major",
            data["subject_id"],
            data["worst_frame"],
            data,
        )

def frames_for_interaction_target(event, target_id, frames):
    contact_frames = []
    for constraint in event.get("contact_constraints", []) or []:
        ctype = str(constraint.get("constraint_type", ""))
        if ctype not in ("touching", "attachment", "carry_contact"):
            continue
        subject_id = constraint.get("subject_id")
        object_id = constraint.get("object_id")
        event_subjects = set(event.get("subject_ids", []) or [])
        if target_id not in (subject_id, object_id) and not (event_subjects & {{subject_id, object_id}}):
            continue
        for frame in contact_constraint_frames(constraint):
            if frame in frames:
                contact_frames.append(frame)
    if contact_frames:
        return sorted(set(contact_frames))
    if not is_static_destination_target(target_id):
        text = " ".join([
            str(event.get("id", "")),
            str(event.get("description", "")),
            str(event.get("expected_visual_result", "")),
            " ".join(event.get("constraints", []) or []),
        ]).lower()
        if any(token in text for token in ("approach", "descend", "move down", "moves down", "contact the top", "pick")):
            return [int(event.get("end_frame", frames[-1] if frames else 1))]
        if any(token in text for token in ("carry", "carried", "grasp", "grasped", "transfer", "lift")):
            return frames
        return []
    text = " ".join([
        str(event.get("id", "")),
        str(event.get("description", "")),
        str(event.get("expected_visual_result", "")),
        " ".join(event.get("constraints", []) or []),
    ]).lower()
    if any(token in text for token in ("place", "placed", "output", "destination", "final")):
        return [int(event.get("end_frame", frames[-1] if frames else 1))]
    return []

def interaction_targets(event):
    if event.get("action") in ("appear", "disappear"):
        return []
    text = " ".join([
        str(event.get("id", "")),
        str(event.get("description", "")),
        str(event.get("expected_visual_result", "")),
        " ".join(event.get("constraints", []) or []),
    ]).lower()
    if mentions_signal_or_light(text):
        return []
    if not any(token in text for token in ("grasp", "gripper", "lift", "carry", "pick", "place", "transfer")):
        return []
    targets = []
    for constraint in event.get("contact_constraints", []) or []:
        ctype = str(constraint.get("constraint_type", ""))
        if ctype not in ("touching", "attachment", "carry_contact"):
            continue
        for target in (constraint.get("subject_id"), constraint.get("object_id")):
            if target and target not in targets:
                targets.append(target)
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
            key = "rotation" if path == "rotation_euler" else path
            samples = [item[key] for item in trace[sid]]
            max_delta = 0.0
            for left_index, left in enumerate(samples):
                for right in samples[left_index + 1:]:
                    max_delta = max(max_delta, distance(left, right))
            if max_delta < 0.01:
                issue("ANIMATION_NO_VISIBLE_CHANGE", f"Event '{{event.get('id')}}' does not visibly change '{{sid}}' {{path}} between sampled frames.", "major", sid, int(event.get("end_frame", 1)), {{"samples": samples, "path": path}})

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
                if target_id == sid:
                    continue
                target_objs = find_objects(target_id)
                if "gripper" in str(target_id).lower() and ("gripper" in str(event.get("description", "")).lower() or "gripper" in str(event.get("expected_visual_result", "")).lower()):
                    target_objs = gripper_subset(target_objs)
                if not target_objs:
                    continue
                for frame in frames_for_interaction_target(event, target_id, frames):
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

    support_targets = motion_support_targets(event)
    if support_targets:
        frames = sorted(set([int(event.get("start_frame", 1)), int((event.get("start_frame", 1) + event.get("end_frame", 1)) / 2), int(event.get("end_frame", 1))]))
        for sid in event.get("subject_ids", []):
            subj_objs = find_objects(sid)
            if not subj_objs:
                continue
            for target_id in support_targets:
                target_objs = find_objects(target_id)
                if not target_objs:
                    continue
                for frame in frames_for_motion_support(event, target_id, frames):
                    bpy.context.scene.frame_set(frame)
                    check_supported_by(sid, subj_objs, target_id, target_objs, frame, str(event.get("id")))

for constraint in anim.get("contact_constraints", []) or []:
    check_contact_constraint(constraint)

for event in events:
    for constraint in event.get("contact_constraints", []) or []:
        check_contact_constraint(constraint)

audit_global_nonpenetration()

if DEFORMABLE_SUBJECT_IDS:
    sample_frames = sorted(set(int(frame) for frame in (anim.get("verifier", {{}}).get("sampled_frames", []) or [1, max(1, int(duration / 2)), max(1, duration)])))
    deformation_samples = {{}}
    for subject_id in DEFORMABLE_SUBJECT_IDS:
        subject_objs = find_objects(subject_id)
        deformation_samples[subject_id] = [deformation_sample(subject_objs, frame) for frame in sample_frames if subject_objs]
        if not deformation_samples[subject_id]:
            issue("MISSING_DEFORMATION_STATISTICS_EVIDENCE", f"Could not collect deformation statistics for '{{subject_id}}'.", "major", subject_id)
    trace["deformation_statistics"] = build_deformation_statistics(deformation_samples)
    if not trace["deformation_statistics"].get("passed"):
        issue("DEFORMATION_STATISTICS_BELOW_THRESHOLD", "Structured deformation statistics did not exceed the configured threshold.", "major", evidence=trace["deformation_statistics"])

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
    target_ids: list[str] = []
    if ir.animation:
        for event in [*ir.animation.events, *ir.animation.camera_events]:
            target_ids.extend(event.subject_ids)
            target_ids.extend(event.target_ids)
            for constraint in event.contact_constraints:
                target_ids.extend([constraint.subject_id, constraint.object_id])
        for constraint in ir.animation.contact_constraints:
            target_ids.extend([constraint.subject_id, constraint.object_id])
    if not target_ids:
        target_ids = [obj.id for obj in ir.scene.objects]
    target_ids = list(dict.fromkeys(target_ids))
    return f"""
import json, math, os, sys
if {str(PROJECT_ROOT)!r} not in sys.path:
    sys.path.insert(0, {str(PROJECT_ROOT)!r})
import bpy
from mathutils import Vector
from blender.ll3m_utils import configure_render

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
original_engine = scene.render.engine
configure_render(scene, width=WIDTH, height=HEIGHT, engine="workbench")

original_view_settings = {{
    "view_transform": getattr(scene.view_settings, "view_transform", None),
    "look": getattr(scene.view_settings, "look", None),
    "exposure": getattr(scene.view_settings, "exposure", None),
    "gamma": getattr(scene.view_settings, "gamma", None),
}}
original_shading_settings = {{}}
for attr in (
    "light",
    "studio_light",
    "color_type",
    "show_shadows",
    "show_cavity",
    "studiolight_intensity",
    "background_type",
    "background_color",
):
    try:
        original_shading_settings[attr] = getattr(scene.display.shading, attr)
    except Exception:
        pass
original_material_colors = {{}}
original_node_values = {{}}
original_material_settings = {{}}
original_object_visibility = {{
    obj.name: {{
        "hide_render": obj.hide_render,
        "hide_viewport": obj.hide_viewport,
        "visible_camera": getattr(obj, "visible_camera", None),
    }}
    for obj in bpy.data.objects
}}
original_eevee_settings = {{}}
for attr in ("use_volume_custom_range", "volumetric_light_clamp", "volumetric_samples", "volumetric_shadow_samples"):
    try:
        original_eevee_settings[attr] = getattr(scene.eevee, attr)
    except Exception:
        pass
original_light_energy = {{obj.name: obj.data.energy for obj in bpy.data.objects if obj.type == "LIGHT" and hasattr(obj.data, "energy")}}
original_world_strength = None
world = scene.world
if world and world.use_nodes:
    for _node in world.node_tree.nodes:
        if _node.type == "BACKGROUND" and len(_node.inputs) > 1:
            original_world_strength = _node.inputs[1].default_value
for mat in bpy.data.materials:
    try:
        original_material_colors[mat.name] = tuple(mat.diffuse_color)
    except Exception:
        pass
    original_material_settings[mat.name] = {{}}
    for attr in ("blend_method", "use_screen_refraction", "show_transparent_back"):
        try:
            original_material_settings[mat.name][attr] = getattr(mat, attr)
        except Exception:
            pass
    try:
        if mat.use_nodes and mat.node_tree:
            for node in mat.node_tree.nodes:
                for input_name in ("Base Color", "Emission Color", "Emission Strength", "Strength"):
                    socket = node.inputs.get(input_name) if hasattr(node, "inputs") else None
                    if socket is not None and hasattr(socket, "default_value"):
                        value = socket.default_value
                        try:
                            value = tuple(value)
                        except TypeError:
                            pass
                        original_node_values[(mat.name, node.name, input_name)] = value
    except Exception:
        pass

def apply_inspection_materials():
    def tone_map_rgba(rgba):
        if len(rgba) < 4:
            return rgba
        rgb = rgba[:3]
        max_channel = max(rgb)
        if max_channel <= 0.82:
            return rgba
        scale = 0.82 / max_channel
        return (rgb[0] * scale, rgb[1] * scale, rgb[2] * scale, rgba[3])
    for mat in bpy.data.materials:
        is_transparent = False
        try:
            rgba = tuple(mat.diffuse_color)
            is_transparent = len(rgba) >= 4 and rgba[3] < 0.55
            if "glass" in mat.name.lower() or "transparent" in mat.name.lower() or is_transparent:
                mapped = tone_map_rgba((rgba[0], rgba[1], rgba[2], 0.08))
                mat.diffuse_color = mapped
                try:
                    mat.blend_method = "BLEND"
                except Exception:
                    pass
                try:
                    mat.show_transparent_back = False
                except Exception:
                    pass
            else:
                mat.diffuse_color = tone_map_rgba(rgba)
        except Exception:
            pass
        try:
            if mat.use_nodes and mat.node_tree:
                for node in mat.node_tree.nodes:
                    socket = node.inputs.get("Base Color") if hasattr(node, "inputs") else None
                    if socket is not None and hasattr(socket, "default_value"):
                        rgba = tuple(socket.default_value)
                        if "glass" in mat.name.lower() or "transparent" in mat.name.lower() or (len(rgba) >= 4 and rgba[3] < 0.55):
                            socket.default_value = tone_map_rgba((rgba[0], rgba[1], rgba[2], 0.08))
                        else:
                            socket.default_value = tone_map_rgba(rgba)
                    socket = node.inputs.get("Emission Color") if hasattr(node, "inputs") else None
                    if socket is not None and hasattr(socket, "default_value"):
                        socket.default_value = tone_map_rgba(tuple(socket.default_value))
                    socket = node.inputs.get("Emission Strength") if hasattr(node, "inputs") else None
                    if socket is not None and hasattr(socket, "default_value"):
                        socket.default_value = min(float(socket.default_value), 0.15)
                    socket = node.inputs.get("Strength") if hasattr(node, "inputs") else None
                    if node.type == "EMISSION" and socket is not None and hasattr(socket, "default_value"):
                        socket.default_value = min(float(socket.default_value), 0.15)
        except Exception:
            pass

try:
    transforms = bpy.types.ColorManagedViewSettings.bl_rna.properties["view_transform"].enum_items.keys()
    scene.view_settings.view_transform = "AgX" if "AgX" in transforms else "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = -1.2
    scene.view_settings.gamma = 1.0
except Exception:
    pass
try:
    scene.display.shading.light = "STUDIO"
    scene.display.shading.studio_light = "studio.exr"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_shadows = True
    scene.display.shading.show_cavity = True
    scene.display.shading.studiolight_intensity = 0.45
    scene.display.shading.background_type = "VIEWPORT"
    scene.display.shading.background_color = (0.42, 0.42, 0.42)
except Exception:
    pass
try:
    scene.eevee.use_volume_custom_range = False
except Exception:
    pass
try:
    scene.eevee.volumetric_light_clamp = 1.0
except Exception:
    pass
for obj in bpy.data.objects:
    if obj.type == "LIGHT" and hasattr(obj.data, "energy"):
        light_type = getattr(obj.data, "type", "")
        limit = 250.0
        if light_type == "SUN":
            limit = 1.0
        elif light_type == "POINT":
            limit = 90.0
        elif light_type == "SPOT":
            limit = 120.0
        elif light_type == "AREA":
            limit = 300.0
        try:
            obj.data.energy = min(float(obj.data.energy), limit)
        except Exception:
            pass
if world and world.use_nodes:
    for node in world.node_tree.nodes:
        if node.type == "BACKGROUND" and len(node.inputs) > 1:
            try:
                node.inputs[1].default_value = min(float(node.inputs[1].default_value), 0.35)
            except Exception:
                pass
apply_inspection_materials()
for obj in bpy.data.objects:
    try:
        mat_names = [slot.material.name.lower() for slot in getattr(obj, "material_slots", []) if slot.material]
        name = obj.name.lower()
        if (
            ("glass" in " ".join(mat_names) or name.startswith("wall_") or name.startswith("roof_") or name in {{"wall_n", "wall_s", "wall_e", "wall_w", "roof_a", "roof_b"}})
            and "floor" not in name
        ):
            obj.hide_render = True
            obj.hide_viewport = True
            try:
                obj.hide_set(True)
            except Exception:
                pass
            try:
                obj.visible_camera = False
            except Exception:
                pass
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
    for attr, value in original_view_settings.items():
        if value is not None:
            try:
                setattr(scene.view_settings, attr, value)
            except Exception:
                pass
    for attr, value in original_shading_settings.items():
        try:
            setattr(scene.display.shading, attr, value)
        except Exception:
            pass
    for attr, value in original_eevee_settings.items():
        try:
            setattr(scene.eevee, attr, value)
        except Exception:
            pass
    for obj in bpy.data.objects:
        if obj.name in original_object_visibility:
            try:
                obj.hide_render = original_object_visibility[obj.name]["hide_render"]
                obj.hide_viewport = original_object_visibility[obj.name]["hide_viewport"]
                obj.hide_set(False)
                if original_object_visibility[obj.name]["visible_camera"] is not None:
                    obj.visible_camera = original_object_visibility[obj.name]["visible_camera"]
            except Exception:
                pass
        if obj.name in original_light_energy and obj.type == "LIGHT" and hasattr(obj.data, "energy"):
            try:
                obj.data.energy = original_light_energy[obj.name]
            except Exception:
                pass
    if original_world_strength is not None and world and world.use_nodes:
        for node in world.node_tree.nodes:
            if node.type == "BACKGROUND" and len(node.inputs) > 1:
                try:
                    node.inputs[1].default_value = original_world_strength
                except Exception:
                    pass
    for mat in bpy.data.materials:
        if mat.name in original_material_colors:
            try:
                mat.diffuse_color = original_material_colors[mat.name]
            except Exception:
                pass
        for attr, value in original_material_settings.get(mat.name, {{}}).items():
            try:
                setattr(mat, attr, value)
            except Exception:
                pass
        try:
            if mat.use_nodes and mat.node_tree:
                for node in mat.node_tree.nodes:
                    for input_name in ("Base Color", "Emission Color", "Emission Strength", "Strength"):
                        key = (mat.name, node.name, input_name)
                        socket = node.inputs.get(input_name) if hasattr(node, "inputs") else None
                        if key in original_node_values and socket is not None and hasattr(socket, "default_value"):
                            socket.default_value = original_node_values[key]
        except Exception:
            pass

print("{SCREENSHOT_MARKER}" + json.dumps({{"paths": paths, "gif_frames": gif_paths, "video": os.path.join(OUT_DIR, "animation.gif").replace("\\\\", "/")}}))
"""


def _render_views_script(view_dicts: list[dict[str, Any]], output_dir: Path, width: int, height: int, frame_default: int | None) -> str:
    return f"""
import json, math, os, sys
if {str(PROJECT_ROOT)!r} not in sys.path:
    sys.path.insert(0, {str(PROJECT_ROOT)!r})
import bpy
from mathutils import Vector
from blender.ll3m_utils import configure_render

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
        if obj not in matches and (obj_id == marker or obj_id.startswith(marker + "_")):
            matches.append(obj)
            matches.extend([child for child in descendants(obj) if child not in matches])
    for obj in bpy.data.objects:
        if obj not in matches and obj.name.startswith(marker):
            matches.append(obj)
            matches.extend([child for child in descendants(obj) if child not in matches])
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
        radius = max(radius * 0.9, 2.0)
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
original_engine = scene.render.engine
configure_render(scene, width=WIDTH, height=HEIGHT, engine="workbench")

original_view_settings = {{
    "view_transform": getattr(scene.view_settings, "view_transform", None),
    "look": getattr(scene.view_settings, "look", None),
    "exposure": getattr(scene.view_settings, "exposure", None),
    "gamma": getattr(scene.view_settings, "gamma", None),
}}
original_shading_settings = {{}}
for attr in (
    "light",
    "studio_light",
    "color_type",
    "show_shadows",
    "show_cavity",
    "studiolight_intensity",
    "background_type",
    "background_color",
):
    try:
        original_shading_settings[attr] = getattr(scene.display.shading, attr)
    except Exception:
        pass
original_material_colors = {{}}
original_node_values = {{}}
original_material_settings = {{}}
original_object_visibility = {{
    obj.name: {{
        "hide_render": obj.hide_render,
        "hide_viewport": obj.hide_viewport,
        "visible_camera": getattr(obj, "visible_camera", None),
    }}
    for obj in bpy.data.objects
}}
original_eevee_settings = {{}}
for attr in ("use_volume_custom_range", "volumetric_light_clamp", "volumetric_samples", "volumetric_shadow_samples"):
    try:
        original_eevee_settings[attr] = getattr(scene.eevee, attr)
    except Exception:
        pass
for mat in bpy.data.materials:
    try:
        original_material_colors[mat.name] = tuple(mat.diffuse_color)
    except Exception:
        pass
    original_material_settings[mat.name] = {{}}
    for attr in ("blend_method", "use_screen_refraction", "show_transparent_back"):
        try:
            original_material_settings[mat.name][attr] = getattr(mat, attr)
        except Exception:
            pass
    try:
        if mat.use_nodes and mat.node_tree:
            for node in mat.node_tree.nodes:
                for input_name in ("Base Color", "Emission Color", "Emission Strength", "Strength"):
                    socket = node.inputs.get(input_name) if hasattr(node, "inputs") else None
                    if socket is not None and hasattr(socket, "default_value"):
                        value = socket.default_value
                        try:
                            value = tuple(value)
                        except TypeError:
                            pass
                        original_node_values[(mat.name, node.name, input_name)] = value
    except Exception:
        pass
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
        transforms = bpy.types.ColorManagedViewSettings.bl_rna.properties["view_transform"].enum_items.keys()
        scene.view_settings.view_transform = "AgX" if "AgX" in transforms else "Standard"
        scene.view_settings.look = "None"
        scene.view_settings.exposure = -1.2
        scene.view_settings.gamma = 1.0
    except Exception:
        pass
    # Verification screenshots should resemble Blender layout/workbench preview:
    # stable geometry-readable lighting, neutral exposure, and no scene-light blowout.
    try:
        scene.display.shading.light = "STUDIO"
        scene.display.shading.studio_light = "studio.exr"
        scene.display.shading.color_type = "MATERIAL"
        scene.display.shading.show_shadows = True
        scene.display.shading.show_cavity = True
        scene.display.shading.studiolight_intensity = 0.45
        scene.display.shading.background_type = "VIEWPORT"
        scene.display.shading.background_color = (0.42, 0.42, 0.42)
    except Exception:
        pass
    try:
        scene.eevee.use_volume_custom_range = False
    except Exception:
        pass
    try:
        scene.eevee.volumetric_light_clamp = 1.0
    except Exception:
        pass
    # Ensure at least one adequate light exists for EEVEE/Cycles fallback.
    # Workbench ignores scene lights, so normal verification should not depend
    # on generated light intensity.
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
            obj.data.energy = max(min(float(obj.data.energy), 2.0), 0.2)
        elif obj.data.type == "AREA":
            obj.data.energy = max(min(float(obj.data.energy), 120.0), 10.0)
        elif obj.data.type == "POINT":
            obj.data.energy = max(min(float(obj.data.energy), 150.0), 10.0)
        else:
            obj.data.energy = max(min(float(obj.data.energy), 150.0), 10.0)
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
    def tone_map_rgba(rgba):
        if len(rgba) < 4:
            return rgba
        rgb = rgba[:3]
        max_channel = max(rgb)
        if max_channel <= 0.82:
            return rgba
        scale = 0.82 / max_channel
        return (rgb[0] * scale, rgb[1] * scale, rgb[2] * scale, rgba[3])
    for mat in bpy.data.materials:
        is_transparent = False
        try:
            rgba = tuple(mat.diffuse_color)
            is_transparent = len(rgba) >= 4 and rgba[3] < 0.55
            if "glass" in mat.name.lower() or "transparent" in mat.name.lower() or is_transparent:
                mapped = tone_map_rgba((rgba[0], rgba[1], rgba[2], 0.08))
                mat.diffuse_color = mapped
                try:
                    mat.blend_method = "BLEND"
                except Exception:
                    pass
                try:
                    mat.show_transparent_back = False
                except Exception:
                    pass
            else:
                mat.diffuse_color = tone_map_rgba(rgba)
        except Exception:
            pass
        try:
            if mat.use_nodes and mat.node_tree:
                for node in mat.node_tree.nodes:
                    socket = node.inputs.get("Base Color") if hasattr(node, "inputs") else None
                    if socket is not None and hasattr(socket, "default_value"):
                        rgba = tuple(socket.default_value)
                        if "glass" in mat.name.lower() or "transparent" in mat.name.lower() or (len(rgba) >= 4 and rgba[3] < 0.55):
                            socket.default_value = tone_map_rgba((rgba[0], rgba[1], rgba[2], 0.08))
                        else:
                            socket.default_value = tone_map_rgba(rgba)
                    socket = node.inputs.get("Emission Color") if hasattr(node, "inputs") else None
                    if socket is not None and hasattr(socket, "default_value"):
                        socket.default_value = tone_map_rgba(tuple(socket.default_value))
                    socket = node.inputs.get("Emission Strength") if hasattr(node, "inputs") else None
                    if socket is not None and hasattr(socket, "default_value"):
                        socket.default_value = min(float(socket.default_value), 0.15)
                    socket = node.inputs.get("Strength") if hasattr(node, "inputs") else None
                    if node.type == "EMISSION" and socket is not None and hasattr(socket, "default_value"):
                        socket.default_value = min(float(socket.default_value), 0.15)
        except Exception:
            pass
    for obj in bpy.data.objects:
        try:
            mat_names = [slot.material.name.lower() for slot in getattr(obj, "material_slots", []) if slot.material]
            name = obj.name.lower()
            if (
                ("glass" in " ".join(mat_names) or name.startswith("wall_") or name.startswith("roof_") or name in {{"wall_n", "wall_s", "wall_e", "wall_w", "roof_a", "roof_b"}})
                and "floor" not in name
            ):
                obj.hide_render = True
                obj.hide_viewport = True
                try:
                    obj.hide_set(True)
                except Exception:
                    pass
                try:
                    obj.visible_camera = False
                except Exception:
                    pass
        except Exception:
            pass

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
    for attr, value in original_shading_settings.items():
        try:
            setattr(scene.display.shading, attr, value)
        except Exception:
            pass
    for attr, value in original_eevee_settings.items():
        try:
            setattr(scene.eevee, attr, value)
        except Exception:
            pass
    for obj in bpy.data.objects:
        if obj.name in original_object_visibility:
            try:
                obj.hide_render = original_object_visibility[obj.name]["hide_render"]
                obj.hide_viewport = original_object_visibility[obj.name]["hide_viewport"]
                obj.hide_set(False)
                if original_object_visibility[obj.name]["visible_camera"] is not None:
                    obj.visible_camera = original_object_visibility[obj.name]["visible_camera"]
            except Exception:
                pass
    for mat in bpy.data.materials:
        if mat.name in original_material_colors:
            try:
                mat.diffuse_color = original_material_colors[mat.name]
            except Exception:
                pass
        for attr, value in original_material_settings.get(mat.name, {{}}).items():
            try:
                setattr(mat, attr, value)
            except Exception:
                pass
        try:
            if mat.use_nodes and mat.node_tree:
                for node in mat.node_tree.nodes:
                    for input_name in ("Base Color", "Emission Color", "Emission Strength", "Strength"):
                        key = (mat.name, node.name, input_name)
                        socket = node.inputs.get(input_name) if hasattr(node, "inputs") else None
                        if key in original_node_values and socket is not None and hasattr(socket, "default_value"):
                            socket.default_value = original_node_values[key]
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
        cam_data.lens = 55 if view_type == "close_up" else 35
        scene.camera = cam
        path = os.path.join(OUT_DIR, view["id"] + ".png").replace("\\\\", "/")
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        paths.append(path)
finally:
    restore_render_settings()

print("{SCREENSHOT_MARKER}" + json.dumps({{"paths": paths, "video": None}}))
"""
