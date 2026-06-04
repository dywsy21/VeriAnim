# Green Cube Table Slide

Simple table-top slide used as a clean baseline; deterministic, vision, and qwen3-omni video checks passed.

Source run: `runs\run_20260602_223630`
Type: `animation`

Models:
- planner: `openai/deepseek-v4-pro`
- coder: `openai/deepseek-v4-pro`
- refiner: `openai/deepseek-v4-pro`
- vision: `openai/qwen3-omni`
- video: `openai/qwen3-omni`

Validation snapshot:
- animation_stage_round_0_scene_preservation.json: passed - Animation stage preserved static scene baseline geometry.
- animation_stage_round_0_scene_deterministic.json: passed - Scene deterministic validation passed.
- animation_stage_round_0_scene_vision.json: passed - All required objects are present and correctly positioned. The green cube is resting on the gray tabletop with no visible gap or penetration. The table is supported by four legs on the floor. The camera views clearly show the required contact and spatial relationships. No visible geometry defects or occlusion issues are present.
- animation_stage_round_0_animation_deterministic.json: passed - Animation deterministic validation passed.
- animation_stage_round_0_animation_video.json: passed - The animation meets all specified requirements. The green cube is visible at the start, middle, and end frames, sliding smoothly from the left to the right side of the gray table. The cube remains in contact with the tabletop throughout, with no visible floating, sinking, or penetration. The side camera view clearly shows the cube's motion and contact with the table surface. The transform trace aligns with the visual evidence, confirming the cube's horizontal translation. All pass criteria are satisfied.

Prompt:
```text
Create a simple animation of a green cube sliding from the left side of a gray table to the right side of the same table. The cube must remain resting on the tabletop with no floating or penetration for the whole motion. Use a side camera that clearly shows the contact.
```
