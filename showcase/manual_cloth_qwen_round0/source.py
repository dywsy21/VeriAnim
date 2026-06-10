import math

import bpy
from blender import ll3m_utils as ll3m


scene = ll3m.clear_scene()
ll3m.configure_render(scene, width=1280, height=720, fps=24, engine="workbench")
scene.frame_start = 1
scene.frame_end = 120
scene.render.fps = 24

cloth_mat = ll3m.make_material("cloth_yellow", (0.95, 0.72, 0.12, 1.0), roughness=0.8)
post_mat = ll3m.make_material("post_gray", (0.22, 0.22, 0.24, 1.0), roughness=0.65)
edge_mat = ll3m.make_material("dark_ripple_markers", (0.12, 0.10, 0.08, 1.0), roughness=0.7)
floor_mat = ll3m.make_material("floor_neutral", (0.42, 0.42, 0.44, 1.0), roughness=0.8)

left_post = ll3m.add_cube("left_post", size=(0.08, 0.08, 1.4), material=post_mat, location=(-0.9, 0.0, 0.7), ll3m_id="left_post", ll3m_role="support")
right_post = ll3m.add_cube("right_post", size=(0.08, 0.08, 1.4), material=post_mat, location=(0.9, 0.0, 0.7), ll3m_id="right_post", ll3m_role="support")
floor = ll3m.add_plane("floor", size=4.5, material=floor_mat, location=(0.0, 0.0, 0.0), ll3m_id="floor", ll3m_role="background")

cols = 24
rows = 10
width = 1.50
height = 0.62
verts = []
for row in range(rows + 1):
    z = -height / 2.0 + height * row / rows
    for col in range(cols + 1):
        x = -width / 2.0 + width * col / cols
        y = 0.035 * math.sin(col / cols * math.tau * 2.0)
        verts.append((x, y, z))
faces = []
for row in range(rows):
    for col in range(cols):
        a = row * (cols + 1) + col
        faces.append((a, a + 1, a + cols + 2, a + cols + 1))

cloth = ll3m.create_mesh_object("cloth_patch", verts, faces, material=cloth_mat, location=(-0.02, 0.0, 1.12), ll3m_id="cloth_patch", ll3m_part="cloth_surface", ll3m_role="primary")
cloth.show_wire = True

basis = cloth.shape_key_add(name="Basis")
wave_mid = cloth.shape_key_add(name="opposite_ripple")
wave_end = cloth.shape_key_add(name="stretched_ripple")
for idx, key in enumerate(wave_mid.data):
    x, y, z = verts[idx]
    key.co.y = y + 0.12 * math.sin((x + width / 2.0) / width * math.tau * 1.5 + math.pi)
    key.co.z = z + 0.045 * math.sin((x + width / 2.0) / width * math.tau * 2.0)
for idx, key in enumerate(wave_end.data):
    x, y, z = verts[idx]
    key.co.y = y + 0.10 * math.sin((x + width / 2.0) / width * math.tau * 1.5 + math.pi / 2.0)
    key.co.z = z + 0.035 * math.sin((x + width / 2.0) / width * math.tau * 2.0 + math.pi / 2.0)

for frame, mid_value, end_value, scale in (
    (1, 0.0, 0.0, (1.0, 1.0, 1.0)),
    (60, 1.0, 0.0, (0.92, 1.0, 1.12)),
    (120, 0.0, 1.0, (1.08, 1.0, 0.86)),
):
    scene.frame_set(frame)
    cloth.scale = scale
    cloth.keyframe_insert(data_path="scale", frame=frame)
    wave_mid.value = mid_value
    wave_end.value = end_value
    wave_mid.keyframe_insert("value", frame=frame)
    wave_end.keyframe_insert("value", frame=frame)

for name, x in (("left", -0.82), ("right", 0.82)):
    connector = ll3m.add_cube(f"{name}_anchor_stripe", size=(0.18, 0.028, 0.08), material=edge_mat, location=(x, -0.055, 1.12), ll3m_role="decoration")
for name, x in (("left", -width / 2.0), ("right", width / 2.0)):
    edge = ll3m.add_cube(f"{name}_cloth_edge_stripe", size=(0.024, 0.018, height * 0.95), material=edge_mat, location=(0.0, 0.0, 0.0), ll3m_role="decoration")
    edge.parent = cloth
    edge.location = (x, -0.035, 0.0)
for idx, z in enumerate((-0.18, 0.0, 0.18)):
    marker = ll3m.add_cube(f"ripple_stripe_{idx}", size=(1.24, 0.014, 0.014), material=edge_mat, location=(0.0, 0.0, 0.0), ll3m_role="decoration")
    marker.parent = cloth
    marker.location = (0.0, -0.04, z)

camera = ll3m.add_camera("camera_main", location=(0.0, -4.2, 1.35), look_at_target=(0.0, 0.0, 1.1), lens=45, make_active=True)
camera["ll3m_id"] = "camera_main"
ll3m.add_light("key_light", light_type="AREA", location=(0.0, -3.0, 4.0), energy=500, size=5.0)

LL3M_METADATA = {
    "objects": {
        "cloth_patch": cloth.name,
        "left_post": left_post.name,
        "right_post": right_post.name,
        "floor": floor.name,
        "camera_main": camera.name,
    },
    "materials": {
        "cloth_yellow": cloth_mat.name,
        "post_gray": post_mat.name,
    },
}
