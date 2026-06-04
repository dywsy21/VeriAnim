# Blender 4.5.4 High-Value API Notes for Agents

This file summarizes official Blender 4.5 API documentation into coding rules
for the local VeriAnim animation harness. It is deliberately concise so it can be
retrieved directly by planner, coder, verifier, and refiner agents.

Sources are official Blender pages listed in `blender_4_5_official_sources.md`.

## Version Rule

Use Blender API docs under:

- https://docs.blender.org/api/4.5/

Do not use `current` as the default source for this project. The user target is
Blender 4.5.4 LTS. Treat patch version 4.5.4 as Blender 4.5 API behavior plus
bug fixes.

## General Code Generation Rules

Prefer deterministic data API calls over context-sensitive operators.

Good defaults:

- Use `bpy.data.meshes.new`, `mesh.from_pydata`, `mesh.update`, and
  `bpy.data.objects.new` for generated geometry.
- Link objects explicitly to a collection with `collection.objects.link(obj)`.
- Set transforms through `obj.location`, `obj.rotation_euler`, `obj.scale`, or
  `obj.matrix_world`.
- Use stable object names and custom properties for IR ids.
- Return metadata from object factory functions: root object, created parts,
  material ids, bbox hints, and validation hooks.

Use `bpy.ops` only when the API task is naturally operator-driven, such as
rendering, importing/exporting, mode switching, or a Blender operation with no
reasonable data API equivalent. The official operator gotcha page warns that
operators can fail their `poll()` check when context is wrong.

## Scene Organization Pattern

Generated code should create predictable collections:

```python
root = bpy.data.collections.new("verianim_scene")
bpy.context.scene.collection.children.link(root)

objects_col = bpy.data.collections.new("objects")
environment_col = bpy.data.collections.new("environment")
root.children.link(objects_col)
root.children.link(environment_col)
```

Each object factory should link its objects into a provided collection instead
of relying on the active context.

Recommended custom properties:

```python
obj["verianim_id"] = "cup"
obj["verianim_role"] = "primary"
obj["verianim_part"] = "handle"
```

These properties make deterministic and visual-verification reports easier to
map back to IR ids.

## Mesh Creation Rules

For simple procedural geometry, prefer:

```python
mesh = bpy.data.meshes.new("cup_body_mesh")
mesh.from_pydata(vertices, edges, faces)
mesh.update(calc_edges=True)
obj = bpy.data.objects.new("cup_body", mesh)
collection.objects.link(obj)
```

Important details from the official Mesh docs:

- `Mesh.from_pydata(vertices, edges, faces)` builds mesh data from Python lists.
- Empty edge lists can be inferred from faces.
- Call `mesh.update()` after mutating mesh data.

For complex mesh edits:

- Use `bmesh` for procedural construction and edit-mode workflows.
- Be careful with edit mode: object-mode mesh data can be out of sync while the
  user is in edit mode.
- A verifier/refiner should either exit edit mode, call the correct bmesh update
  path, or work directly with edit-mode data.

## Object and Transform Rules

Use `bpy.types.Object` for scene assembly and validation.

Useful object fields and methods:

- `obj.location`, `obj.rotation_euler`, `obj.scale`
- `obj.matrix_world`
- `obj.bound_box`
- `obj.data`
- `obj.type`
- `obj.animation_data`
- `obj.evaluated_get(depsgraph)`

Bounding boxes in `obj.bound_box` are object-space. Convert corners with
`obj.matrix_world @ Vector(corner)` before checking world-space relations.

For evaluated geometry after modifiers or animation:

```python
depsgraph = bpy.context.evaluated_depsgraph_get()
evaluated = obj.evaluated_get(depsgraph)
```

Use evaluated state in validators when modifiers, constraints, or animation are
involved.

## Materials and Shading Rules

Use material ids from the IR. Assign materials explicitly to mesh slots.

Basic material pattern:

```python
mat = bpy.data.materials.new("warm_ceramic")
mat.diffuse_color = (0.9, 0.82, 0.72, 1.0)
obj.data.materials.append(mat)
```

For richer materials:

- Set `mat.use_nodes = True`.
- Work through `mat.node_tree`.
- Use the Principled BSDF node for common surface appearance.
- Blender UI and node display names can be localized. Do not retrieve shader
  nodes with `nodes.get("Principled BSDF")`; find them by `node.type ==
  "BSDF_PRINCIPLED"` and set `mat.diffuse_color` as well as shader inputs.

Robust localized-node-safe pattern:

```python
mat.diffuse_color = base_color
principled = next(
    (node for node in mat.node_tree.nodes if node.type == "BSDF_PRINCIPLED"),
    None,
)
if principled and "Base Color" in principled.inputs:
    principled.inputs["Base Color"].default_value = base_color
```

Keep material generation conservative at first. Visual verification can ask for
improvements such as "more metallic", "less glossy", or "wood grain needed".

## Camera and Screenshot Rules

The IR screenshot plan should drive camera creation. Do not rely on one default
camera angle for visual validation.

Static scene minimum:

- front
- side
- top
- three-quarter
- close-up for important relations

Camera setup should be explicit:

```python
camera_data = bpy.data.cameras.new("camera_three_quarter_data")
camera = bpy.data.objects.new("camera_three_quarter", camera_data)
collection.objects.link(camera)
bpy.context.scene.camera = camera
```

Use a deterministic look-at helper based on `mathutils.Vector` to point cameras
at target objects or target points. Validators should check that required target
objects are inside the camera view before sending screenshots to a vision model.

## Lighting and Environment Rules

Use explicit lights. Do not rely on default scene lighting.

Basic area light:

```python
light_data = bpy.data.lights.new("key_light_data", type="AREA")
light_data.energy = 500
light_data.size = 4
light = bpy.data.objects.new("key_light", light_data)
collection.objects.link(light)
```

Environment should be separate from object factories:

- floor
- walls or sky
- world background
- key/fill/rim lights where appropriate

This separation lets the refiner change camera/lighting/background without
regenerating modeled objects.

## Rendering Rules

Use the scene render settings before invoking render operators:

```python
from blender import verianim_utils as verianim

scene = bpy.context.scene
verianim.configure_render(scene, width=1280, height=720, fps=24, engine="workbench")
scene.frame_set(frame)
scene.camera = camera
scene.render.filepath = output_path
bpy.ops.render.render(write_still=True)
```

Prefer Workbench for validation/inspection renders. It is less sensitive to
overpowered lights, glossy reflections, and transparent exterior shells that can
hide interior objects from vision checks.

For animation preview video, configure frame range and output format explicitly.
The video verifier should receive both a preview video and sampled still frames.

## Animation Rules

For the first animation milestone, prefer simple keyframes:

```python
obj.location = start
obj.keyframe_insert(data_path="location", frame=start_frame)
obj.location = end
obj.keyframe_insert(data_path="location", frame=end_frame)
```

Then optionally repair interpolation through F-Curves:

```python
if obj.animation_data and obj.animation_data.action:
    for fcurve in obj.animation_data.action.fcurves:
        for keyframe in fcurve.keyframe_points:
            keyframe.interpolation = "BEZIER"
        fcurve.update()
```

Important 4.5 note:

- Blender 4.5 inherits the layered Action system introduced in Blender 4.4.
- Simple `keyframe_insert` workflows remain the safest starting point.
- Advanced direct Action editing should retrieve the official `Action`,
  `AnimData`, and `FCurve` docs before generating code.

Validation should inspect:

- whether animated objects have `animation_data`
- whether an action exists
- whether F-Curves exist for expected data paths
- whether frame ranges match the IR
- whether sampled transforms match intended motion

## Relationship Verification Rules

Use deterministic geometry before asking a vision model.

World-space bbox helper:

```python
from mathutils import Vector

def world_bbox(obj):
    return [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
```

Relation checks:

- `on_top_of`: subject bottom z is near object top z and x/y projections overlap.
- `left_of` / `right_of`: compare bbox centers along the selected axis.
- `near`: compare bbox center distance.
- `not_intersecting`: compare bbox overlap first, then use BVHTree for precise
  mesh intersection when needed.

Use `mathutils.bvhtree.BVHTree` for higher-confidence intersection checks on
evaluated mesh data.

Support/contact placement recipes:

- Horizontal support: create and scale both objects first. Compute
  `support_top_z` from the support world bbox, compute the subject half height
  from its world bbox, then set `subject.location.z = support_top_z +
  subject_half_height + margin`. Also move x/y so the subject footprint overlaps
  the support footprint before checking z.
- Animation final state: if the prompt says "stops near", "ends on", or "lands
  in", satisfy that at the final keyframe and sampled final frame. Do not move
  the static initial pose unless the relation must hold at frame 1.
- Ramp sliding: do not treat a slanted ramp as a horizontal `on_top_of` stack.
  Define start/end surface points along the ramp length and offset the moving
  object's center from the surface along the ramp normal by its radius or half
  extent. Keep start, middle, and end sampled frames on or just above the ramp
  without bbox penetration.

Refiner issue-code playbook:

- `RELATION_ON_TOP_OF_FAILED`: fix x/y overlap first, then align subject bottom
  to the reported support top z.
- `CONTACT_CONSTRAINT_SUPPORT_OVERLAP_FAILED`: move the subject footprint into
  the support footprint before changing z.
- `CONTACT_CONSTRAINT_SUPPORT_PENETRATION`: lift by the reported penetration or
  negative gap plus a small margin.
- `CONTACT_CONSTRAINT_PENETRATION` / `ANIMATION_GLOBAL_PENETRATION`: separate
  along the reported axis or shallowest overlap axis by penetration depth plus a
  small margin and update all affected keyframes.
- `RELATION_DISTANCE_FAILED` in an animation prompt may be a final-state
  problem; repair the end keyframe/end transform when the language says the
  object stops or ends near a target.

## Visual Verification Rules

Vision verification should not receive unlabeled screenshots only. Send:

- screenshot file paths
- view ids
- camera/view type
- target object ids
- relation ids under inspection
- original prompt
- relevant SceneSpec excerpt
- deterministic validation report

Ask the model for structured output:

```json
{
  "pass": false,
  "issues": [
    {
      "severity": "major",
      "target_id": "chair",
      "relation_id": null,
      "problem": "backrest is missing",
      "suggested_fix": "add a vertical backrest behind the seat"
    }
  ]
}
```

The refiner should receive only relevant code snippets, not the full generated
script unless the issue affects global scene assembly.

## Video Verification Rules

For animation, send the video model:

- preview video or GIF
- ordered sampled frames
- frame labels
- AnimationSpec events
- sampled transform trace
- deterministic animation report

Use a video-capable model such as Qwen3.5-Omni if the deployment supports video
or ordered multi-frame input. If cost or reliability is a problem, run full video
verification only on final candidates and use sampled-frame verification during
intermediate refinement.

## High-Frequency Failure Patterns

Operator context failure:

- Symptom: `poll()` failure or context error.
- Fix: use data API, set active object/view layer explicitly, or avoid operator.

Edit-mode mesh mismatch:

- Symptom: validator sees stale mesh data.
- Fix: leave edit mode or use bmesh edit-mode update APIs.

Object-space bbox used as world-space bbox:

- Symptom: relation verifier says positions are wrong after transforms.
- Fix: multiply bbox corners by `obj.matrix_world`.

Render path mismatch:

- Symptom: render succeeds but no image uploaded.
- Fix: set `scene.render.filepath` to the harness output path before rendering.

Animation exists but does not move:

- Symptom: object has keyframes but sampled transforms stay constant.
- Fix: verify F-Curves data paths, frame range, and interpolation; sample with
  `scene.frame_set(frame)` before reading transforms.
