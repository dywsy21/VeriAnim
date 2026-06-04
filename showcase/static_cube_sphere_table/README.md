# Static Cube Sphere Table

Clean static spatial-relation example with cube and sphere on a table; deterministic and vision checks passed.

Source run: `runs\run_20260514_212346`
Type: `static_scene`

Validation snapshot:
- initial_round_0_scene_deterministic.json: passed - Scene deterministic validation passed.
- initial_round_0_scene_vision.json: passed - All required objects are visible with correct colors and shapes. The red cube and green sphere appear to rest on the blue tabletop, the sphere is adjacent to the cube without evident intersection, and the table appears stable with connected legs under neutral studio lighting. Camera views provide sufficient coverage of the required spatial relationships.

Prompt:
```text
a red cube resting on top of a blue rectangular table, with a green sphere next to the cube on the same table surface, all objects clearly visible under neutral studio lighting
```
