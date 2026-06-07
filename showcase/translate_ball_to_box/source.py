import bpy
from blender import verianim_utils as verianim


class _LegacyLL3MCompat:
    def __getattr__(self, name):
        return getattr(verianim, name)

    @staticmethod
    def _kwargs(kwargs):
        kwargs = dict(kwargs)
        if "ll3m_id" in kwargs:
            kwargs["verianim_id"] = kwargs.pop("ll3m_id")
        if "ll3m_part" in kwargs:
            kwargs["verianim_part"] = kwargs.pop("ll3m_part")
        if "ll3m_role" in kwargs:
            kwargs["verianim_role"] = kwargs.pop("ll3m_role")
        return kwargs

    def add_cube(self, *args, **kwargs):
        return verianim.add_cube(*args, **self._kwargs(kwargs))

    def add_plane(self, *args, **kwargs):
        return verianim.add_plane(*args, **self._kwargs(kwargs))

    def add_uv_sphere(self, *args, **kwargs):
        return verianim.add_uv_sphere(*args, **self._kwargs(kwargs))

    def create_mesh_object(self, *args, **kwargs):
        return verianim.create_mesh_object(*args, **self._kwargs(kwargs))


ll3m = _LegacyLL3MCompat()


scene = ll3m.clear_scene()
ll3m.configure_render(scene, width=1280, height=720, fps=24, engine="workbench")
scene.frame_start = 1
scene.frame_end = 120
scene.render.fps = 24

red = ll3m.make_material("red_rubber", (0.9, 0.05, 0.04, 1.0), roughness=0.65)
stripe_mat = ll3m.make_material("dark_rolling_stripe", (0.08, 0.01, 0.01, 1.0), roughness=0.7)
blue = ll3m.make_material("blue_plastic", (0.05, 0.2, 0.9, 1.0), roughness=0.5)
floor_mat = ll3m.make_material("floor_gray", (0.50, 0.50, 0.50, 1.0), roughness=0.8)

ball = bpy.data.objects.new("ball", None)
bpy.context.scene.collection.objects.link(ball)
ball["ll3m_id"] = "ball"
ball["ll3m_role"] = "primary"
ball.location = (-2.0, 0.0, 0.225)
ball_body = ll3m.add_uv_sphere("ball_body", radius=0.225, segments=32, rings=16, material=red, location=(0.0, -0.58, 0.0), ll3m_part="ball_body", ll3m_role="primary")
ball_body.parent = ball
stripe_a = ll3m.add_cube("rolling_stripe_equator", size=(0.43, 0.025, 0.025), material=stripe_mat, location=(0.0, 0.0, 0.0), ll3m_role="decoration")
stripe_b = ll3m.add_cube("rolling_stripe_meridian", size=(0.025, 0.43, 0.025), material=stripe_mat, location=(0.0, 0.0, 0.0), ll3m_role="decoration")
for stripe in (stripe_a, stripe_b):
    stripe.parent = ball_body
    stripe.location = (0.0, 0.0, 0.0)

box = ll3m.add_cube("box", size=(0.7, 0.7, 0.7), material=blue, location=(1.6, 0.0, 0.35), ll3m_id="box", ll3m_part="box_body", ll3m_role="secondary")
floor = ll3m.add_plane("floor", size=6.0, material=floor_mat, location=(0.0, 0.0, 0.0), ll3m_id="floor", ll3m_role="background")

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
