# LL3M Animation Extension Plan

## Current Repository Shape

This repository currently exposes the local LL3M client and Blender execution layer, not the full cloud-side multi-agent harness described in the paper.

- `main.py` polls a remote server for run events, executes returned Blender Python code, uploads rendered images, and reports execution results.
- `blender/addon.py` starts a local Blender socket server that can execute code, inspect simple scene/object metadata, and save `.blend` snapshots.
- `blender/client.py` is the Python client for talking to the Blender addon.
- `blender/headless.py` supports background Blender execution for render-heavy code.
- `config/config.yaml` still points to the discontinued LL3M cloud server.

The extension should therefore add a local or self-hosted agent harness while reusing the existing Blender execution path.

## Target Architecture

Build a staged harness around Blender code generation:

1. Planner
   - Converts the user prompt into a structured scene or animation specification.
   - Produces object lists, relationships, background requirements, camera needs, and animation events.

2. Code Generator
   - Generates modular Blender Python rather than one large script.
   - Emits object factories, environment setup, scene assembly, and animation functions.

3. Deterministic Verifier
   - Uses Blender API measurements to verify object existence, dimensions, materials, positions, relationships, keyframes, and camera coverage.

4. Vision Verifier
   - Uses rendered screenshots and a vision-capable model to judge visual quality and semantic correctness.
   - Produces concrete improvement instructions for the refiner.

5. Video Verifier
   - In the animation stage, uses rendered videos or sampled frame sequences and a video-capable model to judge temporal correctness.
   - Models such as Qwen3.5-Omni are plausible candidates if they can reliably consume video or ordered frame sequences in the deployed environment.

6. Refiner
   - Applies localized fixes based on deterministic validation reports plus visual/video critique.
   - Avoids regenerating the entire scene unless the structural plan is wrong.

## Core Intermediate Representation

Use a structured scene and animation spec as the contract between agents:

```json
{
  "scene": {
    "objects": [
      {
        "id": "table",
        "description": "wooden table",
        "expected_parts": ["top", "legs"],
        "approx_bbox": [1.2, 0.8, 0.7],
        "role": "support"
      }
    ],
    "relations": [
      {"type": "on_top_of", "subject": "cup", "object": "table"},
      {"type": "left_of", "subject": "chair", "object": "table"}
    ],
    "background": {
      "type": "room",
      "floor": "wood",
      "lighting": "soft indoor"
    },
    "camera": {
      "coverage": "all primary objects visible",
      "views": ["front", "three_quarter", "top"]
    }
  },
  "animation": {
    "duration_frames": 120,
    "events": [
      {
        "object": "ball",
        "action": "rolls_to",
        "target": "box",
        "frames": [1, 80]
      }
    ]
  }
}
```

This representation gives both deterministic validators and visual validators a stable reference.

## Phase 1: Single Object to Full Scene

### 1. Scene Planning

Add a local planner that turns the prompt into `SceneSpec`:

- Object inventory.
- Object roles and expected parts.
- Approximate size and placement hints.
- Pairwise spatial relationships.
- Background, lighting, and camera requirements.
- Screenshot plan for visual verification.

The screenshot plan matters because a visual verifier is only as good as the views it receives. The planner should request enough viewpoints to inspect geometry and relationships, not just beauty shots.

Recommended initial screenshot set:

- Front view.
- Left or right side view.
- Top-down view.
- Three-quarter perspective view.
- Close-up crops for important relations, such as "cup on table" or "chair beside desk".

### 2. Object-Level Generation and Validation

Generate each object as a separate factory function:

```python
def create_table(scene_ctx) -> dict:
    ...
    return {
        "root": table_root.name,
        "parts": ["table_top", "leg_1", "leg_2"],
        "bbox": ...
    }
```

Deterministic checks:

- Root object exists.
- Expected parts exist.
- Meshes are non-empty.
- Bounding box is plausible.
- Materials are assigned.
- Object is locally centered and suitable for later placement.

Visual checks:

- Render isolated object from multiple angles.
- Ask a vision model whether the object matches the prompt and whether any visible defects exist.
- Require the model to return structured critique:

```json
{
  "pass": false,
  "issues": [
    {
      "severity": "major",
      "object": "chair",
      "problem": "backrest is missing",
      "suggested_fix": "add a vertical backrest behind the seat"
    }
  ]
}
```

### 3. Scene Assembly

Use a `SceneAssembler` to place objects in world coordinates. Avoid letting each object script decide global placement independently.

Initial relation types:

- `on_top_of`
- `inside`
- `left_of`
- `right_of`
- `in_front_of`
- `behind`
- `near`
- `facing`
- `not_intersecting`

Deterministic checks:

- Compute world-space bounding boxes.
- Check contact distances.
- Check overlap and intersection severity.
- Check object ordering and relative positions.

Visual checks:

- Render relation-focused screenshots.
- The vision verifier checks whether the intended spatial relations are visually apparent.
- If deterministic and visual checks disagree, keep both reports. For example, a cup may be geometrically on the table but visually hidden by a bad camera angle.

### 4. Background and Camera Setup

Generate environment separately from object code:

- Floor, walls, skybox, or domain-specific environment.
- Lighting setup.
- Camera placement.
- Render settings.

Deterministic checks:

- Background objects exist.
- Lights exist and have nonzero energy.
- Camera exists and frames primary objects.
- Main objects are not behind the camera or outside view frustum.

Visual checks:

- Vision model evaluates whether the environment matches the prompt.
- It should also flag underexposure, overexposure, tiny subjects, occlusion, poor composition, or missing background elements.

### 5. Scene-Level Refinement Loop

Recommended loop:

1. Run generated Blender code.
2. Run deterministic verifier.
3. Render planned screenshots.
4. Run vision verifier.
5. Merge reports into a single repair brief.
6. Ask the refiner for localized code changes.
7. Repeat until every enabled verifier passes. A configurable safety cap
   prevents infinite runs, but the normal stop condition is verifier approval,
   not a fixed number of attempts.

The visual verifier is a blocking gate, not advisory text. It should fail
visible physical implausibility such as floating tabletop objects, detached
lamp heads or arms, impossible contact/support, severe intersection, missing
objects, poor camera coverage, or semantic mismatch. When the screenshot set is
insufficient, it should return `INSUFFICIENT_VIEW_COVERAGE` and ask for the
needed view instead of passing the scene.

The repair brief should include:

- Failing deterministic constraints.
- Vision model critique.
- Screenshot paths and view labels.
- Relevant code snippets.
- SceneSpec excerpt.

## Phase 2: Scene to Animation

For anything beyond a trivial smoke test, animation should be built on top of a
validated static scene instead of generated in one unbounded step. Direct
animation generation is still useful for fast capability tests, but the stable
production path is:

1. Plan and generate the scene.
2. Run deterministic plus screenshot-based vision verification until the scene
   passes.
3. Add `AnimationSpec` events against the validated object ids.
4. Generate or refine only the animation code.
5. Run deterministic animation checks plus temporal visual/video verification.

This separation makes verifier feedback easier to act on. A failed table leg,
floating prop, or bad camera angle is a scene problem; absent motion, reversed
motion, hidden rotation, or broken contact over time is an animation problem.

### 1. Animation Planning

Extend the planner to produce `AnimationSpec`:

- Duration and frame rate.
- Animated objects.
- Timeline events.
- Motion paths.
- Object state changes.
- Camera motion.
- Expected start, middle, and final states.

Start with deterministic keyframe animation before using physics simulation. Keyframes are easier to verify, reproduce, and repair.

### 2. Animation Code Generation

Generate animation as modular functions:

```python
def animate_ball_roll(ball, start_frame, end_frame, start_pos, end_pos):
    ball.location = start_pos
    ball.keyframe_insert(data_path="location", frame=start_frame)
    ball.location = end_pos
    ball.keyframe_insert(data_path="location", frame=end_frame)
```

Initial supported actions:

- Translate.
- Rotate.
- Scale.
- Appear/disappear.
- Follow path.
- Camera pan/orbit/dolly.

### 3. Deterministic Animation Verification

Checks:

- Required objects have animation data.
- Keyframes exist in the expected frame ranges.
- Start and end transforms match the spec.
- Sampled intermediate transforms follow the expected direction.
- Objects do not severely intersect unless intended.
- Camera sees the main subject across sampled frames.

### 4. Video/Temporal Vision Verification

Yes, the second stage should use a model that can understand video or ordered frame sequences. Qwen3.5-Omni is a reasonable candidate to evaluate, especially if its API supports video input or multi-frame visual context in your deployment.

The video verifier should receive:

- The animation spec.
- A low-resolution MP4 or GIF.
- A sequence of labeled sampled frames, for example frames 1, 30, 60, 90, 120.
- Optional optical-flow-like metadata from deterministic sampling, such as object positions per frame.

The verifier should answer structured questions:

- Does the animation show the requested action?
- Is the temporal order correct?
- Does the object move along the intended path?
- Is motion smooth enough?
- Are there unnatural jumps?
- Does the camera keep the subject visible?
- Are important interactions visible at the right time?
- What exact changes should the code refiner make?

Expected output:

```json
{
  "pass": false,
  "temporal_issues": [
    {
      "severity": "major",
      "frames": [40, 80],
      "object": "ball",
      "problem": "ball slides sideways instead of rolling toward the box",
      "suggested_fix": "align the path endpoint with the box center and add rotation keyframes around the local X axis"
    }
  ],
  "camera_issues": [
    {
      "severity": "minor",
      "frames": [90, 120],
      "problem": "subject becomes too small near the end",
      "suggested_fix": "add a slow camera dolly-in from frame 70 to 120"
    }
  ]
}
```

If the chosen video model is unreliable or expensive, use a fallback:

- Deterministic animation verifier first.
- Multi-frame image verifier second.
- Full video verifier only for final pass or high-risk cases.

### 5. Animation Refinement Loop

Recommended loop:

1. Generate animation code on top of the validated scene.
2. Run deterministic animation verifier.
3. Render sampled frames.
4. Render short video preview.
5. Run temporal vision/video verifier.
6. Merge deterministic and temporal critiques.
7. Ask the refiner for localized animation-code changes.
8. Repeat until deterministic and temporal/video verifiers both pass, subject
   to the configured safety cap.

## Screenshot and Video Sampling Strategy

Visual validation should be explicit and planned, not incidental.

For static scenes:

- Always render at least three canonical views: front, side, top.
- Add one perspective beauty view for holistic judgment.
- Add relation close-ups for important object relationships.
- Label each screenshot with camera/view metadata outside the image payload.

For animations:

- Render a low-resolution preview video.
- Sample keyframes from every important event boundary.
- Sample at least start, middle, and end.
- For interactions, sample just before, during, and just after the interaction.
- Keep a per-frame object transform trace so video-model judgments can be cross-checked.
- Use one fixed camera for sampled frames, computed from the union bounding box
  across all sampled frames. Re-centering the camera per frame hides motion and
  can create false passes.
- Prefer visible surface markings or asymmetric features when rotation itself
  must be verified. A perfectly smooth sphere can rotate correctly in the
  transform trace while still looking like it is sliding.

## Current Implementation Notes

The local harness now implements the main architecture described above:

- `harness.ir` defines the full scene/animation IR.
- `PlannerAgent` receives the full IR definition and emits structured JSON.
- `CoderAgent` and `RefinerAgent` use a compact code-generation IR to keep long
  LLM calls stable while preserving the full validation IR.
- `LLMClient` retries failed non-streaming calls with streaming, which is
  necessary for long Blender scripts on some OpenAI-compatible gateways.
- `BlenderRuntime` injects current validation/render scripts into Blender, so
  harness fixes take effect without depending on an already-loaded addon copy.
- Deterministic scene validation expands `ll3m_id` prefixes such as
  `table_top` and `table_leg_1` when computing whole-object bounding boxes.
- Screenshot verification treats `close_up` and `relation_close_up` as true
  tight inspection views.
- Static vision verification is a blocking gate for visible scene quality;
  video verification is a blocking gate for temporal correctness.

Validated examples:

- `runs/run_20260515_124204` passed a seven-object static desk scene after
  visual-loop refinement fixed floating objects, detached supports, missing
  clock details, and material mismatch.
- `runs/run_20260515_132436` passed a rolling-ball animation after the video
  loop caught visually broken contact and hidden rotation.

## Proposed New Modules

```text
harness/
  runner.py
  llm_client.py
  prompts/
  schemas.py
  planner.py
  coder.py
  refiner.py

scene/
  spec.py
  assembler.py
  verifier.py
  visual_verifier.py
  environment.py
  relations.py
  screenshot_plan.py

animation/
  spec.py
  timeline.py
  generator.py
  verifier.py
  video_verifier.py
  renderer.py

blender/
  inspection.py
  validation_scripts.py
```

## Blender Addon Extensions

Extend `blender/addon.py` and `blender/client.py` with richer inspection commands:

- `get_scene_graph`
- `get_object_bbox`
- `get_material_info`
- `get_camera_view_report`
- `get_animation_info`
- `sample_object_transforms`
- `run_validation`
- `render_view_plan`
- `render_animation_preview`

These commands should return structured JSON where possible.

## Milestones

1. Local single-object harness
   - Generate one object.
   - Execute in Blender.
   - Run deterministic object verifier.
   - Render multi-view screenshots.
   - Run vision verifier.
   - Refine once or twice.

2. Multi-object scene harness
   - Generate object factories independently.
   - Assemble a scene.
   - Verify object relationships.
   - Use vision verification for visual correctness and composition.

3. Background and camera
   - Generate environment separately.
   - Verify camera coverage and lighting.
   - Use vision model to critique screenshot set quality.

4. Simple keyframe animation
   - Animate validated scenes with translate/rotate/scale/camera moves.
   - Verify keyframes and sampled transforms.
   - Render sampled frames.

5. Video-verified animation
   - Render preview videos.
   - Use Qwen3.5-Omni or another video-capable model for temporal critique.
   - Feed structured critique back into animation refinement.

6. Advanced animation
   - Add paths, constraints, procedural motion, and selective physics.
   - Keep deterministic verification before visual/video verification.

## Implementation Priority

Start with Phase 1 plus visual verification:

1. Add `SceneSpec` schema.
2. Add local harness runner.
3. Add object-level deterministic verifier.
4. Add screenshot planner and renderer.
5. Add vision verifier interface.
6. Add scene relation verifier.
7. Add scene refiner.

Only then move to animation:

1. Add `AnimationSpec`.
2. Add keyframe generator.
3. Add deterministic animation verifier.
4. Add sampled-frame renderer.
5. Add video verifier interface.
6. Add animation refiner.

The key design principle is to combine objective Blender API checks with model-based visual critique. Deterministic checks catch measurable failures; visual and video models catch semantic, aesthetic, composition, camera, and temporal failures that geometry alone cannot validate.
