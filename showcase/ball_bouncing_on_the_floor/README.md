# Ball Bouncing On The Floor

Promoted animation artifact from run_20260607_005435.

Source run: `runs/run_20260607_005435`
Type: `animation`

Models:
- planner: `openai/deepseek-v4-pro`
- coder: `openai/deepseek-v4-pro`
- refiner: `openai/deepseek-v4-pro`
- vision: `openai/qwen3.5-omni-plus`
- video: `openai/qwen3.5-omni-plus`

Validation snapshot:
- animation_stage_round_1_animation_deterministic.json: passed - Animation deterministic validation passed.
- animation_stage_round_1_animation_video.json: failed - The animation fails to display the required motion. The visual evidence (video and sampled frames) shows a static red ball resting on the ground throughout the entire duration. There is no visible falling, bouncing, or vertical movement as specified in the AnimationSpec and Transform Trace. The object remains stationary at the final rest position from the very first frame.
- animation_stage_round_1_scene_deterministic.json: passed - Scene deterministic validation passed.
- animation_stage_round_1_scene_preservation.json: passed - Animation stage preserved static scene baseline geometry.
- animation_stage_round_1_scene_vision.json: passed - The scene correctly depicts the required objects (ball and ground) in the specified initial state. The ball is clearly positioned above the ground plane without intersection, consistent with the 'floating' start of a bounce animation described in the prompt and visual check prompts. All three required views (three_quarter, side_support, closeup) are present and show the subjects fully within the frame. Lighting and materials are sufficient to distinguish the objects.
- animation_stage_round_2_code_static.json: failed - Generated code failed static completeness checks.
- animation_stage_round_3_code_static.json: failed - Generated code failed static completeness checks.
- scene_stage_round_2_scene_deterministic.json: passed - Scene deterministic validation passed.
- scene_stage_round_2_scene_vision.json: passed - The scene correctly depicts the required objects (red ball and gray ground plane) in the specified spatial relationship. The ball is clearly floating above the ground without intersection, consistent with the 'ball_above_ground_not_intersecting' relation and the deterministic report. All three required views are present and legible.

Prompt:
```text
A ball bounces on the ground and slowly comes to a stop
```
