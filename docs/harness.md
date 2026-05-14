# Local Harness Implementation

The local harness is separate from the original cloud-client flow in `main.py`.
Use it with:

```bash
python -m harness.runner --text "a cup on a wooden table in a small kitchen"
```

Or use the interactive TUI:

```bash
python -m harness.tui
```

In the TUI, the first message creates a new scene. Later messages are treated as
multi-turn change requests against the current Blender scene and script, for
example "make the chair taller" or "add a small lamp on the table". Blender is
updated after each turn when the generated script executes successfully.

For animation:

```bash
python -m harness.runner --text "a red ball rolls into a cardboard box" --animation
```

To use a previously planned IR:

```bash
python -m harness.runner --ir runs/run_xxx/ir.json
```

## Dependencies

The harness uses open-source libraries:

- `python-dotenv` for `.env` loading.
- `litellm` for unified LLM and multimodal LLM calls.

Install with:

```bash
pip install -r requirements.txt
```

## Environment

Copy `.env.example` to `.env` and fill the API keys for the providers you use.
Different agents can use different models:

- `LL3M_PLANNER_MODEL`
- `LL3M_CODER_MODEL`
- `LL3M_REFINER_MODEL`
- `LL3M_VISION_MODEL`
- `LL3M_VIDEO_MODEL`

Each agent can also use its own endpoint and key:

- `LL3M_PLANNER_API_BASE`, `LL3M_PLANNER_API_KEY`, `LL3M_PLANNER_API_VERSION`, `LL3M_PLANNER_PROVIDER`
- `LL3M_CODER_API_BASE`, `LL3M_CODER_API_KEY`, `LL3M_CODER_API_VERSION`, `LL3M_CODER_PROVIDER`
- `LL3M_REFINER_API_BASE`, `LL3M_REFINER_API_KEY`, `LL3M_REFINER_API_VERSION`, `LL3M_REFINER_PROVIDER`
- `LL3M_VISION_API_BASE`, `LL3M_VISION_API_KEY`, `LL3M_VISION_API_VERSION`, `LL3M_VISION_PROVIDER`
- `LL3M_VIDEO_API_BASE`, `LL3M_VIDEO_API_KEY`, `LL3M_VIDEO_API_VERSION`, `LL3M_VIDEO_PROVIDER`

Leave these empty when the global provider environment variables are sufficient.
Set them when, for example, coder uses OpenAI, vision uses an OpenRouter VLM, and
video uses a DashScope/Qwen Omni endpoint.

The default video model hint is `dashscope/qwen-omni-turbo`. Replace it with the
LiteLLM model name that matches your deployed Qwen Omni or other video-capable
endpoint.

## Pipeline

1. `PlannerAgent`
   - Converts prompt into `GenerationIR`.
   - Includes screenshot and video verification plans.

2. `CoderAgent`
   - Uses RAG notes from `docs/rag`.
   - Generates one Blender 4.5.4 Python script.

3. `BlenderRuntime`
   - Executes generated code through the existing Blender addon socket server.
   - Runs deterministic validation inside Blender.
   - Renders screenshot views and animation sampled frames.

4. `VisionVerifierAgent`
   - Sends labeled screenshots to a multimodal model through LiteLLM.
   - Produces `ValidationReport`.

5. `VideoVerifierAgent`
   - Sends ordered sampled frames plus video metadata to a temporal/multimodal
     model through LiteLLM.
   - Produces `ValidationReport`.

6. `RefinerAgent`
   - Repairs the Blender Python script from execution errors and validation
     reports.

## Outputs

Each run creates:

```text
runs/run_YYYYMMDD_HHMMSS/
  ir.json
  code/
    generated_scene.py
    refined_round_*.py
    final_scene.py
  reports/
  screenshots/
  animation/
  logs/
```

## Blender Requirement

Open Blender 4.5.4, install the existing `blender/addon.py`, and start the LL3M
server in Blender before running the harness. The harness talks to the addon on
`LL3M_BLENDER_HOST` and `LL3M_BLENDER_PORT`.

The addon now exposes structured commands used by the local harness:

- `get_scene_graph`
- `get_object_bbox`
- `get_material_info`
- `get_camera_view_report`
- `get_animation_info`
- `sample_object_transforms`
- `run_validation`
- `render_view_plan`
- `render_animation_preview`

Reinstall or reload `blender/addon.py` in Blender after pulling these changes.
