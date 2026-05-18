# IR Design Audit

This audit records why each IR component exists and what high-level changes were
made after the recent medium-difficulty animation runs.

## Findings From Recent Runs

- Static and animation stages must remain separate. Static generation must not
  infer keyframes from the original natural-language prompt.
- Relation type alone is insufficient. Horizontal support, slanted support,
  hinges, loose proximity, and occluded contacts need different verification
  strategies.
- Visual/video verification depends on evidence quality. Camera target size,
  cropping, relation close-ups, and final-state visibility must be represented
  in the IR, not only in verifier prompts.
- Texture use is a policy decision. If the user asks for solid colors or no
  external textures, planner output and texture resolution must honor that as a
  hard constraint.
- Animation transform traces can be correct while the visual result is not
  verifiable. Video verification needs explicit subject and final-state
  visibility requirements.

## Component Justification

`GenerationIR`

- Rationale: single contract shared by planner, coder, verifiers, refiner, and
  artifacts.
- Invariant: all ids referenced by relations, cameras, screenshots, and
  animation events must resolve to declared objects or cameras.
- Improvement: version bumped to `0.2` to mark explicit verification semantics.

`SourcePrompt`

- Rationale: preserves original user intent, negative prompts, reference images,
  and hard constraints.
- Boundary: it is not executable by itself. Static code generation uses a
  synthesized scene-only prompt to prevent animation leakage.

`SceneSpec`

- Rationale: complete static baseline that can be generated and verified before
  animation.
- Boundary: anything that changes over time belongs in `AnimationSpec`, not in
  scene generation.

`ObjectSpec` and `ObjectPartSpec`

- Rationale: root objects are stable handles for placement, relations, and
  animation. Parts describe required geometry inside an object.
- Boundary: if a part must move independently or be verified as a contact target,
  it should be promoted to an `ObjectSpec`.

`MaterialSpec`

- Rationale: separates visual surface intent from object geometry and allows
  texture resolution before code generation.
- Improvement: `texture_policy` makes user intent explicit: `auto`, `required`,
  `forbidden`, or `solid_only`.

`SpatialRelationSpec`

- Rationale: gives deterministic and visual verifiers object-level constraints
  with stable ids.
- Improvement: `verification_method` separates semantic relation from checking
  strategy. This prevents slanted ramps or hinges from being forced through
  horizontal `on_top_of` bbox logic.

`EnvironmentSpec` and `LightSpec`

- Rationale: background and lighting affect verifier reliability as much as
  aesthetics. Bad lighting can create false negatives or false positives.
- Boundary: lighting should make verification legible before it tries to be
  cinematic.

`CameraSpec` and `ScreenshotPlan`

- Rationale: screenshot verification is only grounded when views are planned.
- Improvement: target size, crop allowance, full-target visibility, and view
  purpose are now expressible.

`VerificationPlan`

- Rationale: deterministic, vision, and video gates have distinct jobs and
  should report failures against the same ids.
- Boundary: visual verification is not advisory; it gates scene acceptance.
  Video verification gates temporal acceptance.

`AnimationSpec`

- Rationale: animation is an extension over a validated scene. Events reference
  existing ids and should be locally repairable.
- Improvement: event-level `visibility_requirements` and video-level final-state
  visibility flags make it invalid to pass an animation whose final state is
  off-camera or occluded.

`PipelineStageSpec`

- Rationale: encodes the production rule: static scene first, animation second.
- Boundary: the animation stage freezes scene geometry unless an explicit user
  revision changes the static scene.

## Implementation Changes

- Added `RelationVerificationMethod` and relation fields:
  `verification_method`, `contact_points`, `expected_clearance`.
- Added `TexturePolicy` and `MaterialSpec.texture_policy`.
- Added camera/screenshot framing fields:
  `min_subject_pixel_fraction`, `allow_subject_crop`,
  `must_show_full_targets`, and `purpose`.
- Added video visibility fields:
  `require_subject_visibility`, `require_final_state_visibility`,
  `min_subject_pixel_fraction`.
- Added `AnimationEventSpec.visibility_requirements`.
- Updated planner prompts and JSON skeleton to produce the new fields.
- Updated deterministic relation validation to respect `visual_only`,
  `distance`, and `attachment` methods.
- Updated material texture selection to respect `forbidden` and `solid_only`.

## Remaining Work

- Add pixel-level camera coverage checks for `min_subject_pixel_fraction`.
- Add relation-specific deterministic checks for `bbox_order` and explicit
  `contact_points`.
- Add object-level part verification when parts are not promoted to root objects.
- Add tests for IR v0.2 normalization and old v0.1 compatibility.
