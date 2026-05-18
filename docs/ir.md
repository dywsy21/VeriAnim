# LL3M Scene and Animation IR

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
- `version`: IR version. Current version is `0.1`.
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
- `generation_notes`: extra instructions for the code generator.
- `visual_check_prompts`: object-specific questions for the vision verifier.

`ObjectPartSpec`

- `id`: stable part id.
- `description`: semantic part description.
- `required`: whether the part must exist.
- `material_id`: optional material reference.
- `expected_count`: expected number of repeated parts.
- `dimension`: optional size bounds for the part.

## Materials

`MaterialSpec`

- `id`: stable material id.
- `description`: material description.
- `base_color`: RGBA tuple.
- `metallic`, `roughness`, `alpha`: Blender-style material hints.
- `texture_hints`: natural language texture hints.
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
- `visual_priority`: whether the relation must be obvious in screenshots.

Deterministic verification should measure these with Blender world-space
bounding boxes. Vision verification should judge whether the relation is visible
and semantically clear.

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

`RenderSpec`

- `resolution`: width and height.
- `engine`: eevee, cycles, or workbench.
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
- `constraints`: event-specific constraints.

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
- Unknown relation subjects or objects.
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

This is not a substitute for Blender execution or visual/video verification. It
only ensures that the IR itself is internally coherent before generation starts.
