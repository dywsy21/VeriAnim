# VeriAnim-AnimBench

VeriAnim-AnimBench contains 300 natural-language Blender animation prompts.
The benchmark has 100 easy, 100 medium, and 100 hard prompts.

- Easy prompts isolate one object and one dominant motion.
- Medium prompts coordinate two or three objects, contact relations, or camera coverage.
- Hard prompts combine multi-object composition, temporal ownership, concurrent motion, or mixed animation families.

Each JSONL record contains `id`, `tier`, `families`, `prompt`, `required_motions`, `verifier_focus`, and `difficulty_rationale`.
