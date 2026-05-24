# LL3M Animation Harness

This repository is a local Blender scene and animation generation harness built
on top of the original LL3M codebase. It does not use the discontinued LL3M
cloud service flow. The active entry points are:

```bash
python -m harness.runner --text "a cup on a wooden table in a small kitchen"
python -m harness.tui
```

The harness targets **Blender 4.5.4 LTS** and uses LiteLLM-compatible models for
planning, Blender Python generation, refinement, visual verification, and video
verification.

## What It Adds

- Structured scene and animation IR v0.2.
- Planner, coder, refiner, visual verifier, and video verifier agents.
- Deterministic Blender validation plus screenshot/video verification gates.
- Two-stage animation generation: validate the static scene first, then add
  animation while preserving the baseline scene.
- Blender addon socket server with scene inspection, validation, screenshot,
  and animation sampling commands.
- Optional material texture search and vision approval.
- Interactive Textual TUI for multi-turn scene editing.

## Requirements

- Python 3.12 recommended.
- Blender 4.5.4 LTS.
- An LLM provider supported by LiteLLM.
- A multimodal model for visual verification if `LL3M_TUI_SKIP_VISION=false` or
  runner visual verification is enabled.
- A video-capable model if animation video verification is enabled.

Install Python dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows, activate the environment with:

```powershell
.\.venv\Scripts\activate
```

## Configuration

Copy the example environment file and fill the provider keys/models you use:

```bash
cp .env.example .env
```

The most common settings are:

```env
OPENAI_API_KEY=
DASHSCOPE_API_KEY=

LL3M_PLANNER_MODEL=openai/gpt-4.1
LL3M_CODER_MODEL=openai/gpt-4.1
LL3M_REFINER_MODEL=openai/gpt-4.1
LL3M_VISION_MODEL=openai/gpt-4.1
LL3M_VIDEO_MODEL=dashscope/qwen-omni-turbo

LL3M_BLENDER_HOST=localhost
LL3M_BLENDER_PORT=8888
LL3M_RUNS_DIR=runs
```

All LLM calls go through LiteLLM. You can route all agents through a shared
OpenAI-compatible gateway with `LITELLM_API_BASE` and `LITELLM_API_KEY`, or set
agent-specific keys such as `LL3M_CODER_API_KEY` and `LL3M_VISION_API_KEY`.

## Start Blender

Open Blender 4.5.4, install `blender/addon.py`, enable the "LL3M Blender"
addon, then use the 3D View sidebar:

```text
N panel > LL3M > Start LL3M Server
```

The default server port is `8888`, matching `LL3M_BLENDER_PORT`.

You can also start the server from Blender's command line:

```bash
blender --background --python scripts/start_ll3m_server.py
```

For a GUI Blender process:

```bash
blender --python scripts/start_ll3m_server_gui.py
```

## Run The Harness

Generate a static scene:

```bash
python -m harness.runner --text "a wooden chair beside a small round table"
```

Generate a scene with animation:

```bash
python -m harness.runner --text "a red ball rolls into a cardboard box" --animation
```

Run from an existing IR file:

```bash
python -m harness.runner --ir examples/animation_ir/translate_ball_to_box.json --animation
```

Skip model-based visual or video verification when debugging provider setup:

```bash
python -m harness.runner --text "a blue mug on a shelf" --skip-vision --skip-video
```

Use the interactive TUI:

```bash
python -m harness.tui
```

In the TUI, the first prompt creates a new scene. Later prompts modify the
current scene and script. Useful commands are `/animation`, `/vision`, `/video`,
`/clear`, and `/quit`.

## Outputs

Each run writes artifacts under `runs/`, including:

- `ir.json` and stage-specific IR snapshots.
- Generated/refined Blender Python under `code/`.
- Execution logs under `logs/`.
- Deterministic and model verifier reports under `reports/`.
- Screenshot and animation previews under `screenshots/` and `animation/`.
- Selected texture assets under `textures/` when texture search is enabled.

## Validation And Tests

Run the current unit tests with:

```bash
make test
```

Quickly check whether the default Blender socket port can bind inside Blender:

```bash
blender --background --python scripts/blender_socket_bind_smoke.py
```

Run local non-Blender checks with:

```bash
make check
```

The runner also performs startup checks before a normal run. To check only
configuration and Blender connectivity:

```bash
python -m harness.runner --text "a blue mug" --preflight-only
```

## Troubleshooting

- If the runner cannot connect to Blender, verify the addon server is running on
  `localhost:8888` or set `LL3M_BLENDER_PORT` consistently in `.env`.
- If you intentionally use a custom local LLM gateway or a nonstandard provider
  setup, pass `--skip-preflight` after verifying the configuration yourself.
- If LiteLLM reports missing credentials, fill the provider key for the model
  prefix you selected in `.env`.
- If a provider rejects streaming or JSON response format, set the affected
  `LL3M_*_STREAM=false` or choose a model/gateway that supports structured JSON.
- If rendering is slow, lower `LL3M_RENDER_WIDTH`, `LL3M_RENDER_HEIGHT`, or set
  `LL3M_RENDER_GIF_EACH_ROUND=false`.
- Validation renders default to Workbench to keep transparent or reflective
  surfaces from hiding interior geometry from vision checks.
- If external texture lookup is not needed, set
  `LL3M_TEXTURE_SEARCH_ENABLED=false`.

More implementation details are in `docs/harness.md`, and the IR format is
documented in `docs/ir.md`.
