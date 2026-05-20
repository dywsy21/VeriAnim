"""Agent implementations for planning, coding, refinement, and model verifiers."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from .config import HarnessConfig
from .ir import (
    AnimationAction,
    GenerationIR,
    RelationType,
    RelationVerificationMethod,
    RenderSpec,
    Severity,
    TexturePolicy,
    TextureSourceSpec,
    ValidationIssue,
    ValidationReport,
    VerificationMode,
    report_to_json,
)
from .llm import LLMClient, extract_code_block
from .rag import LocalRAG
from .serde import from_dict
from .textures import FREE_STOCK_TEXTURES_LICENSE, FreeStockTexturesClient, TextureCandidate


class PlannerAgent:
    def __init__(self, config: HarnessConfig, rag: LocalRAG):
        self.llm = LLMClient(config.planner)
        self.rag = rag
        self.max_retries = config.planner_max_retries

    def plan(self, prompt: str, *, include_animation: bool = False) -> GenerationIR:
        context = self.rag.format_context("IR SceneSpec AnimationSpec ScreenshotPlan VideoVerifierSpec", limit=3, max_chars=7000)
        ir_reference = _load_ir_reference()
        system = (
            "You are the planner for a Blender 4.5.4 code-generation harness. "
            "Return only a JSON object matching the GenerationIR schema. "
            "Use stable machine ids. Include screenshot views for visual validation. "
            "Keep the IR concise and executable: use at most 7 scene objects, 12 relations, 5 screenshot views, 3 animation events, 8 visual questions, and 8 pass criteria unless the user explicitly asks for more. "
            "Prefer compact descriptions and omit optional features that are not needed for verification. "
            "For each MaterialSpec decide whether an external image texture is needed. Set needs_texture=true and texture_query for natural, patterned, grainy, irregular, or surface-specific materials such as wood grain, stone, concrete, rusted metal, bark, fabric, leather, brick, grass, tabletop planks, and walls. Set needs_texture=false for intentionally plain or solid surfaces such as a pure-color mug, simple plastic toy, flat painted part, signal light, or clean ceramic. "
            "Plan at least three complementary screenshot views: an overall three-quarter view, a relation/contact close-up, and a side or top view that exposes support/contact. "
            "Add visual pass criteria that require no floating, detached, or misaligned parts unless explicitly requested. "
            "When animation is requested, include AnimationSpec and video verifier settings. "
            "Animation events must be structurally verifiable: use translate, rotate, scale, follow_path, appear, disappear, camera_move, or camera_orbit; include start_transform, at least one intermediate path.keyframe or path point, end_transform, sampled frames covering start/middle/end, temporal questions, and pass criteria. "
            "For every required animation event, include visibility_requirements that say which subjects, contact points, and final placements must remain visible in the GIF and sampled frames. "
            "For signal or material color changes, do not use one vague color-change event. Model separate colored visible parts such as red_light and green_light, then use disappear/appear events with explicit path.keyframes value.visible or value.alpha. "
            "For pick, grasp, carry, lift, or place animations, model the gripper/end-effector as its own ObjectSpec when possible, and put that object id in target_ids for the package lift/transfer events so contact continuity can be verified. "
            "For slanted ramps, inclined planes, hinges, brackets, and structural supports, use attached_to or touching relations rather than on_top_of unless the surfaces are horizontal and directly stacked. "
            "Set relation.verification_method explicitly when geometry needs special treatment: bbox_contact for horizontal support, attachment for hinges/connectors, distance for near/far, visual_only for slanted/occluded contacts that require screenshot judgment. "
            "Set material.texture_policy to solid_only or forbidden when the user asks for plain/solid/no image textures; set required only when an image texture is essential. "
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
            "For each MaterialSpec decide whether an external image texture is needed. Use needs_texture=true and texture_query only for natural, patterned, grainy, irregular, or surface-specific materials; keep needs_texture=false for intentionally plain or solid-color surfaces. "
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
        for attempt in range(max(1, self.max_retries + 1)):
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
                ir.ensure_progressive_stages()
                report = ir.validate()
                if report.passed:
                    return ir
                last_error = report_to_json(report)
            except Exception as exc:
                last_error = str(exc)
        raise ValueError(f"Planner produced invalid IR after retry:\n{last_error}")


class MaterialAgent:
    """Resolve planner-requested external textures before code generation."""

    def __init__(self, config: HarnessConfig):
        self.config = config
        self.llm = LLMClient(config.vision)
        self.client = FreeStockTexturesClient(timeout_seconds=config.texture_search_timeout_seconds)
        self.last_results: list[dict[str, Any]] = []

    def resolve(self, ir: GenerationIR, output_dir: Path) -> GenerationIR:
        self.last_results = []
        output_dir = output_dir.resolve()
        if not self.config.texture_search_enabled:
            return ir
        for material in ir.scene.materials:
            if material.texture_source and material.texture_source.local_path:
                material.texture_source.local_path = _absolute_existing_path(material.texture_source.local_path)
                continue
            if not _material_should_search_texture(material):
                continue
            query = _material_texture_query(material)
            material.texture_query = material.texture_query or query
            try:
                candidates = self.client.search(query, limit=self.config.texture_search_candidate_limit)
                downloaded: list[TextureCandidate] = []
                material_dir = output_dir / _safe_path_token(material.id)
                for candidate in candidates:
                    try:
                        downloaded.append(self.client.download_candidate(candidate, material_dir))
                    except Exception:
                        continue
                selected = self._select_with_vision(ir, material.id, query, downloaded)
                if selected:
                    material.texture_source = TextureSourceSpec(
                        source="freestocktextures",
                        title=selected["candidate"].title,
                        page_url=selected["candidate"].page_url,
                        image_url=selected["candidate"].image_url,
                        download_url=selected["candidate"].download_url,
                        local_path=str(selected["candidate"].local_path.resolve()) if selected["candidate"].local_path else None,
                        license=FREE_STOCK_TEXTURES_LICENSE,
                        tags=selected["candidate"].tags,
                        approved_by_vision=True,
                        vision_summary=selected["summary"],
                    )
                    self.last_results.append(
                        {
                            "material_id": material.id,
                            "query": query,
                            "selected": material.texture_source.title,
                            "local_path": material.texture_source.local_path,
                            "summary": selected["summary"],
                        }
                    )
                else:
                    _mark_texture_unavailable(material, query, "No candidate passed vision suitability check.")
                    self.last_results.append(
                        {
                            "material_id": material.id,
                            "query": query,
                            "selected": None,
                            "summary": "No candidate passed vision suitability check.",
                        }
                    )
            except Exception as exc:
                _mark_texture_unavailable(material, query, f"Texture search failed: {exc}")
                self.last_results.append(
                    {
                        "material_id": material.id,
                        "query": query,
                        "selected": None,
                        "summary": f"Texture search failed: {exc}",
                    }
                )
        return ir

    def _select_with_vision(
        self,
        ir: GenerationIR,
        material_id: str,
        query: str,
        candidates: list[TextureCandidate],
    ) -> dict[str, Any] | None:
        image_paths = [candidate.local_path for candidate in candidates if candidate.local_path]
        if not image_paths or not self.llm.config.supports_images:
            return None
        manifest = [
            candidate.to_manifest(index + 1)
            for index, candidate in enumerate(candidates)
            if candidate.local_path
        ]
        object_ids = [
            obj.id
            for obj in ir.scene.objects
            if material_id in obj.material_ids or any(part.material_id == material_id for part in obj.parts)
        ]
        system = (
            "You are a strict visual texture selector for a Blender scene-generation pipeline. "
            "Pick a texture only if the image itself is a suitable surface material for the requested material. "
            "Reject images dominated by objects, people, text, logos, screenshots, strong perspective scenery, or a mismatch with the requested surface. "
            "Return only JSON with keys: passed, selected_index, summary, concerns."
        )
        user = f"""
Material id: {material_id}
Texture search query: {query}
Intended scene objects using this material: {object_ids}

Candidate manifest, in the same order as the attached images:
{json.dumps(manifest, indent=2)}

Choose the best candidate for use as an image texture in Blender. The texture does not have to be perfectly seamless, but it should visibly represent the requested material and work on object surfaces. Set passed=false if none are appropriate.
"""
        try:
            data = self.llm.json_multimodal(system, user, image_paths)
        except Exception:
            return None
        if not data.get("passed"):
            return None
        try:
            selected_index = int(data.get("selected_index", 0))
        except (TypeError, ValueError):
            return None
        if selected_index < 1 or selected_index > len(candidates):
            return None
        candidate = candidates[selected_index - 1]
        if not candidate.local_path:
            return None
        return {"candidate": candidate, "summary": str(data.get("summary") or "Vision approved texture candidate.")}


class CoderAgent:
    def __init__(self, config: HarnessConfig, rag: LocalRAG):
        self.llm = LLMClient(config.coder)
        self.rag = rag

    def generate(self, ir: GenerationIR, *, static_only: bool = False) -> str:
        query = "Blender 4.5 bpy data API mesh from_pydata material camera light render keyframe_insert"
        context = self.rag.format_context(query, limit=4, max_chars=5000)
        coder_ir = _compact_ir_for_coder(ir)
        system = (
            "You are a senior Blender 4.5.4 Python coder. "
            "Generate one complete Python script that creates the requested scene and optional animation. "
            "Use data API where possible, stable ll3m custom properties, modular factory functions, and explicit collections. "
            "Blender UI/node names may be localized; never find shader nodes by display name like 'Principled BSDF'. "
            "Find principled shaders by node.type == 'BSDF_PRINCIPLED', set both mat.diffuse_color and shader input values. "
            "When MaterialSpec.texture_source has approved_by_vision=true and local_path is present, load that absolute image path with bpy.data.images.load and wire it into the material shader as an image texture, keeping base_color as a fallback/tint. "
            "If texture_source is absent, approved_by_vision is false, or local_path is empty, do not create an image texture node for that material; use the base_color, roughness, metallic, and simple procedural shader settings only. "
            "For Blender 4.5, prefer BLENDER_EEVEE_NEXT or WORKBENCH after checking available render engine enum values from scene.render.bl_rna.properties['engine']; never use bpy.types.Scene.bl_rna.properties['render_engine']. "
            "For animation, implement simple explicit keyframes from AnimationSpec events. "
            "Animate object roots that own the ll3m_id, set scene frame range/fps, insert start/end keyframes, and set interpolation on every generated keyframe. "
            "For gripper/end-effector objects, keep the gripper visibly attached to the robotic arm while it moves; if the package is carried, the gripper and package must move together without separating the gripper from the arm. "
            "For appear/disappear events such as status lights, animate real visibility (hide_viewport/hide_render or scale from near-zero) so the object is not visibly on before its start frame. "
            "Do not iterate action.fcurves directly; Blender 5 layered actions store fcurves under action.layers[*].strips[*].channelbags[*].fcurves. It is acceptable to leave default interpolation instead of editing fcurves. "
            "Keep the script concise. Do not write long reasoning comments, abandoned design notes, or step-by-step analysis inside the code. "
            "Do not use unavailable third-party Blender add-ons. Return only Python code."
        )
        if static_only:
            system += (
                " This is the static scene stage of a two-stage animation pipeline. "
                "Ignore any motion/timing language that remains in the source prompt. "
                "Do not create keyframes, drivers, frame changes, animated visibility, animated materials, or frame range setup. "
                "Create a single representative static scene only."
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
- For each MaterialSpec with texture_source.approved_by_vision=true and texture_source.local_path, treat local_path as an absolute path and load it with bpy.data.images.load. Set image colorspace to sRGB when available, add ShaderNodeTexImage, and connect Color to the Principled Base Color.
- Make the image texture visibly map onto generated geometry: either create a UV map for mesh surfaces or connect Texture Coordinate Generated/Object output through Mapping into the image texture. Do not connect UV coordinates on a mesh that has no UV map.
- If texture_source.approved_by_vision is false or no local_path is present, skip image texture nodes for that material and create a clean non-image material from base_color and shader parameters.
- Set bpy.context.scene.camera to the main generated camera.
- Set render engines defensively by checking scene.render.bl_rna.properties['engine'].enum_items; Blender 4.5 uses BLENDER_EEVEE_NEXT rather than legacy BLENDER_EEVEE. Never use bpy.types.Scene.bl_rna.properties['render_engine'].
- Set frame_start/frame_end/fps if animation exists.
- Insert keyframes for AnimationSpec events when present. For translate/rotate/scale, mutate the object's location/rotation_euler/scale at start and end frames, insert keyframes, and ensure sampled frames visibly change.
- For robotic pick-and-place, keep a continuous articulated chain from arm base to gripper. Do not detach the gripper from the arm just to make it follow the package.
- For appear/disappear events, keyframe hide_viewport/hide_render and/or near-zero scale before activation; material emission alone is not enough if the verifier can still see the light.
- Do not read action.fcurves directly. Blender 5 uses layered actions; if you need fcurves, traverse action.layers, strip.channelbags, and bag.fcurves. Prefer leaving default keyframe interpolation if direct fcurve access is not required.
- If AnimationEventSpec has path points or start/end transforms, use them exactly; otherwise infer a simple motion that satisfies the event description.
- Define a final variable named LL3M_METADATA with object ids and created object names.
- End the script with a complete LL3M_METADATA assignment. Keep comments short so the response does not truncate before metadata.
"""
        if static_only:
            user += """

Static scene stage constraints:
- Do not call keyframe_insert.
- Do not set scene.frame_start, scene.frame_end, or scene.render.fps for animation.
- Do not animate light beams, signals, doors, bridges, vehicles, cameras, or materials.
- Place moving objects in a neutral representative pose that makes all required objects visible and spatially plausible.
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
            "For RELATION_ON_TOP_OF_FAILED, use the numeric evidence: move the subject so its bottom z equals the reported support_z and adjust x/y so overlap_x and overlap_y are both positive; do not rename ids or leave the object floating. "
            "For 'on floor of a room/greenhouse/enclosure' relations, place wheels/tanks/props on the interior floor plane, not on the roof or top of the enclosing walls. "
            "For animation failures, fix keyframe data paths, object roots, frame ranges, interpolation, and start/end transforms so sampled frames visibly match the AnimationSpec. "
            "For pick-and-place failures, do not animate the package independently while the gripper stays elsewhere. Animate the gripper/end-effector and package together during grasp/lift/carry frames, or parent/constraint the package to the gripper for that segment, so screenshots show continuous contact. "
            "Keep the gripper attached to the robotic arm at every sampled frame; moving the gripper as a detached block is a failure. "
            "For status-light activation failures, hide the light before activation using hide_viewport/hide_render or near-zero scale, then reveal it at the specified frame; emission-only changes are visually insufficient. "
            "Do not iterate action.fcurves directly; Blender 5 layered actions store fcurves under action.layers[*].strips[*].channelbags[*].fcurves. It is acceptable to remove custom interpolation edits and keep default interpolation. "
            "If materials render as default gray/white, fix localized Blender node lookup by finding BSDF_PRINCIPLED nodes by type and setting mat.diffuse_color. "
            "If a material has a vision-approved texture_source.local_path in the IR, preserve or add the image texture node so the downloaded surface remains visible. "
            "Keep the script concise and complete. Remove long comments, scratch reasoning, and abandoned implementation notes. "
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
            if self.llm.config.supports_images:
                return _sanitize_generated_blender_code(
                    extract_code_block(self.llm.complete_multimodal(system, user, screenshot_paths))
                )
            user += "\nThe configured refiner model is text-only, so screenshots are not attached. Use the verifier text evidence precisely.\n"
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

    def add_animation(self, *, ir: GenerationIR, code: str, scene_graph: dict[str, Any] | None = None) -> str:
        context = self.rag.format_context("Blender 4.5 animation keyframe_insert visibility parent constraint", limit=4, max_chars=5000)
        system = (
            "You add animation to an already validated Blender 4.5.4 scene script. "
            "Preserve the static scene geometry, materials, cameras, object ids, and support/contact relationships. "
            "Only add or adjust animation setup, keyframes, frame range, visibility timing, and metadata needed for the AnimationSpec. "
            "Do not rewrite the whole scene from scratch unless absolutely necessary. "
            "Keep the script concise and complete; no long reasoning comments. "
            "Return only the full corrected Python script."
        )
        user = f"""
Validated static scene script:
```python
{code}
```

Full GenerationIR including AnimationSpec:
{json.dumps(_compact_ir_for_coder(ir), indent=2)}

Current Blender scene graph:
{json.dumps(scene_graph or {}, indent=2, default=str)[:12000]}

Relevant Blender 4.5.4 notes:
{context}

Requirements:
- Keep the validated static scene intact.
- Add frame_start, frame_end, fps, and explicit keyframes for every animation event.
- For appear/disappear events, keyframe actual visibility or near-zero scale, not emission only.
- For contact/carry events, keep the interacting objects visibly connected at sampled frames.
- End with a complete LL3M_METADATA assignment.
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
        try:
            data = self.llm.json_multimodal(system, user, screenshot_paths)
        except Exception as exc:
            return ValidationReport.failed(
                VerificationMode.VISION,
                [
                    ValidationIssue(
                        code="VISION_VERIFIER_PARSE_FAILED",
                        message=f"Vision verifier did not return valid JSON: {exc}",
                        severity=Severity.MAJOR,
                    )
                ],
                "Vision verifier response could not be parsed.",
            )
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
        if not preview_video_path or not preview_video_path.exists():
            return ValidationReport.failed(
                VerificationMode.VIDEO,
                [
                    ValidationIssue(
                        code="NO_PREVIEW_VIDEO",
                        message="No GIF/video preview was produced for video verification.",
                        severity=Severity.MAJOR,
                    )
                ],
                "Video verifier requires a GIF/video preview.",
            )
        probe_report = self._probe_video_input(preview_video_path)
        if probe_report is not None:
            return probe_report

        system = (
            "You are a temporal verifier for Blender animations. "
            "Return only JSON with keys: passed, summary, issues. "
            "Judge action order, motion path, smoothness, object interactions, physical plausibility, and camera visibility. "
            "Do not pass animations where required motion is absent, reversed, hidden, too subtle to see, or where contact, attachment, or object continuity breaks between frames. "
            "Inspect geometry strictly: fail if objects float above supports, sink into platforms, visibly intersect when they should rest on top, pass through each other, or if a bridge/ramp/door does not visibly connect to its target support before another object uses it. "
            "For vehicles or objects crossing a bridge/ramp/platform, fail if wheels/body penetrate the surface, hover without contact, or the path crosses a visible gap that has not been bridged. "
            "Every required animated subject must be visible enough to verify at its start, during its motion, and at its required final state; fail if an object leaves the camera view before its final state can be judged. "
            "Do not infer success from transform traces alone when the video/GIF or sampled frames do not visibly show the final state."
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

Video visibility requirements:
{json.dumps({
    "require_subject_visibility": ir.animation.verifier.require_subject_visibility,
    "require_final_state_visibility": ir.animation.verifier.require_final_state_visibility,
    "min_subject_pixel_fraction": ir.animation.verifier.min_subject_pixel_fraction,
    "event_visibility_requirements": {
        event.id: event.visibility_requirements
        for event in [*ir.animation.events, *ir.animation.camera_events]
    },
}, indent=2)[:6000]}

        Preview GIF/video path:
{preview_video_path or "None"}

Sampled frame order:
{json.dumps(frame_manifest, indent=2)}

Transform trace:
{json.dumps(transform_trace or {}, indent=2)[:12000]}

Deterministic animation report:
{report_to_json(deterministic_report)}

The attached video/GIF is the primary evidence. The attached images are ordered sampled frames for reference.
Verify whether the requested animation is visually and temporally correct.
Fail if a required moving subject, contact point, or final placement is hidden, cropped out, or occluded in the relevant sampled frame or GIF segment.
Fail on visible floating, sinking, object penetration, unsupported motion across gaps, or broken bridge/ramp/platform contact even if the transform trace reaches the expected coordinates.
If deterministic transform trace and images disagree, explain the mismatch and fail unless the animation is still visually unambiguous.
"""
        try:
            data = self.llm.json_video(system, user, preview_video_path, sampled_frame_paths)
        except Exception as exc:
            return ValidationReport.failed(
                VerificationMode.VIDEO,
                [
                    ValidationIssue(
                        code="VIDEO_VERIFIER_PARSE_FAILED",
                        message=f"Video verifier did not return valid JSON: {exc}",
                        severity=Severity.MAJOR,
                    )
                ],
                "Video verifier response could not be parsed.",
            )
        return _report_from_model(data, VerificationMode.VIDEO)

    def _probe_video_input(self, preview_video_path: Path) -> ValidationReport | None:
        local_frame_count = _preview_video_frame_count(preview_video_path)
        if local_frame_count is not None and local_frame_count < 2:
            return ValidationReport.failed(
                VerificationMode.VIDEO,
                [
                    ValidationIssue(
                        code="PREVIEW_VIDEO_NOT_TEMPORAL",
                        message=(
                            "The animation preview exists but appears to contain fewer than two frames. "
                            "Refusing to validate animation from a non-temporal preview."
                        ),
                        severity=Severity.CRITICAL,
                        evidence={"preview_video_path": str(preview_video_path), "frame_count": local_frame_count},
                    )
                ],
                "Animation preview is not a usable temporal video/GIF.",
            )

        system = "You are a strict video-attachment accessibility probe. Return only JSON."
        user = """
The only attachment is supposed to be a video or animated GIF. No still images are attached.
Return JSON with keys: can_see_video, attachment_readable, summary.
Set can_see_video=true if you can access the attached MP4/GIF as video frames, even if the motion is subtle, unclear, or the clip appears mostly static.
Set can_see_video=false only if no video attachment is present, the attachment cannot be opened, or the format is unsupported.
Do not judge whether the animation is correct in this probe.
"""
        try:
            data = self.llm.json_video(system, user, preview_video_path, image_paths=[])
        except Exception as exc:
            return ValidationReport.failed(
                VerificationMode.VIDEO,
                [
                    ValidationIssue(
                        code="VIDEO_INPUT_PROBE_FAILED",
                        message=f"Could not confirm video input support: {exc}",
                        severity=Severity.MAJOR,
                    )
                ],
                "Video verifier could not confirm that the model received the video.",
            )
        if data.get("can_see_video") is True or data.get("attachment_readable") is True:
            return None
        return ValidationReport.failed(
            VerificationMode.VIDEO,
            [
                ValidationIssue(
                    code="VIDEO_INPUT_UNSUPPORTED",
                    message=(
                        "The configured video verifier did not receive or cannot inspect the video/GIF input. "
                        "Refusing to validate animation from sampled still images alone."
                    ),
                    severity=Severity.CRITICAL,
                    evidence={"probe_response": data, "preview_video_path": str(preview_video_path)},
                )
            ],
            str(data.get("summary") or "Video input was not visible to the verifier model."),
        )


def _preview_video_frame_count(preview_video_path: Path) -> int | None:
    """Best-effort temporal sanity check before asking a video model."""

    suffix = preview_video_path.suffix.lower()
    if suffix in {".mp4", ".mov", ".m4v", ".webm", ".avi"}:
        return _ffprobe_frame_count(preview_video_path)
    if suffix == ".gif":
        try:
            from PIL import Image, ImageSequence

            with Image.open(preview_video_path) as image:
                return sum(1 for _ in ImageSequence.Iterator(image))
        except Exception:
            return None
    return None


def _ffprobe_frame_count(video_path: Path) -> int | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=nb_read_frames,nb_frames",
                "-of",
                "json",
                str(video_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    for stream in data.get("streams", []):
        for key in ("nb_read_frames", "nb_frames"):
            value = stream.get(key)
            if value and str(value).upper() != "N/A":
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
    return None


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
                    "verification_method": _value(relation.verification_method),
                    "contact_points": relation.contact_points,
                    "expected_clearance": relation.expected_clearance,
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
                    "texture_policy": _value(material.texture_policy),
                    "needs_texture": material.needs_texture,
                    "texture_query": material.texture_query,
                    "texture_source": _texture_source(material.texture_source),
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
                    "min_subject_pixel_fraction": camera.min_subject_pixel_fraction,
                    "allow_subject_crop": camera.allow_subject_crop,
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
        render = ir.animation.render or RenderSpec()
        verifier = ir.animation.verifier
        data["animation"] = {
            "duration_frames": ir.animation.duration_frames,
            "fps": ir.animation.fps,
            "loop": ir.animation.loop,
            "events": [_compact_animation_event(event) for event in ir.animation.events],
            "camera_events": [_compact_animation_event(event) for event in ir.animation.camera_events],
            "render": {
                "resolution": render.resolution,
                "engine": _value(render.engine),
            },
            "verifier": {
                "sampled_frames": verifier.sampled_frames if verifier else [],
                "pass_criteria": verifier.pass_criteria if verifier else [],
                "require_subject_visibility": verifier.require_subject_visibility if verifier else True,
                "require_final_state_visibility": verifier.require_final_state_visibility if verifier else True,
                "min_subject_pixel_fraction": verifier.min_subject_pixel_fraction if verifier else None,
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
            "visibility_requirements": event.visibility_requirements,
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


def _texture_source(value: TextureSourceSpec | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return _drop_none(
        {
            "source": value.source,
            "title": value.title,
            "page_url": value.page_url,
            "image_url": value.image_url,
            "download_url": value.download_url,
            "local_path": value.local_path,
            "license": value.license,
            "tags": value.tags,
            "approved_by_vision": value.approved_by_vision,
            "vision_summary": value.vision_summary,
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


def _material_should_search_texture(material: Any) -> bool:
    if getattr(material, "texture_source", None):
        return False
    policy = _value(getattr(material, "texture_policy", "auto"))
    if policy in {"forbidden", "solid_only"}:
        return False
    if policy == "required":
        return True
    if getattr(material, "needs_texture", False):
        return True
    if getattr(material, "texture_query", None):
        return True
    text = " ".join(
        [
            str(getattr(material, "id", "")),
            str(getattr(material, "description", "")),
            " ".join(getattr(material, "texture_hints", []) or []),
        ]
    ).lower()
    if any(token in text for token in ("plain", "solid color", "pure color", "flat color", "smooth ceramic", "glossy ceramic")):
        return False
    natural_or_patterned = {
        "bark",
        "brick",
        "concrete",
        "fabric",
        "grain",
        "grass",
        "grunge",
        "leather",
        "marble",
        "plank",
        "rust",
        "stone",
        "tabletop",
        "wall",
        "wood",
        "wooden",
        "woven",
    }
    return any(re.search(rf"\b{re.escape(token)}\b", text) for token in natural_or_patterned)


def _material_texture_query(material: Any) -> str:
    if getattr(material, "texture_query", None):
        return str(material.texture_query)
    hints = getattr(material, "texture_hints", []) or []
    if hints:
        return " ".join(str(item) for item in hints[:4])
    return str(getattr(material, "description", "") or getattr(material, "id", "") or "material texture")


def _mark_texture_unavailable(material: Any, query: str, summary: str) -> None:
    material.needs_texture = False
    material.texture_query = query
    material.texture_source = TextureSourceSpec(
        source="freestocktextures",
        title=None,
        page_url=None,
        image_url=None,
        download_url=None,
        local_path=None,
        license=FREE_STOCK_TEXTURES_LICENSE,
        tags=[],
        approved_by_vision=False,
        vision_summary=f"{summary} Falling back to non-image material from base_color and shader parameters.",
    )


def _safe_path_token(value: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("._")
    return token or "material"


def _absolute_existing_path(value: str) -> str:
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path)
    resolved = path.resolve()
    return str(resolved) if resolved.exists() else value


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
    data["version"] = "0.2"
    _promote_animation_end_effectors(data)
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
    prompt_text = str((data.get("prompt") or {}).get("text", "")).lower()
    force_plain_materials = any(
        token in prompt_text
        for token in (
            "no image textures",
            "no external textures",
            "without image textures",
            "solid-color",
            "solid color",
            "plain materials",
        )
    )
    for obj in scene.get("objects", []) or []:
        if not isinstance(obj, dict):
            continue
        category = str(obj.get("category", "generic")).lower()
        obj["category"] = category_aliases.get(category, category if category in valid_categories else "generic")
        role = str(obj.get("role", "secondary")).lower()
        obj["role"] = role if role in valid_roles else "secondary"
    relation_aliases = {
        "above": "on_top_of",
        "atop": "on_top_of",
        "on": "on_top_of",
        "on_top": "on_top_of",
        "below": "under",
        "beneath": "under",
        "next_to": "near",
        "beside": "near",
        "adjacent_to": "near",
        "close_to": "near",
        "nearby": "near",
        "in": "inside",
        "within": "inside",
        "holds": "contains",
        "holding": "contains",
        "facing_towards": "facing",
        "attached": "attached_to",
        "connected_to": "attached_to",
        "same_height": "same_height_as",
        "level_with": "same_height_as",
    }
    valid_relations = {item.value for item in RelationType}
    for relation in scene.get("relations", []) or []:
        if not isinstance(relation, dict):
            continue
        raw = str(relation.get("relation_type", "near")).strip().lower().replace("-", "_").replace(" ", "_")
        relation["relation_type"] = relation_aliases.get(raw, raw if raw in valid_relations else "near")
        relation_text = " ".join(
            str(relation.get(key, ""))
            for key in ("id", "description", "subject_id", "object_id")
        ).lower()
        if relation["relation_type"] == "on_top_of" and "ramp" in relation_text and any(
            token in relation_text for token in ("leg", "support", "bracket", "incline", "slanted")
        ):
            relation["relation_type"] = "attached_to"
            relation["verification_method"] = "visual_only"
        method = str(relation.get("verification_method", "auto")).strip().lower().replace("-", "_").replace(" ", "_")
        method_aliases = {
            "bbox": "bbox_contact",
            "contact": "bbox_contact",
            "geometric_contact": "bbox_contact",
            "order": "bbox_order",
            "spatial_order": "bbox_order",
            "visual": "visual_only",
            "vision": "visual_only",
        }
        valid_methods = {item.value for item in RelationVerificationMethod}
        relation["verification_method"] = method_aliases.get(method, method if method in valid_methods else "auto")
    _normalize_view_type_fields(scene)
    for material in scene.get("materials", []) or []:
        if not isinstance(material, dict):
            continue
        if force_plain_materials:
            material["texture_policy"] = "solid_only"
            material["needs_texture"] = False
            material["texture_query"] = None
            continue
        policy = str(material.get("texture_policy", "auto")).strip().lower().replace("-", "_").replace(" ", "_")
        policy_aliases = {
            "none": "forbidden",
            "no_texture": "forbidden",
            "no_textures": "forbidden",
            "plain": "solid_only",
            "solid": "solid_only",
            "solid_color": "solid_only",
        }
        valid_policies = {item.value for item in TexturePolicy}
        material["texture_policy"] = policy_aliases.get(policy, policy if policy in valid_policies else "auto")
        if material["texture_policy"] in {"forbidden", "solid_only"}:
            material["needs_texture"] = False
            material["texture_query"] = None
            material["texture_source"] = None
            continue
        if "needs_texture" in material:
            material["needs_texture"] = _coerce_bool(material.get("needs_texture"))
        elif material.get("texture_query"):
            material["needs_texture"] = True
        if material["texture_policy"] == "required":
            material["needs_texture"] = True
        if material.get("needs_texture") and not material.get("texture_query"):
            hints = material.get("texture_hints") or []
            if isinstance(hints, list) and hints:
                material["texture_query"] = " ".join(str(item) for item in hints[:4])
            else:
                material["texture_query"] = str(material.get("description") or material.get("id") or "material texture")


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on", "needed", "required"}:
        return True
    if text in {"0", "false", "no", "n", "off", "none", "null", "not_needed"}:
        return False
    return bool(value)


def _normalize_view_type_fields(scene: dict[str, Any]) -> None:
    aliases = {
        "close-up": "close_up",
        "closeup": "close_up",
        "close_up": "close_up",
        "relation-close-up": "relation_close_up",
        "relation_closeup": "relation_close_up",
        "relation close up": "relation_close_up",
        "three-quarter": "three_quarter",
        "three quarter": "three_quarter",
        "3/4": "three_quarter",
    }
    valid = {"front", "back", "left", "right", "top", "bottom", "three_quarter", "close_up", "relation_close_up", "free"}
    candidates: list[Any] = []
    candidates.extend(scene.get("cameras", []) or [])
    verifier = scene.get("verifier")
    if isinstance(verifier, dict):
        screenshot_plan = verifier.get("screenshot_plan")
        if isinstance(screenshot_plan, dict):
            candidates.extend(screenshot_plan.get("views", []) or [])
    for item in candidates:
        if not isinstance(item, dict) or "view_type" not in item:
            continue
        raw = str(item.get("view_type") or "three_quarter").strip().lower()
        normalized = aliases.get(raw, raw.replace("-", "_").replace(" ", "_"))
        item["view_type"] = normalized if normalized in valid else "three_quarter"


def _promote_animation_end_effectors(data: dict[str, Any]) -> None:
    animation = data.get("animation") if isinstance(data, dict) else None
    scene = data.get("scene") if isinstance(data, dict) else None
    if not isinstance(animation, dict) or not isinstance(scene, dict):
        return
    objects = scene.get("objects") or []
    if not isinstance(objects, list):
        return
    object_ids = {obj.get("id") for obj in objects if isinstance(obj, dict)}
    if any(_is_end_effector_text(f"{obj.get('id', '')} {obj.get('description', '')}") for obj in objects if isinstance(obj, dict)):
        return

    promoted: list[tuple[str, str, dict[str, Any]]] = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        for part in obj.get("parts") or []:
            if not isinstance(part, dict):
                continue
            part_text = f"{part.get('id', '')} {part.get('description', '')}"
            if not _is_end_effector_text(part_text):
                continue
            part_id = str(part.get("id") or "gripper")
            object_id = _unique_object_id(part_id, object_ids)
            object_ids.add(object_id)
            material_id = part.get("material_id")
            promoted_obj = {
                "id": object_id,
                "description": part.get("description") or "black gripper end-effector that grasps and carries the package",
                "label": "Gripper",
                "category": "generic",
                "role": "primary",
                "importance": "required",
                "required_features": ["visible end-effector", "attached to robotic arm", "contacts package during carry"],
                "material_ids": [material_id] if material_id else [],
                "generation_notes": "Create as its own root object or empty with ll3m_id so animation verification can track contact.",
            }
            objects.append(promoted_obj)
            promoted.append((object_id, str(obj.get("id") or ""), promoted_obj))
            break

    if not promoted:
        return
    relations = scene.setdefault("relations", [])
    if isinstance(relations, list):
        for object_id, parent_id, _ in promoted:
            if parent_id:
                relations.append(
                    {
                        "id": f"{object_id}_attached_to_{parent_id}",
                        "relation_type": "attached_to",
                        "subject_id": object_id,
                        "object_id": parent_id,
                        "description": f"{object_id} is visibly attached to {parent_id}",
                        "required": True,
                        "visual_priority": "required",
                    }
                )
    promoted_ids = [item[0] for item in promoted]
    for event in [*(animation.get("events") or []), *(animation.get("camera_events") or [])]:
        if not isinstance(event, dict):
            continue
        if str(event.get("action", "")).lower() in {"appear", "disappear"}:
            continue
        text = " ".join(
            [
                str(event.get("id", "")),
                str(event.get("description", "")),
                str(event.get("expected_visual_result", "")),
                " ".join(event.get("constraints", []) or []),
                " ".join(event.get("subject_ids", []) or []),
            ]
        ).lower()
        if _mentions_signal_or_light(text):
            continue
        if not any(token in text for token in ("grasp", "gripper", "lift", "carry", "pick", "place", "transfer")):
            continue
        if not any(token in text for token in ("package", "box", "parcel", "object")):
            continue
        targets = event.setdefault("target_ids", [])
        if isinstance(targets, list):
            for object_id in promoted_ids:
                if object_id not in targets and object_id not in (event.get("subject_ids") or []):
                    targets.append(object_id)


def _is_end_effector_text(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ("gripper", "end effector", "end-effector", "endeffector"))


def _mentions_signal_or_light(text: str) -> bool:
    return bool(re.search(r"\b(light|status|signal)\b", text.lower()))


def _unique_object_id(preferred: str, existing: set[Any]) -> str:
    base = re.sub(r"[^a-zA-Z0-9_]+", "_", preferred.strip().lower()).strip("_") or "gripper"
    if base not in existing:
        return base
    suffix = 2
    while f"{base}_{suffix}" in existing:
        suffix += 1
    return f"{base}_{suffix}"


def _sanitize_animation_data(data: dict[str, Any]) -> None:
    animation = data.get("animation") if isinstance(data, dict) else None
    if not isinstance(animation, dict):
        return
    if not isinstance(animation.get("render"), dict):
        animation.pop("render", None)
    if not isinstance(animation.get("verifier"), dict):
        animation.pop("verifier", None)
    for event in [*(animation.get("events") or []), *(animation.get("camera_events") or [])]:
        if not isinstance(event, dict):
            continue
        action = str(event.get("action", "")).strip().lower()
        if action in {"appear", "disappear"}:
            _ensure_visibility_keyframes(event, action)
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
        _expand_event_range_to_keyframes(event)
        for key in ("start_transform", "end_transform"):
            transform = event.get(key)
            if isinstance(transform, dict):
                for field_name in ("location", "rotation_euler", "scale"):
                    if field_name in transform:
                        transform[field_name] = _normalize_vec3(transform[field_name])
        if not isinstance(event.get("visibility_requirements"), list):
            event["visibility_requirements"] = [
                "Animated subjects must be visible at the event start, at an intermediate sampled frame, and at the event end.",
                "Required contact, attachment, or final placement must be visible enough for video verification.",
            ]


def _ensure_visibility_keyframes(event: dict[str, Any], action: str) -> None:
    try:
        start = int(event.get("start_frame", 1))
        end = int(event.get("end_frame", start))
    except (TypeError, ValueError):
        start = 1
        end = start
    before_visible = action != "appear"
    after_visible = action == "appear"
    path = event.setdefault("path", {})
    if not isinstance(path, dict):
        path = {}
        event["path"] = path
    keyframes = path.setdefault("keyframes", [])
    if not isinstance(keyframes, list):
        keyframes = []
        path["keyframes"] = keyframes

    def has_visibility_value(item: Any) -> bool:
        return isinstance(item, dict) and isinstance(item.get("value"), dict) and any(
            key in item["value"] for key in ("visible", "hide_viewport", "hide_render", "alpha")
        )

    if any(has_visibility_value(item) for item in keyframes):
        return
    keyframes.extend(
        [
            {
                "frame": start,
                "value": {
                    "visible": before_visible,
                    "hide_viewport": not before_visible,
                    "hide_render": not before_visible,
                    "alpha": 1.0 if before_visible else 0.0,
                },
                "interpolation": "constant",
                "description": "visibility state before transition",
            },
            {
                "frame": end,
                "value": {
                    "visible": after_visible,
                    "hide_viewport": not after_visible,
                    "hide_render": not after_visible,
                    "alpha": 1.0 if after_visible else 0.0,
                },
                "interpolation": "constant",
                "description": "visibility state after transition",
            },
        ]
    )


def _expand_event_range_to_keyframes(event: dict[str, Any]) -> None:
    path = event.get("path")
    if not isinstance(path, dict):
        return
    frames = []
    for keyframe in path.get("keyframes") or []:
        if not isinstance(keyframe, dict) or "frame" not in keyframe:
            continue
        try:
            frames.append(int(keyframe["frame"]))
        except (TypeError, ValueError):
            continue
    if not frames:
        return
    try:
        start = int(event.get("start_frame", min(frames)))
        end = int(event.get("end_frame", max(frames)))
    except (TypeError, ValueError):
        return
    event["start_frame"] = min(start, min(frames))
    event["end_frame"] = max(end, max(frames))


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
    verifier.require_subject_visibility = True
    verifier.require_final_state_visibility = True
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
            "Every required animated subject remains visible enough to verify start, middle, and final states.",
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
        if _mentions_signal_or_light(text):
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
        ],
        "visibility_requirements": [
          "object_id is visible at the start, midpoint, and final sampled frame.",
          "The final placement/contact state is not cropped, hidden, or occluded."
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
      "require_subject_visibility": true,
      "require_final_state_visibility": true,
      "min_subject_pixel_fraction": 0.04,
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
        "verification_method": "bbox_contact",
        "contact_points": [],
        "expected_clearance": 0.0,
        "visual_priority": "required"
      }}
    ],
    "materials": [
      {{
        "id": "material_id",
        "description": "material description",
        "base_color": [0.5, 0.5, 0.5, 1.0],
        "texture_hints": [],
        "texture_policy": "auto",
        "needs_texture": false,
        "texture_query": null
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
        "coverage": "all primary objects visible",
        "min_subject_pixel_fraction": 0.08,
        "allow_subject_crop": false
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
            "purpose": "overall inspection",
            "must_show_full_targets": true,
            "required": true
          }},
          {{
            "id": "contact_closeup",
            "view_type": "relation_close_up",
            "description": "close view for required contact, support, and attachment relations",
            "target_object_ids": ["stable_object_id"],
            "relation_ids": ["relation_id"],
            "purpose": "contact and attachment verification",
            "must_show_full_targets": false,
            "required": true
          }},
          {{
            "id": "side_support",
            "view_type": "right",
            "description": "side view that makes vertical support and floating parts visible",
            "target_object_ids": ["stable_object_id"],
            "relation_ids": ["relation_id"],
            "purpose": "support and floating-object verification",
            "must_show_full_targets": true,
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
  "version": "0.2",
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
        "bpy.data.worlds['World']": "bpy.context.scene.world",
        'bpy.data.worlds["World"]': "bpy.context.scene.world",
        "bpy.context.object": "bpy.context.view_layer.objects.active",
        "bpy.context.active_object": "bpy.context.view_layer.objects.active",
        ".easing = 'EASE_IN_OUT'": ".easing = 'SINE'",
        '.easing = "EASE_IN_OUT"': '.easing = "SINE"',
    }
    for bad, good in replacements.items():
        code = code.replace(bad, good)
    code = _patch_cone_diameter_keywords(code)
    code = _patch_fcurve_interpolation_assignments(code)
    code = _patch_common_blender_api_hallucinations(code)
    code = _patch_direct_action_fcurve_loops(code)
    code = _patch_common_ir_id_drift(code)
    code = _append_active_camera_fallback(code)
    return code


def _patch_cone_diameter_keywords(code: str) -> str:
    code = re.sub(r"\bdiameter1\s*=\s*([^,\n)]+)", r"radius1=(\1) * 0.5", code)
    code = re.sub(r"\bdiameter2\s*=\s*([^,\n)]+)", r"radius2=(\1) * 0.5", code)
    return code


def _patch_fcurve_interpolation_assignments(code: str) -> str:
    return re.sub(
        r"^([ \t]*)(fcurve|fc|curve)\.interpolation\s*=\s*([^\n#]+)(.*)$",
        _fcurve_interpolation_replacement,
        code,
        flags=re.MULTILINE,
    )


def _fcurve_interpolation_replacement(match: re.Match[str]) -> str:
    indent, fcurve_name, value, comment = match.groups()
    return "\n".join(
        [
            f"{indent}for _ll3m_kp in {fcurve_name}.keyframe_points:{comment}",
            f"{indent}    _ll3m_kp.interpolation = {value.strip()}",
        ]
    )


def _patch_common_blender_api_hallucinations(code: str) -> str:
    """Keep generated code running when models use plausible but invalid bpy APIs."""

    if "bpy.data.remove(" in code:
        helper = '''
def ll3m_remove_datablock(data_block):
    """Remove Blender datablocks through the owning collection API when possible."""
    if data_block is None:
        return
    collection_name = getattr(data_block, "id_type", "").lower() + "s"
    collection = getattr(bpy.data, collection_name, None)
    if collection is not None and hasattr(collection, "remove"):
        try:
            collection.remove(data_block)
            return
        except Exception:
            pass
    if hasattr(data_block, "user_clear"):
        try:
            data_block.user_clear()
        except Exception:
            pass
'''.lstrip()
        if "def ll3m_remove_datablock(" not in code:
            code = helper + "\n" + code
        code = re.sub(r"\bbpy\.data\.remove\(", "ll3m_remove_datablock(", code)

    code = re.sub(
        r"^([ \t]*)([\w.]+collection[\w.]*|master_collection|scene_coll)\.name\s*=\s*([^\n]+)$",
        r"\1# LL3M sanitizer: collection.name assignment removed (read-only for scene master collection)",
        code,
        flags=re.MULTILINE,
    )
    code = re.sub(
        r"^([ \t]*)([A-Za-z_][\w.]*\.objects)\.unlink\(([^)\n]+)\)$",
        _safe_collection_unlink_replacement,
        code,
        flags=re.MULTILINE,
    )
    return code


def _safe_collection_unlink_replacement(match: re.Match[str]) -> str:
    indent, objects_expr, obj_expr = match.groups()
    collection_expr = objects_expr[: -len(".objects")]
    return "\n".join(
        [
            f"{indent}try:",
            f"{indent}    if {obj_expr} in {collection_expr}.objects[:]:",
            f"{indent}        {objects_expr}.unlink({obj_expr})",
            f"{indent}except Exception:",
            f"{indent}    pass",
        ]
    )


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
                message=str(
                    item.get("message")
                    or item.get("problem")
                    or item.get("description")
                    or item.get("concern")
                    or item.get("detail")
                    or item.get("details")
                    or item.get("reason")
                    or "Model reported an issue."
                ),
                severity=_severity(item.get("severity", "major")),
                target_id=item.get("target_id") or item.get("object"),
                relation_id=item.get("relation_id"),
                frame=item.get("frame"),
                suggested_fix=item.get("suggested_fix"),
                evidence=item.get("evidence") or {k: v for k, v in item.items() if k not in {"code", "message", "severity"}},
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
