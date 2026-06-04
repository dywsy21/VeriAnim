# Green Car Ramp To Platform

Vehicle support-sequence animation over road, ramp, and platform; deterministic contact repair and qwen3-omni video checks passed.

Source run: `runs\run_20260530_160705`
Type: `animation`

Models:
- planner: `openai/deepseek-v4-pro`
- coder: `openai/deepseek-v4-pro`
- refiner: `openai/deepseek-v4-pro`
- vision: `openai/qwen3-omni`
- video: `openai/qwen3-omni`

Validation snapshot:
- animation_stage_round_0_scene_vision.json: passed - All required objects are present and correctly positioned. The car is on the road, the ramp connects the road to the platform, and all components are properly supported. The camera views provide sufficient coverage for verification.
- animation_stage_round_0_animation_deterministic.json: failed - Animation deterministic validation found issues.
- animation_stage_round_0_animation_deterministic_after_contact_repair.json: passed - Animation deterministic validation passed.
- animation_stage_round_0_animation_video.json: passed - The animation satisfies all specified requirements. The green car is visible at all sampled frames (1, 28, 60, 87, 120). It starts on the lower gray road, moves up the sloped ramp, and comes to rest fully on the raised blue platform. The car's position and orientation in the sampled frames align with the expected path and support constraints. No visible clipping, floating, or unsupported motion is observed. The final state is fully visible and correctly placed.

Prompt:
```text
A small green toy car starts on a lower gray road, drives up a sloped ramp onto a raised blue platform, stays supported by the road, ramp, or platform at every sampled frame, never clips through any surface, and stops fully on top of the platform.
```
