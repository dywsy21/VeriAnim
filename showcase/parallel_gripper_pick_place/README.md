# Parallel Gripper Pick Place Showcase

Prompt is saved in `prompt.txt`.

This showcase preserves the validated sampled frames from the factory pick-and-place animation used to verify the parallel gripper rigid-animation primitive.

Key validation notes:
- The orange box rides the gray conveyor, is lifted by a two-finger gripper, and is placed on the blue tray.
- The final tray placement uses the safe destination support logic, moving the box away from the conveyor footprint.
- Manual bbox audit after reloading `blender.verianim_utils` showed final box/conveyor x-overlap changed to `-0.04`, so the placed box no longer horizontally overlaps the conveyor edge.
- The final box/tray z gap was approximately `-0.001`, consistent with contact margin tolerance.

Frames:
- `frame_0001.png`: initial conveyor pose.
- `frame_0030.png`: gripper over the box.
- `frame_0065.png`: box lifted clear of the conveyor.
- `frame_0095.png`: carried toward the tray.
- `frame_0115.png`: lowered onto the tray.
- `frame_0130.png`: final placement with green indicator visible.
