"""Agent implementations for planning, coding, refinement, and model verifiers."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from .config import HarnessConfig
from .ir import GenerationIR, RelationType, Severity, ValidationIssue, ValidationReport, VerificationMode, report_to_json
from .llm import LLMClient, extract_code_block
from .rag import LocalRAG
from .serde import from_dict


class PlannerAgent:
    def __init__(self, config: HarnessConfig, rag: LocalRAG):
        self.llm = LLMClient(config.planner)
        self.rag = rag

    def plan(self, prompt: str, *, include_animation: bool = False) -> GenerationIR:
        context = self.rag.format_context("IR SceneSpec AnimationSpec ScreenshotPlan VideoVerifierSpec", limit=3, max_chars=7000)
        ir_reference = _load_ir_reference()
        system = (
            "You are the planner for a Blender 4.5.4 code-generation harness. "
            "Return only a JSON object matching the GenerationIR schema. "
            "Use stable machine ids. Include screenshot views for visual validation. "
            "Keep the IR concise and executable: use at most 7 scene objects, 12 relations, 5 screenshot views, 3 animation events, 8 visual questions, and 8 pass criteria unless the user explicitly asks for more. "
            "Prefer compact descriptions and omit optional features that are not needed for verification. "
            "Plan at least three complementary screenshot views: an overall three-quarter view, a relation/contact close-up, and a side or top view that exposes support/contact. "
            "Add visual pass criteria that require no floating, detached, or misaligned parts unless explicitly requested. "
            "When animation is requested, include AnimationSpec and video verifier settings. "
            "Animation events must be structurally verifiable: use translate, rotate, scale, follow_path, appear, disappear, camera_move, or camera_orbit; include start_transform, at least one intermediate path.keyframe or path point, end_transform, sampled frames covering start/middle/end, temporal questions, and pass criteria. "
            "For signal or material color changes, do not use one vague color-change event. Model separate colored visible parts such as red_light and green_light, then use disappear/appear events with explicit path.keyframes value.visible or value.alpha. "
            "For pick, grasp, carry, lift, or place animations, model the gripper/end-effector as its own ObjectSpec when possible, and put that object id in target_ids for the package lift/transfer events so contact continuity can be verified. "
            "Do not invent fields outside the schema. If you create a relation, it must include id, relation_type, subject_id, and object_id. "
            "Relations, cameras, screenshot targets, and object animation subjects must reference ObjectSpec ids, not ObjectPartSpec ids. Camera event subjects must reference CameraSpec ids."
        )
        user = f"""
User prompt:
{prompt}

Animation requested: {include_animation}

Complete IR definition:
{ir_reference}

Strict JSON skeleton:
{_planner_json_skeleton(include_animation)}

Relevant IR documentation:
{context}

Return a JSON object with keys: prompt, scene, optional animation, version, notes.
Use Blender's Z-up coordinate system and meters.
"""
        return self._generate_valid_ir(system, user)

    def revise(self, ir: GenerationIR, user_request: str, *, include_animation: bool | None = None) -> GenerationIR:
        context = self.rag.format_context("IR revision SceneSpec AnimationSpec user refinement", limit=3, max_chars=7000)
        ir_reference = _load_ir_reference()
        system = (
            "You revise an existing GenerationIR for a Blender 4.5.4 harness. "
            "Return only the complete revised GenerationIR JSON. Preserve stable ids where possible. "
            "Add new ids only for new objects, relations, cameras, screenshots, or animation events. "
            "Keep the revised IR concise and executable: at most 7 scene objects, 12 relations, 5 screenshot views, 3 animation events, 8 visual questions, and 8 pass criteria unless the user explicitly asks for more. "
            "Animation events must stay structurally verifiable: include required start/end transforms, at least one intermediate keyframe or path point, sampled start/middle/end frames, temporal questions, and pass criteria. "
            "For signal or material color changes, use separate colored visible parts and explicit appear/disappear visibility keyframes. "
            "For pick, grasp, carry, lift, or place animations, keep an explicit gripper/end-effector object id in target_ids for package motion events. "
            "Do not invent fields outside the schema. If you create a relation, it must include id, relation_type, subject_id, and object_id. "
            "Relations, cameras, screenshot targets, and object animation subjects must reference ObjectSpec ids, not ObjectPartSpec ids. Camera event subjects must reference CameraSpec ids."
        )
        user = f"""
Current GenerationIR:
{ir.to_json()}

User change request:
{user_request}

Animation requested override:
{include_animation}

Complete IR definition:
{ir_reference}

Strict JSON skeleton:
{_planner_json_skeleton(include_animation if include_animation is not None else bool(ir.animation))}

Relevant IR documentation:
{context}

Return the full revised GenerationIR JSON.
"""
        return self._generate_valid_ir(system, user)

    def _generate_valid_ir(self, system: str, user: str) -> GenerationIR:
        last_error = ""
        for attempt in range(3):
            request = user
            if attempt:
                request += f"""

The previous JSON failed to decode or validate:
{last_error}

Return corrected complete GenerationIR JSON only, and make it shorter than the
previous attempt. Every relation must include
relation_type, subject_id, and object_id. Every relation/camera/view/object
animation reference must point to an ObjectSpec id, never a part id. Camera
event subjects must point to CameraSpec ids. Every object must include id and
description. Every required animation event must include expected_visual_result,
start/middle/end states, sampled frames, temporal video questions, and pass
criteria. Remove nonessential optional_features, visual_check_prompts, long
notes, duplicate questions, and redundant pass criteria.
"""
            try:
                data = self.llm.json_text(system, request)
                _sanitize_planner_data(data)
                _sanitize_animation_data(data)
                ir = from_dict(GenerationIR, data)
                _normalize_part_references(ir)
                _normalize_ambiguous_beside_relations(ir)
                _normalize_animation_verifier(ir)
                report = ir.validate()
                if report.passed:
                    return ir
                last_error = report_to_json(report)
            except Exception as exc:
                last_error = str(exc)
        raise ValueError(f"Planner produced invalid IR after retry:\n{last_error}")


class CoderAgent:
    def __init__(self, config: HarnessConfig, rag: LocalRAG):
        self.llm = LLMClient(config.coder)
        self.rag = rag

    def generate(self, ir: GenerationIR) -> str:
        query = "Blender 4.5 bpy data API mesh from_pydata material camera light render keyframe_insert"
        context = self.rag.format_context(query, limit=4, max_chars=5000)
        coder_ir = _compact_ir_for_coder(ir)
        system = (
            "You are a senior Blender 4.5.4 Python coder. "
            "Generate one complete Python script that creates the requested scene and optional animation. "
            "Use data API where possible, stable ll3m custom properties, modular factory functions, and explicit collections. "
            "Blender UI/node names may be localized; never find shader nodes by display name like 'Principled BSDF'. "
            "Find principled shaders by node.type == 'BSDF_PRINCIPLED', set both mat.diffuse_color and shader input values. "
            "For Blender 4.5, prefer BLENDER_EEVEE_NEXT or WORKBENCH after checking available render engine enum values from scene.render.bl_rna.properties['engine']; never use bpy.types.Scene.bl_rna.properties['render_engine']. "
            "For animation, implement simple explicit keyframes from AnimationSpec events. "
            "Animate object roots that own the ll3m_id, set scene frame range/fps, insert start/end keyframes, and set interpolation on every generated keyframe. "
            "Do not iterate action.fcurves directly; Blender 5 layered actions store fcurves under action.layers[*].strips[*].channelbags[*].fcurves. It is acceptable to leave default interpolation instead of editing fcurves. "
            "Do not use unavailable third-party Blender add-ons. Return only Python code."
        )
        user = f"""
Compact GenerationIR JSON for code generation:
{json.dumps(coder_ir, indent=2)}

Blender 4.5.4 RAG context:
{context}

Script requirements:
- Clear the current scene safely at the start.
- Create all objects, materials, cameras, lights, and environment from the IR.
- Assign custom properties exactly: root objects must have ll3m_id equal to ObjectSpec.id. Parts may use ll3m_part, but never replace the root object's ll3m_id with a part id.
- Create every MaterialSpec using a Blender material name equal to MaterialSpec.id and set material['ll3m_id'] to that same id.
- Keep object names stable and human-readable.
- Create robust materials by setting mat.diffuse_color and locating shader nodes by node.type, not localized node names.
- Set bpy.context.scene.camera to the main generated camera.
- Set render engines defensively by checking scene.render.bl_rna.properties['engine'].enum_items; Blender 4.5 uses BLENDER_EEVEE_NEXT rather than legacy BLENDER_EEVEE. Never use bpy.types.Scene.bl_rna.properties['render_engine'].
- Set frame_start/frame_end/fps if animation exists.
- Insert keyframes for AnimationSpec events when present. For translate/rotate/scale, mutate the object's location/rotation_euler/scale at start and end frames, insert keyframes, and ensure sampled frames visibly change.
- Do not read action.fcurves directly. Blender 5 uses layered actions; if you need fcurves, traverse action.layers, strip.channelbags, and bag.fcurves. Prefer leaving default keyframe interpolation if direct fcurve access is not required.
- If AnimationEventSpec has path points or start/end transforms, use them exactly; otherwise infer a simple motion that satisfies the event description.
- Define a final variable named LL3M_METADATA with object ids and created object names.
"""
        return _sanitize_generated_blender_code(extract_code_block(self.llm.complete_text(system, user)))


class RefinerAgent:
    def __init__(self, config: HarnessConfig, rag: LocalRAG):
        self.llm = LLMClient(config.refiner)
        self.rag = rag

    def refine(
        self,
        *,
        ir: GenerationIR,
        code: str,
        reports: list[ValidationReport],
        execution_error: str | None = None,
        screenshot_paths: list[Path] | None = None,
    ) -> str:
        context = self.rag.format_context(_issue_query(reports, execution_error), limit=4, max_chars=5000)
        report_json = json.dumps([report.to_dict() if hasattr(report, "to_dict") else _report_dict(report) for report in reports], indent=2)
        system = (
            "You are a Blender 4.5.4 refiner. Repair the Python script locally. "
            "Keep correct existing structure. Treat visual verifier failures as blocking. "
            "For floating, detached, penetrated, or misaligned object parts, fix transforms, origins, connector geometry, parenting, and contact points directly in code. "
            "For animation failures, fix keyframe data paths, object roots, frame ranges, interpolation, and start/end transforms so sampled frames visibly match the AnimationSpec. "
            "For pick-and-place failures, do not animate the package independently while the gripper stays elsewhere. Animate the gripper/end-effector and package together during grasp/lift/carry frames, or parent/constraint the package to the gripper for that segment, so screenshots show continuous contact. "
            "Do not iterate action.fcurves directly; Blender 5 layered actions store fcurves under action.layers[*].strips[*].channelbags[*].fcurves. It is acceptable to remove custom interpolation edits and keep default interpolation. "
            "If materials render as default gray/white, fix localized Blender node lookup by finding BSDF_PRINCIPLED nodes by type and setting mat.diffuse_color. "
            "Return only the full corrected Python script."
        )
        user = f"""
Compact GenerationIR:
{json.dumps(_compact_ir_for_coder(ir), indent=2)}

Execution error:
{execution_error or "None"}

Validation reports:
{report_json}

Relevant Blender 4.5.4 notes:
{context}

Current script:
```python
{code}
```
"""
        if screenshot_paths:
            user += f"""

Attached images are the latest failed validation screenshots and/or sampled animation frames in verifier order.
Use them to fix actual visual layout, contact, motion direction, timing, and visibility problems, not just the text report.
"""
            try:
                return _sanitize_generated_blender_code(
                    extract_code_block(self.llm.complete_multimodal(system, user, screenshot_paths))
                )
            except Exception:
                pass
        return _sanitize_generated_blender_code(extract_code_block(self.llm.complete_text(system, user)))

    def apply_user_request(
        self,
        *,
        ir: GenerationIR,
        code: str,
        user_request: str,
        scene_graph: dict[str, Any] | None = None,
    ) -> str:
        context = self.rag.format_context(user_request + " Blender 4.5 bpy scene update", limit=4, max_chars=5000)
        system = (
            "You are an interactive Blender 4.5.4 code refiner. "
            "Update the existing full Python script to satisfy the user's new request. "
            "Preserve working code and object ids where possible. "
            "Return only the full corrected Python script."
        )
        user = f"""
Compact revised GenerationIR:
{json.dumps(_compact_ir_for_coder(ir), indent=2)}

User change request:
{user_request}

Current Blender scene graph:
{json.dumps(scene_graph or {}, indent=2, default=str)[:16000]}

Relevant Blender 4.5.4 notes:
{context}

Current script:
```python
{code}
```
"""
        return _sanitize_generated_blender_code(extract_code_block(self.llm.complete_text(system, user)))


class VisionVerifierAgent:
    def __init__(self, config: HarnessConfig):
        self.llm = LLMClient(config.vision)

    def verify(self, ir: GenerationIR, screenshot_paths: list[Path], deterministic_report: ValidationReport) -> ValidationReport:
        if not screenshot_paths:
            return ValidationReport.failed(
                VerificationMode.VISION,
                [
                    ValidationIssue(
                        code="NO_SCREENSHOTS",
                        message="Vision verification could not run because no screenshots were produced.",
                    )
                ],
            )

        system = (
            "You are a visual verifier for Blender-generated scenes. "
            "You are the final gate: do not pass a scene with visible physical implausibility, floating parts, detached connectors, impossible support/contact, missing objects, or badly misleading camera views. "
            "Your PRIMARY evidence is the screenshots. Judge what you SEE in the images. "
            "The deterministic report is supplementary context only. If the images clearly show objects are present and correctly placed, do NOT fail the scene just because the deterministic report mentions technical issues like MISSING_BBOX or MISSING_ACTIVE_CAMERA. "
            "Only fail for issues you can visually confirm in the screenshots. "
            "Return only JSON with keys: passed, summary, issues. "
            "Each issue must include code, message, severity, optional target_id, relation_id, frame, suggested_fix, evidence."
        )
        screenshot_manifest = [
            {"index": index + 1, "path": str(path), "name": path.name}
            for index, path in enumerate(screenshot_paths)
        ]
        user = f"""
Original prompt:
{ir.prompt.text}

SceneSpec excerpt:
{json.dumps(ir.to_dict().get("scene", {}), indent=2)[:12000]}

Screenshot order:
{json.dumps(screenshot_manifest, indent=2)}

Deterministic report:
{report_to_json(deterministic_report)}

Evaluate the screenshots for semantic correctness, missing objects, wrong spatial relationships,
bad camera angle, occlusion, poor lighting, and visible geometry defects.

Critical checks:
- Every multi-part object must look physically connected and intentionally assembled.
- Objects that should rest on, attach to, point at, or illuminate another object must have believable contact/alignment.
- Tabletop props, lamp heads/arms, handles, stems, legs, and other connectors must not float, detach, or penetrate incorrectly.
- If the screenshot set is insufficient to judge a required relation, fail with code INSUFFICIENT_VIEW_COVERAGE and suggest additional views.
- Do not reject a symmetric relation like "beside" merely because an object is on the opposite left/right side, unless the SceneSpec explicitly requires left or right.
- Do not report potential intersection from a single occluded side view if other views and deterministic validation do not support intersection; request an additional view or mark it minor.
- Set passed=true only when all required objects, relations, composition, and visible geometry are acceptable.
- If animation is present, judge only static geometry, object presence, per-frame contact/visibility shown in the screenshots, and camera coverage. Do not fail this scene verifier solely for temporal smoothness, full-frame motion continuity, or video-only questions; those are handled by the video verifier.
"""
        data = self.llm.json_multimodal(system, user, screenshot_paths)
        return _report_from_model(data, VerificationMode.VISION)


class VideoVerifierAgent:
    def __init__(self, config: HarnessConfig):
        self.llm = LLMClient(config.video)

    def verify(
        self,
        ir: GenerationIR,
        sampled_frame_paths: list[Path],
        preview_video_path: Path | None,
        deterministic_report: ValidationReport,
        transform_trace: dict[str, Any] | None = None,
    ) -> ValidationReport:
        if not ir.animation:
            return ValidationReport.ok(VerificationMode.VIDEO, "No animation requested.")
        if not sampled_frame_paths:
            return ValidationReport.failed(
                VerificationMode.VIDEO,
                [ValidationIssue(code="NO_SAMPLED_FRAMES", message="No sampled animation frames were produced.")],
            )

        system = (
            "You are a temporal verifier for Blender animations. "
            "Return only JSON with keys: passed, summary, issues. "
            "Judge action order, motion path, smoothness, object interactions, physical plausibility, and camera visibility. "
            "Do not pass animations where required motion is absent, reversed, hidden, too subtle to see, or where contact, attachment, or object continuity breaks between frames."
        )
        frame_manifest = [
            {"index": index + 1, "path": str(path), "name": path.name}
            for index, path in enumerate(sampled_frame_paths)
        ]
        user = f"""
Original prompt:
{ir.prompt.text}

AnimationSpec:
{json.dumps(ir.to_dict().get("animation", {}), indent=2)[:12000]}

Preview video path for metadata only:
{preview_video_path or "None"}

Sampled frame order:
{json.dumps(frame_manifest, indent=2)}

Transform trace:
{json.dumps(transform_trace or {}, indent=2)[:12000]}

Deterministic animation report:
{report_to_json(deterministic_report)}

The attached images are ordered sampled frames. Verify whether the requested animation is visually and temporally correct.
If deterministic transform trace and images disagree, explain the mismatch and fail unless the animation is still visually unambiguous.
"""
        if preview_video_path and preview_video_path.exists():
            data = self.llm.json_video(system, user, preview_video_path, sampled_frame_paths)
        else:
            data = self.llm.json_multimodal(system, user, sampled_frame_paths)
        return _report_from_model(data, VerificationMode.VIDEO)


def _compact_ir_for_coder(ir: GenerationIR) -> dict[str, Any]:
    """Keep code-generation prompts small without weakening validation IR."""

    scene = ir.scene
    data: dict[str, Any] = {
        "prompt": {
            "text": ir.prompt.text,
            "negative_text": ir.prompt.negative_text,
            "constraints": ir.prompt.user_constraints,
        },
        "scene": {
            "objects": [
                {
                    "id": obj.id,
                    "description": obj.description,
                    "label": obj.label,
                    "category": _value(obj.category),
                    "role": _value(obj.role),
                    "importance": _value(obj.importance),
                    "parts": [
                        {
                            "id": part.id,
                            "description": part.description,
                            "required": part.required,
                            "material_id": part.material_id,
                            "expected_count": part.expected_count,
                            "dimension": _dimension(part.dimension),
                        }
                        for part in obj.parts
                    ],
                    "required_features": obj.required_features,
                    "forbidden_features": obj.forbidden_features,
                    "dimensions": _dimension(obj.dimensions),
                    "placement": {
                        "transform": _transform(obj.placement.transform),
                        "anchor": obj.placement.anchor,
                        "parent_id": obj.placement.parent_id,
                        "notes": obj.placement.notes,
                    },
                    "material_ids": obj.material_ids,
                    "generation_notes": obj.generation_notes,
                }
                for obj in scene.objects
            ],
            "relations": [
                {
                    "id": relation.id,
                    "relation_type": _value(relation.relation_type),
                    "subject_id": relation.subject_id,
                    "object_id": relation.object_id,
                    "description": relation.description,
                    "required": relation.required,
                    "tolerance": relation.tolerance,
                    "min_distance": relation.min_distance,
                    "max_distance": relation.max_distance,
                    "offset": relation.offset,
                    "axis": relation.axis,
                }
                for relation in scene.relations
            ],
            "materials": [
                {
                    "id": material.id,
                    "description": material.description,
                    "base_color": material.base_color,
                    "metallic": material.metallic,
                    "roughness": material.roughness,
                    "alpha": material.alpha,
                    "texture_hints": material.texture_hints,
                }
                for material in scene.materials
            ],
            "environment": {
                "environment_type": _value(scene.environment.environment_type),
                "description": scene.environment.description,
                "floor": scene.environment.floor,
                "walls": scene.environment.walls,
                "sky": scene.environment.sky,
                "world_background": scene.environment.world_background,
                "ambient_occlusion": scene.environment.ambient_occlusion,
                "lights": [
                    {
                        "id": light.id,
                        "light_type": _value(light.light_type),
                        "description": light.description,
                        "location": light.location,
                        "rotation_euler": light.rotation_euler,
                        "energy": light.energy,
                        "color": light.color,
                        "size": light.size,
                    }
                    for light in scene.environment.lights
                ],
            },
            "cameras": [
                {
                    "id": camera.id,
                    "view_type": _value(camera.view_type),
                    "description": camera.description,
                    "location": camera.location,
                    "look_at": camera.look_at,
                    "target_object_ids": camera.target_object_ids,
                    "focal_length_mm": camera.focal_length_mm,
                    "coverage": camera.coverage,
                }
                for camera in scene.cameras
            ],
            "style": {
                "description": scene.style.description,
                "detail_level": scene.style.detail_level,
                "color_palette": scene.style.color_palette,
                "material_style": scene.style.material_style,
            },
        },
        "version": ir.version,
        "notes": ir.notes,
    }
    if ir.animation:
        data["animation"] = {
            "duration_frames": ir.animation.duration_frames,
            "fps": ir.animation.fps,
            "loop": ir.animation.loop,
            "events": [_compact_animation_event(event) for event in ir.animation.events],
            "camera_events": [_compact_animation_event(event) for event in ir.animation.camera_events],
            "render": {
                "resolution": ir.animation.render.resolution,
                "engine": _value(ir.animation.render.engine),
            },
            "verifier": {
                "sampled_frames": ir.animation.verifier.sampled_frames,
                "pass_criteria": ir.animation.verifier.pass_criteria,
            },
        }
    return _drop_none(data)


def _compact_animation_event(event: Any) -> dict[str, Any]:
    return _drop_none(
        {
            "id": event.id,
            "action": _value(event.action),
            "subject_ids": event.subject_ids,
            "target_ids": event.target_ids,
            "start_frame": event.start_frame,
            "end_frame": event.end_frame,
            "description": event.description,
            "start_transform": _transform(event.start_transform),
            "end_transform": _transform(event.end_transform),
            "path": {
                "points": event.path.points,
                "keyframes": [
                    {
                        "frame": keyframe.frame,
                        "transform": _transform(keyframe.transform),
                        "value": keyframe.value,
                        "interpolation": _value(keyframe.interpolation),
                        "description": keyframe.description,
                    }
                    for keyframe in event.path.keyframes
                ],
                "path_object_id": event.path.path_object_id,
                "follow_orientation": event.path.follow_orientation,
            }
            if event.path
            else None,
            "interpolation": _value(event.interpolation),
            "required": event.required,
            "expected_visual_result": event.expected_visual_result,
            "constraints": event.constraints,
        }
    )


def _dimension(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return _drop_none(
        {
            "size": value.size,
            "min_size": value.min_size,
            "max_size": value.max_size,
            "tolerance": value.tolerance,
        }
    )


def _transform(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return _drop_none(
        {
            "location": value.location,
            "rotation_euler": value.rotation_euler,
            "scale": value.scale,
        }
    )


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _issue_query(reports: list[ValidationReport], execution_error: str | None) -> str:
    parts = [execution_error or ""]
    for report in reports:
        for issue in report.issues:
            parts.extend([issue.code, issue.message, issue.suggested_fix or ""])
    return " ".join(parts) or "Blender 4.5 bpy gotchas"


def _sanitize_planner_data(data: dict[str, Any]) -> None:
    """Repair harmless planner enum drift before strict dataclass decoding."""

    scene = data.get("scene") if isinstance(data, dict) else None
    if not isinstance(scene, dict):
        return
    valid_categories = {
        "generic",
        "furniture",
        "prop",
        "character",
        "vehicle",
        "architecture",
        "terrain",
        "lighting",
        "camera_rig",
        "effect",
    }
    category_aliases = {
        "stationery": "prop",
        "decor": "decoration",
        "decoration": "generic",
        "plant": "prop",
        "book": "prop",
        "tableware": "prop",
    }
    valid_roles = {"primary", "secondary", "background", "support", "decoration", "camera_target", "collider"}
    for obj in scene.get("objects", []) or []:
        if not isinstance(obj, dict):
            continue
        category = str(obj.get("category", "generic")).lower()
        obj["category"] = category_aliases.get(category, category if category in valid_categories else "generic")
        role = str(obj.get("role", "secondary")).lower()
        obj["role"] = role if role in valid_roles else "secondary"


def _sanitize_animation_data(data: dict[str, Any]) -> None:
    animation = data.get("animation") if isinstance(data, dict) else None
    if not isinstance(animation, dict):
        return
    for event in [*(animation.get("events") or []), *(animation.get("camera_events") or [])]:
        if not isinstance(event, dict):
            continue
        if "interpolation" in event:
            event["interpolation"] = _normalize_interpolation(event["interpolation"])
        path = event.get("path")
        if isinstance(path, dict):
            points = path.get("points")
            if isinstance(points, list):
                path["points"] = [_normalize_path_point(point) for point in points]
            keyframes = path.get("keyframes")
            if isinstance(keyframes, list):
                for keyframe in keyframes:
                    if not isinstance(keyframe, dict):
                        continue
                    if "interpolation" in keyframe:
                        keyframe["interpolation"] = _normalize_interpolation(keyframe["interpolation"])
                    transform = keyframe.get("transform")
                    if isinstance(transform, dict) and "location" in transform:
                        transform["location"] = _normalize_vec3(transform["location"])
        for key in ("start_transform", "end_transform"):
            transform = event.get(key)
            if isinstance(transform, dict):
                for field_name in ("location", "rotation_euler", "scale"):
                    if field_name in transform:
                        transform[field_name] = _normalize_vec3(transform[field_name])


def _normalize_interpolation(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": "ease_in_out",
        "step": "constant",
        "stepped": "constant",
        "hold": "constant",
        "held": "constant",
        "none": "constant",
        "linear_interpolation": "linear",
        "easein": "ease_in",
        "ease_in": "ease_in",
        "easeout": "ease_out",
        "ease_out": "ease_out",
        "easeinout": "ease_in_out",
        "ease_inout": "ease_in_out",
        "ease_out_in": "ease_in_out",
        "ease_in_out": "ease_in_out",
        "smooth": "ease_in_out",
        "spline": "bezier",
        "bezier_curve": "bezier",
    }
    valid = {"constant", "linear", "ease_in", "ease_out", "ease_in_out", "bezier"}
    return aliases.get(text, text if text in valid else "ease_in_out")


def _normalize_path_point(point: Any) -> list[float]:
    if isinstance(point, dict):
        for key in ("location", "point", "position", "coordinate", "coordinates", "value"):
            if key in point:
                return _normalize_vec3(point[key])
        values = [point.get("x"), point.get("y"), point.get("z")]
        if values[0] is not None and values[1] is not None:
            return _normalize_vec3(values)
    if isinstance(point, (list, tuple)) and len(point) == 2 and isinstance(point[1], (list, tuple, dict)):
        return _normalize_vec3(point[1])
    return _normalize_vec3(point)


def _normalize_vec3(value: Any) -> list[float]:
    if isinstance(value, dict):
        if all(key in value for key in ("x", "y")):
            raw = [value.get("x"), value.get("y"), value.get("z", 0.0)]
        else:
            raw = list(value.values())
    elif isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raw = [value, 0.0, 0.0]
    if len(raw) == 2:
        raw.append(0.0)
    while len(raw) < 3:
        raw.append(0.0)
    return [_float_or_zero(item) for item in raw[:3]]


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalize_part_references(ir: GenerationIR) -> None:
    """Map planner-emitted part ids back to object ids where the IR expects objects."""

    object_ids = {obj.id for obj in ir.scene.objects}
    part_to_object: dict[str, str] = {}
    object_by_id = {obj.id: obj for obj in ir.scene.objects}
    for obj in ir.scene.objects:
        for part in obj.parts:
            if part.id and part.id not in object_ids:
                part_to_object[part.id] = obj.id

    if not part_to_object:
        return

    normalized_relations = []
    removed_relation_ids: set[str] = set()
    for relation in ir.scene.relations:
        original_subject = relation.subject_id
        original_object = relation.object_id
        relation.subject_id = part_to_object.get(relation.subject_id, relation.subject_id)
        relation.object_id = part_to_object.get(relation.object_id, relation.object_id)
        if relation.subject_id == relation.object_id and relation.subject_id in object_by_id:
            obj = object_by_id[relation.subject_id]
            detail = relation.description or f"{original_subject} {relation.relation_type.value} {original_object}"
            feature = f"Internal part relationship must be visible and physically plausible: {detail}."
            if feature not in obj.required_features:
                obj.required_features.append(feature)
            if feature not in obj.visual_check_prompts:
                obj.visual_check_prompts.append(feature)
            removed_relation_ids.add(relation.id)
            continue
        normalized_relations.append(relation)
    ir.scene.relations = normalized_relations
    valid_relation_ids = {relation.id for relation in ir.scene.relations}

    for camera in ir.scene.cameras:
        camera.target_object_ids = _map_ids(camera.target_object_ids, part_to_object)

    for view in ir.scene.verifier.screenshot_plan.views:
        view.target_object_ids = _map_ids(view.target_object_ids, part_to_object)
        view.relation_ids = [
            relation_id
            for relation_id in view.relation_ids
            if relation_id in valid_relation_ids and relation_id not in removed_relation_ids
        ]

    visual = ir.scene.verifier.visual
    visual.required_view_ids = list(dict.fromkeys(visual.required_view_ids))

    if ir.animation:
        for event in [*ir.animation.events, *ir.animation.camera_events]:
            event.subject_ids = _map_ids(event.subject_ids, part_to_object)
            event.target_ids = _map_ids(event.target_ids, part_to_object)


def _map_ids(values: list[str], mapping: dict[str, str]) -> list[str]:
    return list(dict.fromkeys(mapping.get(value, value) for value in values))


def _normalize_ambiguous_beside_relations(ir: GenerationIR) -> None:
    """Avoid turning natural-language 'beside' into a forced left/right constraint."""

    prompt_text = ir.prompt.text.lower()
    if any(token in prompt_text for token in ("left of", "right of", "to the left", "to the right")):
        return
    for relation in ir.scene.relations:
        if relation.relation_type not in {RelationType.LEFT_OF, RelationType.RIGHT_OF}:
            continue
        text = " ".join(
            value or ""
            for value in (
                relation.description,
                relation.id,
                prompt_text,
            )
        ).lower()
        if not any(token in text for token in ("beside", "next to", "near", "adjacent")):
            continue
        relation.relation_type = RelationType.NEAR
        relation.description = (
            relation.description or "Objects should be beside each other."
        ) + " Interpreted as symmetric beside/near, not a forced left/right direction."
        relation.min_distance = relation.min_distance if relation.min_distance is not None else 0.2
        relation.max_distance = relation.max_distance if relation.max_distance is not None else 3.0
        relation.axis = None


def _normalize_animation_verifier(ir: GenerationIR) -> None:
    if not ir.animation:
        return
    _normalize_animation_interaction_targets(ir)
    verifier = ir.animation.verifier
    verifier.enabled = True
    duration = max(1, int(ir.animation.duration_frames))
    frames = {1, duration, max(1, duration // 2)}
    for event in [*ir.animation.events, *ir.animation.camera_events]:
        start = max(1, min(duration, int(event.start_frame)))
        middle = max(1, min(duration, int((event.start_frame + event.end_frame) / 2)))
        end = max(1, min(duration, int(event.end_frame)))
        frames.update({start, middle, end})
        if not event.expected_visual_result:
            event.expected_visual_result = _default_event_visual_result(event)
    verifier.sampled_frames = sorted(frames)
    verifier.require_preview_video = False
    if not verifier.questions:
        verifier.questions = _default_video_questions(ir)
    if not verifier.pass_criteria:
        verifier.pass_criteria = [
            "Sampled frames show visible temporal change for every required animation event.",
            "Animated objects remain visible and preserve required scene relationships unless the event intentionally changes them.",
            "Motion direction, timing, and final state match the AnimationSpec.",
        ]


def _normalize_animation_interaction_targets(ir: GenerationIR) -> None:
    if not ir.animation:
        return
    object_ids = {obj.id for obj in ir.scene.objects}
    gripper_like = [
        obj.id
        for obj in ir.scene.objects
        if any(token in f"{obj.id} {obj.description} {obj.label or ''}".lower() for token in ("gripper", "end effector", "end-effector"))
    ]
    arm_like = [
        obj.id
        for obj in ir.scene.objects
        if any(token in f"{obj.id} {obj.description} {obj.label or ''}".lower() for token in ("robotic_arm", "robotic arm", "gripper", "end effector", "end-effector"))
    ]
    target_candidates = gripper_like or arm_like
    if not target_candidates:
        return
    for event in ir.animation.events:
        if event.action in {AnimationAction.APPEAR, AnimationAction.DISAPPEAR}:
            continue
        text = " ".join(
            [
                event.id,
                event.description,
                event.expected_visual_result or "",
                " ".join(event.constraints),
                " ".join(event.subject_ids),
            ]
        ).lower()
        if any(token in text for token in ("light", "status", "signal")):
            continue
        if not any(token in text for token in ("grasp", "gripper", "lift", "carry", "pick", "place", "transfer")):
            continue
        if not any(token in text for token in ("package", "box", "parcel", "object")):
            continue
        for target_id in target_candidates:
            if target_id in object_ids and target_id not in event.target_ids and target_id not in event.subject_ids:
                event.target_ids.append(target_id)


def _default_event_visual_result(event: Any) -> str:
    subject = ", ".join(event.subject_ids) or "the animated subject"
    return (
        f"{subject} visibly performs {getattr(event.action, 'value', event.action)} "
        f"from frame {event.start_frame} to frame {event.end_frame}."
    )


def _default_video_questions(ir: GenerationIR) -> list[str]:
    questions = [
        "Do the ordered sampled frames show visible temporal change rather than a static scene?",
        "Does each animated subject reach the expected final state at the final sampled frame?",
        "Does the camera keep the main animated subjects visible during the animation?",
    ]
    for event in [*ir.animation.events, *ir.animation.camera_events] if ir.animation else []:
        subject = ", ".join(event.subject_ids) or "the subject"
        questions.append(
            f"Does {subject} perform the requested {getattr(event.action, 'value', event.action)} action from frame {event.start_frame} to {event.end_frame}?"
        )
    return questions


def _load_ir_reference() -> str:
    path = Path("docs/ir.md")
    if not path.exists():
        return "IR reference file docs/ir.md is unavailable. Use harness.ir dataclass field names."
    text = path.read_text(encoding="utf-8")
    # Keep the whole practical definition. This is more reliable than retrieval
    # for planner correctness and still small enough for the configured models.
    return text[:24000]


def _planner_json_skeleton(include_animation: bool | None) -> str:
    animation_block = """
  "animation": {
    "duration_frames": 120,
    "fps": 24,
    "events": [
      {
        "id": "event_id",
        "action": "translate",
        "subject_ids": ["object_id"],
        "start_frame": 1,
        "end_frame": 120,
        "description": "what happens",
        "target_ids": [],
        "start_transform": {
          "location": [-2.0, 0.0, 0.5]
        },
        "end_transform": {
          "location": [2.0, 0.0, 0.5]
        },
        "path": {
          "points": [],
          "keyframes": [
            {
              "frame": 60,
              "transform": {
                "location": [0.0, 0.0, 0.5]
              },
              "value": {},
              "interpolation": "ease_in_out",
              "description": "midpoint state that the deterministic and video verifier can inspect"
            }
          ],
          "follow_orientation": false
        },
        "interpolation": "ease_in_out",
        "required": true,
        "expected_visual_result": "object_id visibly moves from the start location through the midpoint to the end location",
        "constraints": [
          "Start, middle, and end states must be visible in sampled frames."
        ]
      }
    ],
    "camera_events": [],
    "loop": false,
    "verifier": {
      "enabled": true,
      "model_hint": "qwen3.5-omni",
      "sampled_frames": [1, 60, 120],
      "require_preview_video": false,
      "questions": [
        "Does object_id visibly change over the ordered sampled frames?",
        "Does object_id move from the expected start state through the midpoint to the expected end state?",
        "Does the camera keep object_id visible throughout the sampled frames?"
      ],
      "pass_criteria": [
        "Every required animation event shows visible temporal change.",
        "Sampled frames cover each event start, at least one intermediate state, and event end.",
        "Motion direction, timing, and final state match the AnimationSpec."
      ],
      "max_rounds": 6
    }
  },""" if include_animation else ""
    return f"""{{
  "prompt": {{
    "text": "original user prompt",
    "negative_text": null,
    "image_paths": [],
    "user_constraints": []
  }},
  "scene": {{
    "objects": [
      {{
        "id": "stable_object_id",
        "description": "semantic description",
        "label": "Human Label",
        "category": "generic",
        "role": "primary",
        "importance": "required",
        "parts": [
          {{
            "id": "part_id",
            "description": "part description",
            "required": true,
            "material_id": "material_id",
            "expected_count": 1
          }}
        ],
        "required_features": [],
        "optional_features": [],
        "forbidden_features": [],
        "material_ids": ["material_id"],
        "visual_check_prompts": []
      }}
    ],
    "relations": [
      {{
        "id": "relation_id",
        "relation_type": "on_top_of",
        "subject_id": "stable_object_id",
        "object_id": "other_object_id",
        "description": "semantic relation",
        "required": true,
        "tolerance": 0.05,
        "visual_priority": "required"
      }}
    ],
    "materials": [
      {{
        "id": "material_id",
        "description": "material description",
        "base_color": [0.5, 0.5, 0.5, 1.0],
        "texture_hints": []
      }}
    ],
    "environment": {{
      "environment_type": "studio",
      "description": "environment description",
      "floor": "floor description",
      "lights": [
        {{
          "id": "key_light",
          "light_type": "area",
          "description": "soft key light",
          "location": [2.0, -3.0, 4.0],
          "energy": 500,
          "size": 4.0
        }}
      ],
      "ambient_occlusion": true
    }},
    "cameras": [
      {{
        "id": "camera_main",
        "view_type": "three_quarter",
        "description": "main inspection view",
        "target_object_ids": ["stable_object_id"],
        "coverage": "all primary objects visible"
      }}
    ],
    "style": {{
      "description": "style description",
      "detail_level": "medium",
      "color_palette": []
    }},
    "verifier": {{
      "deterministic_checks": [],
      "screenshot_plan": {{
        "views": [
          {{
            "id": "three_quarter",
            "view_type": "three_quarter",
            "description": "overall scene view",
            "target_object_ids": ["stable_object_id"],
            "relation_ids": [],
            "required": true
          }},
          {{
            "id": "contact_closeup",
            "view_type": "relation_close_up",
            "description": "close view for required contact, support, and attachment relations",
            "target_object_ids": ["stable_object_id"],
            "relation_ids": ["relation_id"],
            "required": true
          }},
          {{
            "id": "side_support",
            "view_type": "right",
            "description": "side view that makes vertical support and floating parts visible",
            "target_object_ids": ["stable_object_id"],
            "relation_ids": ["relation_id"],
            "required": true
          }}
        ],
        "min_required_views": 3
      }},
      "visual": {{
        "enabled": true,
        "model_hint": null,
        "required_view_ids": ["three_quarter", "contact_closeup", "side_support"],
        "questions": [],
        "pass_criteria": [
          "All required objects are visible.",
          "Required spatial relations are visually correct.",
          "No object or required part is floating, detached, misaligned, or visibly intersecting unless requested.",
          "Camera angles are sufficient for judging contact and support."
        ],
        "max_rounds": 6
      }},
      "video": {{
        "enabled": false,
        "model_hint": "qwen3.5-omni",
        "sampled_frames": [],
        "require_preview_video": true,
        "questions": [],
        "pass_criteria": [],
        "max_rounds": 6
      }}
    }},
    "coordinate_system": "Blender default: Z up, right-handed",
    "units": "meters"
  }},
{animation_block}
  "version": "0.1",
  "project_id": null,
  "notes": "planner notes"
}}"""


def _report_dict(report: ValidationReport) -> dict[str, Any]:
    from .ir import report_to_dict

    return report_to_dict(report)


def _sanitize_generated_blender_code(code: str) -> str:
    """Patch common Blender API hallucinations before execution."""
    replacements = {
        "bpy.types.Scene.bl_rna.properties['render_engine'].enum_items": "bpy.context.scene.render.bl_rna.properties['engine'].enum_items",
        'bpy.types.Scene.bl_rna.properties["render_engine"].enum_items': 'bpy.context.scene.render.bl_rna.properties["engine"].enum_items',
        "bpy.types.Scene.bl_rna.properties['render_engine']": "bpy.context.scene.render.bl_rna.properties['engine']",
        'bpy.types.Scene.bl_rna.properties["render_engine"]': 'bpy.context.scene.render.bl_rna.properties["engine"]',
    }
    for bad, good in replacements.items():
        code = code.replace(bad, good)
    code = _patch_direct_action_fcurve_loops(code)
    code = _patch_common_ir_id_drift(code)
    code = _append_active_camera_fallback(code)
    return code


def _append_active_camera_fallback(code: str) -> str:
    snippet = '''

if bpy.context.scene.camera is None:
    for _ll3m_camera in bpy.data.objects:
        if _ll3m_camera.type == "CAMERA":
            bpy.context.scene.camera = _ll3m_camera
            break
'''.rstrip()
    if "bpy.context.scene.camera is None" in code or "scene.camera is None" in code:
        return code
    return code.rstrip() + snippet + "\n"


def _patch_common_ir_id_drift(code: str) -> str:
    replacements = {
        '"BallMaterial"': '"ball_material"',
        '"FloorMaterial"': '"floor_material"',
        '"BoxMaterial"': '"box_material"',
        '"ball_body"': '"ball"',
        '"box_body"': '"box"',
    }
    for bad, good in replacements.items():
        code = code.replace(bad, good)
    code = re.sub(r"(120\s*:\s*)\((1\.0|1),\s*0\.0,\s*0\.2\)", r"\1(1.2, 0.0, 0.2)", code)
    code = re.sub(
        r"(end_loc\s*=\s*(?:mathutils\.)?Vector\()\((1\.0|1),\s*0\.0,\s*(0\.15|0\.2)\)\)",
        r"\1(1.2, 0.0, \3))",
        code,
    )
    return code


def _patch_direct_action_fcurve_loops(code: str) -> str:
    helper = '''
def ll3m_iter_action_fcurves(action):
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
'''.strip()
    if ".animation_data.action.fcurves" in code and "def ll3m_iter_action_fcurves(" not in code:
        code = helper + "\n\n" + code
    code = re.sub(
        r"for\s+(\w+)\s+in\s+([A-Za-z_][A-Za-z0-9_]*)\.animation_data\.action\.fcurves\s*:",
        r"for \1 in ll3m_iter_action_fcurves(\2.animation_data.action):",
        code,
    )
    code = re.sub(
        r"for\s+(\w+)\s+in\s+([A-Za-z_][A-Za-z0-9_]*)\.action\.fcurves\s*:",
        r"for \1 in ll3m_iter_action_fcurves(\2.action):",
        code,
    )
    return code


def _report_from_model(data: dict[str, Any], mode: VerificationMode) -> ValidationReport:
    issues = []
    for item in data.get("issues", []) or []:
        if isinstance(item, str):
            issues.append(
                ValidationIssue(
                    code="MODEL_REPORTED_ISSUE",
                    message=item,
                    severity=Severity.MAJOR,
                )
            )
            continue
        if not isinstance(item, dict):
            issues.append(
                ValidationIssue(
                    code="MODEL_REPORTED_ISSUE",
                    message=str(item),
                    severity=Severity.MAJOR,
                )
            )
            continue
        issues.append(
            ValidationIssue(
                code=str(item.get("code") or "MODEL_REPORTED_ISSUE"),
                message=str(item.get("message") or item.get("problem") or "Model reported an issue."),
                severity=_severity(item.get("severity", "major")),
                target_id=item.get("target_id") or item.get("object"),
                relation_id=item.get("relation_id"),
                frame=item.get("frame"),
                suggested_fix=item.get("suggested_fix"),
                evidence=item.get("evidence") or {},
            )
        )
    passed = bool(data.get("passed", data.get("pass", not issues)))
    if passed and any(issue.severity in {Severity.MAJOR, Severity.CRITICAL} for issue in issues):
        passed = False
    if not passed and not issues:
        issues.append(
            ValidationIssue(
                code="MODEL_REPORTED_FAILURE",
                message=str(data.get("summary") or "Verifier reported failure without structured issues."),
                severity=Severity.MAJOR,
            )
        )
    return ValidationReport(
        mode=mode,
        passed=passed,
        issues=issues,
        summary=data.get("summary"),
    )


def _severity(value: Any) -> Severity:
    if isinstance(value, Severity):
        return value
    try:
        return Severity(str(value))
    except ValueError:
        return Severity.MAJOR
