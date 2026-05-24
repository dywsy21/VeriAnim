# Blender API Breaking Changes: 3.x to 4.5.4

This document lists API changes that break code written for older Blender
versions. LLMs trained on pre-4.0 code will generate broken patterns unless
guided by this reference.

Target: Blender 4.5.4 LTS. All code must work with this version.

## Blender 4.0 (from 3.6)

### Node Tree Interface (BREAKING)

`node_tree.inputs` and `node_tree.outputs` are REMOVED.

WRONG (3.x):
```python
node_tree.inputs.new('NodeSocketFloat', 'My Input')
node_tree.outputs.new('NodeSocketColor', 'My Output')
```

CORRECT (4.0+):
```python
node_tree.interface.new_socket(
    name='My Input', in_out='INPUT', socket_type='NodeSocketFloat'
)
node_tree.interface.new_socket(
    name='My Output', in_out='OUTPUT', socket_type='NodeSocketColor'
)
```

Access sockets via `node_tree.interface.items_tree`.

### Principled BSDF Input Renames (BREAKING)

The Principled BSDF was rewritten in 4.0. Input socket names changed:

| Old Name (3.x) | New Name (4.0+) |
|-----------------|-----------------|
| `Subsurface` | `Subsurface Weight` |
| `Subsurface Color` | REMOVED (uses Base Color) |
| `Specular` | `Specular IOR Level` |
| `Transmission` | `Transmission Weight` |
| `Transmission Roughness` | REMOVED |
| `Clearcoat` | `Coat Weight` |
| `Clearcoat Roughness` | `Coat Roughness` |
| `Clearcoat Normal` | `Coat Normal` |
| `Sheen` | `Sheen Weight` |
| `Emission` | `Emission Color` |

New inputs added: `Emission Strength`, `Coat Tint`, `Coat IOR`,
`Sheen Roughness`, `Subsurface Scale`.

The node type `"BSDF_PRINCIPLED"` is unchanged. Always find by type.

Safe pattern for basic materials:
```python
principled.inputs["Base Color"].default_value = (r, g, b, 1.0)
principled.inputs["Roughness"].default_value = 0.5
principled.inputs["Metallic"].default_value = 0.0
```

These three inputs kept their names across all versions.

### Mesh API Changes (BREAKING)

- `mesh.vertex_colors` → REMOVED. Use `mesh.color_attributes`.
- `mesh.sculpt_vertex_colors` → REMOVED. Use `mesh.color_attributes`.
- `mesh.edges[i].bevel_weight` → REMOVED. Use generic attributes:
  ```python
  mesh.attributes.new('bevel_weight_edge', 'FLOAT', 'EDGE')
  ```
- Edge/vertex creases moved to generic attributes:
  ```python
  # Old: mesh.edges[i].crease
  # New: mesh.attributes['crease_edge']
  ```

### Other 4.0 Changes

- `context.asset_file_handle` → REMOVED. Use `context.asset`.
- Python upgraded to 3.10.

---

## Blender 4.1

### Auto Smooth REMOVED (BREAKING)

- `mesh.use_auto_smooth` → AttributeError in 4.1+
- `mesh.auto_smooth_angle` → REMOVED

Replacement: Use "Smooth by Angle" modifier or set sharp edges via attribute:
```python
# Mark all faces smooth
for face in mesh.polygons:
    face.use_smooth = True

# Sharp edges via attribute
sharp = mesh.attributes.new('sharp_edge', 'BOOLEAN', 'EDGE')
```

Or use the operator (requires object context):
```python
bpy.ops.object.shade_auto_smooth()
```

For simple generated geometry, just set faces smooth and skip auto-smooth
entirely. The visual difference is negligible for procedural meshes.

### Python Version

- Python upgraded to 3.11.

---

## Blender 4.2 (LTS)

### EEVEE Rewrite (BREAKING)

EEVEE was completely rewritten as "EEVEE-Next".

- Render engine enum: `'BLENDER_EEVEE_NEXT'` (not `'BLENDER_EEVEE'`)
- In 4.2, `'BLENDER_EEVEE'` still works as deprecated alias
- In 4.4+, `'BLENDER_EEVEE'` is REMOVED entirely

### Material Properties REMOVED

These no longer exist in the Python API:
```python
# ALL OF THESE WILL CRASH IN 4.2+:
material.blend_method = 'HASHED'     # REMOVED
material.shadow_method = 'HASHED'    # REMOVED
material.alpha_threshold = 0.5       # REMOVED
```

Transparency in EEVEE-Next is handled automatically based on shader setup.

### SceneEEVEE Properties REMOVED

```python
# ALL OF THESE WILL CRASH IN 4.2+:
scene.eevee.use_gtao = True          # REMOVED
scene.eevee.gtao_factor = 1.0        # REMOVED
scene.eevee.gtao_distance = 0.2      # REMOVED
scene.eevee.use_bloom = True          # REMOVED
scene.eevee.bloom_threshold = 0.8    # REMOVED
scene.eevee.bloom_intensity = 0.05   # REMOVED
scene.eevee.use_ssr = True           # REMOVED
scene.eevee.ssr_thickness = 0.1      # REMOVED
scene.eevee.use_ao = True            # REMOVED (4.5 confirmed)
```

EEVEE-Next handles these effects internally. Do not set any `scene.eevee.*`
properties unless you have verified they exist in 4.5.

Safe EEVEE-Next properties that DO exist in 4.5:
```python
scene.eevee.use_shadows = True
scene.eevee.shadow_ray_count = 1
scene.eevee.shadow_step_count = 6
```

### bgl Module Deprecated

- `import bgl` still works but is deprecated
- Use `import gpu` for all GPU operations
- Will be removed in Blender 5.0

---

## Blender 4.3

Relatively minor Python API changes:
- `ID.rename()` function added
- Curves: `remove_curves(indices=[])` removes all curves

---

## Blender 4.4

### Render Engine Enum (BREAKING)

- `'BLENDER_EEVEE'` is FULLY REMOVED. Only `'BLENDER_EEVEE_NEXT'` works.
- `'BLENDER_WORKBENCH'` is the correct workbench name (not `'WORKBENCH'`).

Valid engine values in 4.4+:
```python
scene.render.engine = 'BLENDER_WORKBENCH'    # fastest, basic
scene.render.engine = 'BLENDER_EEVEE_NEXT'  # fast
scene.render.engine = 'CYCLES'               # ray-traced
```

### Layered Action System (MAJOR CHANGE)

Actions now have Layers → Strips → ChannelBags → FCurves internally.

GOOD NEWS: Simple `keyframe_insert` still works unchanged:
```python
obj.location = (1, 2, 3)
obj.keyframe_insert(data_path="location", frame=1)
```

Blender auto-creates the internal layer/strip/slot structure.

However, direct FCurve access changed:

STILL WORKS for reading (convenience accessor):
```python
action = obj.animation_data.action
for fcurve in action.fcurves:
    for kp in fcurve.keyframe_points:
        kp.interpolation = 'LINEAR'
```

For generated code, prefer the simple `keyframe_insert` workflow and access
`action.fcurves` for post-processing interpolation. This is the safest
cross-version pattern.

---

## Blender 4.5 (LTS - TARGET VERSION)

### Confirmed Working APIs

These patterns are verified working in Blender 4.5.4:

```python
# Render engine
scene.render.engine = 'BLENDER_WORKBENCH'

# Object creation
mesh = bpy.data.meshes.new("name")
mesh.from_pydata(verts, edges, faces)
mesh.update()
obj = bpy.data.objects.new("name", mesh)
collection.objects.link(obj)

# Materials
mat = bpy.data.materials.new("name")
mat.use_nodes = True
mat.diffuse_color = (r, g, b, 1.0)
# Find principled by type
principled = next(
    (n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None
)

# Keyframes
obj.keyframe_insert(data_path="location", frame=1)

# Camera
scene.camera = camera_obj

# Custom properties
obj["ll3m_id"] = "my_id"
```

### Deprecated (still works but avoid)

- `GPUShader(...)` constructor — deprecated, removed in 5.0
- `bgl` module — deprecated, removed in 5.0
- Collada import/export — last version to include it

### Workbench Engine Name

The correct name is `'BLENDER_WORKBENCH'` (not `'WORKBENCH'` or
`'BLENDER_WORKBENCH_NEXT'`).

---

## Quick Reference: What NOT to Use in 4.5.4

```python
# WILL CRASH - DO NOT USE:
node_tree.inputs.new(...)              # Use interface.new_socket()
node_tree.outputs.new(...)             # Use interface.new_socket()
mesh.vertex_colors                     # Use mesh.color_attributes
mesh.use_auto_smooth                   # REMOVED
mesh.auto_smooth_angle                 # REMOVED
scene.render.engine = 'BLENDER_EEVEE'  # Use 'BLENDER_EEVEE_NEXT'
scene.render.engine = 'WORKBENCH'      # Use 'BLENDER_WORKBENCH'
scene.eevee.use_ao                     # REMOVED
scene.eevee.gtao_factor                # REMOVED
scene.eevee.gtao_distance              # REMOVED
scene.eevee.use_bloom                  # REMOVED
scene.eevee.bloom_threshold            # REMOVED
scene.eevee.use_ssr                    # REMOVED
material.blend_method                  # REMOVED
material.shadow_method                 # REMOVED
principled.inputs["Subsurface"]        # Now "Subsurface Weight"
principled.inputs["Clearcoat"]         # Now "Coat Weight"
principled.inputs["Transmission"]      # Now "Transmission Weight"
principled.inputs["Emission"]          # Now "Emission Color"
principled.inputs["Specular"]          # Now "Specular IOR Level"
context.asset_file_handle              # Use context.asset
```

## Safe Minimal Render Setup for 4.5.4

```python
from blender import ll3m_utils as ll3m

scene = bpy.context.scene
ll3m.configure_render(scene, width=960, height=540, engine="workbench", transparent_background=False)
# Do NOT set any scene.eevee.* properties unless verified
```

## Node Name Localization (ALL VERSIONS)

Blender translates node display names based on UI language. Code that looks up
nodes by name will crash on non-English installations.

WRONG (crashes on localized Blender):
```python
nodes["Principled BSDF"]       # KeyError
nodes["World Output"]          # KeyError
nodes["Material Output"]       # KeyError
nodes.get("Background")        # returns None
```

CORRECT (works on all languages):
```python
# Find by node.type, which is always English
principled = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
world_out = next((n for n in nodes if n.type == "OUTPUT_WORLD"), None)
mat_out = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None)
bg = next((n for n in nodes if n.type == "BACKGROUND"), None)
```

Common node types:
| Display Name (English) | node.type |
|------------------------|-----------|
| Principled BSDF | BSDF_PRINCIPLED |
| Material Output | OUTPUT_MATERIAL |
| World Output | OUTPUT_WORLD |
| Background | BACKGROUND |
| Mix Shader | MIX_SHADER |
| Image Texture | TEX_IMAGE |
| Noise Texture | TEX_NOISE |
| Color Ramp | VALTORGB |
| Mapping | MAPPING |
| Texture Coordinate | TEX_COORD |
| Emission | EMISSION |
| Glass BSDF | BSDF_GLASS |
| Glossy BSDF | BSDF_GLOSSY |
