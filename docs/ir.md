# VeriAnim Scene and Animation IR

This document defines the first implementation version of the intermediate
representation used by the local harness. The source of truth is
`harness/ir.py`; this document explains how the fields should be used by
planner, code generation, deterministic verification, visual verification, video
verification, and refinement agents.

## Design Goals

The IR must make generation inspectable and repairable.

- It separates scene planning from Blender code generation.
- It makes every important object addressable by a stable id.
- It represents spatial relationships explicitly, not only in natural language.
- It treats screenshot and video sampling plans as part of the task.
- It allows deterministic Blender checks and model-based visual checks to report
  issues against the same ids.
- It keeps animation events modular so failures can be repaired locally.

## Component Design Audit

The IR is split by responsibility rather than by prompt phrasing:

- `SourcePrompt` preserves user intent and hard constraints. It should not be
  used as the executable plan because motion language can leak into static
  scene generation.
- `SceneSpec` is the static contract. It owns objects, materials, environment,
  cameras, and spatial relations so the harness can validate the scene before
  adding animation.
- `ObjectSpec` and `ObjectPartSpec` make entities addressable. The recent
  gripper and windmill tests show why parts that need independent motion or
  verification should be promoted to root objects; parts are descriptive, root
  objects are controllable.
- `CollisionProxySpec` gives each controllable object a simple physical proxy.
  This borrows the stable object identity of scene graphs/USD and the proxy
  idea used in dynamic scene graphs, but keeps the representation small enough
  for an LLM planner to emit and for Blender validation to audit.
- `MaterialSpec` is intentionally separate from objects so texture resolution
  can happen before code generation. `texture_policy` is now explicit because
  "solid color" and "no image textures" are user constraints, not model
  preferences.
- `SpatialRelationSpec` is a semantic relation plus a verification strategy.
  This avoids forcing every relation through one bbox rule; horizontal support,
  hinge attachment, slanted ramp support, inside/contains, and visual-only
  occluded contacts need different checks.
- `EnvironmentSpec`, `CameraSpec`, and `ScreenshotPlan` are first-class because
  visual verification is only as good as the rendered evidence. Lighting,
  framing, relation close-ups, and target size are not cosmetic; they determine
  whether the verifier can make a grounded judgment.
- `VerificationPlan` keeps deterministic, visual, and video gates explicit.
  Deterministic checks catch measurable failures; visual/video checks catch
  semantic layout, occlusion, lighting, and temporal visibility failures.
- `AnimationSpec` extends a validated `SceneSpec`. It should never replace the
  scene plan. Its events reference stable scene ids and encode start, middle,
  end, expected visual result, and visibility requirements so animation repair
  can be local.
- `ContactConstraintSpec` is the animation counterpart to spatial relations.
  Instead of relying on natural-language instructions such as "do not pass
  through the table", it records frame windows for nonpenetration, support,
  attachment, carried contact, and containment. This is inspired by behavior
  markup and spacetime constraints, but differs by being repair-oriented:
  failed constraints produce object ids, frames, and measured evidence for the
  refiner.
- `PipelineStageSpec` records the progressive contract: static scene first,
  animation extension second. This is the architectural guardrail that prevents
  the static stage from inserting keyframes or the animation stage from
  rebuilding the whole scene.

The main boundary exposed by recent tests is that LLMs often generate plausible
natural-language plans with underspecified verification semantics. IR v0.2
therefore adds explicit texture policy, relation verification method, camera
framing requirements, screenshot purpose, video visibility requirements,
collision proxies, and contact constraints.

Compared with prior scene or animation IRs, this IR is not primarily an asset
interchange format like USD/BIFS, not only a text-to-layout scene graph, and not
only a behavior script. Its distinguishing property is that it is LLM-facing,
executable, and verifier-facing at the same time: the same ids and constraints
are used by planner prompts, code generation, deterministic Blender audits,
vision/video verification, and repair prompts.

## Top-Level Object

`GenerationIR` is the top-level object.

Required:

- `prompt`: original user request and optional negative constraints.
- `scene`: complete static scene specification.

Optional:

- `animation`: animation specification. If omitted, the task is static scene
  generation.
- `project_id`: caller-provided id for logging and artifacts.
- `notes`: free-form planner notes.
- `version`: IR version. Current version is `0.2`.
- `stages`: progressive generation plan. For animation tasks this should be
  `static_scene` followed by `animation_extension`.

The IR is progressive. A task that ultimately produces animation still has a
complete static scene baseline first. The animation spec is an extension that
references and modifies that validated baseline; it is not a license to mix
scene construction and motion into one undifferentiated code-generation step.

The harness uses two IR projections:

- `static_scene_projection()`: contains `prompt` and `scene`, omits
  `animation`, and synthesizes a static-only prompt from `SceneSpec` so leftover
  natural-language motion instructions cannot leak into scene generation.
- Full `GenerationIR`: contains the same `scene` plus `animation`, and is used
  only after the static scene has passed deterministic and visual verification.

`PipelineStageSpec`

- `id`: stable stage id, normally `static_scene` or `animation_extension`.
- `stage_type`: `static_scene` or `animation_extension`.
- `description`: stage purpose.
- `depends_on`: earlier stages that must pass first.
- `freezes_scene_geometry`: whether the stage should preserve the validated
  static scene geometry.
- `verifier_modes`: verifier gates for the stage.

## Prompt

`SourcePrompt`

- `text`: user request.
- `negative_text`: optional text describing things to avoid.
- `image_paths`: optional reference image paths.
- `user_constraints`: explicit hard constraints extracted from the user request.

The planner should preserve the original prompt here and put inferred structure
into `scene` and `animation`.

## Scene

`SceneSpec`

- `objects`: list of `ObjectSpec`. This is required.
- `relations`: list of `SpatialRelationSpec`.
- `materials`: reusable `MaterialSpec` definitions.
- `environment`: background, floor, sky, and lighting.
- `cameras`: named camera plans.
- `style`: global style hints.
- `verifier`: deterministic, visual, and video verification plan.
- `coordinate_system`: defaults to Blender's Z-up right-handed system.
- `units`: defaults to meters.

## Objects

`ObjectSpec`

- `id`: stable machine id. Must be unique.
- `description`: semantic description used by generation and visual checks.
- `label`: optional display name.
- `category`: broad object category.
- `role`: primary, secondary, support, background, decoration, etc.
- `importance`: whether the object is required or optional.
- `parts`: expected sub-parts, such as seat, backrest, legs, handle, wheel.
- `required_features`: features that must be visible or represented.
- `optional_features`: nice-to-have features.
- `forbidden_features`: things that should not appear.
- `dimensions`: approximate or bounded object size.
- `placement`: local/global placement hints.
- `material_ids`: references to `MaterialSpec`.
- `collision`: `CollisionProxySpec` used for deterministic contact and
  penetration checks.
- `generation_notes`: extra instructions for the code generator.
- `visual_check_prompts`: object-specific questions for the vision verifier.

`ObjectPartSpec`

- `id`: stable part id.
- `description`: semantic part description.
- `required`: whether the part must exist.
- `material_id`: optional material reference.
- `expected_count`: expected number of repeated parts.
- `dimension`: optional size bounds for the part.

## Collision Proxies

`CollisionProxySpec`

- `proxy_type`: `auto`, `bbox`, `sphere`, `capsule`, `convex_hull`, `mesh`, or
  `compound`. The current deterministic validator uses world-space aggregate
  bounding boxes as the first implementation, but the proxy type tells the
  planner/coder what physical simplification is intended and leaves room for
  BVH or convex checks later.
- `role`: `active`, `passive`, `kinematic`, `carried`, `support`, or `trigger`.
  Triggers are ignored by global penetration audits. Supports and carried
  objects should usually also appear in explicit contact constraints.
- `dimensions`: optional proxy-specific dimensions when they differ from the
  visual object dimensions.
- `margin`: allowed penetration tolerance in meters. This should be small, for
  example `0.01` to `0.03`, for hard props.
- `enabled`: set false only for purely visual effects, transparent guides, or
  background decoration that should not participate in collision audits.
- `group`: optional future grouping hint.
- `notes`: free-form physical simplification notes.

Planner guidance:

- Give every required, movable, support, container, or obstacle object a
  collision proxy.
- Use `sphere` for balls, `capsule` for limbs/cylinders, `bbox` for boxes and
  platforms, and `compound` for objects whose visible parts form separated
  supports.
- Disable collision for smoke, light cones, labels, purely decorative decals,
  or distant background geometry.

## Materials

`MaterialSpec`

- `id`: stable material id.
- `description`: material description.
- `base_color`: RGBA tuple.
- `metallic`, `roughness`, `alpha`: Blender-style material hints.
- `texture_hints`: natural language texture hints.
- `texture_policy`: `auto`, `required`, `forbidden`, or `solid_only`.
  This is the high-level policy; `needs_texture` is the operational decision.
  Use `solid_only` for plain procedural colors and `forbidden` when the user
  explicitly disallows external image textures. Use `required` only when an
  image texture or provided `texture_source` is essential to the prompt.
- `needs_texture`: planner decision for whether the harness should search for
  an external image texture before code generation. Use this for natural,
  patterned, grainy, or irregular surfaces such as wood, stone, concrete,
  rusted metal, brick, bark, fabric, leather, tabletop planks, or walls.
  Keep it false for intentionally plain surfaces such as a pure-color mug,
  clean ceramic, flat plastic, signal lights, or simple painted parts.
- `texture_query`: concise search query for the texture agent, for example
  `wood tabletop grain`, `rough concrete wall`, or `rusted metal`.
- `texture_source`: optional resolved texture asset populated after planning by
  the texture agent. It includes source/page/image URLs, local cached path,
  license, tags, and VISION approval metadata.

Object specs reference materials by id. The generator may create additional
procedural material internals, but should keep these ids stable.

## Placement and Dimensions

`DimensionSpec`

- `size`: approximate `(x, y, z)` size.
- `min_size`: lower size bound.
- `max_size`: upper size bound.
- `tolerance`: acceptable relative or absolute tolerance for checks.

`TransformSpec`

- `location`: `(x, y, z)`.
- `rotation_euler`: `(x, y, z)` in radians unless the generator explicitly
  converts units.
- `scale`: `(x, y, z)`.

`PlacementSpec`

- `transform`: desired transform.
- `anchor`: semantic anchor, for example `origin`, `bottom_center`, or
  `center`.
- `parent_id`: optional parent object id.
- `notes`: free-form placement notes.

## Relations

`SpatialRelationSpec`

- `id`: stable relation id.
- `relation_type`: relation enum, such as `on_top_of`, `left_of`,
  `not_intersecting`, `facing`.
- `subject_id`: object being constrained.
- `object_id`: reference object.
- `description`: natural language relation.
- `required`: whether failure should block the scene.
- `tolerance`: geometric tolerance.
- `min_distance`, `max_distance`: distance bounds where applicable.
- `offset`: expected relative offset.
- `axis`: optional relation axis, usually `x`, `y`, or `z`.
- `verification_method`: deterministic validation strategy. Use `auto` for the
  default relation check, `bbox_contact` for horizontal support/contact,
  `bbox_order` for axis ordering, `distance` for near/far, `attachment` for
  hinges/connectors/brackets/supports, and `visual_only` when non-axis-aligned
  or occluded geometry should be judged from screenshots instead of bbox
  support math.
- `contact_points`: optional approximate contact points for future
  geometry-aware checks.
- `expected_clearance`: expected gap or clearance, normally `0.0` for contact.
- `visual_priority`: whether the relation must be obvious in screenshots.

Deterministic verification should measure these with Blender world-space
bounding boxes. Vision verification should judge whether the relation is visible
and semantically clear.

For horizontal support, plan and code the placement with an explicit bbox
alignment recipe: compute the support top in world z, compute the subject half
height after scale is applied, set the subject center z to support_top +
subject_half_height, and keep the subject footprint inside or overlapping the
support footprint in x/y. A support relation should not pass by moving only z
when x/y overlap is missing.

Animation prompts often describe a final relation, for example "rolls across a
table and stops near a box". Treat that as an animation end-state unless the
initial scene must already satisfy it. Encode the final placement in
`AnimationEventSpec.end_transform`, sampled final frames, pass criteria, and
event-scoped `contact_constraints`; avoid adding a static `near` or `on_top_of`
relation that must be true before the motion starts.

Recent medium-animation tests exposed the main relation-design risk: a single
semantic enum is not enough to choose a reliable deterministic check. A slanted
ramp supported by legs is structurally attached/touching, not a horizontal
`on_top_of` stack. The planner should therefore specify both `relation_type`
and `verification_method`, and the screenshot plan should include views that
make visually checked relations inspectable.

For a cube, ball, or vehicle moving on a ramp, use a ramp-aware path instead of
horizontal support math: define the ramp's length direction, top-surface
normal, start/end surface points, and sampled contact frames. Place the moving
object center on the surface plus the normal times the object's radius or half
extent. Use `touching` or `visual_only` for static slanted contact relations and
event-scoped support/nonpenetration constraints for the animation window.

## Environment

`EnvironmentSpec`

- `environment_type`: studio, room, outdoor, skybox, abstract, etc.
- `description`: semantic background description.
- `floor`, `walls`, `sky`, `world_background`: environment elements.
- `lights`: list of `LightSpec`.
- `ambient_occlusion`: render hint.
- `notes`: free-form environment notes.

`LightSpec`

- `id`, `light_type`, `description`.
- `location`, `rotation_euler`.
- `energy`, `color`, `size`.

## Cameras and Rendering

`CameraSpec`

- `id`: stable camera id.
- `view_type`: front, side, top, three-quarter, close-up, etc.
- `description`: purpose of the camera.
- `location`: camera location.
- `look_at`: target point.
- `target_object_ids`: objects expected to be visible.
- `focal_length_mm`: focal length hint.
- `coverage`: natural language framing requirement.
- `frame_range`: optional animation frame range for animated camera use.
- `min_subject_pixel_fraction`: optional lower bound for target subject size in
  the rendered image. This prevents objects from passing while too tiny to
  inspect.
- `allow_subject_crop`: whether target objects may be cropped by this camera.

`RenderSpec`

- `resolution`: width and height.
- `engine`: eevee, cycles, or workbench. Defaults to workbench for inspection
  renders so transparent or reflective surfaces do not obscure internal models.
- `samples`: optional sample count.
- `transparent_background`: render alpha hint.
- `output_dir`: optional output location.

## Screenshot Plan

`ScreenshotPlan`

- `views`: list of `ScreenshotViewSpec`.
- `render`: render settings for screenshots.
- `min_required_views`: minimum number of screenshots required before visual
  verification can run.

`ScreenshotViewSpec`

- `id`: stable screenshot id.
- `view_type`: canonical view type.
- `description`: what this view is meant to inspect.
- `camera_id`: optional camera reference.
- `target_object_ids`: objects the view should include.
- `relation_ids`: relations the view should inspect.
- `frame`: optional animation frame for sampled-frame checks.
- `crop_hint`: optional crop instruction for close-ups.
- `required`: whether missing this view blocks verification.
- `min_subject_pixel_fraction`: optional framing requirement for this view.
- `must_show_full_targets`: whether target objects should be fully visible.
- `purpose`: why the view exists, such as `overall inspection`,
  `contact verification`, `support check`, or `animation final state`.

The planner should create views deliberately. A static scene should usually have
front, side, top, three-quarter, and relation close-up views. A visual verifier
cannot reliably catch missing or misplaced objects if all screenshots are beauty
shots from one angle.

## Verification Plan

`VerificationPlan`

- `deterministic_checks`: list of hard Blender API checks.
- `screenshot_plan`: views required for visual inspection.
- `visual`: visual model verification settings.
- `video`: video model verification settings.

`DeterministicCheckSpec`

- `id`: check id.
- `description`: what is measured.
- `target_ids`: object ids involved.
- `relation_ids`: relation ids involved.
- `required`: whether failure blocks pass.

`VisualVerifierSpec`

- `enabled`: whether to run visual verification.
- `model_hint`: optional model name.
- `required_view_ids`: screenshot ids needed by the verifier.
- `questions`: specific questions to ask the model.
- `pass_criteria`: conditions for visual pass.
- `max_rounds`: maximum visual-refinement rounds. Default implementation
  uses 6 so the visual verifier can remain a blocking gate through several
  repair attempts.

Vision verifier output should become `ValidationReport` with issue targets
matching object or relation ids.

## Animation

`AnimationSpec`

- `duration_frames`: total animation duration.
- `fps`: frames per second.
- `events`: object animation events.
- `camera_events`: camera animation events.
- `loop`: whether the animation should loop.
- `render`: animation render settings.
- `verifier`: video verifier settings.
- `contact_constraints`: global animation contact constraints that apply across
  events or final states.

`AnimationEventSpec`

- `id`: stable event id.
- `action`: translate, rotate, scale, follow path, appear, disappear, camera
  move, physics, or custom.
- `subject_ids`: animated object ids.
- `start_frame`, `end_frame`: event frame range.
- `description`: semantic description.
- `target_ids`: object or camera targets.
- `path`: optional motion path.
- `start_transform`, `end_transform`: expected transforms.
- `interpolation`: interpolation hint.
- `required`: whether failure blocks pass.
- `expected_visual_result`: what the video verifier should see.
- `visibility_requirements`: per-event visibility constraints. Use this to
  state which subjects, contact points, and final placements must remain visible
  in the GIF/video and sampled frames.
- `constraints`: event-specific constraints.
- `contact_constraints`: event-scoped `ContactConstraintSpec` entries. Use
  these when a constraint only applies during one event window.

`ContactConstraintSpec`

- `id`: stable constraint id.
- `constraint_type`: `nonpenetration`, `support`, `touching`, `attachment`,
  `carry_contact`, or `inside`.
- `subject_id`: constrained object.
- `object_id`: reference/support/container/object to avoid.
- `start_frame`, `end_frame`: inclusive frame window.
- `required`: whether failure blocks pass. Optional constraints report as lower
  severity in deterministic validation.
- `max_penetration`: allowed bbox penetration in meters.
- `max_gap`: allowed separation for support/contact, or escape tolerance for
  `inside`.
- `min_overlap`: optional footprint overlap requirement for future stricter
  checks.
- `axis`: optional axis hint, `x`, `y`, or `z`.
- `description`: human-readable intent for the coder and verifier.

Recommended use:

- `nonpenetration`: moving object must not pass through obstacle, wall, table,
  container side, another actor, or final target.
- `support`: object must rest on a platform/floor/surface without floating or
  sinking.
- `touching` / `attachment`: hinge, connector, bracket, hand contact, or object
  that must remain connected.
- `carry_contact`: gripper, hand, crane hook, or tray must remain close to the
  carried object during the carry window.
- `inside`: object must remain within a basket, box, drawer, pipe, or boundary.

Repair priority for contact reports should follow the same geometry order as
the validator. Fix missing support footprint overlap in x/y before z alignment;
then fix support penetration or floating; then resolve pairwise penetration by
separating along the reported axis or the shallowest bbox overlap axis. For
animation reports, apply the same correction to every affected keyframe or path
point in the failing frame window, not just to the current frame.

`MotionPathSpec`

- `points`: path points.
- `keyframes`: explicit keyframes.
- `path_object_id`: optional Blender path object id.
- `follow_orientation`: whether the object should orient along the path.

`KeyframeSpec`

- `frame`: frame number.
- `transform`: optional transform at that frame.
- `value`: generic animated value payload.
- `interpolation`: interpolation hint.
- `description`: semantic keyframe note.

## Animation Event Requirements

Animation planning is intentionally constrained to a repeatably verifiable
subset. A planner should not emit an animation as only a natural-language
sentence. Each required event must expose start, middle, and end states that can
be checked by structural validation, deterministic Blender sampling, and the
video verifier.

Supported first-pass actions and required fields:

- `translate`: `start_transform.location`, `end_transform.location`, and at
  least one intermediate `path.keyframes[].transform.location`.
- `rotate`: `start_transform.rotation_euler`, `end_transform.rotation_euler`,
  and at least one intermediate `path.keyframes[].transform.rotation_euler`.
- `scale`: `start_transform.scale`, `end_transform.scale`, and at least one
  intermediate `path.keyframes[].transform.scale`.
- `follow_path`: `start_transform.location`, `end_transform.location`, and at
  least two `path.points` or two `path.keyframes`; three path points are
  preferred because they explicitly encode a middle state.
- `appear` / `disappear`: `path.keyframes` at the start and end frames with
  `value.visible`, `value.hide_viewport`, `value.hide_render`, or `value.alpha`.
- `camera_move`: put the event in `camera_events`; the subject must be a camera
  id and the event must include start, middle, and end camera locations.
- `camera_orbit`: put the event in `camera_events`; the subject must be a
  camera id, `target_ids` must name the object or camera target being orbited,
  and the path should include a start point, at least one orbit midpoint, and
  an end point.

For every required event:

- `start_frame` must be at least 1.
- `end_frame` must be greater than `start_frame` and no later than
  `duration_frames`.
- `subject_ids` must be non-empty and reference scene object ids, except camera
  events, which reference camera ids.
- `expected_visual_result` must describe what the ordered frames should show.
- `animation.verifier.sampled_frames` must include the event start frame, at
  least one frame strictly between start and end, and the event end frame.
- `animation.verifier.questions` must ask temporal questions about visible
  motion, final state, and camera coverage.
- `animation.verifier.pass_criteria` must state objective pass conditions.
- Motions that can collide, carry, rest, enter containers, or pass close to
  obstacles should include explicit `contact_constraints`; natural-language
  event constraints alone are not enough.

The repository includes repeatable fixtures:

- `examples/animation_ir/translate_ball_to_box.json`
- `examples/animation_ir/rotate_windmill_blades.json`
- `examples/animation_ir/camera_orbit_showcase.json`

Run structural validation without an LLM or Blender:

```bash
python -m unittest tests.test_animation_ir_validation
```

Run a single example through the harness after Blender and model credentials are
configured:

```bash
python -m harness.runner --ir examples/animation_ir/translate_ball_to_box.json --animation
```

## Video Verification

`VideoVerifierSpec`

- `enabled`: whether to run video verification.
- `model_hint`: optional model hint. Initial default is `qwen3.5-omni`.
- `sampled_frames`: frames that must be rendered and sent as an ordered set.
- `require_preview_video`: whether to render a low-resolution preview video.
- `questions`: temporal questions for the model.
- `pass_criteria`: conditions for pass.
- `max_rounds`: maximum video-refinement rounds. Default implementation
  uses 6 so temporal/video verification can drive repeated animation repairs.
- `require_subject_visibility`: every required animated subject must be visible
  enough to judge at relevant sampled frames.
- `require_final_state_visibility`: final placement/contact states must be
  visible in the GIF/video and sampled frames, not merely implied by transform
  traces.
- `min_subject_pixel_fraction`: optional minimum visible subject size for
  temporal verification.

The video verifier should receive both visual artifacts and deterministic
metadata:

- Preview video or GIF.
- Ordered sampled frames.
- Animation spec.
- Object transform trace from sampled frames.
- Deterministic animation validation report.

This lets the system cross-check temporal model feedback against measurable
Blender state.

## Validation Reports

All verifiers should return `ValidationReport`.

`ValidationReport`

- `mode`: deterministic, vision, video, or human.
- `passed`: pass/fail.
- `issues`: list of `ValidationIssue`.
- `summary`: short human-readable summary.
- `artifacts`: paths to screenshots, videos, logs, or JSON traces.

`ValidationIssue`

- `code`: stable issue code.
- `message`: human-readable problem.
- `severity`: info, minor, major, critical.
- `target_id`: object id when applicable.
- `relation_id`: relation id when applicable.
- `frame`: animation frame when applicable.
- `suggested_fix`: repair instruction.
- `evidence`: structured measurements or model evidence.

## Structural Validation

`GenerationIR.validate()` performs first-pass structural checks:

- Duplicate object ids.
- Empty object ids.
- Unknown material references.
- Material texture-policy conflicts.
- Unknown relation subjects or objects.
- Relation verification-method mismatches.
- Unknown camera targets.
- Unknown screenshot targets, cameras, or relations.
- Invalid animation duration or fps.
- Invalid event frame ranges.
- Animation events that exceed the duration.
- Animation events that reference unknown objects or targets.
- Animation events missing action-specific start/end transforms.
- Animation events missing explicit intermediate states.
- Video verifier settings missing sampled start/middle/end frames, temporal
  questions, or pass criteria.
- Camera events whose subjects do not reference known camera ids.
- Collision proxies with invalid margins or dimensions.
- Contact constraints with unknown object ids, invalid frame ranges, negative
  gap/penetration tolerances, invalid axes, or self-contact.

This is not a substitute for Blender execution or visual/video verification. It
only ensures that the IR itself is internally coherent before generation starts.

## Penetration Validation

The first collision-aware deterministic pass is deliberately conservative and
explainable:

- It samples every event boundary, event midpoint, requested verifier sample,
  and every frame for animations up to 180 frames. Longer animations are
  subsampled to roughly 180 audit frames.
- It aggregates all mesh-like descendants for each `verianim_id` into a world-space
  bbox.
- It checks explicit `ContactConstraintSpec` windows for penetration, floating,
  missing support footprint overlap, detached contact, and failed containment.
- It runs a global nonpenetration audit across collision-enabled scene objects.
  Relation/contact pairs such as `attached_to`, `touching`, `inside`, and
  `carry_contact` are exempted from global overlap because they are governed by
  their explicit constraints.
- Explicit contact failures report a stable code, object ids, frame,
  penetration depth, axis, overlap vector, and configured tolerance. Global
  penetration failures are aggregated to one report per object pair with the
  worst frame, worst depth, sampled failing frames, and frame count, so repair
  prompts stay compact.

This will not catch every curved mesh intersection, and bbox checks can be
stricter than visual reality for concave or compound objects. The practical
benefit is that the common failure mode in current GIFs, obvious object
interpenetration or floating during animation, is now a deterministic repair
signal before the video model is asked to judge the result. Future upgrades can
replace selected proxies with Blender `BVHTree` or convex-hull checks without
changing the high-level IR contract.

## Animation Extension Contract

`GenerationIR.extension` is optional. Existing rigid IR files remain valid
without it; this feature keeps legacy rigid IR and extension IR as parallel
supported formats.

The extension contract is a narrow metadata layer, not a second harness:

- `families` classify rigid, character, deformable, fluid, or mixed concerns.
- `target_profiles` describe Blender, Unity, Maya, or other targets as
  `supported`, `unsupported`, or `degraded`.
- `verification_probes` state the evidence that can pass a family and evidence
  that is insufficient by itself.
- `simulation_caches` describe ownership, timing, status, and invalidation
  inputs for future simulation cache reuse.
- `rigid_specs`, `character_specs`, `fluid_specs`, and `prototype` keep domain
  responsibility separated while sharing the same scene and animation ids.

Format detection and bridge/comparison helpers live in `harness/serde.py`.
Runner and verifier consumers should consume parsed `GenerationIR` structures
and should not branch on raw JSON to decide whether a payload is legacy rigid IR
or extension IR.

The selected prototype is `DeformableSimulationSpec`. It requires both:

- a `deformation_statistics` probe recorded in the animation trace or
  `reports/*_deformation_statistics.json`;
- a `video` probe recorded by the video verifier before final acceptance.

Unity, Unreal, Maya, full character, mocap, IK, fluid, smoke, particle, and full
cloth/soft-body runtimes are capability-profile-only in this feature. Unsupported
requests must be reported as unsupported or deferred rather than executed.
