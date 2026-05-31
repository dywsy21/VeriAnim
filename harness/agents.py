"""Agent implementations for planning, coding, refinement, and model verifiers."""

from __future__ import annotations

import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tokenize
from typing import Any, Callable

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


LL3M_UTILS_API_GUIDE = """
Available `blender.ll3m_utils` API. Import it exactly as:
`from blender import ll3m_utils as ll3m`

Use only these `ll3m.*` helper names. If a helper you want is not listed here,
use Blender's native `bpy` data API instead of inventing a new `ll3m` function.

Scene and collections:
- `scene = ll3m.clear_scene()`
- `collection = ll3m.create_collection(name, parent=None)`
- `collection = ll3m.ensure_collection(name, parent=None)`
- `collection = ll3m.get_or_create_collection(name, parent=None)`
- `obj = ll3m.link_object(obj, collection=None)`
- `obj = ll3m.link_to_collection(obj, collection=None)`
- `obj = ll3m.set_ll3m_properties(obj, ll3m_id=None, ll3m_part=None, ll3m_role=None)`

Materials:
- `mat = ll3m.make_material(name, base_color=None, metallic=None, roughness=None, alpha=None, texture_path=None)`
- `mat = ll3m.make_material(spec_dict)` where `spec_dict` may contain `id`, `base_color`, `metallic`, `roughness`, `alpha`, and `texture_source.local_path`
- `mat = ll3m.create_material(...)` is an alias for `make_material`
- `node = ll3m.find_node_by_type(node_tree, node_type)`

Objects and mesh primitives:
- `obj = ll3m.create_mesh_object(name, vertices, faces, collection=None, material=None, location=(0,0,0), rotation=(0,0,0), scale=(1,1,1), ll3m_id=None, ll3m_part=None, ll3m_role=None)`
- `obj = ll3m.add_cube(name, size=1.0, collection=None, material=None, location=(0,0,0), rotation=(0,0,0), ll3m_id=None, ll3m_part=None, ll3m_role=None)`
- `obj = ll3m.create_box(name, size=1.0, ...)`
- `obj = ll3m.make_box(name, size=1.0, ...)`
- `obj = ll3m.add_plane(name, size=1.0, collection=None, material=None, location=(0,0,0), ll3m_id=None, ll3m_part=None, ll3m_role=None)`
- `obj = ll3m.add_cylinder(name, radius=0.5, depth=1.0, vertices_count=32, collection=None, material=None, location=(0,0,0), rotation=(0,0,0), ll3m_id=None, ll3m_part=None, ll3m_role=None)`
- `obj = ll3m.add_uv_sphere(name, radius=0.5, segments=32, rings=16, collection=None, material=None, location=(0,0,0), ll3m_id=None, ll3m_part=None, ll3m_role=None)`

Primitive coordinate rules:
- `add_cube`, `create_box`, `make_box`, `add_cylinder`, and `add_uv_sphere`
  create geometry centered on `obj.location`; `location` is the center/origin,
  not the bottom contact point.
- `add_plane` creates a flat XY plane at `location.z`; for a floor plane at
  `z=0`, its top/support height is `0`.
- To place a box/cylinder/sphere on a horizontal support, set
  `obj.location.z = support_top_z + object_height / 2`; never set a centered
  primitive's `location.z` to `support_top_z` and claim its bottom is there.
- After changing `obj.scale`, use the resulting world bbox or known scaled
  height for support alignment; do not pass unsupported `scale=` to `add_cube`
  because this helper does not accept that keyword.

Cameras, lights, and rendering:
- `camera = ll3m.add_camera(name="camera_main", location=(3,-4,2.5), look_at_target=(0,0,0), lens=35, collection=None, make_active=True)`
- `camera = ll3m.create_camera(name, location=..., look_at=..., lens=35, collection=None, make_active=True)`
- `camera = ll3m.make_camera(name, location=..., look_at=..., lens=35, collection=None, make_active=True)`
- `obj = ll3m.look_at(obj, target)`
- `light = ll3m.add_light(name, light_type="AREA", location=(0,0,4), rotation=(0,0,0), energy=500, size=None, color=None, collection=None)`
- `light = ll3m.create_light(name, light_type="AREA", ...)`
- `light = ll3m.make_light(name, light_type="AREA", ...)`
- `light = ll3m.create_area_light(name, location=(0,0,4), rotation=(0,0,0), energy=500, size=None, color=None, collection=None)`
- `engine = ll3m.configure_render(scene, width=None, height=None, fps=None, engine="workbench", transparent_background=None)`
- `engine = ll3m.set_render_engine(scene, engine="workbench")`
""".strip()


def _with_ll3m_utils_api(system: str) -> str:
    return f"{system}\n\nll3m_utils API contract:\n{LL3M_UTILS_API_GUIDE}"


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
            "If you include pipeline stages, `verifier_modes` must use only these exact enum values: deterministic, vision, video, human. Never use visual; use vision instead. "
            "Keep the IR concise and executable: use at most 7 scene objects, 12 relations, 5 screenshot views, 3 animation events, 8 visual questions, and 8 pass criteria unless the user explicitly asks for more. "
            "Prefer compact descriptions and omit optional features that are not needed for verification. "
            "For each MaterialSpec decide whether an external image texture is needed. Set needs_texture=true and texture_query for natural, patterned, grainy, irregular, or surface-specific materials such as wood grain, stone, concrete, rusted metal, bark, fabric, leather, brick, grass, tabletop planks, and walls. Set needs_texture=false for intentionally plain or solid surfaces such as a pure-color mug, simple plastic toy, flat painted part, signal light, or clean ceramic. "
            "Scope no-texture instructions only to the named object or material. For example, 'no image textures on the statue' forbids statue/bronze textures but does not forbid a stone pedestal texture; 'solid-color plastic ball' forbids only the plastic ball texture and does not forbid grass. "
            "Plan at least three complementary screenshot views: an overall three-quarter view, a relation/contact close-up, and a side or top view that exposes support/contact. "
            "Add visual pass criteria that require no floating, detached, or misaligned parts unless explicitly requested. "
            "For every required object, include a collision proxy unless it is purely visual: use bbox for box-like props, sphere/capsule for round or elongated props, compound for multi-part supports, and role support/passive/kinematic/active/carried as appropriate. "
            "When animation is requested, include AnimationSpec and video verifier settings. "
            "Animation events must be structurally verifiable: use translate, rotate, scale, follow_path, appear, disappear, camera_move, or camera_orbit; include start_transform, at least one intermediate path.keyframe or path point, end_transform, sampled frames covering start/middle/end, temporal questions, and pass criteria. "
            "For every required animation event, include visibility_requirements that say which subjects, contact points, and final placements must remain visible in the GIF and sampled frames. "
            "For moving objects that push, carry, rest on, slide on, land in, pass near, or must not penetrate another object, add contact_constraints with frame windows: nonpenetration for forbidden intersections, support for resting on surfaces, touching/attachment for connectors, and carry_contact for carried objects. "
            "For bridge, deck, platform, floor, shelf, table, and ramp crossings, make transforms physically solvable: specify object/support dimensions when possible, set root-location z values as support top plus the moving subject half height, and keep support contact windows aligned with the frames where x/y footprints actually overlap. "
            "For bridge/platform crossings, split the motion into approach-on-ground, transition/up-ramp, on-support, transition/down-ramp, and exit-on-ground phases when needed. A low bridge with vertical sides is not physically solvable as one straight line from ground to deck through the deck volume; add ramps/approach slabs or put the first support-contact keyframe fully on top of the deck. "
            "For bridge crossings, start and end positions should be fully outside the bridge/deck footprint unless the object is intentionally on the bridge at those frames: place the moving root at least subject_half_width plus a small margin beyond the deck min/max x (or y) bound, not exactly on the deck edge. Otherwise the nonpenetration audit will correctly report an overlap. "
            "For deterministic bridge keyframes, use an outside-then-lift template when no ramp is explicitly modeled: ground approach at x <= deck_min - subject_half_extent - margin, lift to deck height at the same outside x, enter the deck horizontally at deck height, cross, exit horizontally to x >= deck_max + subject_half_extent + margin at deck height, then lower to ground at that same outside x. This avoids interpolating through the deck side wall. "
            "Bridge legs, pillars, rail posts, and decorative supports must leave the travel lane clear. Put collision-enabled supports laterally outside the moving object's y footprint, under side edges or corners of the deck, or mark purely decorative supports collision.enabled=false. Do not place a support pillar at the same centerline y as a car path unless the car is routed around it. "
            "When using the outside-then-lift bridge template, deck support contact_constraints must start only at the first frame where the moving object's x/y footprint already overlaps the deck at deck height, and end at the last frame before the object horizontally exits that footprint. Do not include outside lift/lower frames in a deck support window. "
            "For nonpenetration contact_constraints, choose frame windows where the pair can actually be non-overlapping or correctly separated. Do not create a full-duration nonpenetration window for a bridge/deck crossing if the start/end pose is under the deck edge or if interpolation passes through the deck side; instead fix the path geometry or narrow support/contact windows to the true crossing frames. "
            "For animation final states such as 'stops near', 'ends on', or 'lands in', put the final placement in the AnimationSpec end_transform, sampled frames, pass criteria, and contact_constraints; do not add a static initial-scene relation unless it must already be true before motion starts. "
            "For signal or material color changes, do not use one vague color-change event. Model separate colored visible parts such as red_light and green_light, then use disappear/appear events with explicit path.keyframes value.visible or value.alpha. "
            "For pick, grasp, carry, lift, or place animations, model the gripper/end-effector as its own ObjectSpec when possible, and put that object id in target_ids for the package lift/transfer events so contact continuity can be verified. "
            "Use on_top_of with bbox_contact only for horizontal stacking: the subject footprint must overlap the support in x/y and the subject bottom must touch the support top. "
            "For elevated bridges/platforms with legs or supports, do not relate the raised deck directly on_top_of the ground. Relate legs/supports on_top_of the ground, and relate the deck to those supports with on_top_of or attached_to; the moving object may be on_top_of the deck only during the deck crossing window. "
            "For floors inside rooms, boxes, greenhouses, or enclosures, relate objects to the interior floor surface, not to the enclosing shell or roof. "
            "For slanted ramps, inclined planes, hinges, brackets, and structural supports, use attached_to or touching relations rather than on_top_of unless the surfaces are horizontal and directly stacked. "
            "For an object sliding on a ramp, model the ramp contact as touching or visual_only plus event-scoped support/nonpenetration contact_constraints over the sampled frame window; do not rely on a static on_top_of relation for the slanted plane. "
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
            "If you include pipeline stages, `verifier_modes` must use only these exact enum values: deterministic, vision, video, human. Never use visual; use vision instead. "
            "Keep the revised IR concise and executable: at most 7 scene objects, 12 relations, 5 screenshot views, 3 animation events, 8 visual questions, and 8 pass criteria unless the user explicitly asks for more. "
            "For each MaterialSpec decide whether an external image texture is needed. Use needs_texture=true and texture_query only for natural, patterned, grainy, irregular, or surface-specific materials; keep needs_texture=false for intentionally plain or solid-color surfaces. "
            "Scope no-texture instructions only to the named object or material. A no-image or solid-color instruction for one object must not disable texture search for other natural materials in the scene. "
            "Animation events must stay structurally verifiable: include required start/end transforms, at least one intermediate keyframe or path point, sampled start/middle/end frames, temporal questions, and pass criteria. "
            "Preserve or add collision proxies and contact_constraints when animation involves support, pushing, carrying, placement, or collision avoidance. "
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
notes, duplicate questions, and redundant pass criteria. In stages.verifier_modes,
use `vision`, not `visual`.
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
            except Exception as exc:
                _mark_texture_unavailable(material, query, f"Texture search failed: {exc}")
                self.last_results.append(
                    _texture_search_result_record(
                        material_id=material.id,
                        query=query,
                        candidates=[],
                        downloaded=[],
                        status="search_failed",
                        summary=f"Texture search failed: {exc}",
                    )
                )
                continue

            downloaded: list[TextureCandidate] = []
            download_errors: list[dict[str, Any]] = []
            material_dir = output_dir / _safe_path_token(material.id)
            for index, candidate in enumerate(candidates):
                try:
                    downloaded_candidate = self.client.download_candidate(candidate, material_dir)
                    candidates[index] = downloaded_candidate
                    downloaded.append(downloaded_candidate)
                except Exception as exc:
                    download_errors.append(
                        {
                            "index": index + 1,
                            "title": candidate.title,
                            "page_url": candidate.page_url,
                            "error": str(exc),
                        }
                    )

            if not candidates:
                summary = "Texture search returned no candidates."
                _mark_texture_unavailable(material, query, summary)
                self.last_results.append(
                    _texture_search_result_record(
                        material_id=material.id,
                        query=query,
                        candidates=candidates,
                        downloaded=downloaded,
                        status="search_no_candidates",
                        summary=summary,
                        download_errors=download_errors,
                    )
                )
                continue

            if not downloaded:
                summary = "Texture search returned candidates, but no candidate image downloaded."
                _mark_texture_unavailable(material, query, summary)
                self.last_results.append(
                    _texture_search_result_record(
                        material_id=material.id,
                        query=query,
                        candidates=candidates,
                        downloaded=downloaded,
                        status="download_failure",
                        summary=summary,
                        download_errors=download_errors,
                    )
                )
                continue

            selection = self._select_with_vision(ir, material.id, query, downloaded)
            selected_candidate = selection.get("candidate")
            if selection.get("status") == "selected" and selected_candidate:
                material.texture_source = TextureSourceSpec(
                    source="freestocktextures",
                    title=selected_candidate.title,
                    page_url=selected_candidate.page_url,
                    image_url=selected_candidate.image_url,
                    download_url=selected_candidate.download_url,
                    local_path=str(selected_candidate.local_path.resolve()) if selected_candidate.local_path else None,
                    license=FREE_STOCK_TEXTURES_LICENSE,
                    tags=selected_candidate.tags,
                    approved_by_vision=True,
                    vision_summary=selection["summary"],
                )
                self.last_results.append(
                    _texture_search_result_record(
                        material_id=material.id,
                        query=query,
                        candidates=candidates,
                        downloaded=downloaded,
                        status="selected",
                        summary=selection["summary"],
                        selected=selected_candidate,
                        download_errors=download_errors,
                    )
                )
            else:
                summary = str(selection.get("summary") or "No candidate passed vision suitability check.")
                _mark_texture_unavailable(material, query, summary)
                self.last_results.append(
                    _texture_search_result_record(
                        material_id=material.id,
                        query=query,
                        candidates=candidates,
                        downloaded=downloaded,
                        status=str(selection.get("status") or "vision_reject_all"),
                        summary=summary,
                        download_errors=download_errors,
                    )
                )
        return ir

    def _select_with_vision(
        self,
        ir: GenerationIR,
        material_id: str,
        query: str,
        candidates: list[TextureCandidate],
    ) -> dict[str, Any]:
        selectable = [candidate for candidate in candidates if candidate.local_path]
        image_paths = [candidate.local_path for candidate in selectable if candidate.local_path]
        if not image_paths:
            return {
                "status": "download_failure",
                "summary": "No downloaded candidate images were available for vision texture selection.",
            }
        if not self.llm.config.supports_images:
            return {
                "status": "vision_blocked",
                "summary": "Configured vision selector has supports_images=false, so image suitability could not be checked.",
            }
        manifest = [
            candidate.to_manifest(index + 1)
            for index, candidate in enumerate(selectable)
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
        except Exception as exc:
            if _is_multimodal_input_unsupported(exc):
                summary = f"Vision texture selector model does not accept image input: {exc}"
            else:
                summary = f"Vision texture selector failed: {exc}"
            return {"status": "vision_blocked", "summary": summary}
        if not data.get("passed"):
            return {
                "status": "vision_reject_all",
                "summary": str(data.get("summary") or "Vision did not approve any texture candidate."),
            }
        try:
            selected_index = int(data.get("selected_index", 0))
        except (TypeError, ValueError):
            return {
                "status": "vision_reject_all",
                "summary": "Vision response did not identify a valid downloaded candidate.",
            }
        if selected_index < 1 or selected_index > len(selectable):
            return {
                "status": "vision_reject_all",
                "summary": "Vision response selected an out-of-range candidate index.",
            }
        candidate = selectable[selected_index - 1]
        if not candidate.local_path:
            return {
                "status": "vision_reject_all",
                "summary": "Vision selected a candidate without a downloaded local image.",
            }
        return {
            "status": "selected",
            "candidate": candidate,
            "summary": str(data.get("summary") or "Vision approved texture candidate."),
        }


class CoderAgent:
    def __init__(self, config: HarnessConfig, rag: LocalRAG):
        self.llm = LLMClient(config.coder)
        self.rag = rag

    def generate(self, ir: GenerationIR, *, static_only: bool = False) -> str:
        query = "Blender 4.5 bpy data API mesh from_pydata material camera light render workbench ll3m_utils keyframe_insert"
        context = self.rag.format_context(query, limit=4, max_chars=5000)
        coder_ir = _compact_ir_for_coder(ir)
        system = (
            "You are a senior Blender 4.5.4 Python coder. "
            "Generate one complete Python script that creates the requested scene and optional animation. "
            "Use data API where possible, stable ll3m custom properties, modular factory functions, and explicit collections. "
            "Prefer importing `from blender import ll3m_utils as ll3m` and using its common helpers for clearing scenes, collections, render setup, materials, cameras, lights, and primitive mesh objects. "
            "Use only the ll3m_utils helper functions listed in the API contract below; do not invent helper names. "
            "Blender UI/node names may be localized; never find shader nodes by display name like 'Principled BSDF'. "
            "Find principled shaders by node.type == 'BSDF_PRINCIPLED', set both mat.diffuse_color and shader input values. "
            "When MaterialSpec.texture_source has approved_by_vision=true and local_path is present, load that absolute image path with bpy.data.images.load and wire it into the material shader as an image texture, keeping base_color as a fallback/tint. "
            "If texture_source is absent, approved_by_vision is false, or local_path is empty, do not create an image texture node for that material; use the base_color, roughness, metallic, and simple procedural shader settings only. "
            "Default render setup must use Workbench via `ll3m.configure_render(scene, engine='workbench')` unless the IR explicitly requires another engine. "
            "For Blender 4.5, the raw Workbench enum is BLENDER_WORKBENCH; never use the invalid literal WORKBENCH and never use bpy.types.Scene.bl_rna.properties['render_engine']. "
            "For animation, implement simple explicit keyframes from AnimationSpec events. "
            "Animate object roots that own the ll3m_id, set scene frame range/fps, insert start/end keyframes, and set interpolation on every generated keyframe. "
            "If setting Blender keyframe interpolation, use valid Blender enum strings such as LINEAR, BEZIER, SINE, QUAD, CUBIC, QUART, QUINT, EXPO, CIRC, BACK, BOUNCE, ELASTIC, or CONSTANT. Never assign EASE_IN_OUT to keyframe interpolation; it is an IR wording alias, not a Blender enum. "
            "Write at least two concrete keyframe_insert call sites for each animated subject, such as one at the start frame and one at the end frame. Do not put all keyframe_insert calls behind a single loop or a single helper invocation, because the harness static completeness check must see multiple actual keyframe call sites before Blender execution. "
            "For gripper/end-effector objects, keep the gripper visibly attached to the robotic arm while it moves; if the package is carried, the gripper and package must move together without separating the gripper from the arm. "
            "For appear/disappear events such as status lights, animate real visibility (hide_viewport/hide_render or scale from near-zero) so the object is not visibly on before its start frame. "
            "For horizontal supports, compute placements from world-space bbox dimensions after creating and scaling objects: support_top_z = support.location.z + support_height/2, then subject.location.z = support_top_z + subject_height/2 plus a tiny clearance margin; keep subject x/y inside the support footprint. "
            "For tables, shelves, counters, pedestals, and floors, do not guess midpoint z values. Align the subject bottom to the support top and adjust x/y overlap before adding decoration or animation. "
            "For bridge, deck, and platform crossings, never hard-code vehicle/object z keyframes from the prompt alone. After creating the real meshes, compute aggregate world bounding boxes for the moving root and its child/part meshes, compute each support top from the actual support bbox, then keyframe the moving root so its aggregate bottom is just above the active support at every support-contact frame. "
            "A vehicle/object on ground before or after a bridge must be fully outside the deck bbox in the travel axis: use deck_min - subject_half_extent - margin and deck_max + subject_half_extent + margin, not the deck edge itself. "
            "Do not rely on one linear segment from ground height to deck height if that segment passes through a vertical bridge/deck bbox. Add transition keyframes that keep x/y fixed outside the deck footprint while z changes, then move horizontally onto the deck only after the subject bottom is at or above deck top. Reverse the same pattern when leaving the bridge. "
            "Place bridge supports outside the drivable lane: if the car path is y=0, supports should be at side/corner y positions outside the car half-width plus margin, not directly on y=0. If supports are decorative or intentionally intersect the deck, set their ll3m_id/collision role so they are not collision-enabled active blockers for the car path. "
            "If an AnimationSpec location is fixed, make the generated mesh dimensions, local offsets, and support height compatible with that root location instead of moving the root away from the requested start/end/path transform. "
            "For slanted ramps, define a clear ramp coordinate system and keyframed path along the visible top surface. Place the sliding object's center on the surface plus the surface normal times its half extent, and keep sampled start/middle/end frames free of penetration. "
            "For final-state relations in animation, apply 'near', 'on', or 'inside' by setting the final keyframe/end_transform and corresponding sampled frame, not by moving the static initial pose unless the IR explicitly requires that relation at frame 1. "
            "Do not iterate action.fcurves directly; Blender 5 layered actions store fcurves under action.layers[*].strips[*].channelbags[*].fcurves. It is acceptable to leave default interpolation instead of editing fcurves. "
            "Keep the script concise. Do not write long reasoning comments, abandoned design notes, or step-by-step analysis inside the code. "
            "Do not use unavailable third-party Blender add-ons. Return only Python code."
        )
        system = _with_ll3m_utils_api(system)
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
- Import `from blender import ll3m_utils as ll3m`; use ll3m helpers where practical instead of reimplementing boilerplate.
- Use only helper functions listed in the ll3m_utils API contract above. If the contract does not cover something, use native `bpy` data API.
- Create all objects, materials, cameras, lights, and environment from the IR.
- Assign custom properties exactly: root objects must have ll3m_id equal to ObjectSpec.id. Parts may use ll3m_part, but never replace the root object's ll3m_id with a part id.
- Create every MaterialSpec using a Blender material name equal to MaterialSpec.id and set material['ll3m_id'] to that same id.
- Keep object names stable and human-readable.
- Create robust materials by setting mat.diffuse_color and locating shader nodes by node.type, not localized node names.
- For each MaterialSpec with texture_source.approved_by_vision=true and texture_source.local_path, treat local_path as an absolute path and load it with bpy.data.images.load. Set image colorspace to sRGB when available, add ShaderNodeTexImage, and connect Color to the Principled Base Color.
- Make the image texture visibly map onto generated geometry: either create a UV map for mesh surfaces or connect Texture Coordinate Generated/Object output through Mapping into the image texture. Do not connect UV coordinates on a mesh that has no UV map.
- If texture_source.approved_by_vision is false or no local_path is present, skip image texture nodes for that material and create a clean non-image material from base_color and shader parameters.
- Set bpy.context.scene.camera to the main generated camera.
- Set render settings with `ll3m.configure_render(scene, engine="workbench")` by default; Blender 4.5 uses BLENDER_WORKBENCH for Workbench and BLENDER_EEVEE_NEXT for Eevee. Never use bpy.types.Scene.bl_rna.properties['render_engine'].
- Set frame_start/frame_end/fps if animation exists.
- Insert keyframes for AnimationSpec events when present. For translate/rotate/scale, mutate the object's location/rotation_euler/scale at start and end frames, insert keyframes, and ensure sampled frames visibly change.
- If editing interpolation values, use Blender enum values such as `LINEAR` or `BEZIER`; do not use `EASE_IN_OUT`.
- Use explicit keyframe statements for at least the start and end pose of every animated subject. Do not rely on one loop containing a single `keyframe_insert` call for all keyframes; unroll the main start/middle/end insertions or call a helper separately for each required keyframe.
- For robotic pick-and-place, keep a continuous articulated chain from arm base to gripper. Do not detach the gripper from the arm just to make it follow the package.
- For appear/disappear events, keyframe hide_viewport/hide_render and/or near-zero scale before activation; material emission alone is not enough if the verifier can still see the light.
- For horizontal support/contact, create and scale the support and subject first, then align bbox top/bottom: subject bottom equals support top with positive x/y footprint overlap and no penetration.
- For bridge/deck/platform crossing animations, add a small local bbox helper if needed. Use actual world bboxes after object creation to set start/middle/end keyframe z values; the moving subject's aggregate bbox bottom must sit on the active support top, and start/end frames must not horizontally overlap the bridge deck unless they are intentionally on it.
- Put bridge approach/exit root positions completely outside the deck bbox by at least the moving subject half extent plus a small margin; a root centered on the deck edge still overlaps.
- Avoid a single linear ground-to-deck segment that crosses the bridge side wall or deck slab. Use outside-footprint vertical transition keyframes: keep the same outside x/y while changing z, then move horizontally over the deck at deck height. Do the reverse on exit so every sampled and interpolated frame remains nonpenetrating.
- Keep bridge supports out of the moving lane. Collision-enabled pillars at the same y centerline as the vehicle path will be audited as real obstacles; move them to side/corner positions or make them decorative non-collision parts.
- When AnimationSpec start/end/path locations are explicit, preserve those root coordinates by choosing compatible mesh dimensions/local offsets/support heights; do not "fix" contact by moving the final root location away from the IR.
- For ramp sliding, keep all sampled frame positions on the ramp top surface or just above it. Use the ramp's length direction for path points and avoid placing the cube center at an arbitrary world z midpoint.
- For final animation placement such as "stops near a box", set the end keyframe near the target while preserving the start pose and intermediate contact path.
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
            "Return a complete executable script, not a patch or excerpt. Preserve imports, object ids, created root objects with ll3m_id, animation keyframes, and the final LL3M_METADATA assignment unless a specific reported issue requires a local change. "
            "Prefer preserving or adding `from blender import ll3m_utils as ll3m` for common render setup, materials, cameras, lights, and primitive mesh objects instead of duplicating boilerplate. "
            "Use only the ll3m_utils helper functions listed in the API contract below; do not invent helper names. "
            "For floating, detached, penetrated, or misaligned object parts, fix transforms, origins, connector geometry, parenting, and contact points directly in code. "
            "For RELATION_ON_TOP_OF_FAILED, use the numeric evidence: first adjust x/y so overlap_x and overlap_y are both positive, then move the subject so its bottom z equals the reported support_z; do not rename ids or leave the object floating. "
            "For RELATION_DISTANCE_FAILED, determine whether the relation is a static scene requirement or an animation final-state requirement. For final states such as 'stops near', move the end keyframe/end_transform and matching sampled frame, not the initial static pose. "
            "For CONTACT_CONSTRAINT_SUPPORT_OVERLAP_FAILED, move the subject footprint inside the support footprint in x/y before changing z. "
            "For CONTACT_CONSTRAINT_SUPPORT_PENETRATION, lift the subject along z by the reported penetration/gap amount plus a small margin, while preserving positive support overlap. "
            "For CONTACT_CONSTRAINT_PENETRATION or ANIMATION_GLOBAL_PENETRATION, separate the reported object pair along the reported axis or shallowest overlap axis by penetration_depth plus a small margin, and update affected keyframes consistently. "
            "For bridge/deck penetration at start or end frames, do not just raise the car/object if the spec says it is on the ground. Move the ground pose fully outside the deck footprint by subject_half_extent plus margin, or add a ramp/approach geometry that makes the pose physically supported. "
            "For bridge/deck penetration during interpolation, insert outside-footprint transition keyframes so the moving subject changes z while x/y remains outside the deck bbox, then moves horizontally onto/off the deck at deck height. Do not keep a diagonal segment from ground height into the deck footprint. "
            "For vehicle collisions with bridge supports, do not route the car through centerline pillars. Move collision-enabled supports to deck side/corner positions outside the car y footprint, or mark decorative supports as collision-disabled if they are not meant to block the lane. "
            "For bridge/deck/platform support failures, fix the whole support-contact window, not just one sampled frame: compute actual aggregate world bboxes for the moving root and its child/part meshes, align the aggregate bottom to the active support top for every affected keyframe, and keep x/y footprint overlap only during the intended support window. "
            "When the report shows bridge/deck penetration at frame 1 or the final frame, check whether the ground pose still overlaps the deck bbox in x/y. If so, move that ground keyframe farther outside the deck by the subject half extent plus margin and update AnimationSpec-compatible metadata rather than leaving edge overlap. "
            "When support penetration appears together with ANIMATION_END_LOCATION_MISMATCH, do not trade one failure for the other. Preserve exact AnimationSpec start/end/path root locations when they are explicit, and instead adjust mesh dimensions, local mesh offsets, support height, or intermediate path points so those root locations are physically valid. "
            "For ANIMATION_END_LOCATION_MISMATCH, update the end keyframe/end_transform and any path point that drives it so the sampled final frame reaches the requested target without breaking contact constraints. "
            "For 'on floor of a room/greenhouse/enclosure' relations, place wheels/tanks/props on the interior floor plane, not on the roof or top of the enclosing walls. "
            "For ramp failures, do not convert the slanted ramp contact into a horizontal on_top_of stack. Keep the sliding object on the ramp surface along the path and fix start/middle/end frames with support/nonpenetration constraints. "
            "For animation failures, fix keyframe data paths, object roots, frame ranges, interpolation, and start/end transforms so sampled frames visibly match the AnimationSpec. "
            "For CODE_MISSING_ANIMATION_KEYFRAMES, unroll keyframe insertion so the script contains multiple concrete keyframe_insert call sites or multiple explicit helper calls. A single keyframe_insert inside one loop over a keyframe list is still treated as too few by the static completeness check. "
            "For Blender interpolation errors, replace IR aliases such as EASE_IN_OUT with valid Blender interpolation enum values like BEZIER or LINEAR, or remove custom interpolation edits entirely. "
            "For pick-and-place failures, do not animate the package independently while the gripper stays elsewhere. Animate the gripper/end-effector and package together during grasp/lift/carry frames, or parent/constraint the package to the gripper for that segment, so screenshots show continuous contact. "
            "Keep the gripper attached to the robotic arm at every sampled frame; moving the gripper as a detached block is a failure. "
            "For status-light activation failures, hide the light before activation using hide_viewport/hide_render or near-zero scale, then reveal it at the specified frame; emission-only changes are visually insufficient. "
            "Do not iterate action.fcurves directly; Blender 5 layered actions store fcurves under action.layers[*].strips[*].channelbags[*].fcurves. It is acceptable to remove custom interpolation edits and keep default interpolation. "
            "If materials render as default gray/white, fix localized Blender node lookup by finding BSDF_PRINCIPLED nodes by type and setting mat.diffuse_color. "
            "Default render setup should use Workbench via `ll3m.configure_render(scene, engine='workbench')` unless the IR explicitly requires another engine. "
            "If a material has a vision-approved texture_source.local_path in the IR, preserve or add the image texture node so the downloaded surface remains visible. "
            "Keep the script concise and complete. Remove long comments, scratch reasoning, and abandoned implementation notes. Never drop LL3M_METADATA, required imports, cameras, lights, or unaffected scene objects while repairing a local relation/contact issue. "
            "Return only the full corrected Python script."
        )
        system = _with_ll3m_utils_api(system)
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
            "Use `from blender import ll3m_utils as ll3m` for common helpers when adding new render, material, camera, light, or primitive object code. "
            "Use only the ll3m_utils helper functions listed in the API contract below; do not invent helper names. "
            "Return only the full corrected Python script."
        )
        system = _with_ll3m_utils_api(system)
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
            "Keep the existing validated scene code as the base. Do not call clear_scene again, delete objects, or recreate the static geometry when adding only animation; append or minimally edit animation code around the existing root objects. "
            "Preserve helper imports such as `from blender import ll3m_utils as ll3m`; use `ll3m.configure_render(scene, engine='workbench')` for default render setup. "
            "Use only the ll3m_utils helper functions listed in the API contract below; do not invent helper names. "
            "Write explicit keyframe_insert statements for at least the start and end pose of every animated subject. Do not hide all keyframes behind a single loop over a list; the pre-execution static check must see multiple actual keyframe call sites or multiple explicit helper calls. "
            "For bridge, deck, platform, floor, table, and ramp contact windows, derive animation keyframes from actual world bounding boxes in the validated scene. Align the moving root's aggregate bbox bottom to the active support top at each support-contact frame, and keep start/end positions fully outside a bridge/deck footprint by subject half extent plus margin unless the IR says the object is on that support at those frames. "
            "If the static bridge has vertical sides and no ramp, do not animate a single straight segment from ground to deck that intersects the bridge volume. Add transition keyframes at the same outside x/y: first ground outside, then deck height outside, then horizontal entry over the deck. Use the reverse sequence for exit. "
            "Before adding car keyframes, verify bridge supports do not occupy the car's lane. If supports are on the same y centerline as the car path, move them to side/corner locations or treat them as decorative non-collision parts before animating the crossing. "
            "If the AnimationSpec gives exact start/end/path root locations, preserve them by using compatible mesh local offsets, object dimensions, and support heights; do not move the requested final root coordinate merely to satisfy support. "
            "Keep the script concise and complete; no long reasoning comments. "
            "Return only the full corrected Python script."
        )
        system = _with_ll3m_utils_api(system)
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
- For each animated subject, write separate concrete statements for start, middle if present, and end keyframe insertion. Avoid a single `for keyframe in keyframes: obj.keyframe_insert(...)` loop as the only keyframe call site.
- If you set interpolation, use Blender enum values such as `LINEAR` or `BEZIER`; never use `EASE_IN_OUT`.
- For appear/disappear events, keyframe actual visibility or near-zero scale, not emission only.
- For contact/carry events, keep the interacting objects visibly connected at sampled frames.
- For support/nonpenetration windows on bridges, decks, platforms, floors, tables, or ramps, compute actual world bbox top/bottom after loading the static scene and set keyframes so the moving subject's aggregate bottom rests on the active support top without penetration or floating.
- Keep the moving subject fully outside bridge/deck x/y footprint at frames where it is supposed to be on the ground rather than on the bridge/deck; use subject half extent plus a small margin beyond the deck bound, not an edge-touching center coordinate.
- Add outside-footprint transition keyframes when moving from ground height to deck height: keep x/y outside the bridge/deck bbox while z changes, then move horizontally onto the deck at deck height. Reverse this on exit so interpolation never passes through the bridge/deck bbox.
- Ensure bridge supports are not collision obstacles in the travel lane; move them to side/corner y positions or make them decorative non-collision parts before keyframing the moving object.
- Preserve explicit AnimationSpec root locations by adjusting mesh local offsets/support dimensions when necessary; avoid changing the requested final root coordinate and causing ANIMATION_END_LOCATION_MISMATCH.
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
            unsupported_media = _is_multimodal_input_unsupported(exc)
            return ValidationReport.failed(
                VerificationMode.VISION,
                [
                    ValidationIssue(
                        code="VISION_INPUT_UNSUPPORTED" if unsupported_media else "VISION_VERIFIER_PARSE_FAILED",
                        message=(
                            f"Vision verifier model does not accept image input: {exc}"
                            if unsupported_media
                            else f"Vision verifier did not return valid JSON: {exc}"
                        ),
                        severity=Severity.CRITICAL if unsupported_media else Severity.MAJOR,
                        suggested_fix=(
                            "Configure LL3M_VISION_MODEL/API_BASE for a multimodal model that accepts image_url inputs, "
                            "or rerun with --skip-vision."
                            if unsupported_media
                            else None
                        ),
                    )
                ],
                "Vision verifier model does not accept image input."
                if unsupported_media
                else "Vision verifier response could not be parsed.",
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
            unsupported_media = _is_multimodal_input_unsupported(exc)
            return ValidationReport.failed(
                VerificationMode.VIDEO,
                [
                    ValidationIssue(
                        code="VIDEO_INPUT_UNSUPPORTED" if unsupported_media else "VIDEO_VERIFIER_PARSE_FAILED",
                        message=(
                            f"Video verifier model does not accept video/image input: {exc}"
                            if unsupported_media
                            else f"Video verifier did not return valid JSON: {exc}"
                        ),
                        severity=Severity.CRITICAL if unsupported_media else Severity.MAJOR,
                        suggested_fix=(
                            "Configure LL3M_VIDEO_MODEL/API_BASE for a multimodal model that accepts video_url/image_url inputs, "
                            "or rerun with --skip-video."
                            if unsupported_media
                            else None
                        ),
                    )
                ],
                "Video verifier model does not accept video/image input."
                if unsupported_media
                else "Video verifier response could not be parsed.",
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
            unsupported_media = _is_multimodal_input_unsupported(exc)
            return ValidationReport.failed(
                VerificationMode.VIDEO,
                [
                    ValidationIssue(
                        code="VIDEO_INPUT_UNSUPPORTED" if unsupported_media else "VIDEO_INPUT_PROBE_FAILED",
                        message=(
                            f"Video verifier model does not accept video input: {exc}"
                            if unsupported_media
                            else f"Could not confirm video input support: {exc}"
                        ),
                        severity=Severity.CRITICAL if unsupported_media else Severity.MAJOR,
                        suggested_fix=(
                            "Configure LL3M_VIDEO_MODEL/API_BASE for a multimodal model that accepts video_url inputs, "
                            "or rerun with --skip-video."
                            if unsupported_media
                            else None
                        ),
                    )
                ],
                "Video verifier model does not accept video input."
                if unsupported_media
                else "Video verifier could not confirm that the model received the video.",
            )
        if data.get("can_see_video") is True or data.get("attachment_readable") is True:
            return None
        if local_frame_count is not None and local_frame_count >= 2:
            retry_data = self._retry_video_input_probe(preview_video_path, local_frame_count)
            if retry_data.get("can_see_video") is True or retry_data.get("attachment_readable") is True:
                return None
            data = {"first_probe": data, "retry_probe": retry_data, "local_frame_count": local_frame_count}
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

    def _retry_video_input_probe(self, preview_video_path: Path, local_frame_count: int) -> dict[str, Any]:
        system = "Return only JSON. You are checking whether an attached MP4/GIF can be opened."
        user = f"""
The attachment is a locally verified temporal preview with {local_frame_count} video frames.
Return JSON with keys: can_see_video, attachment_readable, summary.
Set can_see_video=true if you can access any frames from the attached MP4/GIF.
Set attachment_readable=true if the file opens as video or animated GIF.
Do not decide whether the animation is correct; only report whether the attachment is readable.
"""
        try:
            return self.llm.json_video(system, user, preview_video_path, image_paths=[])
        except Exception as exc:
            return {"can_see_video": False, "attachment_readable": False, "summary": f"Retry probe failed: {exc}"}


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


def _is_multimodal_input_unsupported(exc: Exception) -> bool:
    text = str(exc).lower()
    if "unknown variant" in text and ("image_url" in text or "video_url" in text) and "expected" in text:
        return True
    return (
        ("image_url" in text or "video_url" in text or "image input" in text or "video input" in text)
        and any(token in text for token in ("unsupported", "not support", "does not support", "expected `text`"))
    )


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
                    "collision": {
                        "proxy_type": _value(obj.collision.proxy_type),
                        "role": _value(obj.collision.role),
                        "dimensions": _dimension(obj.collision.dimensions),
                        "margin": obj.collision.margin,
                        "enabled": obj.collision.enabled,
                        "group": obj.collision.group,
                    },
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
            "contact_constraints": [_compact_contact_constraint(constraint) for constraint in ir.animation.contact_constraints],
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
            "contact_constraints": [_compact_contact_constraint(constraint) for constraint in event.contact_constraints],
        }
    )


def _compact_contact_constraint(constraint: Any) -> dict[str, Any]:
    return _drop_none(
        {
            "id": constraint.id,
            "constraint_type": _value(constraint.constraint_type),
            "subject_id": constraint.subject_id,
            "object_id": constraint.object_id,
            "start_frame": constraint.start_frame,
            "end_frame": constraint.end_frame,
            "required": constraint.required,
            "max_penetration": constraint.max_penetration,
            "max_gap": constraint.max_gap,
            "min_overlap": constraint.min_overlap,
            "axis": constraint.axis,
            "description": constraint.description,
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


def _texture_search_result_record(
    *,
    material_id: str,
    query: str,
    candidates: list[TextureCandidate],
    downloaded: list[TextureCandidate],
    status: str,
    summary: str,
    selected: TextureCandidate | None = None,
    download_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    selected_manifest: dict[str, Any] | None = None
    if selected:
        try:
            selected_index = next(index for index, item in enumerate(candidates, start=1) if item.page_url == selected.page_url)
        except StopIteration:
            selected_index = 1
        selected_manifest = selected.to_manifest(selected_index)

    return {
        "material_id": material_id,
        "query": query,
        "status": status,
        "candidate_count": len(candidates),
        "downloaded_count": len(downloaded),
        "candidates": [candidate.to_manifest(index + 1) for index, candidate in enumerate(candidates)],
        "selected": selected.title if selected else None,
        "selected_candidate": selected_manifest,
        "local_path": str(selected.local_path) if selected and selected.local_path else None,
        "download_errors": download_errors or [],
        "summary": summary,
    }


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
    _sanitize_stage_data(data)
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
    importance_aliases = {
        "low": "optional",
        "minor": "optional",
        "nice_to_have": "optional",
        "normal": "preferred",
        "medium": "preferred",
        "important": "required",
        "high": "required",
        "critical": "required",
        "mandatory": "required",
    }
    valid_importance = {"optional", "preferred", "required"}
    prompt_text = str((data.get("prompt") or {}).get("text", "")).lower()
    for obj in scene.get("objects", []) or []:
        if not isinstance(obj, dict):
            continue
        category = str(obj.get("category", "generic")).lower()
        obj["category"] = category_aliases.get(category, category if category in valid_categories else "generic")
        role = str(obj.get("role", "secondary")).lower()
        obj["role"] = role if role in valid_roles else "secondary"
    _ensure_common_support_objects(scene, data.get("animation"))
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
        if relation["relation_type"] == "on_top_of" and any(
            token in relation_text for token in ("ramp", "incline", "inclined", "slanted", "slope", "angled")
        ):
            if any(token in relation_text for token in ("leg", "support", "bracket", "hinge", "connector", "base")):
                relation["relation_type"] = "attached_to"
            else:
                relation["relation_type"] = "touching"
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
        if relation["verification_method"] == "bbox_contact" and relation["relation_type"] not in {
            "on_top_of",
            "touching",
            "attached_to",
        }:
            relation["verification_method"] = "auto"
        priority = str(relation.get("visual_priority", "required")).strip().lower().replace("-", "_").replace(" ", "_")
        relation["visual_priority"] = importance_aliases.get(priority, priority if priority in valid_importance else "required")
    _normalize_view_type_fields(scene)
    _prune_invalid_verifier_references(scene)
    for material in scene.get("materials", []) or []:
        if not isinstance(material, dict):
            continue
        if _prompt_forbids_material_texture(prompt_text, material):
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


_COMMON_SUPPORT_IDS = {
    "floor",
    "floor_plane",
    "floor_surface",
    "ground",
    "ground_plane",
    "ground_surface",
}


def _ensure_common_support_objects(scene: dict[str, Any], animation: Any = None) -> None:
    objects = scene.get("objects")
    if not isinstance(objects, list):
        return
    existing_ids = {str(obj.get("id")) for obj in objects if isinstance(obj, dict) and obj.get("id")}
    missing_support_ids = sorted(
        reference_id
        for reference_id in _referenced_common_support_ids(scene, animation)
        if reference_id not in existing_ids
    )
    for support_id in missing_support_ids:
        objects.append(
            {
                "id": support_id,
                "label": _common_support_label(support_id),
                "category": "terrain",
                "role": "support",
                "importance": "required",
                "description": f"Horizontal {_common_support_label(support_id)} support plane.",
                "dimensions": {"size": [6.0, 6.0, 0.02]},
                "placement": {
                    "transform": {"location": [0.0, 0.0, 0.0]},
                    "anchor": "center",
                },
                "collision": {
                    "proxy_type": "bbox",
                    "role": "support",
                    "margin": 0.02,
                    "enabled": True,
                },
            }
        )


def _referenced_common_support_ids(scene: dict[str, Any], animation: Any = None) -> set[str]:
    references: set[str] = set()

    def collect(value: Any) -> None:
        if not isinstance(value, str):
            return
        normalized = value.strip()
        if normalized and normalized.lower() in _COMMON_SUPPORT_IDS:
            references.add(normalized)

    for relation in scene.get("relations", []) or []:
        if isinstance(relation, dict):
            collect(relation.get("subject_id"))
            collect(relation.get("object_id"))
    for camera in scene.get("cameras", []) or []:
        if isinstance(camera, dict):
            for target_id in camera.get("target_object_ids") or []:
                collect(target_id)
    verifier = scene.get("verifier")
    if isinstance(verifier, dict):
        screenshot_plan = verifier.get("screenshot_plan")
        if isinstance(screenshot_plan, dict):
            for view in screenshot_plan.get("views", []) or []:
                if isinstance(view, dict):
                    for target_id in view.get("target_object_ids") or []:
                        collect(target_id)

    if isinstance(animation, dict):
        _collect_common_supports_from_animation(animation, collect)
    return references


def _collect_common_supports_from_animation(animation: dict[str, Any], collect: Any) -> None:
    for constraint in animation.get("contact_constraints") or []:
        if isinstance(constraint, dict):
            collect(constraint.get("subject_id"))
            collect(constraint.get("object_id"))
    for event in [*(animation.get("events") or []), *(animation.get("camera_events") or [])]:
        if not isinstance(event, dict):
            continue
        for target_id in event.get("target_ids") or []:
            collect(target_id)
        for constraint in event.get("contact_constraints") or []:
            if isinstance(constraint, dict):
                collect(constraint.get("subject_id"))
                collect(constraint.get("object_id"))


def _common_support_label(support_id: str) -> str:
    return "ground plane" if "ground" in support_id.lower() else "floor plane"


def _prune_invalid_verifier_references(scene: dict[str, Any]) -> None:
    object_ids = _raw_object_and_part_ids(scene)
    relation_ids = {
        str(relation.get("id"))
        for relation in scene.get("relations", []) or []
        if isinstance(relation, dict) and relation.get("id")
    }
    camera_ids = {
        str(camera.get("id"))
        for camera in scene.get("cameras", []) or []
        if isinstance(camera, dict) and camera.get("id")
    }
    for camera in scene.get("cameras", []) or []:
        if isinstance(camera, dict):
            camera["target_object_ids"] = _filter_existing_ids(camera.get("target_object_ids"), object_ids)
    verifier = scene.get("verifier")
    if not isinstance(verifier, dict):
        return
    screenshot_plan = verifier.get("screenshot_plan")
    if not isinstance(screenshot_plan, dict):
        return
    for view in screenshot_plan.get("views", []) or []:
        if not isinstance(view, dict):
            continue
        if view.get("camera_id") and view.get("camera_id") not in camera_ids:
            view["camera_id"] = None
        view["target_object_ids"] = _filter_existing_ids(view.get("target_object_ids"), object_ids)
        view["relation_ids"] = _filter_existing_ids(view.get("relation_ids"), relation_ids)


def _filter_existing_ids(values: Any, valid_ids: set[str]) -> list[str]:
    if not isinstance(values, list):
        return []
    return [value for value in dict.fromkeys(str(item) for item in values if item) if value in valid_ids]


def _raw_object_and_part_ids(scene: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for obj in scene.get("objects", []) or []:
        if not isinstance(obj, dict):
            continue
        if obj.get("id"):
            ids.add(str(obj["id"]))
        for part in obj.get("parts") or []:
            if isinstance(part, dict) and part.get("id"):
                ids.add(str(part["id"]))
    return ids


def _prompt_forbids_material_texture(prompt_text: str, material: dict[str, Any]) -> bool:
    if not prompt_text:
        return False
    material_text = " ".join(str(material.get(key, "")) for key in ("id", "description")).lower()
    scoped_no_texture_phrases = (
        "no image textures",
        "no external textures",
        "without image textures",
        "no texture",
        "solid-color",
        "solid color",
    )
    if any(phrase in material_text for phrase in ("solid color", "solid-color", "plain", "clean ceramic")):
        return True
    if "statue" in material_text and any(
        phrase in prompt_text for phrase in ("on the statue", "statue, no image textures", "statue no image textures")
    ):
        return True
    if "ball" in material_text and any(
        phrase in prompt_text for phrase in ("solid-color plastic ball", "solid color plastic ball", "plastic ball")
    ):
        return True
    if "mug" in material_text and any(
        phrase in prompt_text for phrase in ("plain white ceramic mug", "plain ceramic mug", "white ceramic mug")
    ):
        return True
    if " all " in f" {prompt_text} " or "entire scene" in prompt_text or "all materials" in prompt_text:
        return any(phrase in prompt_text for phrase in scoped_no_texture_phrases)
    return False


def _sanitize_stage_data(data: dict[str, Any]) -> None:
    stages = data.get("stages")
    if not isinstance(stages, list):
        return
    valid_modes = {item.value for item in VerificationMode}
    mode_aliases = {
        "visual": "vision",
        "visual_verification": "vision",
        "visual_verifier": "vision",
        "image": "vision",
        "images": "vision",
        "screenshot": "vision",
        "screenshots": "vision",
        "static_visual": "vision",
        "temporal": "video",
        "movie": "video",
        "animation": "video",
    }
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        modes = stage.get("verifier_modes")
        if not isinstance(modes, list):
            continue
        normalized = []
        for mode in modes:
            raw = str(mode).strip().lower().replace("-", "_").replace(" ", "_")
            value = mode_aliases.get(raw, raw)
            if value in valid_modes and value not in normalized:
                normalized.append(value)
        stage["verifier_modes"] = normalized


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
        "contact_constraints": [
          {
            "id": "event_nonpenetration",
            "constraint_type": "nonpenetration",
            "subject_id": "object_id",
            "object_id": "other_object_id",
            "start_frame": 1,
            "end_frame": 120,
            "max_penetration": 0.02,
            "description": "object_id must not pass through other_object_id during this event"
          }
        ],
        "visibility_requirements": [
          "object_id is visible at the start, midpoint, and final sampled frame.",
          "The final placement/contact state is not cropped, hidden, or occluded."
        ]
      }
    ],
    "camera_events": [],
    "contact_constraints": [
      {
        "id": "final_support",
        "constraint_type": "support",
        "subject_id": "object_id",
        "object_id": "support_object_id",
        "start_frame": 120,
        "end_frame": 120,
        "max_penetration": 0.02,
        "max_gap": 0.05,
        "description": "object_id ends resting on support_object_id without floating or sinking"
      }
    ],
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
        "collision": {{
          "proxy_type": "bbox",
          "role": "kinematic",
          "margin": 0.02,
          "enabled": true,
          "group": null
        }},
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
        "scene.render.engine = 'WORKBENCH'": "scene.render.engine = 'BLENDER_WORKBENCH'",
        'scene.render.engine = "WORKBENCH"': 'scene.render.engine = "BLENDER_WORKBENCH"',
        "bpy.context.scene.render.engine = 'WORKBENCH'": "bpy.context.scene.render.engine = 'BLENDER_WORKBENCH'",
        'bpy.context.scene.render.engine = "WORKBENCH"': 'bpy.context.scene.render.engine = "BLENDER_WORKBENCH"',
        ".easing = 'EASE_IN_OUT'": ".easing = 'SINE'",
        '.easing = "EASE_IN_OUT"': '.easing = "SINE"',
        ".interpolation = 'EASE_IN_OUT'": ".interpolation = 'SINE'",
        '.interpolation = "EASE_IN_OUT"': '.interpolation = "SINE"',
    }
    for bad, good in replacements.items():
        code = code.replace(bad, good)
    code = _patch_ll3m_utils_import_aliases(code)
    code = _patch_ll3m_helper_keyword_compatibility(code)
    code = _patch_ll3m_look_at_object_targets(code)
    code = _patch_cone_diameter_keywords(code)
    code = _patch_fcurve_interpolation_assignments(code)
    code = _patch_common_blender_api_hallucinations(code)
    code = _patch_mathutils_vector_hallucinations(code)
    code = _patch_wave_modifier_falloff(code)
    code = _patch_mode_set_context(code)
    code = _patch_context_sensitive_ops(code)
    code = _patch_direct_action_fcurve_loops(code)
    code = _patch_common_ir_id_drift(code)
    code = _append_active_camera_fallback(code)
    return code


def _patch_ll3m_utils_import_aliases(code: str) -> str:
    code = re.sub(
        r"^([ \t]*)from blender import llm_utils(\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?([ \t]*(?:#.*)?)$",
        r"\1from blender import ll3m_utils\2\3",
        code,
        flags=re.MULTILINE,
    )
    code = re.sub(
        r"^([ \t]*)import blender\.llm_utils(\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?([ \t]*(?:#.*)?)$",
        r"\1import blender.ll3m_utils\2\3",
        code,
        flags=re.MULTILINE,
    )
    return code


def _patch_ll3m_helper_keyword_compatibility(code: str) -> str:
    """Route common generated helper keyword drift through local wrappers."""

    code = _regex_sub_unprotected(
        r"\b((?:ll3m|llm)\.make_material)\(\s*spec_dict\s*=",
        r"\1(",
        code,
    )
    needs_wrapper = bool(re.search(r"\b(?:ll3m|llm)\.add_(?:cube|plane)\(", code)) and bool(
        re.search(r"\b(?:scale|rotation)\s*=", code)
    )
    if not needs_wrapper:
        return code
    patched = _regex_sub_unprotected(r"\b(?:ll3m|llm)\.add_cube\(", "ll3m_safe_add_cube(", code)
    patched = _regex_sub_unprotected(r"\b(?:ll3m|llm)\.add_plane\(", "ll3m_safe_add_plane(", patched)
    if patched == code or "def ll3m_safe_add_cube(" in patched:
        return patched
    helper = '''
def _ll3m_safe_utils():
    utils = globals().get("ll3m") or globals().get("llm")
    if utils is None:
        raise RuntimeError("LL3M helper alias ll3m/llm is not available")
    return utils


def ll3m_safe_add_cube(*args, scale=None, **kwargs):
    obj = _ll3m_safe_utils().add_cube(*args, **kwargs)
    if scale is not None:
        obj.scale = scale
    return obj


def ll3m_safe_add_plane(*args, scale=None, rotation=None, **kwargs):
    obj = _ll3m_safe_utils().add_plane(*args, **kwargs)
    if rotation is not None:
        obj.rotation_euler = rotation
    if scale is not None:
        obj.scale = scale
    return obj
'''.lstrip()
    return helper + "\n" + patched


def _patch_ll3m_look_at_object_targets(code: str) -> str:
    if not re.search(r"\b(?:ll3m|llm)\.look_at\(", code):
        return code
    patched = _regex_sub_unprotected(r"\b(?:ll3m|llm)\.look_at\(", "ll3m_safe_look_at(", code)
    if patched == code:
        return code
    helpers: list[str] = []
    if "def _ll3m_safe_utils(" not in patched:
        helpers.append(
            '''
def _ll3m_safe_utils():
    utils = globals().get("ll3m") or globals().get("llm")
    if utils is None:
        raise RuntimeError("LL3M helper alias ll3m/llm is not available")
    return utils
'''.lstrip()
        )
    if "def ll3m_safe_look_at(" not in patched:
        helpers.append(
            '''
def ll3m_safe_look_at(obj, target):
    if hasattr(target, "location"):
        target = target.location
    return _ll3m_safe_utils().look_at(obj, target)
'''.lstrip()
        )
    if helpers:
        patched = "\n".join(helpers) + "\n" + patched
    return patched


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


def _patch_mathutils_vector_hallucinations(code: str) -> str:
    code = _regex_sub_unprotected(
        r"(\b[A-Za-z_][A-Za-z0-9_.]*\.matrix_world\s*@\s*)(?:mathutils\.)?Vector\(([A-Za-z_][A-Za-z0-9_]*)\)(\s+for\s+\2\s+in\s+[^\]\n]+\.data\.vertices)",
        r"\1\2.co\3",
        code,
    )
    code = _regex_sub_unprotected(r"\bmathutils\.Vector\(\s*\)", "mathutils.Vector((0.0, 0.0, 0.0))", code)
    code = _regex_sub_unprotected(r"(?<!\.)\bVector\(\s*\)", "Vector((0.0, 0.0, 0.0))", code)
    return code


def _patch_wave_modifier_falloff(code: str) -> str:
    pattern = re.compile(
        r"^([ \t]*)([A-Za-z_][A-Za-z0-9_]*)\.falloff\s*=\s*([^\n#]*)(.*)$",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    lines: list[str] = []
    for line in code.splitlines(keepends=True):
        line_body = line[:-1] if line.endswith("\n") else line
        newline = "\n" if line.endswith("\n") else ""
        match = pattern.match(line_body)
        if match and "wave" in match.group(2).lower():
            lines.append(
                f"{match.group(1)}# LL3M sanitizer: removed unsupported WaveModifier falloff assignment{match.group(4)}{newline}"
            )
        else:
            lines.append(line)
    return "".join(lines)


def _patch_mode_set_context(code: str) -> str:
    """Make generated edit/object mode switches tolerate missing active context."""

    if "bpy.ops.object.mode_set(" not in code:
        return code
    helper = '''
def ll3m_safe_mode_set(mode):
    """Switch object mode only after ensuring Blender has an active object."""
    view_layer = bpy.context.view_layer
    obj = view_layer.objects.active
    if obj is None or obj.name not in view_layer.objects:
        selected = [candidate for candidate in bpy.context.selected_objects if candidate.name in view_layer.objects]
        obj = selected[0] if selected else next((candidate for candidate in bpy.context.scene.objects if candidate.type == "MESH"), None)
        if obj is not None:
            view_layer.objects.active = obj
            obj.select_set(True)
    if obj is None:
        return False
    try:
        bpy.ops.object.mode_set(mode=mode)
        return True
    except Exception:
        return False
'''.lstrip()
    if "def ll3m_safe_mode_set(" not in code:
        code = helper + "\n" + code
    return re.sub(r"\bbpy\.ops\.object\.mode_set\(", "ll3m_safe_mode_set(", code)


def _patch_context_sensitive_ops(code: str) -> str:
    """Guard UI-context-dependent mesh/transform operators generated by LLMs."""

    needs_mesh_select = "bpy.ops.mesh.select_all(" in code
    needs_translate = "bpy.ops.transform.translate(" in code
    if not needs_mesh_select and not needs_translate:
        return code
    helpers: list[str] = []
    if needs_mesh_select and "def ll3m_safe_mesh_select_all(" not in code:
        helpers.append(
            '''
def ll3m_safe_mesh_select_all(action="SELECT"):
    try:
        bpy.ops.mesh.select_all(action=action)
        return True
    except Exception:
        return False
'''.lstrip()
        )
    if needs_translate and "def ll3m_safe_transform_translate(" not in code:
        helpers.append(
            '''
def ll3m_safe_transform_translate(value=(0, 0, 0)):
    try:
        bpy.ops.transform.translate(value=value)
        return True
    except Exception:
        obj = bpy.context.view_layer.objects.active
        if obj is not None:
            obj.location.x += value[0]
            obj.location.y += value[1]
            obj.location.z += value[2]
            return True
        return False
'''.lstrip()
        )
    if helpers:
        code = "\n".join(helpers) + "\n" + code
    code = re.sub(r"\bbpy\.ops\.mesh\.select_all\(", "ll3m_safe_mesh_select_all(", code)
    code = re.sub(r"\bbpy\.ops\.transform\.translate\(", "ll3m_safe_transform_translate(", code)
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
    already_has_helper = "def ll3m_iter_action_fcurves(" in code
    lines = code.splitlines(keepends=True)
    helper_body_lines = _fcurve_iterator_body_line_indexes(lines)
    patched_lines: list[str] = []
    changed = False
    patterns: list[tuple[re.Pattern[str], str]] = [
        (
            re.compile(r"^([ \t]*)for\s+(\w+)\s+in\s+([A-Za-z_][A-Za-z0-9_]*)\.animation_data\.action\.fcurves\s*:(.*)$"),
            r"\1for \2 in ll3m_iter_action_fcurves(\3.animation_data.action):\4",
        ),
        (
            re.compile(r"^([ \t]*)for\s+(\w+)\s+in\s+([A-Za-z_][A-Za-z0-9_]*)\.action\.fcurves\s*:(.*)$"),
            r"\1for \2 in ll3m_iter_action_fcurves(\3.action):\4",
        ),
        (
            re.compile(r"^([ \t]*)for\s+(\w+)\s+in\s+([A-Za-z_][A-Za-z0-9_]*)\.fcurves\s*:(.*)$"),
            r"\1for \2 in ll3m_iter_action_fcurves(\3):\4",
        ),
    ]
    for line_index, line in enumerate(lines):
        if line_index in helper_body_lines:
            patched_lines.append(line)
            continue
        line_body = line[:-1] if line.endswith("\n") else line
        newline = "\n" if line.endswith("\n") else ""
        patched_body = line_body
        for pattern, replacement in patterns:
            patched_body = pattern.sub(replacement, patched_body)
        if patched_body != line_body:
            changed = True
        patched_lines.append(patched_body + newline)
    patched = "".join(patched_lines)
    if changed and not already_has_helper:
        patched = helper + "\n\n" + patched
    return patched


def _fcurve_iterator_body_line_indexes(lines: list[str]) -> set[int]:
    indexes: set[int] = set()
    def_pattern = re.compile(r"^([ \t]*)def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    for line in lines:
        match = def_pattern.match(line)
        if not match:
            continue
        function_name = match.group(2)
        lowered = function_name.lower()
        if "iter" in lowered and "fcurve" in lowered:
            indexes.update(_function_body_line_indexes(lines, function_name))
    return indexes


def _function_body_line_indexes(lines: list[str], function_name: str) -> set[int]:
    indexes: set[int] = set()
    def_pattern = re.compile(rf"^([ \t]*)def\s+{re.escape(function_name)}\s*\(")
    start_index: int | None = None
    def_indent = 0
    for index, line in enumerate(lines):
        match = def_pattern.match(line)
        if match:
            start_index = index
            def_indent = len(match.group(1).replace("\t", "    "))
            indexes.add(index)
            continue
        if start_index is None or index <= start_index:
            continue
        if not line.strip():
            indexes.add(index)
            continue
        indent = len(line[: len(line) - len(line.lstrip(" \t"))].replace("\t", "    "))
        if indent <= def_indent:
            break
        indexes.add(index)
    return indexes


def _regex_sub_unprotected(
    pattern: str,
    repl: str | Callable[[re.Match[str]], str],
    code: str,
    *,
    flags: int = 0,
) -> str:
    protected_spans = _string_and_comment_spans(code)

    def replace(match: re.Match[str]) -> str:
        if _overlaps_any(match.start(), match.end(), protected_spans):
            return match.group(0)
        if callable(repl):
            return repl(match)
        return match.expand(repl)

    return re.sub(pattern, replace, code, flags=flags)


def _string_and_comment_spans(code: str) -> list[tuple[int, int]]:
    line_offsets: list[int] = []
    offset = 0
    for line in code.splitlines(keepends=True):
        line_offsets.append(offset)
        offset += len(line)
    if not line_offsets:
        line_offsets.append(0)
    spans: list[tuple[int, int]] = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(code).readline)
        for token in tokens:
            if token.type not in {tokenize.STRING, tokenize.COMMENT}:
                continue
            start_line, start_col = token.start
            end_line, end_col = token.end
            if start_line - 1 >= len(line_offsets) or end_line - 1 >= len(line_offsets):
                continue
            start = line_offsets[start_line - 1] + start_col
            end = line_offsets[end_line - 1] + end_col
            spans.append((start, end))
    except tokenize.TokenError:
        return []
    return spans


def _overlaps_any(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


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
