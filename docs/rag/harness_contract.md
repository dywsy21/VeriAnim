# Harness Contract: Mandatory Code Requirements

Generated Blender scripts MUST satisfy these requirements or the harness
deterministic validator will reject them. These are not suggestions.

## 1. Object Identification (CRITICAL)

Every object created for the scene MUST have an `verianim_id` custom property
matching the IR object id. The deterministic validator finds objects by this
property. Without it, the object is invisible to validation and will be
reported as MISSING_OBJECT.

```python
obj["verianim_id"] = "cup"        # REQUIRED - must match IR object id exactly
obj["verianim_role"] = "primary"   # recommended
obj["verianim_part"] = "handle"    # for sub-parts of multi-part objects
```

Set `verianim_id` on the ROOT object (the one with the mesh or the parent Empty
that owns the logical object). Do not set it only on child parts.

For multi-part objects, set `verianim_id` on the parent/root AND set `verianim_part`
on each child mesh to identify sub-components.

## 2. VERIANIM_METADATA Variable (REQUIRED)

Every script must define a module-level variable at the end:

```python
VERIANIM_METADATA = {
    "objects": {
        "cup": "Cup",           # ir_id: blender_object_name
        "table": "Table",
    },
    "materials": ["warm_ceramic", "dark_wood"],
    "cameras": ["camera_main"],
}
```

## 3. Script Structure Template

Follow this exact structure:

```python
import bpy
import bmesh
from mathutils import Vector
import math

from blender import verianim_utils as verianim

# 1. Clear scene
verianim.clear_scene()
scene = bpy.context.scene

# 2. Create collections
root_col = bpy.data.collections.new("verianim_scene")
scene.collection.children.link(root_col)
objects_col = bpy.data.collections.new("objects")
env_col = bpy.data.collections.new("environment")
root_col.children.link(objects_col)
root_col.children.link(env_col)

# 3. Create materials (before objects that use them)
# ... material creation functions ...

# 4. Create objects with verianim_id
# ... object factory functions ...

# 5. Create environment (floor, lights, world)
# ... environment setup ...

# 6. Create and activate camera
camera_data = bpy.data.cameras.new("camera_main_data")
camera_obj = bpy.data.objects.new("camera_main", camera_data)
env_col.objects.link(camera_obj)
scene.camera = camera_obj  # MUST set active camera

# 7. Set render engine (see section below)

# 8. Animation keyframes (if animation requested)

# 9. VERIANIM_METADATA at the end
VERIANIM_METADATA = { ... }
```

## 4. Render Engine Selection (CRITICAL)

Blender 4.5 only supports these render engine enum values:
- `'BLENDER_WORKBENCH'` (fastest, basic shading)
- `'BLENDER_EEVEE_NEXT'` (fast, good for previews)
- `'CYCLES'` (ray-traced, slow)

WRONG values that will crash:
- `'BLENDER_EEVEE'` — REMOVED in Blender 4.4
- `'WORKBENCH'` — wrong name, must be `'BLENDER_WORKBENCH'`
- `'EEVEE'` — never valid

Safe pattern:

```python
from blender import verianim_utils as verianim

verianim.configure_render(scene, engine="workbench")
```

Use Workbench by default for validation/inspection renders. It avoids strong
lighting, mirror-like reflections, and transparent-shell artifacts that can hide
internal geometry from the vision verifier.

## 5. EEVEE Properties (CRITICAL)

These SceneEEVEE properties are REMOVED in Blender 4.2+ (EEVEE-Next):
- `scene.eevee.use_ao` — REMOVED
- `scene.eevee.gtao_factor` — REMOVED
- `scene.eevee.gtao_distance` — REMOVED
- `scene.eevee.use_bloom` — REMOVED
- `scene.eevee.bloom_threshold` — REMOVED
- `scene.eevee.use_ssr` — REMOVED
- `scene.eevee.use_gtao` — REMOVED

Do NOT set any of these. EEVEE-Next handles AO and bloom automatically.

These Material properties are also REMOVED:
- `material.blend_method` — REMOVED
- `material.shadow_method` — REMOVED
- `material.alpha_threshold` — REMOVED

## 6. Active Camera (REQUIRED)

The scene MUST have an active camera set:

```python
scene.camera = camera_obj
```

Without this, the deterministic validator reports MISSING_ACTIVE_CAMERA and
screenshot rendering fails.

## 7. Material and Node Creation (REQUIRED PATTERN)

Never find ANY shader/world node by display name. Always use node.type:

```python
# Material nodes
mat = bpy.data.materials.new("material_name")
mat.use_nodes = True
mat.diffuse_color = (r, g, b, 1.0)  # viewport color

# Find principled shader by TYPE, not name
principled = None
for node in mat.node_tree.nodes:
    if node.type == "BSDF_PRINCIPLED":
        principled = node
        break

if principled:
    principled.inputs["Base Color"].default_value = (r, g, b, 1.0)
    principled.inputs["Roughness"].default_value = 0.5
    principled.inputs["Metallic"].default_value = 0.0
```

World background nodes — same rule:

```python
world = bpy.data.worlds.new("World")
bpy.context.scene.world = world
world.use_nodes = True
nodes = world.node_tree.nodes
links = world.node_tree.links

# Find nodes by TYPE, never by name
bg_node = None
output_node = None
for node in nodes:
    if node.type == "BACKGROUND":
        bg_node = node
    elif node.type == "OUTPUT_WORLD":
        output_node = node

if bg_node:
    bg_node.inputs["Color"].default_value = (0.05, 0.05, 0.08, 1.0)
if bg_node and output_node:
    links.new(bg_node.outputs["Background"], output_node.inputs["Surface"])
```

WRONG patterns that WILL CRASH on localized Blender:
```python
# WRONG - will crash with KeyError on non-English Blender:
nodes["Principled BSDF"]
nodes["World Output"]
nodes["Background"]  # name lookup
nodes.get("Material Output")
```

Node type reference for common nodes:
- Principled BSDF: `node.type == "BSDF_PRINCIPLED"`
- Material Output: `node.type == "OUTPUT_MATERIAL"`
- World Output: `node.type == "OUTPUT_WORLD"`
- Background: `node.type == "BACKGROUND"`
- Mix Shader: `node.type == "MIX_SHADER"`
- Image Texture: `node.type == "TEX_IMAGE"`
- Noise Texture: `node.type == "TEX_NOISE"`
- Color Ramp: `node.type == "VALTORGB"`
- Mapping: `node.type == "MAPPING"`
- Texture Coordinate: `node.type == "TEX_COORD"`

## 8. Animation Requirements

When animation is requested:

```python
# Set frame range BEFORE inserting keyframes
scene.frame_start = 1
scene.frame_end = duration_frames
scene.render.fps = fps

# Insert keyframes on the object that has verianim_id
obj.location = start_location
obj.keyframe_insert(data_path="location", frame=start_frame)
obj.location = end_location
obj.keyframe_insert(data_path="location", frame=end_frame)

# Set interpolation on F-Curves
if obj.animation_data and obj.animation_data.action:
    for fcurve in obj.animation_data.action.fcurves:
        for kp in fcurve.keyframe_points:
            kp.interpolation = 'LINEAR'  # or 'BEZIER'
```

Animate the ROOT object (the one with `verianim_id`), not child parts.

## 9. Common Validation Failures and Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| MISSING_OBJECT | No `verianim_id` property | Add `obj["verianim_id"] = "ir_id"` |
| MISSING_ACTIVE_CAMERA | `scene.camera` not set | `scene.camera = cam_obj` |
| MISSING_MATERIAL_SPEC | Material not created | Create material and append to mesh |
| FRAME_END_TOO_SHORT | `scene.frame_end` < duration | Set `scene.frame_end = duration` |
| MISSING_ANIMATED_OBJECT | Keyframes on wrong object | Animate the `verianim_id` root object |
| gtao_factor error | Using removed EEVEE props | Remove all `scene.eevee.*` calls |
| enum not found | Wrong engine name | Use `verianim.configure_render(scene, engine="workbench")` |
