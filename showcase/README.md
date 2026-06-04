# VeriAnim Showcase

Presentation-ready artifacts selected from existing runs. Each directory contains the prompt, model manifest when available, generated Python source, a static scene JPG, metadata, and a `.blend` file exported from the Python source. Animation entries also include GIF previews and MP4 previews when the run produced them.

## Animation

- `parallel_gripper_pick_place`: curated factory pick-and-place primitive replay with sampled frames and GIF.
- `conveyor_box_on_belt`: orange box riding on a gray conveyor; deterministic, vision, and qwen3-omni video checks passed.
- `garage_door_car_exit`: hinged garage door opens and a car drives out.
- `marble_run_into_cup`: green marble rolls down a ramp into a blue catch cup.
- `green_car_ramp_to_platform`: toy car follows road, ramp, and platform support sequence.
- `yellow_puck_down_ramp`: puck moves from table to ramp to lower platform.
- `green_cube_table_slide`: simple clean baseline for supported table-top sliding.
- `collision_two_cubes`: equal-mass elastic collision where the blue cube stops and the red cube departs.

## Static Scenes

- `static_desktop_workspace`: warm desktop workspace scene with table, mug, plant, notebook, and lamp.
- `static_cube_sphere_table`: red cube and green sphere on a blue table.
- `static_conveyor_box_gripper`: static rigid setup with belt, box, tray, and gripper components.

## Re-exporting Blend Files

Run:

```powershell
python scripts\export_blend_from_python.py showcase\<name>\source.py showcase\<name>\scene.blend --blender "C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
```
