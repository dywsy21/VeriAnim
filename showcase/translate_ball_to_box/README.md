# Translate Ball To Box

Red ball rolls across the floor and stops beside a blue box; deterministic, scene vision, and video checks passed.

Source run: `runs/ir_qwen_key_until_pass_20260607_065033/manual_ball_qwen_pass_hist`
Type: `animation`

Models:
- planner: `openai/deepseek-v4-flash`
- coder: `openai/deepseek-v4-flash`
- refiner: `openai/deepseek-v4-flash`
- vision: `dashscope/qwen-vl-max`
- video: `dashscope/qwen3.6-plus`

Validation snapshot:
- manual_round_0_animation_deterministic.json: passed - Animation deterministic validation passed.
- manual_round_0_animation_video.json: passed - The animation successfully depicts a red ball rolling from the left side of the floor to stop beside a blue box. The ball is visible throughout the sequence, moving from left to right. The final frame shows the ball resting next to the box. The ball remains on the floor surface without floating or sinking. The rotation of the ball is visible, consistent with rolling motion.
- manual_round_0_scene_deterministic.json: passed - Scene deterministic validation passed.
- manual_round_0_scene_vision.json: passed - The scene correctly depicts a small red ball and a blue box with proper placement, materials, and spatial relationships. All required objects are present, visually correct, and positioned as specified. The ball is shown resting on the floor in all views, and it stops near the blue box as required. Camera angles provide sufficient coverage to verify the final state and path.

Prompt:
```text
Create a small red ball that rolls from the left side of the floor to stop beside a blue box.
```
