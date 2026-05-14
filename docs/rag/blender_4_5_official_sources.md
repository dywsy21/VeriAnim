# Blender 4.5 Official API Source Index

This file is the seed source map for a Blender 4.5.4 LTS RAG. Blender 4.5.4 is
part of the Blender 4.5 LTS line, so the correct Python API baseline is the
official Blender 4.5 API documentation, not `current`.

Primary documentation root:

- https://docs.blender.org/api/4.5/

Release and LTS references:

- https://www.blender.org/download/lts/
- https://www.blender.org/download/lts/4-5/
- https://www.blender.org/download/releases/4-5/
- https://developer.blender.org/docs/release_notes/4.5/
- https://developer.blender.org/docs/release_notes/4.5/python_api/

The official LTS page states that LTS releases receive critical fixes and do not
receive new features or API changes. For code generation, treat 4.5.4 as Blender
4.5 API plus bugfixes.

## P0: Always Include for Code Generation

These pages should be available to coder and refiner agents in most runs.

| Source | URL | Use When | Why It Matters |
| --- | --- | --- | --- |
| API Index | https://docs.blender.org/api/4.5/ | Any Blender code generation | Version-locked entry point and module list. |
| Quickstart | https://docs.blender.org/api/4.5/info_quickstart.html | Any agent new prompt, examples, animation basics | Covers key API concepts and simple script structure. |
| API Overview | https://docs.blender.org/api/4.5/info_overview.html | Addon integration, registration, execution context | Explains Python integration and Blender's runtime environment. |
| Best Practice | https://docs.blender.org/api/4.5/info_best_practice.html | Any generated code | Style and efficiency conventions. |
| Tips and Tricks | https://docs.blender.org/api/4.5/info_tips_and_tricks.html | Headless execution, debugging, terminal use | Useful for harness execution and debugging. |
| Gotchas | https://docs.blender.org/api/4.5/info_gotcha.html | Any refiner/debugger pass | Top-level list of common Blender scripting traps. |
| Operators Gotcha | https://docs.blender.org/api/4.5/info_gotchas_operators.html | Code uses `bpy.ops` | Operators are context-sensitive and poll can fail. |
| Mesh Gotcha | https://docs.blender.org/api/4.5/info_gotchas_meshes.html | Code reads/writes mesh data | Edit-mode mesh data can be out of sync with object data. |
| Data Access | https://docs.blender.org/api/4.5/bpy.data.html | Creating or finding datablocks | Preferred path for deterministic creation and lookup. |
| Context Access | https://docs.blender.org/api/4.5/bpy.context.html | Code uses active scene/object/view layer | Required when context-sensitive operations cannot be avoided. |
| Object Type | https://docs.blender.org/api/4.5/bpy.types.Object.html | Object transforms, parenting, bbox, animation | Central type for scene assembly and validation. |
| Mesh Type | https://docs.blender.org/api/4.5/bpy.types.Mesh.html | Mesh creation and inspection | Includes `from_pydata`, `update`, polygons, vertices, attributes. |
| Material Type | https://docs.blender.org/api/4.5/bpy.types.Material.html | Materials and shader setup | Required for generated appearance. |
| Camera Type | https://docs.blender.org/api/4.5/bpy.types.Camera.html | Screenshot and render planning | Camera data settings. |
| Light Type | https://docs.blender.org/api/4.5/bpy.types.Light.html | Lighting/environment | Light data settings. |
| Scene Type | https://docs.blender.org/api/4.5/bpy.types.Scene.html | Frame ranges, render settings, object collections | Top-level scene state. |
| Render Operators | https://docs.blender.org/api/4.5/bpy.ops.render.html | Still image and animation render | Invoking renders from generated scripts. |
| Render Settings | https://docs.blender.org/api/4.5/bpy.types.RenderSettings.html | Resolution, fps, output paths | Deterministic render configuration. |
| Image Format Settings | https://docs.blender.org/api/4.5/bpy.types.ImageFormatSettings.html | PNG/FFmpeg output | Static screenshots and preview videos. |
| BMesh Module | https://docs.blender.org/api/4.5/bmesh.html | Procedural mesh creation and edit-mode workflows | Safer complex geometry construction than ad hoc mesh mutation. |
| Mathutils | https://docs.blender.org/api/4.5/mathutils.html | Vectors, matrices, rotations | Required for transforms and geometric verification. |
| BVHTree | https://docs.blender.org/api/4.5/mathutils.bvhtree.html | Intersection/collision checks | Useful for deterministic relation and collision verification. |

## P1: Include for Scene Quality and Validation

| Source | URL | Use When | Why It Matters |
| --- | --- | --- | --- |
| Collections | https://docs.blender.org/api/4.5/bpy.types.Collection.html | Grouping objects by scene/object factory | Keeps generated scenes organized and inspectable. |
| View Layer | https://docs.blender.org/api/4.5/bpy.types.ViewLayer.html | Evaluated depsgraph and object visibility | Needed for reliable rendering and validation. |
| Depsgraph | https://docs.blender.org/api/4.5/bpy.types.Depsgraph.html | Evaluated geometry, modifiers, animation sampling | Required to inspect final evaluated state. |
| Modifier | https://docs.blender.org/api/4.5/bpy.types.Modifier.html | Procedural object detail | Common generated objects use bevels, arrays, mirrors, subdivisions. |
| Constraint | https://docs.blender.org/api/4.5/bpy.types.Constraint.html | Camera tracking, follow-path, rig-like animation | Useful for animation and camera behavior. |
| World | https://docs.blender.org/api/4.5/bpy.types.World.html | Background/lighting | Needed for environment generation. |
| Node Tree | https://docs.blender.org/api/4.5/bpy.types.NodeTree.html | Shader nodes and procedural materials | Advanced material generation. |
| Shader Node BSDF | https://docs.blender.org/api/4.5/bpy.types.ShaderNodeBsdfPrincipled.html | Principled material setup | Common material node target. |
| Mesh Utils | https://docs.blender.org/api/4.5/bpy_extras.mesh_utils.html | Mesh helpers | Useful for generated geometry and validation. |
| Object Utils | https://docs.blender.org/api/4.5/bpy_extras.object_utils.html | Object creation helpers | Useful when object placement needs context helpers. |
| View3D Utils | https://docs.blender.org/api/4.5/bpy_extras.view3d_utils.html | Camera/view calculations | Useful for screenshot coverage checks. |

## P2: Include for Animation and Advanced Motion

| Source | URL | Use When | Why It Matters |
| --- | --- | --- | --- |
| Action Type | https://docs.blender.org/api/4.5/bpy.types.Action.html | Animation data inspection and repair | Blender 4.5 uses the layered action system introduced in 4.4. |
| FCurve Type | https://docs.blender.org/api/4.5/bpy.types.FCurve.html | Keyframe interpolation and curve repair | Needed to inspect and adjust animation curves. |
| Keyframe Type | https://docs.blender.org/api/4.5/bpy.types.Keyframe.html | Fine-grained keyframe validation | Needed for temporal validation and smoothing. |
| AnimData Type | https://docs.blender.org/api/4.5/bpy.types.AnimData.html | Object animation ownership | Entry point from object to action/NLA data. |
| Timeline Marker | https://docs.blender.org/api/4.5/bpy.types.TimelineMarker.html | Event labels and camera cuts | Useful for structured animation planning. |
| NLA Strip | https://docs.blender.org/api/4.5/bpy.types.NlaStrip.html | Advanced action composition | Later-stage animation workflows. |
| Curve Type | https://docs.blender.org/api/4.5/bpy.types.Curve.html | Path animation and curves | Follow-path and procedural path generation. |
| Rigid Body World | https://docs.blender.org/api/4.5/bpy.types.RigidBodyWorld.html | Physics animation | Later-stage physics-based motion. |
| Rigid Body Object | https://docs.blender.org/api/4.5/bpy.types.RigidBodyObject.html | Object-level rigid body settings | Physics verification and repair. |

## Retrieval Triggers

Use these query terms to route retrieval:

- Object creation: `bpy.data.objects`, `bpy.types.Object`, `Collection.objects.link`
- Mesh creation: `Mesh.from_pydata`, `mesh.update`, `bmesh`, `edit mode`
- Materials: `bpy.types.Material`, `use_nodes`, `Principled BSDF`, `node_tree`
- Scene assembly: `Object.location`, `Object.rotation_euler`, `Object.scale`, `matrix_world`
- Relationship validation: `Object.bound_box`, `evaluated_get`, `Depsgraph`, `BVHTree`
- Rendering: `bpy.ops.render.render`, `RenderSettings`, `ImageFormatSettings`, `scene.camera`
- Screenshots: `Camera`, `focal_length`, `look_at`, `resolution`, `output filepath`
- Animation: `keyframe_insert`, `animation_data`, `Action`, `FCurve`, `Keyframe`
- Video output: `FFmpeg`, `ImageFormatSettings`, `frame_start`, `frame_end`, `fps`
- Debugging: `operator poll`, `context`, `mode`, `edit mesh`, `threads`

