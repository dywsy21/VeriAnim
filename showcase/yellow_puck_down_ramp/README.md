# Yellow Puck Down Ramp

Puck moves across table, ramp, and lower platform with repaired support sequence; final video verification passed.

Source run: `runs\run_20260530_161959`
Type: `animation`

Models:
- planner: `openai/deepseek-v4-pro`
- coder: `openai/deepseek-v4-pro`
- refiner: `openai/deepseek-v4-pro`
- vision: `openai/qwen3-omni`
- video: `openai/qwen3-omni`

Validation snapshot:
- animation_stage_round_0_scene_vision.json: passed - All required objects are present and correctly positioned. The ramp is visibly attached to both the table and the platform, and the puck is correctly placed on the table. The camera views provide sufficient coverage to verify all required relations and object placements. No visible geometry defects or incorrect spatial relationships are observed.
- animation_stage_round_0_animation_deterministic.json: failed - Animation deterministic validation found issues.
- animation_stage_round_0_animation_deterministic_after_contact_repair.json: passed - Animation deterministic validation passed.
- animation_stage_round_0_animation_video.json: passed - The animation meets all specified criteria. The yellow puck is visible at all sampled frames (1, 25, 60, 120). It starts on the table, moves down the ramp, and comes to rest flat on the blue platform. The puck is supported by exactly one surface at each frame, with no visible penetration or floating. The final resting state is fully visible and not occluded.

Prompt:
```text
A yellow puck slides from the left side of a gray table, down a short ramp, onto a lower blue platform, stays supported by exactly one surface at each sampled frame, never clips through the table, ramp, or platform, and stops flat on the platform.
```
