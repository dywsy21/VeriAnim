# Conveyor Box On Belt

Clean conveyor belt rigid support animation; deterministic, vision, and qwen3-omni video checks passed.

Source run: `runs\run_20260530_115528`
Type: `animation`

Models:
- planner: `openai/claude-opus-4-8`
- coder: `openai/claude-opus-4-8`
- refiner: `openai/claude-opus-4-8`
- vision: `openai/qwen3-omni`
- video: `openai/qwen3-omni`

Validation snapshot:
- animation_stage_round_0_scene_preservation.json: passed - Animation stage preserved static scene baseline geometry.
- animation_stage_round_0_scene_deterministic.json: passed - Scene deterministic validation passed.
- animation_stage_round_0_scene_vision.json: passed - All required objects are present and correctly positioned. The orange box is visibly resting on the gray conveyor belt with no signs of floating or sinking. The camera views provided are sufficient to verify the required relations, and no geometric defects or occlusion issues are observed.
- animation_stage_round_0_animation_deterministic.json: passed - Animation deterministic validation passed.
- animation_stage_round_0_animation_video.json: passed - The animation satisfies all specified requirements. The orange box moves from left to right along the gray conveyor belt, remains visibly supported on its top surface throughout, and ends resting on the belt. All sampled frames (start, midpoint, end) show the box in contact with the belt, with no visible gaps or penetration. The box is fully visible in all frames, and the final state shows it clearly resting on the belt.

Prompt:
```text
An orange cardboard box rides on top of a gray moving conveyor belt from left to right, stays supported by the belt the entire time, never sinks through it, and stops resting visibly on top of the belt at the end.
```
