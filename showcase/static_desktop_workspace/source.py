
import bpy, math
from mathutils import Vector

# ---------- setup ----------
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()
for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras, bpy.data.curves):
    for block in list(coll):
        if block.users == 0:
            try: coll.remove(block)
            except Exception: pass
scene = bpy.context.scene
scene.frame_start = 1
scene.frame_end = 120
scene.render.fps = 24

# ---------- helpers ----------
def mat_principled(name, color, rough=0.45, metallic=0.0, emission=None, strength=0.0):
    m = bpy.data.materials.new(name)
    m.diffuse_color = color
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        bsdf.inputs['Base Color'].default_value = color
        bsdf.inputs['Roughness'].default_value = rough
        bsdf.inputs['Metallic'].default_value = metallic
        if emission and 'Emission Color' in bsdf.inputs:
            bsdf.inputs['Emission Color'].default_value = emission
            bsdf.inputs['Emission Strength'].default_value = strength
    m['verianim_id'] = name
    return m

def set_props(obj, verianim_id, role='primary', part='root'):
    obj['verianim_id'] = verianim_id
    obj['verianim_role'] = role
    obj['verianim_part'] = part

def cube(name, loc, scale, mat, verianim_id, part):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    set_props(obj, verianim_id, 'primary', part)
    bevel = obj.modifiers.new('soft bevel', 'BEVEL')
    bevel.width = min(scale) * 0.12
    bevel.segments = 3
    obj.modifiers.new('weighted normals', 'WEIGHTED_NORMAL')
    return obj

def cyl(name, loc, radius, depth, mat, verianim_id, part, vertices=48):
    bpy.ops.mesh.primitive_cylinder_add(vertices=vertices, radius=radius, depth=depth, location=loc)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(mat)
    set_props(obj, verianim_id, 'primary', part)
    bevel = obj.modifiers.new('rim bevel', 'BEVEL')
    bevel.width = radius * 0.08
    bevel.segments = 3
    obj.modifiers.new('weighted normals', 'WEIGHTED_NORMAL')
    return obj

def sphere(name, loc, radius, mat, verianim_id, part, scale=(1,1,1)):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=radius, location=loc)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    obj.data.materials.append(mat)
    set_props(obj, verianim_id, 'primary', part)
    obj.modifiers.new('smooth normals', 'WEIGHTED_NORMAL')
    return obj

def look_at(obj, target):
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()

# ---------- materials ----------
wood = mat_principled('warm_wood', (0.55, 0.34, 0.18, 1), 0.38)
wood_dark = mat_principled('wood_grain_dark', (0.30, 0.17, 0.08, 1), 0.5)
blue = mat_principled('glossy_blue_ceramic', (0.08, 0.42, 0.80, 1), 0.18)
paper = mat_principled('warm_notebook_paper', (0.96, 0.91, 0.78, 1), 0.72)
ink = mat_principled('soft_gray_ink', (0.2, 0.22, 0.25, 1), 0.5)
green1 = mat_principled('plant_leaf_light', (0.28, 0.72, 0.32, 1), 0.55)
green2 = mat_principled('plant_leaf_dark', (0.12, 0.48, 0.20, 1), 0.6)
terracotta = mat_principled('matte_terracotta', (0.68, 0.33, 0.18, 1), 0.78)
metal = mat_principled('brushed_dark_metal', (0.30, 0.30, 0.30, 1), 0.28, metallic=0.7)
shade_mat = mat_principled('warm_lamp_shade', (0.95, 0.78, 0.50, 1), 0.42)
bulb_mat = mat_principled('warm_glowing_bulb', (1.0, 0.86, 0.45, 1), 0.2, emission=(1.0, 0.72, 0.28, 1), strength=2.5)
floor_mat = mat_principled('matte_warm_floor', (0.70, 0.66, 0.58, 1), 0.7)

# ---------- environment ----------
floor = cube('warm_floor', (0, 0, -0.025), (4.2, 3.2, 0.05), floor_mat, 'environment_floor', 'floor')
floor['verianim_role'] = 'background'
back = cube('soft_back_wall', (0, 1.35, 0.85), (4.2, 0.05, 1.8), floor_mat, 'environment_wall', 'wall')
back['verianim_role'] = 'background'

# ---------- table ----------
top = cube('wooden_table_top', (0, 0, 0.74), (2.4, 1.5, 0.12), wood, 'wooden_table', 'table_top')
for i, (x, y) in enumerate([(-1.05,-0.62), (1.05,-0.62), (-1.05,0.62), (1.05,0.62)]):
    cyl(f'wooden_table_leg_{i}', (x, y, 0.35), 0.055, 0.70, wood, 'wooden_table', 'table_leg', 32)
for i, y in enumerate([-0.26, -0.06, 0.16, 0.36]):
    strip = cube(f'wood_grain_line_{i}', (0.05, y, 0.805), (1.9, 0.012, 0.006), wood_dark, 'wooden_table', 'subtle_wood_grain')
    strip.rotation_euler.z = 0.03 * i

# ---------- mug ----------
mug_x, mug_y, table_z = -0.55, -0.05, 0.84
body = cyl('blue_mug_body', (mug_x, mug_y, table_z+0.09), 0.10, 0.18, blue, 'blue_mug', 'mug_body')
inner = cyl('blue_mug_dark_inner', (mug_x, mug_y, table_z+0.183), 0.075, 0.012, mat_principled('mug_coffee_shadow', (0.08,0.05,0.03,1), 0.4), 'blue_mug', 'mug_opening')
bpy.ops.mesh.primitive_torus_add(major_radius=0.055, minor_radius=0.014, major_segments=48, minor_segments=12, location=(mug_x-0.095, mug_y, table_z+0.095), rotation=(math.pi/2, 0, 0))
handle = bpy.context.object
handle.name = 'blue_mug_visible_handle'
handle.data.materials.append(blue)
set_props(handle, 'blue_mug', 'primary', 'mug_handle')

# ---------- plant ----------
px, py = -0.78, 0.43
pot = cyl('green_plant_pot', (px, py, table_z+0.055), 0.09, 0.11, terracotta, 'green_plant', 'plant_pot')
leaf_positions = [(-0.03,0,0.18), (0.03,0.01,0.18), (0,0.04,0.21), (0,-0.04,0.21), (-0.05,0.03,0.15), (0.05,-0.02,0.15), (0.0,0.0,0.25)]
for i, (dx, dy, dz) in enumerate(leaf_positions):
    sphere(f'green_plant_leaf_{i}', (px+dx, py+dy, table_z+dz), 0.055, green1 if i % 2 else green2, 'green_plant', 'plant_leaf', scale=(1.1,0.75,0.55))

# ---------- notebook ----------
nx, ny = -0.08, -0.38
left_page = cube('open_notebook_left_page', (nx-0.16, ny, table_z+0.016), (0.30, 0.42, 0.018), paper, 'open_notebook', 'left_page')
right_page = cube('open_notebook_right_page', (nx+0.16, ny, table_z+0.016), (0.30, 0.42, 0.018), paper, 'open_notebook', 'right_page')
left_page.rotation_euler.z = 0.08
right_page.rotation_euler.z = -0.08
spine = cube('open_notebook_spine', (nx, ny, table_z+0.025), (0.035, 0.45, 0.03), ink, 'open_notebook', 'spine')
for i in range(5):
    line = cube(f'notebook_line_{i}', (nx-0.16, ny-0.13+i*0.055, table_z+0.032), (0.20, 0.008, 0.004), ink, 'open_notebook', 'page_lines')
    line.rotation_euler.z = 0.08
for i in range(5):
    line = cube(f'notebook_right_line_{i}', (nx+0.16, ny-0.13+i*0.055, table_z+0.032), (0.20, 0.008, 0.004), ink, 'open_notebook', 'page_lines')
    line.rotation_euler.z = -0.08

# ---------- lamp ----------
lx, ly = 0.72, 0.27
base = cyl('desk_lamp_base', (lx, ly, table_z+0.018), 0.11, 0.035, metal, 'desk_lamp', 'lamp_base')
post = cyl('desk_lamp_post', (lx, ly, table_z+0.23), 0.018, 0.42, metal, 'desk_lamp', 'lamp_post')
arm = cyl('desk_lamp_arm', (lx-0.12, ly-0.08, table_z+0.43), 0.015, 0.34, metal, 'desk_lamp', 'lamp_arm')
arm.rotation_euler[1] = math.radians(63)
bpy.ops.mesh.primitive_cone_add(vertices=48, radius1=0.14, radius2=0.075, depth=0.16, location=(lx-0.28, ly-0.14, table_z+0.42), rotation=(0, math.radians(65), 0))
shade = bpy.context.object
shade.name = 'desk_lamp_warm_shade'
shade.data.materials.append(shade_mat)
set_props(shade, 'desk_lamp', 'primary', 'lamp_shade')
sphere('desk_lamp_glowing_bulb', (lx-0.34, ly-0.17, table_z+0.37), 0.035, bulb_mat, 'desk_lamp', 'lamp_bulb')
light_data = bpy.data.lights.new('desk_lamp_warm_task_light_data', type='POINT')
light_data.energy = 160
light_data.color = (1.0, 0.78, 0.48)
light = bpy.data.objects.new('desk_lamp_warm_task_light', light_data)
light.location = (lx-0.34, ly-0.18, table_z+0.37)
bpy.context.scene.collection.objects.link(light)
set_props(light, 'desk_lamp', 'primary', 'lamp_light')

# ---------- global lighting / camera ----------
area_data = bpy.data.lights.new('large_softbox_data', type='AREA')
area_data.energy = 420
area_data.size = 5.0
area = bpy.data.objects.new('large_softbox', area_data)
area.location = (-1.4, -2.0, 3.2)
bpy.context.scene.collection.objects.link(area)

cam_data = bpy.data.cameras.new('camera_main_data')
cam = bpy.data.objects.new('camera_main', cam_data)
bpy.context.scene.collection.objects.link(cam)
cam.location = (2.4, -2.1, 1.75)
look_at(cam, (0, 0.05, 0.78))
cam_data.lens = 45
scene.camera = cam

# set origins metadata empties for object ids, but keep mesh parts for aggregate bbox
for oid in ['wooden_table','blue_mug','green_plant','open_notebook','desk_lamp']:
    empty = bpy.data.objects.new(oid, None)
    empty.empty_display_type = 'PLAIN_AXES'
    empty.empty_display_size = 0.08
    empty['verianim_id'] = oid
    empty['verianim_role'] = 'primary'
    empty['verianim_part'] = 'root'
    bpy.context.scene.collection.objects.link(empty)

# render settings
scene.view_settings.view_transform = 'Filmic'
scene.view_settings.look = 'Medium High Contrast'
scene.view_settings.exposure = 0
scene.view_settings.gamma = 1
VERIANIM_METADATA = {'objects': ['wooden_table','blue_mug','green_plant','open_notebook','desk_lamp'], 'style': 'warm stylized desktop showcase'}
print('VERIANIM showcase desktop scene created')
