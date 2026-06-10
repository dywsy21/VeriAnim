import bpy
from blender import ll3m_utils as ll3m


scene = ll3m.clear_scene()
ll3m.configure_render(scene, width=1280, height=720, fps=24, engine="workbench")
scene.frame_start = 1
scene.frame_end = 120
scene.render.fps = 24

red = ll3m.make_material("red_rubber", (0.9, 0.05, 0.04, 1.0), roughness=0.65)
blue = ll3m.make_material("blue_plastic", (0.05, 0.2, 0.9, 1.0), roughness=0.5, alpha=0.38)
blue_edge = ll3m.make_material("blue_box_edges", (0.02, 0.08, 0.55, 1.0), roughness=0.5)
floor_mat = ll3m.make_material("floor_gray", (0.50, 0.50, 0.50, 1.0), roughness=0.8)
contact_mat = ll3m.make_material("contact_shadow", (0.09, 0.02, 0.01, 1.0), roughness=0.9)

ball = ll3m.add_uv_sphere("ball", radius=0.20, segments=32, rings=16, material=red, location=(-2.0, 0.0, 0.225), ll3m_id="ball", ll3m_part="ball_body", ll3m_role="primary")

box = ll3m.add_cube("box", size=(0.7, 0.7, 0.7), material=blue, location=(1.6, 0.0, 0.35), ll3m_id="box", ll3m_part="box_body", ll3m_role="secondary")
box.show_transparent = True
box.display_type = "TEXTURED"
for sx in (-1, 1):
    for sy in (-1, 1):
        edge = ll3m.add_cube(f"box_vertical_edge_{sx}_{sy}", size=(0.025, 0.025, 0.72), material=blue_edge, location=(1.6 + sx * 0.35, sy * 0.35, 0.36), ll3m_role="decoration")
for sx in (-1, 1):
    for sz in (0.0, 0.7):
        edge = ll3m.add_cube(f"box_front_back_edge_{sx}_{sz}", size=(0.025, 0.72, 0.025), material=blue_edge, location=(1.6 + sx * 0.35, 0.0, sz), ll3m_role="decoration")
for sy in (-1, 1):
    for sz in (0.0, 0.7):
        edge = ll3m.add_cube(f"box_left_right_edge_{sy}_{sz}", size=(0.72, 0.025, 0.025), material=blue_edge, location=(1.6, sy * 0.35, sz), ll3m_role="decoration")
floor = ll3m.add_plane("floor", size=6.0, material=floor_mat, location=(0.0, 0.0, 0.0), ll3m_id="floor", ll3m_role="background")
contact_shadow = ll3m.add_cube("ball_floor_contact_shadow", size=(0.46, 0.46, 0.006), material=contact_mat, location=(1.05, 0.0, 0.003), ll3m_role="decoration")
contact_shadow.scale.z = 0.15

for frame, loc, rot in (
    (1, (-2.0, 0.0, 0.225), (0.0, 0.0, 0.0)),
    (60, (-0.45, 0.0, 0.225), (0.0, 3.141592, 0.0)),
    (120, (1.05, 0.0, 0.225), (0.0, 6.283185, 0.0)),
):
    ball.location = loc
    ball.rotation_euler = rot
    ball.keyframe_insert(data_path="location", frame=frame)
    ball.keyframe_insert(data_path="rotation_euler", frame=frame)

ll3m.set_keyframe_interpolation(ball, "LINEAR")

camera = ll3m.add_camera("camera_main", location=(3.8, -4.8, 2.3), look_at_target=(-0.2, 0.0, 0.35), lens=35, make_active=True)
camera["ll3m_id"] = "camera_main"
ll3m.add_light("key_light", light_type="AREA", location=(0.0, -3.0, 4.0), energy=450, size=5.0)

LL3M_METADATA = {
    "objects": {
        "ball": ball.name,
        "box": box.name,
        "floor": floor.name,
        "camera_main": camera.name,
    },
    "materials": {
        "red_rubber": red.name,
        "blue_plastic": blue.name,
    },
}
