# Garage Door Car Exit

Garage door hinge rotation followed by a car leaving the garage; all final validation reports passed.

Source run: `runs\run_20260518_144415`
Type: `animation`

Validation snapshot:
- animation_stage_round_1_scene_preservation.json: passed - Animation stage preserved static scene baseline geometry.
- animation_stage_round_1_scene_deterministic.json: passed - Scene deterministic validation passed.
- animation_stage_round_1_scene_vision.json: passed - All required objects are present and correctly positioned in the static scene. The spatial relationships between the garage components and the car are visually confirmed. The camera views provide clear visibility of all required elements and relations. No visible geometry defects or occlusion issues are present.
- animation_stage_round_1_animation_deterministic.json: passed - Animation deterministic validation passed.
- animation_stage_round_1_animation_video.json: passed - The animation satisfies all required conditions. The garage door rotates upward from 0 to 90 degrees, and the car moves from inside the garage to outside, stopping beyond the door. All objects remain properly connected and on the floor. The sampled frames confirm the expected motion at key points.

Prompt:
```text
Create a medium-difficulty animated garage scene. Static scene first: a simple garage with two side walls, a back wall, a roof slab, an open front, a hinged rectangular garage door at the front, and a small yellow car inside on the floor. Camera must clearly see the door, car, floor, walls, and front opening. Required relations: car rests on the garage floor, door is attached at the front top edge as a hinged panel, walls support the roof, and the car is inside the garage behind the door. Then animate it: first the garage door rotates upward around its top hinge until the front opening is clear, then the yellow car drives straight forward out of the garage and stops outside. The garage walls, roof, and floor stay fixed. Make the door rotation and car movement clearly visible in the GIF and sampled frames.
```
