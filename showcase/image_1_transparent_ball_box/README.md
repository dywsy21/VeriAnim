# Image 1 Transparent Ball Box

Red ball moving beside a transparent blue wireframe box, matching the selected image.

Source run: `runs/run_20260603_113929`
Type: `animation`

Models:
- planner: `openai/deepseek-v4-flash`
- coder: `openai/deepseek-v4-flash`
- refiner: `openai/deepseek-v4-flash`
- vision: `openai/deepseek-v4-flash`
- video: `openai/deepseek-v4-flash`

Validation snapshot:
- manual_round_0_animation_deterministic.json: passed - Animation deterministic validation passed.
- manual_round_0_animation_video.json: failed - Video verifier model does not accept video input.
- manual_round_0_scene_deterministic.json: passed - Scene deterministic validation passed.
- manual_round_0_scene_vision.json: failed - Vision verifier model does not accept image input.

Prompt:
```text
Create a small red ball that rolls from the left side of the floor to stop beside a blue box.
```
