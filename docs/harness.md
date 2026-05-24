# Local Harness Implementation

The local harness is separate from the original LL3M cloud-client flow.
Use it with:

```bash
python -m harness.runner --text "a cup on a wooden table in a small kitchen"
```

Or use the interactive TUI:

```bash
python -m harness.tui
```

In the TUI, the first message creates a new scene. Later messages are treated as
multi-turn change requests against the current Blender scene and script, for
example "make the chair taller" or "add a small lamp on the table". Blender is
updated after each turn when the generated script executes successfully.

For animation:

```bash
python -m harness.runner --text "a red ball rolls into a cardboard box" --animation
```

To use a previously planned IR:

```bash
python -m harness.runner --ir runs/run_xxx/ir.json
```

To run the scoped deformable extension prototype:

```bash
python -m harness.runner --ir examples/animation_ir/deformable_cloth_prototype.json --animation --skip-video
```

The `--skip-video` smoke run validates schema, static-scene-first generation,
deterministic animation checks, sampled frames, and structured deformation
statistics. It does not satisfy final deformable acceptance by itself. Final
prototype acceptance requires running without `--skip-video` with a configured
video-capable verifier.

## Dependencies

The harness uses open-source libraries:

- `python-dotenv` for `.env` loading.
- `litellm` for unified LLM and multimodal LLM calls.

Install with:

```bash
pip install -r requirements.txt
```

## Environment

Copy `.env.example` to `.env` and fill the API keys for the providers you use.
Different agents can use different models:

- `LL3M_PLANNER_MODEL`
- `LL3M_CODER_MODEL`
- `LL3M_REFINER_MODEL`
- `LL3M_VISION_MODEL`
- `LL3M_VIDEO_MODEL`

Each agent can also use its own endpoint and key:

- `LL3M_PLANNER_API_BASE`, `LL3M_PLANNER_API_KEY`, `LL3M_PLANNER_API_VERSION`, `LL3M_PLANNER_PROVIDER`
- `LL3M_CODER_API_BASE`, `LL3M_CODER_API_KEY`, `LL3M_CODER_API_VERSION`, `LL3M_CODER_PROVIDER`
- `LL3M_REFINER_API_BASE`, `LL3M_REFINER_API_KEY`, `LL3M_REFINER_API_VERSION`, `LL3M_REFINER_PROVIDER`
- `LL3M_VISION_API_BASE`, `LL3M_VISION_API_KEY`, `LL3M_VISION_API_VERSION`, `LL3M_VISION_PROVIDER`
- `LL3M_VIDEO_API_BASE`, `LL3M_VIDEO_API_KEY`, `LL3M_VIDEO_API_VERSION`, `LL3M_VIDEO_PROVIDER`

LLM calls use `LL3M_LLM_TIMEOUT_SECONDS` as a global timeout. Override a single
agent with `LL3M_PLANNER_TIMEOUT_SECONDS`, `LL3M_CODER_TIMEOUT_SECONDS`,
`LL3M_REFINER_TIMEOUT_SECONDS`, `LL3M_VISION_TIMEOUT_SECONDS`, or
`LL3M_VIDEO_TIMEOUT_SECONDS`.

Leave `LL3M_*_MAX_TOKENS` empty by default. The harness will not send a
`max_tokens` cap unless that agent-specific value is set, which avoids
accidentally clipping long Blender scripts or verifier JSON on providers with
larger output windows.

Leave these empty when the global provider environment variables are sufficient.
Set them when, for example, coder uses OpenAI, vision uses an OpenRouter VLM, and
video uses a DashScope/Qwen Omni endpoint.

External texture search is controlled by:

- `LL3M_TEXTURE_SEARCH_ENABLED` (default `true`)
- `LL3M_TEXTURE_SEARCH_CANDIDATE_LIMIT` (default `4`)
- `LL3M_TEXTURE_SEARCH_TIMEOUT_SECONDS` (default `20`)

The texture selector reuses the configured VISION model, so
`LL3M_VISION_SUPPORTS_IMAGES` must remain enabled for automatic texture approval.

The default video model hint is `dashscope/qwen-omni-turbo`. Replace it with the
LiteLLM model name that matches your deployed Qwen Omni or other video-capable
endpoint.

Long Blender code generations can exceed the stable non-streaming response
window of some OpenAI-compatible gateways. For non-JSON code-generation calls,
the LLM client now streams first and disables LiteLLM's internal retries
(`num_retries=0`) so a single harness call does not silently become several
identical provider requests. JSON calls use a single structured request, with
one compatibility retry only when the provider explicitly rejects
`response_format`. `CoderAgent` and `RefinerAgent` also receive a compact
code-generation IR instead of the full verification IR; the full IR is still
written to disk and used by validators.

Generated scripts can import `from blender import ll3m_utils as ll3m` for
shared helpers such as Workbench render setup, scene clearing, collections,
materials, cameras, lights, and primitive mesh objects. This keeps model output
shorter and reduces repeated Blender API mistakes.

`LL3M_MAX_STAGNANT_REFINEMENT_ROUNDS` stops a verifier loop when the same
failure signature repeats without progress, preventing repeated expensive
refiner calls on an unchanged issue.

When failed validation screenshots are available, the refiner sends them to the
configured refiner model. This is no longer silently retried as a second
text-only Opus call if the multimodal request fails; the error is surfaced so
provider capability/configuration problems are visible in the run.

## Pipeline

1. `PlannerAgent`
   - Converts prompt into `GenerationIR` (IR v0.2).
   - Includes screenshot and video verification plans.
   - Adds collision proxies for required physical objects and contact
     constraints for animation windows that involve support, carrying,
     containment, or nonpenetration.

2. `MaterialAgent`
   - Runs after planning and before coding.
   - Uses planner-provided `MaterialSpec.texture_policy`, `needs_texture`, and
     `texture_query` values to search FreeStockTextures public pages for external image
     textures when a material benefits from natural grain or surface detail.
   - Downloads a small candidate set, asks the configured VISION model to
     approve suitability, and writes only approved `texture_source` assets back
     into the IR. If no candidate passes, the material is explicitly marked as
     not using an image texture and falls back to `base_color` plus shader
     parameters. `texture_policy=solid_only` or `forbidden` is treated as a hard
     no-texture constraint.

3. `CoderAgent`
   - Uses RAG notes from `docs/rag`.
   - Generates one Blender 4.5.4 Python script.

4. `BlenderRuntime`
   - Executes generated code through the existing Blender addon socket server.
   - Runs deterministic validation inside Blender.
   - Renders screenshot views and animation sampled frames.

5. `VisionVerifierAgent`
   - Sends labeled screenshots to a multimodal model through LiteLLM.
   - Produces `ValidationReport`.
   - Acts as a blocking gate in the refinement loop. If it reports visual
     issues such as floating parts, detached connectors, missing contact, poor
     camera coverage, or semantic mismatch, the script is refined and the scene
     is rendered again.

6. `VideoVerifierAgent`
   - Sends ordered sampled frames plus video metadata to a temporal/multimodal
     model through LiteLLM.
   - Produces `ValidationReport`.

7. `RefinerAgent`
   - Repairs the Blender Python script from execution errors and validation
     reports.

The harness repeats execute, deterministic validation, screenshot rendering,
visual/video verification, and code refinement until every enabled verifier
passes. The loop has safety caps to avoid infinite runs:

- `LL3M_MAX_REFINEMENT_ROUNDS` for the baseline deterministic loop.
- `LL3M_MAX_VISUAL_REFINEMENT_ROUNDS` for visual-verifier-gated scene repair.
- `LL3M_MAX_VIDEO_REFINEMENT_ROUNDS` for video-verifier-gated animation repair.
- `LL3M_RENDER_GIF_EACH_ROUND` to render a complete GIF and MP4 preview during
  every video verification pass. Keep this `false` for sampled-frame-only
  experiments; set it `true` when the configured video model directly reads
  video input.

The IR can also set `scene.verifier.visual.max_rounds` and
`animation.verifier.max_rounds`; the harness uses the largest applicable cap.

## Scene-Aware Screenshot Verification

Visual verification is implemented as scene-aware inspection, not as a single
beauty render. The planner writes a screenshot plan into
`scene.verifier.screenshot_plan`; each view can name target object ids,
relation ids, view type, camera id, and crop intent. The runtime then normalizes
that plan before rendering:

- If the plan has too few views, `BlenderRuntime` adds canonical inspection
  views such as three-quarter, relation close-up, side/support, and top/layout.
- Target ids are resolved through `ll3m_id`, then expanded to all matching mesh
  parts. This avoids framing only a parent Empty and missing the actual object
  geometry.
- Relation-focused views inherit the subject/object ids of the relation when
  the planner did not provide explicit targets.
- IR v0.2 views can also state `purpose`, `must_show_full_targets`, and
  `min_subject_pixel_fraction`, so the screenshot plan records why a view is
  needed and whether cropping is acceptable.
- Small scenes use a tighter camera radius so tabletop objects are large enough
  for the verifier to inspect contacts, attachments, and floating parts.

Screenshot rendering now uses an injected inspection script first, then falls
back to the addon command only if needed. This keeps verification behavior
current even when Blender is running an older loaded addon. The injected script
temporarily standardizes render conditions for verifier legibility:

- It uses an inspection render path with stable resolution and camera framing.
- It defaults to Workbench rendering so reflective or transparent shells do not
  hide interior geometry from the vision verifier.
- It clamps extreme world/light settings and restores the original scene
  settings after screenshots are written.
- It computes bounding boxes from all target mesh parts, not just object roots.
- It labels every output path by view id, and the verifier receives a screenshot
  manifest with path order and names.

Before visual verification, deterministic validation also checks that expected
materials exist and that material colors were actually applied. This catches a
Blender 4.5.4 localization pitfall: generated code must find the Principled
shader by `node.type == "BSDF_PRINCIPLED"` instead of localized display names
such as `"Principled BSDF"`.

`VisionVerifierAgent` receives the original prompt, the SceneSpec excerpt, the
deterministic report, and the ordered screenshot manifest. Its JSON report is a
blocking `ValidationReport`. Any major or critical issue, including floating
parts, detached connectors, missing objects, insufficient view coverage, bad
lighting, or semantic mismatch, causes the harness to refine code and rerun the
entire execute-validate-render-verify pass.

When screenshots exist, `RefinerAgent` receives those failed screenshots as
multimodal context in addition to the JSON reports. This is important for
layout repair: text reports often describe a symptom, while the image tells the
coder which direction to move an object, whether a contact point is visibly
wrong, and whether a verifier complaint is caused by camera occlusion.

The planner also normalizes ambiguous spatial language. For example, if the
user says an object is "beside" or "next to" another object, the harness does
not force a left/right relation unless the prompt explicitly says left or right.
It treats that relationship as symmetric `near` plus any existing
`not_intersecting` constraints.

IR v0.2 separates relation semantics from relation checking with
`SpatialRelationSpec.verification_method`. Horizontal support can use
`bbox_contact`; hinges and connectors can use `attachment`; slanted or occluded
contacts can use `visual_only` plus required relation-focused screenshots. This
prevents cases like slanted ramp supports from being misjudged by a horizontal
bottom-vs-top bbox rule.

## Animation Verification

Animation generation uses the same verifier-gated loop as static scenes, with
extra temporal checks:

- The planner emits `AnimationSpec` events with object ids, frame ranges,
  actions, optional start/end transforms, and sampled frame requirements.
- IR v0.2 events also carry `visibility_requirements`; the video verifier
  treats `require_subject_visibility` and `require_final_state_visibility` as
  blocking requirements instead of relying on transform traces alone.
- IR v0.2 objects also carry `collision` proxies, and animation specs/events
  can carry `contact_constraints`. These make common animation failures such as
  object penetration, floating supports, detached carried objects, and failed
  containment measurable before the video verifier runs.
- The harness normalizes animation verifier settings so every animation run has
  start, midpoint, end, and event-boundary frames.
- `CoderAgent` is instructed to create simple explicit keyframes first:
  `location`, `rotation_euler`, or `scale` at event start/end frames, then set
  interpolation on generated F-Curves.
- Deterministic animation validation samples object transforms at event
  boundaries, checks that required F-Curves exist, verifies that motion is not
  static, and compares final transforms against explicit `end_transform` values
  when present.
- Deterministic animation validation now also runs collision-aware audits:
  explicit contact constraints are checked at their start, midpoint, end, and
  relevant sampled frames; then a global nonpenetration pass samples the
  animation and reports one aggregated `ANIMATION_GLOBAL_PENETRATION` per
  object pair with worst frame, object pair, penetration depth, overlap vector,
  axis, tolerance, sampled failing frames, and failing frame count.
- Pick-and-place style events are treated as interaction events, not just
  independent object motion. The planner is asked to expose a gripper or
  end-effector as an object id when possible and attach it through
  `target_ids`; deterministic validation then checks bounding-box gaps between
  the carried object and the gripper/arm at start, midpoint, and end frames.
  This catches cases where a package moves along the intended path while the
  gripper remains visibly detached.
- Sampled animation screenshots are rendered with a fixed camera computed from
  the union bounding box across all sampled frames. This is important: if the
  camera re-centers on the moving object every frame, the visual verifier cannot
  see the motion.
- `VideoVerifierAgent` receives an MP4/GIF preview, the ordered sampled frames,
  the animation spec, and the deterministic transform trace. Before using
  sampled frames, it sends a video-only capability probe. If the configured
  model cannot see the video/GIF attachment, animation verification fails with
  `VIDEO_INPUT_UNSUPPORTED` instead of silently falling back to screenshot-only
  validation.
- If animation verification fails, the sampled frames are included in the
  multimodal refiner prompt so the coding model can see the temporal error it
  needs to repair.
- Every passed animation run writes a complete final GIF to
  `animation/final/animation.gif` and, when `ffmpeg` is available, an MP4
  preview to `animation/final/animation.mp4`. Validation rounds also produce
  previews when `LL3M_RENDER_GIF_EACH_ROUND=true`.

When an IR contains `extension.prototype`, deterministic animation validation
also records `deformation_statistics` in `reports/*_animation_trace.json`. The
session writes a narrow `reports/*_deformation_statistics.json` artifact when
those statistics are present. Existing rigid animation trace and report paths
are unchanged.

The static vision verifier and video verifier have separate responsibilities.
For animation runs, the static verifier judges object presence, support/contact
at sampled screenshot frames, camera coverage, and visible geometry defects. It
should not fail a run solely because temporal smoothness or the full motion is
not proven from still screenshots; those issues belong to the video verifier.

Nontrivial animation now uses two-stage generation by default when the CLI or
TUI requests animation:

1. Generate and verifier-gate the static scene first.
2. Add animation on top of that validated scene.
3. Run deterministic animation validation plus video/multi-frame verification.
4. Feed sampled frames and transform traces into the refiner until both static
   and temporal verifiers pass.

The run directory records both phases. `ir_scene_stage.json` is the static-only
IR used for scene generation, `ir_animation_stage.json` is the full animation
IR, `code/generated_scene_stage.py` is the validated scene script, and
`code/generated_animation_stage.py` is the first animation-bearing script.

If the static scene stage does not pass, the harness skips animation generation
instead of trying to animate a broken scene. This avoids mixing object
construction failures with animation failures. In the
May 15, 2026 validation run, the video loop caught two issues that deterministic
checks alone could not: a rolling ball that visually separated from the table,
and a smooth featureless sphere whose rotation was mathematically present but
not visually observable. The refiner fixed these by improving contact/table
assembly and adding visible surface markings.

## Validation Runs

Recent successful end-to-end runs:

- `runs/run_20260515_124204`: complex static study-desk scene with desk, lamp,
  notebook, mug, plant, books, and clock. The loop repaired detached supports,
  floating tabletop props, missing clock-face details, and material mismatch
  before passing visual verification.
- `runs/run_20260515_132436`: animated red ball rolling left-to-right across a
  blue table. The loop passed deterministic scene checks, deterministic
  animation checks, scene vision verification, and video verification after two
  refinement rounds.

## Outputs

Each run creates:

```text
runs/run_YYYYMMDD_HHMMSS/
  ir.json
  code/
    generated_scene.py
    refined_round_*.py
    final_scene.py
  reports/
  screenshots/
  animation/
  logs/
```

## Blender Requirement

Open Blender 4.5.4, install the existing `blender/addon.py`, and start the LL3M
server in Blender before running the harness. The harness talks to the addon on
`LL3M_BLENDER_HOST` and `LL3M_BLENDER_PORT`.

The addon now exposes structured commands used by the local harness:

- `get_scene_graph`
- `get_object_bbox`
- `get_material_info`
- `get_camera_view_report`
- `get_animation_info`
- `sample_object_transforms`
- `run_validation`
- `render_view_plan`
- `render_animation_preview`

Reinstall or reload `blender/addon.py` in Blender after pulling these changes.
