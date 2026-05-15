"""Agent implementations for planning, coding, refinement, and model verifiers."""

from __future__ import annotations

import json
from pathlib import Path
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
        context = self.rag.format_context("IR SceneSpec AnimationSpec ScreenshotPlan VideoVerifierSpec", limit=5)
        ir_reference = _load_ir_reference()
        system = (
            "You are the planner for a Blender 4.5.4 code-generation harness. "
            "Return only a JSON object matching the GenerationIR schema. "
            "Use stable machine ids. Include screenshot views for visual validation. "
            "Plan at least three complementary screenshot views: an overall three-quarter view, a relation/contact close-up, and a side or top view that exposes support/contact. "
            "Add visual pass criteria that require no floating, detached, or misaligned parts unless explicitly requested. "
            "When animation is requested, include AnimationSpec and video verifier settings. "
            "Do not invent fields outside the schema. If you create a relation, it must include id, relation_type, subject_id, and object_id. "
            "Relations, cameras, screenshot targets, and animation subjects must reference ObjectSpec ids, not ObjectPartSpec ids."
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
        context = self.rag.format_context("IR revision SceneSpec AnimationSpec user refinement", limit=5)
        ir_reference = _load_ir_reference()
        system = (
            "You revise an existing GenerationIR for a Blender 4.5.4 harness. "
            "Return only the complete revised GenerationIR JSON. Preserve stable ids where possible. "
            "Add new ids only for new objects, relations, cameras, screenshots, or animation events. "
            "Do not invent fields outside the schema. If you create a relation, it must include id, relation_type, subject_id, and object_id. "
            "Relations, cameras, screenshot targets, and animation subjects must reference ObjectSpec ids, not ObjectPartSpec ids."
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
        for attempt in range(2):
            request = user
            if attempt:
                request += f"""

The previous JSON failed to decode or validate:
{last_error}

Return corrected complete GenerationIR JSON only. Every relation must include
relation_type, subject_id, and object_id. Every relation/camera/view/animation
reference must point to an ObjectSpec id, never a part id. Every object must
include id and description.
"""
            try:
                data = self.llm.json_text(system, request)
                _sanitize_planner_data(data)
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
            "For Blender 4.5, prefer BLENDER_EEVEE_NEXT or WORKBENCH after checking available render engine enum values; do not hardcode removed BLENDER_EEVEE. "
            "For animation, implement simple explicit keyframes from AnimationSpec events. "
            "Animate object roots that own the ll3m_id, set scene frame range/fps, insert start/end keyframes, and set interpolation on every generated keyframe. "
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
- Assign custom properties: ll3m_id, ll3m_role, ll3m_part where appropriate.
- Keep object names stable and human-readable.
- Create robust materials by setting mat.diffuse_color and locating shader nodes by node.type, not localized node names.
- Set render engines defensively by checking available enum values; Blender 4.5 uses BLENDER_EEVEE_NEXT rather than legacy BLENDER_EEVEE.
- Set frame_start/frame_end/fps if animation exists.
- Insert keyframes for AnimationSpec events when present. For translate/rotate/scale, mutate the object's location/rotation_euler/scale at start and end frames, insert keyframes, and ensure sampled frames visibly change.
- If AnimationEventSpec has path points or start/end transforms, use them exactly; otherwise infer a simple motion that satisfies the event description.
- Define a final variable named LL3M_METADATA with object ids and created object names.
"""
        return extract_code_block(self.llm.complete_text(system, user, max_tokens=32000))


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
                return extract_code_block(
                    self.llm.complete_multimodal(system, user, screenshot_paths, max_tokens=32000)
                )
            except Exception:
                pass
        return extract_code_block(self.llm.complete_text(system, user, max_tokens=32000))

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
        return extract_code_block(self.llm.complete_text(system, user, max_tokens=32000))


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
        data = self.llm.json_multimodal(system, user, screenshot_paths, max_tokens=4000)
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
            data = self.llm.json_video(system, user, preview_video_path, sampled_frame_paths, max_tokens=4000)
        else:
            data = self.llm.json_multimodal(system, user, sampled_frame_paths, max_tokens=4000)
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
    verifier = ir.animation.verifier
    verifier.enabled = True
    duration = max(1, int(ir.animation.duration_frames))
    frames = {1, duration, max(1, duration // 2)}
    for event in [*ir.animation.events, *ir.animation.camera_events]:
        frames.add(max(1, int(event.start_frame)))
        frames.add(max(1, min(duration, int((event.start_frame + event.end_frame) / 2))))
        frames.add(max(1, min(duration, int(event.end_frame))))
    verifier.sampled_frames = sorted(frames)
    verifier.require_preview_video = False
    if not verifier.pass_criteria:
        verifier.pass_criteria = [
            "Sampled frames show visible temporal change for every required animation event.",
            "Animated objects remain visible and preserve required scene relationships unless the event intentionally changes them.",
            "Motion direction, timing, and final state match the AnimationSpec.",
        ]


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
        "interpolation": "ease_in_out",
        "required": true,
        "expected_visual_result": "what the video verifier should see",
        "constraints": []
      }
    ],
    "camera_events": [],
    "loop": false,
    "verifier": {
      "enabled": true,
      "model_hint": "qwen3.5-omni",
      "sampled_frames": [1, 60, 120],
      "require_preview_video": true,
      "questions": [],
      "pass_criteria": [],
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
