import bpy
import math
from mathutils import Vector

# ============================================================
# VERIANIM Blender 4.5.4 Scene Script
# Static studio scene: red cube and green sphere on blue table
# ============================================================

# -----------------------------
# Utilities: scene management
# -----------------------------

def clear_scene_safely():
    """Clear current scene and remove unused datablocks deterministically."""
    if bpy.ops.object.mode_set.poll():
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll)

    for datablock_collection in (
        bpy.data.meshes,
        bpy.data.materials,
        bpy.data.lights,
        bpy.data.cameras,
        bpy.data.curves,
        bpy.data.images,
    ):
        for datablock in list(datablock_collection):
            if datablock.users == 0:
                datablock_collection.remove(datablock)


def create_collection(name, parent=None):
    coll = bpy.data.collections.new(name)
    if parent is None:
        bpy.context.scene.collection.children.link(coll)
    else:
        parent.children.link(coll)
    return coll


def tag_verianim(obj, verianim_id, role=None, part=None):
    obj["verianim_id"] = verianim_id
    if role is not None:
        obj["verianim_role"] = role
    if part is not None:
        obj["verianim_part"] = part
    return obj


# -----------------------------
# Materials
# -----------------------------

def set_principled_input(principled_node, candidate_names, value):
    """Set a Principled BSDF input without relying on node display names."""
    for socket in principled_node.inputs:
        socket_id = getattr(socket, "identifier", "")
        socket_name = getattr(socket, "name", "")
        if socket_id in candidate_names or socket_name in candidate_names:
            try:
                socket.default_value = value
                return True
            except Exception:
                pass
    return False


def create_principled_material(name, verianim_id, base_color, metallic=0.0, roughness=0.5):
    mat = bpy.data.materials.new(name)
    mat["verianim_id"] = verianim_id
    mat.use_nodes = True
    mat.diffuse_color = base_color

    if mat.node_tree:
        for node in mat.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                set_principled_input(node, {"Base Color", "BaseColor", "base_color"}, base_color)
                set_principled_input(node, {"Metallic", "metallic"}, metallic)
                set_principled_input(node, {"Roughness", "roughness"}, roughness)
                break

    return mat


# -----------------------------
# Mesh factories
# -----------------------------

def create_box_mesh(name, dimensions):
    sx, sy, sz = dimensions
    hx, hy, hz = sx / 2.0, sy / 2.0, sz / 2.0

    verts = [
        (-hx, -hy, -hz), ( hx, -hy, -hz), ( hx,  hy, -hz), (-hx,  hy, -hz),
        (-hx, -hy,  hz), ( hx, -hy,  hz), ( hx,  hy,  hz), (-hx,  hy,  hz),
    ]
    faces = [
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    ]

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    return mesh


def create_box_object(name, verianim_id, role, part, dimensions, location, material, collection):
    mesh = create_box_mesh(f"{name}_mesh", dimensions)
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    if material:
        obj.data.materials.append(material)
    tag_verianim(obj, verianim_id, role=role, part=part)
    collection.objects.link(obj)
    return obj


def create_uv_sphere_mesh(name, radius=0.2, segments=48, rings=24):
    verts = []
    faces = []

    verts.append((0.0, 0.0, radius))

    for r in range(1, rings):
        theta = math.pi * r / rings
        z = radius * math.cos(theta)
        ring_radius = radius * math.sin(theta)
        for s in range(segments):
            phi = 2.0 * math.pi * s / segments
            verts.append((ring_radius * math.cos(phi), ring_radius * math.sin(phi), z))

    verts.append((0.0, 0.0, -radius))
    top_index = 0
    bottom_index = len(verts) - 1

    # Top cap
    first_ring = 1
    for s in range(segments):
        faces.append((top_index, first_ring + s, first_ring + ((s + 1) % segments)))

    # Middle quads
    for r in range(rings - 2):
        ring_a = 1 + r * segments
        ring_b = 1 + (r + 1) * segments
        for s in range(segments):
            faces.append((
                ring_a + s,
                ring_b + s,
                ring_b + ((s + 1) % segments),
                ring_a + ((s + 1) % segments),
            ))

    # Bottom cap
    last_ring = 1 + (rings - 2) * segments
    for s in range(segments):
        faces.append((last_ring + ((s + 1) % segments), last_ring + s, bottom_index))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)

    for poly in mesh.polygons:
        poly.use_smooth = True

    return mesh


def create_sphere_object(name, verianim_id, role, radius, location, material, collection):
    mesh = create_uv_sphere_mesh(f"{name}_mesh", radius=radius, segments=64, rings=32)
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    if material:
        obj.data.materials.append(material)
    tag_verianim(obj, verianim_id, role=role)
    collection.objects.link(obj)
    return obj


# -----------------------------
# Table factory
# -----------------------------

def create_blue_table(collection, blue_material):
    """
    Create blue table with total size approximately:
    X=2.0m, Y=1.2m, Z=0.8m.
    Top surface is exactly at z=0.8.
    """
    created = {}

    table_width_x = 2.0
    table_depth_y = 1.2
    total_height = 0.8
    top_thickness = 0.12
    leg_height = total_height - top_thickness
    leg_size = 0.10

    top_center_z = leg_height + top_thickness / 2.0

    top = create_box_object(
        name="Blue Table - Flat Top",
        verianim_id="blue_table",
        role="support",
        part="table_top",
        dimensions=(table_width_x, table_depth_y, top_thickness),
        location=(0.0, 0.0, top_center_z),
        material=blue_material,
        collection=collection,
    )
    created["table_top"] = top

    leg_positions = [
        (-table_width_x / 2 + 0.18, -table_depth_y / 2 + 0.18, leg_height / 2.0),
        ( table_width_x / 2 - 0.18, -table_depth_y / 2 + 0.18, leg_height / 2.0),
        (-table_width_x / 2 + 0.18,  table_depth_y / 2 - 0.18, leg_height / 2.0),
        ( table_width_x / 2 - 0.18,  table_depth_y / 2 - 0.18, leg_height / 2.0),
    ]

    for i, pos in enumerate(leg_positions, start=1):
        leg = create_box_object(
            name=f"Blue Table - Leg {i}",
            verianim_id="blue_table",
            role="support",
            part="table_legs",
            dimensions=(leg_size, leg_size, leg_height),
            location=pos,
            material=blue_material,
            collection=collection,
        )
        created[f"leg_{i}"] = leg

    return created


# -----------------------------
# Camera and lighting
# -----------------------------

def look_at(obj, target):
    loc = Vector(obj.location)
    direction = Vector(target) - loc
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def create_camera(name, verianim_id, location, look_at_point, lens, collection):
    cam_data = bpy.data.cameras.new(f"{name}_data")
    cam_data.lens = lens
    cam_data.sensor_width = 32.0
    cam_data.dof.use_dof = False

    cam_obj = bpy.data.objects.new(name, cam_data)
    cam_obj.location = location
    look_at(cam_obj, look_at_point)
    tag_verianim(cam_obj, verianim_id, role="camera")
    collection.objects.link(cam_obj)
    return cam_obj


def create_area_light(name, verianim_id, location, look_at_point, energy, size, color, collection):
    light_data = bpy.data.lights.new(f"{name}_data", type="AREA")
    light_data.energy = energy
    light_data.size = size
    light_data.color = color

    light_obj = bpy.data.objects.new(name, light_data)
    light_obj.location = location
    look_at(light_obj, look_at_point)
    tag_verianim(light_obj, verianim_id, role="light")
    collection.objects.link(light_obj)
    return light_obj


# -----------------------------
# Environment
# -----------------------------

def create_environment(collection, gray_material):
    floor = create_box_object(
        name="Neutral Gray Studio Floor",
        verianim_id="studio_floor",
        role="environment",
        part="floor",
        dimensions=(6.0, 6.0, 0.04),
        location=(0.0, 0.0, -0.02),
        material=gray_material,
        collection=collection,
    )

    back_wall = create_box_object(
        name="Neutral Gray Studio Back Wall",
        verianim_id="studio_wall_back",
        role="environment",
        part="wall",
        dimensions=(6.0, 0.04, 3.0),
        location=(0.0, 2.0, 1.5),
        material=gray_material,
        collection=collection,
    )

    left_wall = create_box_object(
        name="Neutral Gray Studio Side Wall",
        verianim_id="studio_wall_side",
        role="environment",
        part="wall",
        dimensions=(0.04, 6.0, 3.0),
        location=(-2.8, 0.0, 1.5),
        material=gray_material,
        collection=collection,
    )

    return {
        "floor": floor,
        "back_wall": back_wall,
        "side_wall": left_wall,
    }


# -----------------------------
# Render setup
# -----------------------------

def configure_render(scene):
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        try:
            scene.render.engine = "BLENDER_EEVEE"
        except Exception:
            pass

    if hasattr(scene, "eevee"):
        try:
            scene.eevee.use_gtao = True
            scene.eevee.gtao_distance = 3.0
            scene.eevee.gtao_factor = 1.2
        except Exception:
            pass

    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"

    scene.frame_start = 1
    scene.frame_end = 1
    scene.render.fps = 24

    world = scene.world or bpy.data.worlds.new("VERIANIM_Neutral_Studio_World")
    scene.world = world
    world.color = (0.78, 0.78, 0.78)


# -----------------------------
# Deterministic scene creation
# -----------------------------

clear_scene_safely()

scene = bpy.context.scene
scene.unit_settings.system = "METRIC"
scene.unit_settings.scale_length = 1.0

root_coll = create_collection("VERIANIM_Static_Studio_Scene")
objects_coll = create_collection("VERIANIM_Primary_Objects", root_coll)
environment_coll = create_collection("VERIANIM_Environment", root_coll)
lights_coll = create_collection("VERIANIM_Lights", root_coll)
cameras_coll = create_collection("VERIANIM_Cameras", root_coll)

# Materials from IR
red_material = create_principled_material(
    name="VERIANIM Red Material",
    verianim_id="red_material",
    base_color=(1.0, 0.0, 0.0, 1.0),
    metallic=0.0,
    roughness=0.5,
)

green_material = create_principled_material(
    name="VERIANIM Green Material",
    verianim_id="green_material",
    base_color=(0.0, 1.0, 0.0, 1.0),
    metallic=0.0,
    roughness=0.5,
)

blue_material = create_principled_material(
    name="VERIANIM Blue Material",
    verianim_id="blue_material",
    base_color=(0.0, 0.0, 1.0, 1.0),
    metallic=0.0,
    roughness=0.4,
)

gray_material = create_principled_material(
    name="VERIANIM Neutral Gray Studio Material",
    verianim_id="neutral_gray_material",
    base_color=(0.55, 0.55, 0.55, 1.0),
    metallic=0.0,
    roughness=0.65,
)

# Blue table, top surface z = 0.8 m
table_parts = create_blue_table(objects_coll, blue_material)

# Primary objects
table_top_z = 0.8

red_cube = create_box_object(
    name="Red Cube",
    verianim_id="red_cube",
    role="primary",
    part=None,
    dimensions=(0.5, 0.5, 0.5),
    location=(-0.30, 0.0, table_top_z + 0.25),
    material=red_material,
    collection=objects_coll,
)
red_cube["verianim_relation"] = "rests_on:blue_table"

green_sphere = create_sphere_object(
    name="Green Sphere",
    verianim_id="green_sphere",
    role="primary",
    radius=0.20,
    location=(0.38, 0.0, table_top_z + 0.20),
    material=green_material,
    collection=objects_coll,
)
green_sphere["verianim_relation"] = "rests_on:blue_table; next_to:red_cube"

# Environment
environment_objects = create_environment(environment_coll, gray_material)

# Lights from IR
key_light = create_area_light(
    name="Key Light",
    verianim_id="key_light",
    location=(3.0, -4.0, 5.0),
    look_at_point=(0.0, 0.0, 0.7),
    energy=800.0,
    size=3.0,
    color=(1.0, 1.0, 1.0),
    collection=lights_coll,
)

fill_light = create_area_light(
    name="Fill Light",
    verianim_id="fill_light",
    location=(-3.0, 2.0, 3.0),
    look_at_point=(0.0, 0.0, 0.7),
    energy=400.0,
    size=2.5,
    color=(1.0, 1.0, 1.0),
    collection=lights_coll,
)

back_light = create_area_light(
    name="Back Light",
    verianim_id="back_light",
    location=(0.0, 5.0, 2.0),
    look_at_point=(0.0, 0.0, 0.7),
    energy=300.0,
    size=2.0,
    color=(1.0, 1.0, 1.0),
    collection=lights_coll,
)

# Cameras from IR
camera_three_quarter = create_camera(
    name="Camera Three Quarter",
    verianim_id="camera_three_quarter",
    location=(3.5, -3.5, 2.5),
    look_at_point=(0.0, 0.0, 0.5),
    lens=38.0,
    collection=cameras_coll,
)

camera_contact_closeup = create_camera(
    name="Camera Contact Closeup",
    verianim_id="camera_contact_closeup",
    location=(1.5, -2.0, 1.5),
    look_at_point=(0.0, 0.0, 0.4),
    lens=55.0,
    collection=cameras_coll,
)

camera_side = create_camera(
    name="Camera Side",
    verianim_id="camera_side",
    location=(0.0, -5.0, 1.5),
    look_at_point=(0.0, 0.0, 0.5),
    lens=45.0,
    collection=cameras_coll,
)

scene.camera = camera_three_quarter

# Render settings
configure_render(scene)

# Additional relation markers as custom properties on relevant objects
for obj in table_parts.values():
    obj["verianim_relation_subjects_supported"] = "red_cube,green_sphere"

red_cube["verianim_contact_bottom_z"] = table_top_z
red_cube["verianim_expected_bottom_z"] = table_top_z
red_cube["verianim_dimensions_m"] = "0.5,0.5,0.5"

green_sphere["verianim_contact_bottom_z"] = table_top_z
green_sphere["verianim_expected_bottom_z"] = table_top_z
green_sphere["verianim_radius_m"] = 0.20

# Metadata required by harness/refiner
VERIANIM_METADATA = {
    "version": "0.1",
    "scene_name": "Static studio scene with red cube and green sphere on blue table",
    "units": "meters",
    "object_ids": {
        "red_cube": red_cube.name,
        "green_sphere": green_sphere.name,
        "blue_table": {
            "table_top": table_parts["table_top"].name,
            "table_legs": [
                table_parts["leg_1"].name,
                table_parts["leg_2"].name,
                table_parts["leg_3"].name,
                table_parts["leg_4"].name,
            ],
        },
    },
    "materials": {
        "red_material": red_material.name,
        "green_material": green_material.name,
        "blue_material": blue_material.name,
        "neutral_gray_material": gray_material.name,
    },
    "cameras": {
        "camera_three_quarter": camera_three_quarter.name,
        "camera_contact_closeup": camera_contact_closeup.name,
        "camera_side": camera_side.name,
    },
    "lights": {
        "key_light": key_light.name,
        "fill_light": fill_light.name,
        "back_light": back_light.name,
    },
    "environment": {
        "floor": environment_objects["floor"].name,
        "back_wall": environment_objects["back_wall"].name,
        "side_wall": environment_objects["side_wall"].name,
    },
    "relations": {
        "cube_on_table": {
            "subject": red_cube.name,
            "object": "blue_table",
            "table_top_z": table_top_z,
            "cube_bottom_z": table_top_z,
        },
        "sphere_on_table": {
            "subject": green_sphere.name,
            "object": "blue_table",
            "table_top_z": table_top_z,
            "sphere_bottom_z": table_top_z,
        },
        "sphere_next_to_cube": {
            "subject": green_sphere.name,
            "object": red_cube.name,
            "center_distance_m": round((Vector(green_sphere.location) - Vector(red_cube.location)).length, 4),
        },
        "cube_not_intersecting_sphere": {
            "subject": red_cube.name,
            "object": green_sphere.name,
            "edge_gap_x_m": round((green_sphere.location.x - 0.20) - (red_cube.location.x + 0.25), 4),
        },
    },
    "render": {
        "engine_requested": "eevee",
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "transparent_background": scene.render.film_transparent,
    },
}

# Force dependency graph update for reliable validation after script execution.
bpy.context.view_layer.update()