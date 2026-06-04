import bpy
import bmesh
import mathutils
import math

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def clear_scene():
    """Delete all objects, collections (keeping main), and orphan data."""
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    # Remove all non-default collections
    for coll in bpy.data.collections:
        if coll.name != "Collection":
            bpy.data.collections.remove(coll)
    # Clean up unused data
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras):
        for item in block:
            block.remove(item)
    # Keep the default world

def create_material(name, base_color, roughness=0.5, metallic=0.0):
    """Create a PBR material with Principled BSDF."""
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = base_color[:3] + (1.0,)  # RGBA
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    bsdf = None
    for n in nodes:
        if n.type == 'BSDF_PRINCIPLED':
            bsdf = n
            break
    if bsdf:
        bsdf.inputs['Base Color'].default_value = base_color
        bsdf.inputs['Roughness'].default_value = roughness
        bsdf.inputs['Metallic'].default_value = metallic
    mat['verianim_id'] = name
    return mat

def create_box_mesh(width, depth, height):
    """Return a bmesh cube centered at origin with given dimensions."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    scale = mathutils.Matrix.Diagonal((width, depth, height)).to_4x4()
    bmesh.ops.transform(bm, matrix=scale, verts=bm.verts)
    return bm

def place_box_with_anchor(name, width, depth, height, location, rotation_euler=(0,0,0), anchor='center'):
    """
    Create a box mesh object.
    anchor: 'center' -> origin at geometric center
            'top_center' -> origin at top face center (Z = +height/2)
    """
    bm = create_box_mesh(width, depth, height)
    if anchor == 'top_center':
        for v in bm.verts:
            v.co.z -= height / 2.0
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj.rotation_euler = rotation_euler
    bpy.context.scene.collection.objects.link(obj)
    return obj

def set_verianim_id(obj, id_str):
    obj['verianim_id'] = id_str

def assign_material(obj, mat):
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)

def look_at(camera, target_pos, up=(0,0,1)):
    """Set camera rotation to face target."""
    dir_vec = mathutils.Vector(target_pos) - camera.location
    if dir_vec.length == 0:
        return
    rot_quat = dir_vec.to_track_quat('-Z', 'Y')
    camera.rotation_mode = 'QUATERNION'
    camera.rotation_quaternion = rot_quat

def create_camera(name, loc, target, focal_length=35.0, sensor_width=36.0):
    cam_data = bpy.data.cameras.new(name)
    cam_data.lens = focal_length
    cam_data.sensor_width = sensor_width
    cam_obj = bpy.data.objects.new(name, cam_data)
    cam_obj.location = loc
    look_at(cam_obj, target)
    bpy.context.scene.collection.objects.link(cam_obj)
    return cam_obj

def create_sun(name, loc, energy=5.0):
    sun_data = bpy.data.lights.new(name, 'SUN')
    sun_data.energy = energy
    sun_obj = bpy.data.objects.new(name, sun_data)
    sun_obj.location = loc
    sun_obj.rotation_euler = (math.radians(45), 0, math.radians(45))
    bpy.context.scene.collection.objects.link(sun_obj)
    return sun_obj

# -------------------------------------------------------------------
# Scene Setup
# -------------------------------------------------------------------
clear_scene()

garage_coll = bpy.data.collections.get("Collection")
if not garage_coll:
    garage_coll = bpy.data.collections.new("Collection")
    bpy.context.scene.collection.children.link(garage_coll)
garage_coll.name = "Garage"

render_engine_enum = bpy.context.scene.render.bl_rna.properties['engine'].enum_items
if 'BLENDER_EEVEE_NEXT' in render_engine_enum:
    bpy.context.scene.render.engine = 'BLENDER_EEVEE_NEXT'
else:
    bpy.context.scene.render.engine = 'BLENDER_EEVEE'

if "World" not in bpy.data.worlds:
    world = bpy.data.worlds.new("World")
else:
    world = bpy.data.worlds["World"]
bpy.context.scene.world = world

world.use_nodes = True
bg = world.node_tree.nodes.get('Background')
if bg:
    bg.inputs['Color'].default_value = (0.6, 0.8, 1.0, 1.0)

# -------------------------------------------------------------------
# Materials
# -------------------------------------------------------------------
mat_concrete = create_material("concrete_floor", (0.5, 0.5, 0.5, 1.0), roughness=0.9)
mat_wall = create_material("wall_material", (0.3, 0.3, 0.3, 1.0), roughness=0.8)
mat_roof = create_material("roof_material", (0.4, 0.2, 0.1, 1.0), roughness=0.7)
mat_door = create_material("door_material", (0.6, 0.6, 0.6, 1.0), roughness=0.2, metallic=0.8)
mat_car = create_material("yellow_car", (1.0, 1.0, 0.0, 1.0), roughness=0.3)

# -------------------------------------------------------------------
# Objects
# -------------------------------------------------------------------
garage_w = 6.0
garage_d = 6.0
garage_h = 3.0
wall_thick = 0.2
roof_thick = 0.15
floor_thick = 0.1

floor = place_box_with_anchor("floor", garage_w, garage_d, floor_thick,
                              (0, 0, -floor_thick/2), anchor='center')
assign_material(floor, mat_concrete)
set_verianim_id(floor, "floor")

left_wall = place_box_with_anchor("left_wall", wall_thick, garage_d, garage_h,
                                  (-garage_w/2 + wall_thick/2, 0, garage_h/2))
assign_material(left_wall, mat_wall)
set_verianim_id(left_wall, "left_wall")

right_wall = place_box_with_anchor("right_wall", wall_thick, garage_d, garage_h,
                                   (garage_w/2 - wall_thick/2, 0, garage_h/2))
assign_material(right_wall, mat_wall)
set_verianim_id(right_wall, "right_wall")

back_wall = place_box_with_anchor("back_wall", garage_w, wall_thick, garage_h,
                                  (0, garage_d/2 - wall_thick/2, garage_h/2))
assign_material(back_wall, mat_wall)
set_verianim_id(back_wall, "back_wall")

roof = place_box_with_anchor("roof", garage_w, garage_d, roof_thick,
                             (0, 0, garage_h + roof_thick/2))
assign_material(roof, mat_roof)
set_verianim_id(roof, "roof")

door_w = 5.0
door_h = 2.8
door_thick = 0.1
door_location = (0, -garage_d/2, garage_h)
door = place_box_with_anchor("door", door_w, door_thick, door_h,
                             door_location, anchor='top_center')
assign_material(door, mat_door)
set_verianim_id(door, "door")

# Car construction
car_parts = []
body = place_box_with_anchor("car_body", 2.0, 1.0, 0.8,
                             (0, -1.0, 0.4))
assign_material(body, mat_car)
car_parts.append(body)

cabin = place_box_with_anchor("car_cabin", 1.4, 0.7, 0.4,
                              (0, -1.0, 0.8+0.4/2 + 0.05))
assign_material(cabin, mat_car)
car_parts.append(cabin)

wheel_positions = [
    mathutils.Vector((-0.7, -1.0+0.45, 0.15)),
    mathutils.Vector(( 0.7, -1.0+0.45, 0.15)),
    mathutils.Vector((-0.7, -1.0-0.45, 0.15)),
    mathutils.Vector(( 0.7, -1.0-0.45, 0.15)),
]
for i, pos in enumerate(wheel_positions):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.3, radius2=0.3, depth=0.2)
    rot = mathutils.Matrix.Rotation(math.radians(90), 3, 'X')
    bmesh.ops.transform(bm, matrix=rot, verts=bm.verts)
    mesh = bpy.data.meshes.new(f"wheel_{i}")
    bm.to_mesh(mesh)
    bm.free()
    wheel_obj = bpy.data.objects.new(f"car_wheel_{i}", mesh)
    wheel_obj.location = pos
    bpy.context.scene.collection.objects.link(wheel_obj)
    assign_material(wheel_obj, mat_car)
    car_parts.append(wheel_obj)

bpy.ops.object.select_all(action='DESELECT')
for part in car_parts:
    part.select_set(True)
bpy.context.view_layer.objects.active = car_parts[0]
bpy.ops.object.join()
car_mesh_obj = bpy.context.object
car_mesh_obj.name = "car"

bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='MEDIAN')
car_mesh_obj.location.z = -car_mesh_obj.bound_box[0][2]

set_verianim_id(car_mesh_obj, "car")

# -------------------------------------------------------------------
# Lighting
# -------------------------------------------------------------------
sun = create_sun("sun", (5, -10, 10), energy=5.0)
set_verianim_id(sun, "sun")

# -------------------------------------------------------------------
# Cameras
# -------------------------------------------------------------------
cam_main = create_camera("camera_main", (6, 4, 4), (0, -2, 1.5), focal_length=35)
look_at(cam_main, (0, -2, 1.5))
set_verianim_id(cam_main, "camera_main")

cam_closeup = create_camera("camera_closeup", (0, 1.5, 3.2), (0, 0, 3.0), focal_length=50)
look_at(cam_closeup, (0, 0, 3.0))
set_verianim_id(cam_closeup, "camera_closeup")

cam_side = create_camera("camera_side", (6, -2, 1.5), (0, -2, 1.5), focal_length=35)
look_at(cam_side, (0, -2, 1.5))
set_verianim_id(cam_side, "camera_side")

bpy.context.scene.camera = cam_main

# -------------------------------------------------------------------
# Animation
# -------------------------------------------------------------------
scene = bpy.context.scene
scene.frame_start = 1
scene.frame_end = 120
scene.render.fps = 24

# Position car deeper inside for animation start
car_mesh_obj.location.x = 0.0
car_mesh_obj.location.y = -2.0
car_z = car_mesh_obj.location.z

# Door animation
door.rotation_euler = (0, 0, 0)
door.keyframe_insert('rotation_euler', frame=1)
door.rotation_euler = (0.7854, 0, 0)
door.keyframe_insert('rotation_euler', frame=20)
door.rotation_euler = (1.5708, 0, 0)
door.keyframe_insert('rotation_euler', frame=40)

# Car drive out
car_mesh_obj.location = (0.0, -2.0, car_z)
car_mesh_obj.keyframe_insert('location', frame=41)
car_mesh_obj.location = (0.0, 0.5, car_z)
car_mesh_obj.keyframe_insert('location', frame=70)
car_mesh_obj.location = (0.0, 3.0, car_z)
car_mesh_obj.keyframe_insert('location', frame=100)

# -------------------------------------------------------------------
# Metadata
# -------------------------------------------------------------------
VERIANIM_METADATA = [
    {"id": "floor", "name": "floor"},
    {"id": "left_wall", "name": "left_wall"},
    {"id": "right_wall", "name": "right_wall"},
    {"id": "back_wall", "name": "back_wall"},
    {"id": "roof", "name": "roof"},
    {"id": "door", "name": "door"},
    {"id": "car", "name": "car"},
    {"id": "sun", "name": "sun"},
    {"id": "camera_main", "name": "camera_main"},
    {"id": "camera_closeup", "name": "camera_closeup"},
    {"id": "camera_side", "name": "camera_side"},
]

if bpy.context.scene.camera is None:
    for camera_obj in bpy.data.objects:
        if camera_obj.type == "CAMERA":
            bpy.context.scene.camera = camera_obj
            break