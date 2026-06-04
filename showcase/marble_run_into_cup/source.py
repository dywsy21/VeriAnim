import bpy
import bmesh
import math
from mathutils import Vector

# Clean start
bpy.ops.wm.read_homefile(use_empty=True)

scene = bpy.context.scene
collection = scene.collection
view_layer = bpy.context.view_layer

# Render engine
engines = [e.identifier for e in scene.render.bl_rna.properties['engine'].enum_items]
if 'BLENDER_EEVEE_NEXT' in engines:
    scene.render.engine = 'BLENDER_EEVEE_NEXT'

# -------------------------------
# Materials (solid colours, no textures)
def make_material(mat_id, base_color, roughness=0.4, metallic=0.0):
    mat = bpy.data.materials.new(mat_id)
    mat.use_nodes = True
    mat.diffuse_color = base_color
    mat['verianim_id'] = mat_id
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    principled = nodes.new('ShaderNodeBsdfPrincipled')
    principled.inputs['Base Color'].default_value = base_color
    principled.inputs['Roughness'].default_value = roughness
    principled.inputs['Metallic'].default_value = metallic
    links.new(principled.outputs['BSDF'], output.inputs['Surface'])
    return mat

mat_gray = make_material('gray', (0.4, 0.4, 0.4, 1.0))
mat_black = make_material('black', (0.05, 0.05, 0.05, 1.0))
mat_green = make_material('green', (0.1, 0.8, 0.1, 1.0))
mat_blue = make_material('blue', (0.1, 0.2, 0.9, 1.0))
mat_light_gray = make_material('light_gray', (0.8, 0.8, 0.8, 1.0))

# -------------------------------
# Mesh helpers

def open_cylinder_mesh(name, radius, height, segments=32):
    bm = bmesh.new()
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        cap_tris=False,
        segments=segments,
        radius1=radius,
        radius2=radius,
        depth=height,
    )
    # remove top face
    for f in bm.faces:
        if f.calc_center_median().z > height / 2:
            bm.faces.remove(f)
            break
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return mesh

def create_plane_object(name, size_x, size_y, location, material):
    bpy.ops.mesh.primitive_plane_add(size=2.0, location=location)
    obj = view_layer.objects.active
    obj.name = name
    obj.scale = (size_x * 0.5, size_y * 0.5, 1.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(material)
    obj['verianim_id'] = name
    return obj

def create_box_object(name, dimensions, location, rotation_euler, material):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=location)
    obj = view_layer.objects.active
    obj.name = name
    obj.rotation_euler = rotation_euler
    obj.scale = (dimensions[0], dimensions[1], dimensions[2])
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(material)
    obj['verianim_id'] = name
    return obj

def create_sphere_object(name, radius, location, material):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, location=location)
    obj = view_layer.objects.active
    obj.name = name
    obj.scale = (radius, radius, radius)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(material)
    obj['verianim_id'] = name
    return obj

def create_cup_object(name, radius, height, location, material):
    mesh = open_cylinder_mesh(name + '_mesh', radius, height, 32)
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    collection.objects.link(obj)
    obj.data.materials.append(material)
    obj['verianim_id'] = name
    return obj

# -------------------------------
# Build static scene

ground = create_plane_object('ground', 5.0, 5.0, (0.0, 0.0, 0.0), mat_light_gray)

# ramp: slanted board, 2 m long, 0.3 m wide, 0.05 m thick
# High end bottom at (-1, 0, 0.734), low end bottom at (0.9318, 0, 0.2164)
ramp = create_box_object(
    'ramp', (2.0, 0.3, 0.05),
    (-0.0276, 0.0, 0.49935),
    (0.0, 0.2618, 0.0),
    mat_gray,
)

# Supports at high end
leg_left = create_box_object(
    'leg_left', (0.05, 0.05, 0.734),
    (-1.0, -0.125, 0.367),
    (0.0, 0.0, 0.0),
    mat_black,
)

leg_right = create_box_object(
    'leg_right', (0.05, 0.05, 0.734),
    (-1.0, 0.125, 0.367),
    (0.0, 0.0, 0.0),
    mat_black,
)

# Marble starts on ramp high top
marble = create_sphere_object(
    'marble', 0.05,
    (-0.987, 0.0, 0.8323),
    mat_green,
)

# Catch cup at low end
cup = create_cup_object(
    'cup', 0.15, 0.2,
    (1.1, 0.0, 0.1),
    mat_blue,
)

# -------------------------------
# World & lighting (studio)
world = bpy.data.worlds.new('StudioWorld')
scene.world = world
world.use_nodes = True
nodes = world.node_tree.nodes
links = world.node_tree.links
nodes.clear()
bg = nodes.new('ShaderNodeBackground')
bg.inputs['Color'].default_value = (0.8, 0.8, 0.8, 1.0)
out = nodes.new('ShaderNodeOutputWorld')
links.new(bg.outputs['Background'], out.inputs['Surface'])

bpy.ops.object.light_add(type='AREA', location=(0.0, -3.0, 4.0))
key_light = view_layer.objects.active
key_light.name = 'key_light'
key_light.data.energy = 500.0
key_light.data.size = 4.0

# -------------------------------
# Cameras with Track To
def create_camera(name, location, look_at):
    cam_data = bpy.data.cameras.new(name + '_data')
    cam_obj = bpy.data.objects.new(name, cam_data)
    cam_obj.location = location
    collection.objects.link(cam_obj)

    empty = bpy.data.objects.new(name + '_target', None)
    empty.empty_display_type = 'PLAIN_AXES'
    empty.location = look_at
    collection.objects.link(empty)

    constraint = cam_obj.constraints.new('TRACK_TO')
    constraint.target = empty
    constraint.track_axis = 'TRACK_NEGATIVE_Z'
    constraint.up_axis = 'UP_Y'
    return cam_obj

cam_overall = create_camera('cam_overall', (2.0, -3.0, 2.0), (0.0, 0.0, 0.5))
cam_closeup = create_camera('cam_closeup', (-0.8, -0.5, 1.0), (-0.987, 0.0, 0.8323))
cam_side = create_camera('cam_side', (0.0, 3.0, 0.5), (0.0, 0.0, 0.5))
cam_low_end = create_camera('cam_low_end', (2.0, 0.0, 0.5), (0.0, 0.0, 0.5))
cam_top = create_camera('cam_top', (0.0, 0.0, 3.0), (0.0, 0.0, 0.5))
scene.camera = cam_overall

# -------------------------------
# Animation setup
scene.frame_start = 1
scene.frame_end = 120
scene.render.fps = 24
scene.render.resolution_x = 1280
scene.render.resolution_y = 720
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'

marble_obj = bpy.data.objects['marble']

# Translation keyframes
marble_obj.location = (-0.987, 0.0, 0.8323)
marble_obj.keyframe_insert(data_path='location', frame=1)

marble_obj.location = (0.945, 0.0, 0.315)
marble_obj.keyframe_insert(data_path='location', frame=60)

marble_obj.location = (1.1, 0.0, 0.18)
marble_obj.keyframe_insert(data_path='location', frame=120)

# Rotation keyframes (linear interpolation)
marble_obj.rotation_euler = (0.0, 0.0, 0.0)
marble_obj.keyframe_insert(data_path='rotation_euler', frame=1)

marble_obj.rotation_euler = (0.0, 6.283, 0.0)
marble_obj.keyframe_insert(data_path='rotation_euler', frame=60)

marble_obj.rotation_euler = (0.0, 12.566, 0.0)
marble_obj.keyframe_insert(data_path='rotation_euler', frame=120)

# Set interpolation types on keyframe points
if marble_obj.animation_data and marble_obj.animation_data.action:
    action = marble_obj.animation_data.action
    for fcurve in action.fcurves:
        for kp in fcurve.keyframe_points:
            if fcurve.data_path == 'location':
                kp.interpolation = 'BEZIER'
            elif fcurve.data_path == 'rotation_euler':
                kp.interpolation = 'LINEAR'

# -------------------------------
# Metadata
VERIANIM_METADATA = {
    'objects': {
        'ground': 'ground',
        'ramp': 'ramp',
        'leg_left': 'leg_left',
        'leg_right': 'leg_right',
        'marble': 'marble',
        'cup': 'cup',
    },
    'cameras': {
        'cam_overall': 'cam_overall',
        'cam_closeup': 'cam_closeup',
        'cam_side': 'cam_side',
        'cam_low_end': 'cam_low_end',
        'cam_top': 'cam_top',
    },
    'lights': {
        'key_light': 'key_light',
    },
}

if bpy.context.scene.camera is None:
    for _verianim_camera in bpy.data.objects:
        if _verianim_camera.type == "CAMERA":
            bpy.context.scene.camera = _verianim_camera
            break