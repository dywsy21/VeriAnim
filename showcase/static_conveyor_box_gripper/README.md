# Static Conveyor Box Gripper

Static rigid scene with belt, box, tray, and gripper components; deterministic and vision checks passed.

Source run: `runs\run_20260603_093252`
Type: `static_scene`

Models:
- planner: `openai/deepseek-v4-pro`
- coder: `openai/deepseek-v4-pro`
- refiner: `openai/deepseek-v4-pro`
- vision: `openai/qwen3-omni`
- video: `openai/qwen3-omni`

Validation snapshot:
- scene_stage_round_0_scene_deterministic.json: passed - Scene deterministic validation passed.
- scene_stage_round_0_scene_vision.json: passed - All required objects are present and correctly positioned according to the SceneSpec. The spatial relations 'belt_on_ground', 'box_on_belt_start', and 'tray_on_ground' are visually confirmed. The camera views provide adequate coverage for verification. No visible geometry defects or missing components are observed.

Prompt:
```text
Create a harder rigid-body animation scene: a gray conveyor belt supports an orange box. The box rides smoothly along the top surface of the belt from left to right without ever falling through or penetrating the belt. A simple robotic gripper with two gray fingers approaches from above, closes around the orange box without finger-box penetration, lifts it off the belt, carries it to the right, and places it fully on a blue tray. Add a small green status light that appears only after the box is placed. Keep the belt, box, gripper fingers, tray, and status light visible in sampled frames, with deterministic contact constraints for belt support, carry contact, tray support, and finger nonpenetration.
```
