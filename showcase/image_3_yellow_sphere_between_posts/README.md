# Image 3 Yellow Sphere Between Posts

Yellow sphere suspended between two gray posts, matching the selected image.

Source run: `runs/ir_qwen_key_until_pass_20260607_065033/_promote_sources/image_3_yellow_sphere_between_posts`
Type: `animation`

Models:
- planner: `openai/gpt-4.1`
- coder: `openai/deepseek-v4-flash`
- refiner: `openai/deepseek-v4-flash`
- vision: `dashscope/qwen-vl-max`
- video: `dashscope/qwen3.6-plus`

Validation snapshot:
- round6_animation_deterministic.json: passed - Animation deterministic validation passed.
- round6_animation_video.json: failed - The animation fails because the required subject (a narrow cloth banner) is completely absent. The scene instead contains a large yellow sphere between two posts. The sphere does not deform or ripple; it remains static across all sampled frames. The transform trace indicates scaling changes for a 'cloth_patch', but the visual evidence shows a sphere that does not change shape. The prompt explicitly requested a cloth banner anchored between posts, which is not present.

Prompt:
```text
Create a narrow cloth banner that ripples up and down while anchored between two posts.
```
