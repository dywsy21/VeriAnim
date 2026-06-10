"""Interactive Textual TUI for the local VeriAnim harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import traceback

from .config import HarnessConfig
from .session import HarnessEvent, InteractiveHarnessSession


STAGES = [
    "planner",
    "coder",
    "execute",
    "validate",
    "render",
    "vision",
    "video",
    "refiner",
]


@dataclass(slots=True)
class TUIState:
    busy: bool = False
    has_scene: bool = False
    include_animation: bool = False
    skip_vision: bool = False
    skip_video: bool = False
    run_dir: Path | None = None
    stage_status: dict[str, str] = field(default_factory=lambda: {stage: "waiting" for stage in STAGES})


def main() -> None:
    try:
        from textual import on
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, Vertical
        from textual.widgets import Footer, Header, Input, RichLog, Static
    except Exception as exc:
        raise SystemExit("Textual is not installed. Run `pip install -r requirements.txt`.") from exc

    class HarnessTUI(App[None]):
        CSS = """
        Screen {
            background: #101318;
            color: #d7dde8;
        }
        #root {
            height: 1fr;
        }
        #left {
            width: 2fr;
            min-width: 70;
            border: solid #2f6fed;
            padding: 1 1;
        }
        #right {
            width: 1fr;
            min-width: 38;
            border: solid #2a3441;
            padding: 1 1;
            background: #151a21;
        }
        #title {
            text-style: bold;
            color: #8fb3ff;
            padding-bottom: 1;
        }
        #status {
            height: auto;
            margin-bottom: 1;
        }
        #pipeline {
            height: auto;
            margin-bottom: 1;
        }
        #artifacts {
            height: 1fr;
        }
        #events {
            height: 1fr;
            background: #0f131a;
        }
        #prompt {
            dock: bottom;
            height: 3;
            border: tall #2f6fed;
            background: #111827;
        }
        .hint {
            color: #99a7bd;
        }
        """

        BINDINGS = [
            ("ctrl+c", "quit", "Quit"),
            ("ctrl+q", "quit", "Quit"),
            ("ctrl+l", "clear_log", "Clear log"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.config = HarnessConfig.from_env()
            self.state = TUIState(
                include_animation=self.config.tui_initial_animation,
                skip_vision=self.config.tui_skip_vision,
                skip_video=self.config.tui_skip_video,
            )
            self.session = InteractiveHarnessSession(
                self.config,
                include_animation=self.state.include_animation,
                skip_vision=self.state.skip_vision,
                skip_video=self.state.skip_video,
                callback=self._threadsafe_event,
            )

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Horizontal(id="root"):
                with Vertical(id="left"):
                    yield Static("VeriAnim Animation Harness", id="title")
                    yield RichLog(id="events", wrap=True, markup=True, highlight=True)
                with Vertical(id="right"):
                    yield Static(id="status")
                    yield Static(id="pipeline")
                    yield RichLog(id="artifacts", wrap=True, markup=True, highlight=True)
            yield Input(placeholder="Prompt or command: /set <agent> <field> <value>  /model <agent> <model>  /agent <name>  /help", id="prompt")
            yield Footer()

        def on_mount(self) -> None:
            self._refresh_status()
            self._log("[bold #8fb3ff]Ready.[/] Start with a scene prompt. Blender should have the VeriAnim addon server running.")

        def action_clear_log(self) -> None:
            self.query_one("#events", RichLog).clear()

        @on(Input.Submitted, "#prompt")
        def submit_prompt(self, event: Input.Submitted) -> None:
            text = event.value.strip()
            event.input.value = ""
            if not text:
                return
            if self._handle_command(text):
                return
            if self.state.busy:
                self._log("[yellow]Harness is busy. Wait for the current turn to finish.[/]")
                return

            self.state.busy = True
            self._reset_pipeline()
            self._refresh_status()
            self._log(f"[bold cyan]user>[/] {text}")
            self.run_worker(lambda: self._run_turn(text), thread=True, exclusive=True)

        def _handle_command(self, text: str) -> bool:
            command = text.lower()
            if command in {"/quit", "/exit"}:
                self.exit()
                return True
            if command == "/clear":
                self.action_clear_log()
                return True
            if command == "/animation":
                self.state.include_animation = not self.state.include_animation
                self.session.include_animation = self.state.include_animation
                self._log(f"[green]Animation planning set to {self.state.include_animation}.[/]")
                self._refresh_status()
                return True
            if command == "/vision":
                self.state.skip_vision = not self.state.skip_vision
                self.session.skip_vision = self.state.skip_vision
                self._log(f"[green]Vision verifier enabled: {not self.state.skip_vision}.[/]")
                self._refresh_status()
                return True
            if command == "/video":
                self.state.skip_video = not self.state.skip_video
                self.session.skip_video = self.state.skip_video
                self._log(f"[green]Video verifier enabled: {not self.state.skip_video}.[/]")
                self._refresh_status()
                return True
            if command == "/help":
                agents = "planner, coder, refiner, vision, video"
                fields = "model, api_base, api_key, api_version, provider, temperature, max_tokens, timeout, stream, supports_images"
                self._log(
                    "[bold]Commands[/]\n"
                    "/animation              toggle animation planning\n"
                    "/vision                 toggle visual verifier\n"
                    "/video                  toggle video verifier\n"
                    f"/model <agent> <model>  shortcut to set model (agents: {agents})\n"
                    f"/set <agent> <field> <value>\n"
                    f"                        set any agent field (fields: {fields})\n"
                    "                        use 'none' to clear optional fields\n"
                    "/agent <name>           show all settings for one agent\n"
                    "/models                 show model for each agent\n"
                    "/clear                  clear event log\n"
                    "/quit                   exit"
                )
                return True
            if command == "/models":
                for agent_name in ("planner", "coder", "refiner", "vision", "video"):
                    cfg = self.config.get_agent_config(agent_name)
                    if cfg:
                        self._log(f"[bold]{agent_name:<8}[/] {cfg.model}")
                return True
            if text.lower().startswith("/agent "):
                parts = text.split(None, 1)
                agent_name = parts[1].strip().lower() if len(parts) > 1 else ""
                cfg = self.config.get_agent_config(agent_name)
                if cfg is None:
                    self._log(f"[red]Unknown agent '{agent_name}'. Valid: planner, coder, refiner, vision, video[/]")
                else:
                    key_val = (
                        f"  model          {cfg.model}\n"
                        f"  api_base       {cfg.api_base or '-'}\n"
                        f"  api_key        {'(set)' if cfg.api_key else '-'}\n"
                        f"  api_version    {cfg.api_version or '-'}\n"
                        f"  provider       {cfg.custom_llm_provider or '-'}\n"
                        f"  temperature    {cfg.temperature}\n"
                        f"  max_tokens     {cfg.max_tokens or '-'}\n"
                        f"  timeout        {cfg.timeout_seconds}s\n"
                        f"  stream         {cfg.stream}\n"
                        f"  supports_images {cfg.supports_images}"
                    )
                    self._log(f"[bold]{agent_name}[/]\n{key_val}")
                return True
            if text.lower().startswith("/model "):
                parts = text.split(None, 2)
                if len(parts) < 3:
                    self._log("[yellow]Usage: /model <agent> <model>  e.g. /model coder openai/gpt-4o[/]")
                else:
                    _, agent_name, model_str = parts
                    agent_name = agent_name.lower()
                    if self.config.set_agent_model(agent_name, model_str):
                        self._log(f"[green]Agent [bold]{agent_name}[/] model → [bold]{model_str}[/].[/]")
                        self._refresh_status()
                    else:
                        self._log(f"[red]Unknown agent '{agent_name}'. Valid: planner, coder, refiner, vision, video[/]")
                return True
            if text.lower().startswith("/set "):
                parts = text.split(None, 3)
                if len(parts) < 4:
                    self._log("[yellow]Usage: /set <agent> <field> <value>  e.g. /set coder api_base http://localhost:4000[/]")
                else:
                    _, agent_name, field_name, value_str = parts
                    agent_name = agent_name.lower()
                    field_name = field_name.lower()
                    ok, err = self.config.set_agent_field(agent_name, field_name, value_str)
                    if ok:
                        display_val = "(hidden)" if field_name == "api_key" else value_str
                        self._log(f"[green]Agent [bold]{agent_name}[/] {field_name} → [bold]{display_val}[/].[/]")
                        self._refresh_status()
                    else:
                        self._log(f"[red]{err}[/]")
                return True
            return False

        def _run_turn(self, text: str) -> None:
            try:
                if self.session.has_scene:
                    output_dir = self.session.apply_user_request(text)
                else:
                    output_dir = self.session.start(text)
                self.call_from_thread(self._turn_finished, output_dir)
            except Exception as exc:
                tb = traceback.format_exc()
                self.call_from_thread(self._turn_failed, exc, tb)

        def _turn_finished(self, output_dir: Path) -> None:
            self.state.busy = False
            self.state.has_scene = True
            self.state.run_dir = output_dir
            self._log(f"[bold green]Turn complete.[/] Artifacts: {output_dir}")
            self._refresh_status()

        def _turn_failed(self, exc: Exception, tb: str) -> None:
            self.state.busy = False
            self._log(f"[bold red]Turn failed:[/] {exc}")
            self._log(f"[dim]{tb}[/]")
            self._refresh_status()

        def _threadsafe_event(self, event: HarnessEvent) -> None:
            self.call_from_thread(self._handle_harness_event, event)

        def _handle_harness_event(self, event: HarnessEvent) -> None:
            if event.kind in self.state.stage_status:
                for stage, status in list(self.state.stage_status.items()):
                    if status == "running" and stage != event.kind:
                        self.state.stage_status[stage] = "done"
                self.state.stage_status[event.kind] = "running"
            if event.kind in {"pass", "warn", "error"}:
                for stage, status in list(self.state.stage_status.items()):
                    if status == "running":
                        self.state.stage_status[stage] = "done" if event.kind == "pass" else "check"
            if event.kind == "session" and event.data.get("path"):
                self.state.run_dir = Path(event.data["path"])
                self.query_one("#artifacts", RichLog).write(f"[bold]Run[/] {self.state.run_dir}")
            if event.kind == "render" and event.data.get("paths"):
                for path in event.data["paths"]:
                    self.query_one("#artifacts", RichLog).write(f"[cyan]image[/] {path}")
            style = {
                "error": "bold red",
                "warn": "yellow",
                "pass": "bold green",
                "issue": "red",
                "user": "bold cyan",
                "report": "magenta",
            }.get(event.kind, "#d7dde8")
            self._log(f"[{style}]{event.kind}>[/] {event.message}")
            self._refresh_status()

        def _refresh_status(self) -> None:
            busy = "[bold yellow]busy[/]" if self.state.busy else "[bold green]idle[/]"
            scene = "yes" if self.state.has_scene else "no"
            model_lines = ""
            for agent_name in ("planner", "coder", "refiner", "vision", "video"):
                cfg = self.config.get_agent_config(agent_name)
                if cfg:
                    extra = f" [dim]{cfg.api_base}[/]" if cfg.api_base else ""
                    model_lines += f"\n  {agent_name:<8} {cfg.model}{extra}"
            status = (
                f"[bold]Status[/]\n"
                f"state: {busy}\n"
                f"scene loaded: {scene}\n"
                f"animation planning: {self.state.include_animation}\n"
                f"vision verifier: {not self.state.skip_vision}\n"
                f"video verifier: {not self.state.skip_video}\n"
                f"run dir: {self.state.run_dir or '-'}\n"
                f"[bold]Models[/]{model_lines}"
            )
            self.query_one("#status", Static).update(status)
            lines = ["[bold]Pipeline[/]"]
            for stage in STAGES:
                status_text = self.state.stage_status.get(stage, "waiting")
                icon = {"waiting": "○", "running": "●", "done": "✓", "check": "!"}.get(status_text, "○")
                color = {"waiting": "#65758b", "running": "#8fb3ff", "done": "#53d18c", "check": "#ffcf66"}.get(status_text, "#65758b")
                lines.append(f"[{color}]{icon} {stage:<9} {status_text}[/]")
            self.query_one("#pipeline", Static).update("\n".join(lines))

        def _reset_pipeline(self) -> None:
            self.state.stage_status = {stage: "waiting" for stage in STAGES}

        def _log(self, message: str) -> None:
            self.query_one("#events", RichLog).write(message)

    HarnessTUI().run()


if __name__ == "__main__":
    main()
