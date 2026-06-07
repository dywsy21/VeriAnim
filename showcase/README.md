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
- `fish_swimming_in_the_ocean`: Promoted animation artifact from run_20260531_165731.
- `bird_flying_around_a_tree`: Promoted animation artifact from run_20260606_231935.
- `ball_bouncing_on_the_floor`: Promoted animation artifact from run_20260607_005435.

## Static Scenes

- `static_desktop_workspace`: warm desktop workspace scene with table, mug, plant, notebook, and lamp.
- `static_cube_sphere_table`: red cube and green sphere on a blue table.
- `static_conveyor_box_gripper`: static rigid setup with belt, box, tray, and gripper components.

## Promoting Runs

Convert a successful `runs/run_*` directory into a showcase entry:

```bash
python scripts/promote_run_to_showcase.py runs/run_20260606_231203 wooden_chair_table --note "Clean static furniture scene." --update-index
```

For animation entries, add sampled frames when they are useful for inspection:

```bash
python scripts/promote_run_to_showcase.py runs/run_20260606_231935 bird_orbit --copy-frames --update-index
```

Animation entries automatically create `animation.mp4` from `animation.gif` with
`ffmpeg` when the source run does not already include an MP4 preview.

## Generating The Showroom

Build a mobile-friendly static website for live talks and QR-code sharing:

```bash
python scripts/generate_showroom.py
```

The generator reads every `showcase/<name>/metadata.json`, copies preview media,
and writes `showroom/index.html` plus `showroom/assets/`. Re-run it after adding
or updating showcase entries.

## Re-exporting Blend Files

Run:

```powershell
python scripts\export_blend_from_python.py showcase\<name>\source.py showcase\<name>\scene.blend --blender "C:\Program Files\Blender Foundation\Blender 4.5\blender.exe"
```
