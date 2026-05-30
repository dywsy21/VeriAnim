from __future__ import annotations

import errno
import json
import os
import queue
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from blender.client import BlenderClient
from blender import ll3m_utils
from harness.animation_repair import repair_animation_ir
from harness.blender_runtime import BlenderRunResult, BlenderRuntime
from harness.config import AgentModelConfig, HarnessConfig
from harness.ir import (
    AnimationAction,
    AnimationEventSpec,
    AnimationSpec,
    CameraSpec,
    CollisionProxySpec,
    CollisionProxyType,
    ContactConstraintSpec,
    ContactConstraintType,
    GenerationIR,
    MotionPathSpec,
    ObjectSpec,
    RelationType,
    SceneSpec,
    SourcePrompt,
    RenderEngine,
    RenderSpec,
    SpatialRelationSpec,
    TransformSpec,
    ValidationIssue,
    ValidationReport,
    VerificationMode,
    VideoVerifierSpec,
)
from harness.preflight import format_issue, has_errors, run_preflight
from harness.runner import _runner_lock
from harness.session import InteractiveHarnessSession, _animation_contact_repair_script
from harness.static_support_repair import repair_static_support
from harness.artifacts import ArtifactStore


def run_server_pending_commands(server: object) -> int:
    processed = 0
    while processed < 100:
        try:
            command, response_queue = server.command_queue.get_nowait()
        except queue.Empty:
            break
        try:
            response = server.execute_command(command)
        except Exception as exc:
            response = {"status": "error", "message": str(exc)}
        response_queue.put(response)
        processed += 1
    return processed


class RunnerLockTest(unittest.TestCase):
    def test_runner_lock_clears_stale_pid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            lock_path = runs_dir / ".harness_runner.lock"
            lock_path.write_text(json.dumps({"pid": 999999999, "argv": ["old"]}), encoding="utf-8")

            with _runner_lock(runs_dir):
                data = json.loads(lock_path.read_text(encoding="utf-8"))
                self.assertEqual(data["pid"], os.getpid())

            self.assertFalse(lock_path.exists())

    def test_runner_lock_rejects_live_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            lock_path = runs_dir / ".harness_runner.lock"
            lock_path.write_text(json.dumps({"pid": os.getpid(), "argv": ["current"]}), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "Another harness run is already active"):
                with _runner_lock(runs_dir):
                    pass


class ConfigEnvTest(unittest.TestCase):
    def test_config_reads_port_and_model_env(self) -> None:
        env = {
            "LL3M_BLENDER_PORT": "43210",
            "LL3M_PLANNER_MODEL": "openai/test-planner",
            "LL3M_CODER_STREAM": "false",
            "LL3M_TEXTURE_SEARCH_CANDIDATE_LIMIT": "0",
            "LL3M_DOTENV_OVERRIDE": "false",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            config = HarnessConfig.from_env()

        self.assertEqual(config.blender_port, 43210)
        self.assertEqual(config.planner.model, "openai/test-planner")
        self.assertFalse(config.coder.stream)
        self.assertEqual(config.texture_search_candidate_limit, 1)

    def test_config_uses_8888_default_port(self) -> None:
        with mock.patch.dict(os.environ, {"LL3M_DOTENV_OVERRIDE": "false"}, clear=False):
            os.environ.pop("LL3M_BLENDER_PORT", None)
            config = HarnessConfig.from_env()

        self.assertEqual(config.blender_port, 8888)


class BlenderUtilsTest(unittest.TestCase):
    def test_ll3m_utils_imports_without_blender_runtime(self) -> None:
        self.assertEqual(ll3m_utils.DEFAULT_RENDER_ENGINE, "BLENDER_WORKBENCH")

    def test_normalize_engine_name_prefers_valid_blender_enums(self) -> None:
        self.assertEqual(ll3m_utils.normalize_engine_name(None), "BLENDER_WORKBENCH")
        self.assertEqual(ll3m_utils.normalize_engine_name("workbench"), "BLENDER_WORKBENCH")
        self.assertEqual(ll3m_utils.normalize_engine_name("WORKBENCH"), "BLENDER_WORKBENCH")
        self.assertEqual(ll3m_utils.normalize_engine_name("eevee"), "BLENDER_EEVEE_NEXT")
        self.assertEqual(ll3m_utils.normalize_engine_name("BLENDER_EEVEE"), "BLENDER_EEVEE")

    def test_render_spec_defaults_to_workbench(self) -> None:
        self.assertEqual(RenderSpec().engine, RenderEngine.WORKBENCH)


class PreflightTest(unittest.TestCase):
    def test_preflight_reports_blender_and_missing_key_errors(self) -> None:
        env = {
            "LL3M_DOTENV_OVERRIDE": "false",
            "OPENAI_API_KEY": "",
            "LITELLM_API_KEY": "",
            "LL3M_PLANNER_API_KEY": "",
            "LL3M_CODER_API_KEY": "",
            "LL3M_REFINER_API_KEY": "",
            "LL3M_VISION_API_KEY": "",
            "LL3M_PLANNER_MODEL": "openai/test-planner",
            "LL3M_CODER_MODEL": "openai/test-coder",
            "LL3M_REFINER_MODEL": "openai/test-refiner",
            "LL3M_VISION_MODEL": "openai/test-vision",
            "LL3M_RUNS_DIR": tempfile.mkdtemp(),
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = HarnessConfig.from_env()
            with mock.patch(
                "harness.preflight.BlenderClient.get_scene_info",
                return_value={"status": "error", "message": "connection refused"},
            ):
                issues = run_preflight(
                    config,
                    prompt="scene",
                    ir_path=None,
                    include_animation=False,
                    skip_vision=False,
                    skip_video=True,
                )

        messages = "\n".join(format_issue(issue) for issue in issues)
        self.assertTrue(has_errors(issues))
        self.assertIn("Could not reach Blender addon server", messages)
        self.assertIn("No API key found for planner", messages)

    def test_preflight_accepts_successful_blender_and_provider_key(self) -> None:
        env = {
            "LL3M_DOTENV_OVERRIDE": "false",
            "OPENAI_API_KEY": "test-key",
            "LL3M_RUNS_DIR": tempfile.mkdtemp(),
        }
        with mock.patch.dict(os.environ, env, clear=True):
            config = HarnessConfig.from_env()
            with mock.patch(
                "harness.preflight.BlenderClient.get_scene_info",
                return_value={"status": "success", "result": {"name": "Scene"}},
            ):
                issues = run_preflight(
                    config,
                    prompt="scene",
                    ir_path=None,
                    include_animation=False,
                    skip_vision=False,
                    skip_video=True,
                )

        self.assertFalse(has_errors(issues), [format_issue(issue) for issue in issues])


class FakeSocket:
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks
        self.sent = b""
        self.connected_to: tuple[str, int] | None = None
        self.closed = False

    def connect(self, address: tuple[str, int]) -> None:
        self.connected_to = address

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, _: int) -> bytes:
        if self.chunks:
            return self.chunks.pop(0)
        return b""

    def close(self) -> None:
        self.closed = True


class BlenderClientSocketTest(unittest.TestCase):
    def test_send_command_reads_chunked_json_response(self) -> None:
        fake_socket = FakeSocket([b'{"status": "suc', b'cess", "result": {"ok": true}}'])

        with mock.patch("socket.socket", return_value=fake_socket):
            client = BlenderClient("127.0.0.1", 9999)
            client.connect()
            response = client.send_command({"type": "ping"})
            client.close()

        self.assertEqual(response["status"], "success")
        self.assertEqual(json.loads(fake_socket.sent.decode("utf-8"))["type"], "ping")
        self.assertEqual(fake_socket.connected_to, ("127.0.0.1", 9999))
        self.assertTrue(fake_socket.closed)

    def test_send_command_returns_error_on_socket_failure(self) -> None:
        fake_socket = FakeSocket([])
        with mock.patch.object(fake_socket, "sendall", side_effect=OSError(errno.EPIPE, "broken pipe")):
            with mock.patch("builtins.print"):
                client = BlenderClient("127.0.0.1", 9999)
                client.sock = fake_socket
                response = client.send_command({"type": "ping"})

        self.assertEqual(response["status"], "error")
        self.assertIn("broken pipe", response["message"])


class BlenderRuntimeStatusTest(unittest.TestCase):
    def test_run_result_diagnostic_text_includes_traceback_when_stdout_empty(self) -> None:
        result = BlenderRunResult(
            ok=False,
            message="bad",
            stdout="",
            stderr="",
            traceback="Traceback...\nNameError: bad",
            raw={},
        )

        self.assertIn("Traceback", result.diagnostic_text())
        self.assertIn("NameError", result.diagnostic_text())
        self.assertIn("bad", result.diagnostic_text())

    def test_runtime_uses_structured_ok_status_not_error_words_in_stdout(self) -> None:
        config = HarnessConfig.from_env()
        runtime = BlenderRuntime(config)
        response = {
            "status": "success",
            "result": {
                "ok": True,
                "executed": True,
                "stdout": "This valid diagnostic mentions error and failed but execution passed.",
                "stderr": "",
                "traceback": None,
            },
        }

        with mock.patch("harness.blender_runtime.BlenderClient.execute_code", return_value=response):
            result = runtime.execute_code("print('ok')")

        self.assertTrue(result.ok)
        self.assertIn("error and failed", result.stdout)

    def test_runtime_surfaces_structured_traceback_failure(self) -> None:
        config = HarnessConfig.from_env()
        runtime = BlenderRuntime(config)
        response = {
            "status": "success",
            "result": {
                "ok": False,
                "executed": False,
                "stdout": "before fail\n",
                "stderr": "",
                "traceback": "Traceback...\nValueError: bad",
                "message": "bad",
            },
        }

        with mock.patch("harness.blender_runtime.BlenderClient.execute_code", return_value=response):
            result = runtime.execute_code("raise ValueError('bad')")

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "bad")
        self.assertEqual(result.traceback, "Traceback...\nValueError: bad")


def minimal_config(runs_dir: Path) -> HarnessConfig:
    model = AgentModelConfig(name="test", model="test/model", api_key="test-key")
    return HarnessConfig(
        planner=model,
        coder=model,
        refiner=model,
        vision=model,
        video=model,
        max_refinement_rounds=0,
        max_visual_refinement_rounds=0,
        max_video_refinement_rounds=0,
        max_stagnant_refinement_rounds=1,
        planner_max_retries=0,
        rag_docs=(),
        runs_dir=runs_dir,
        blender_host="localhost",
        blender_port=8888,
        headless_rendering=False,
        render_width=64,
        render_height=64,
        render_gif_each_round=False,
        texture_search_enabled=False,
        texture_search_candidate_limit=1,
        texture_search_timeout_seconds=3,
        tui_initial_animation=False,
        tui_skip_vision=True,
        tui_skip_video=True,
    )


def minimal_ir(*, animation: bool = False) -> GenerationIR:
    return GenerationIR(
        prompt=SourcePrompt(text="test scene"),
        scene=SceneSpec(
            objects=[ObjectSpec(id="cube", description="test cube")],
            cameras=[CameraSpec(id="camera_main", target_object_ids=["cube"])],
        ),
        animation=AnimationSpec(duration_frames=3) if animation else None,
    )


def bridge_animation_ir() -> GenerationIR:
    return GenerationIR(
        prompt=SourcePrompt(text="a toy car drives over a low bridge without clipping through the bridge deck"),
        scene=SceneSpec(
            objects=[
                ObjectSpec(id="car", description="toy car", collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX)),
                ObjectSpec(id="bridge_deck", description="bridge deck", collision=CollisionProxySpec(proxy_type=CollisionProxyType.BBOX)),
            ],
            cameras=[CameraSpec(id="camera_main", target_object_ids=["car", "bridge_deck"])],
        ),
        animation=AnimationSpec(
            duration_frames=120,
            verifier=VideoVerifierSpec(sampled_frames=[1, 60, 120]),
            events=[
                AnimationEventSpec(
                    id="car_drive",
                    action=AnimationAction.TRANSLATE,
                    subject_ids=["car"],
                    start_frame=1,
                    end_frame=120,
                    description="car crosses bridge deck",
                    start_transform=TransformSpec(location=(-2.5, 0.0, 0.2)),
                    end_transform=TransformSpec(location=(2.5, 0.0, 0.2)),
                    path=MotionPathSpec(),
                    contact_constraints=[
                        ContactConstraintSpec(
                            id="car_deck_support",
                            constraint_type=ContactConstraintType.SUPPORT,
                            subject_id="car",
                            object_id="bridge_deck",
                            start_frame=1,
                            end_frame=120,
                        )
                    ],
                )
            ],
        ),
    )


def bridge_scene_graph() -> dict:
    return {
        "objects": [
            {
                "name": "car",
                "ll3m_id": "car",
                "type": "MESH",
                "bbox": {"min": [-3.0, -0.2, 0.0], "max": [-2.0, 0.2, 0.4]},
            },
            {
                "name": "bridge_deck",
                "ll3m_id": "bridge_deck",
                "type": "MESH",
                "bbox": {"min": [-1.0, -0.8, 0.5], "max": [1.0, 0.8, 0.7]},
            },
        ]
    }


def support_repair_ir() -> GenerationIR:
    return GenerationIR(
        prompt=SourcePrompt(text="a mug on a table"),
        scene=SceneSpec(
            objects=[
                ObjectSpec(id="mug", description="plain mug"),
                ObjectSpec(id="table", description="wooden table"),
            ],
            cameras=[CameraSpec(id="camera_main", target_object_ids=["mug", "table"])],
            relations=[
                SpatialRelationSpec(
                    id="mug_on_table",
                    relation_type=RelationType.ON_TOP_OF,
                    subject_id="mug",
                    object_id="table",
                )
            ],
        ),
    )


def support_repair_scene_graph() -> dict:
    return {
        "objects": [
            {
                "name": "mug",
                "ll3m_id": "mug",
                "type": "MESH",
                "bbox": {"min": [0.0, 0.0, 1.0], "max": [0.3, 0.3, 1.4]},
            },
            {
                "name": "table",
                "ll3m_id": "table",
                "type": "MESH",
                "bbox": {"min": [-1.0, -1.0, 0.0], "max": [1.0, 1.0, 0.5]},
            },
        ]
    }


class HarnessSessionDiagnosticsTest(unittest.TestCase):
    def test_static_code_report_flags_undefined_math_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = InteractiveHarnessSession(minimal_config(Path(tmp)), skip_vision=True, skip_video=True)
            session.ir = minimal_ir()
            session.code = "import bpy\nx = math.pi\nLL3M_METADATA = {}\n"

            report = session._static_code_report()

        self.assertFalse(report.passed)
        self.assertIn("CODE_UNDEFINED_MODULE", {issue.code for issue in report.issues})

    def test_execution_failure_writes_traceback_log_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = InteractiveHarnessSession(minimal_config(Path(tmp)), skip_vision=True, skip_video=True)
            session.ir = minimal_ir()
            session.store = ArtifactStore.create(Path(tmp))
            session.code = "LL3M_METADATA = {}\n"
            result = BlenderRunResult(
                ok=False,
                message="bad",
                stdout="",
                stderr="",
                traceback="Traceback...\nRuntimeError: bad",
                raw={},
            )
            with mock.patch.object(session.blender, "execute_scene_code", return_value=result), mock.patch.object(
                session.refiner,
                "refine",
                return_value="LL3M_METADATA = {}\n",
            ):
                session._execute_validate_refine(reason="initial")

            log = (session.store.root / "logs" / "initial_round_0_execution.txt").read_text(encoding="utf-8")
            report = json.loads((session.store.root / "reports" / "initial_round_0_execution.json").read_text(encoding="utf-8"))

        self.assertIn("Traceback", log)
        self.assertEqual(report["issues"][0]["code"], "BLENDER_EXEC_FAILED")

    def test_validation_pass_reports_missing_render_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = InteractiveHarnessSession(minimal_config(Path(tmp)), skip_vision=True, skip_video=True)
            session.ir = minimal_ir()
            session.store = ArtifactStore.create(Path(tmp))
            with mock.patch.object(
                session.blender,
                "validate_scene",
                return_value=ValidationReport.ok(VerificationMode.DETERMINISTIC),
            ), mock.patch.object(
                session.blender,
                "render_screenshots",
                return_value=[],
            ):
                reports = session._run_validation_pass("initial_round_0")

        self.assertIn("RENDER_NO_SCREENSHOTS", {issue.code for report in reports for issue in report.issues})

    def test_validation_pass_applies_static_support_repair_and_writes_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = InteractiveHarnessSession(minimal_config(Path(tmp)), skip_vision=True, skip_video=True)
            session.ir = support_repair_ir()
            session.store = ArtifactStore.create(Path(tmp))
            session.code = "LL3M_METADATA = {}\n"
            failed_report = ValidationReport.failed(
                VerificationMode.DETERMINISTIC,
                [
                    ValidationIssue(
                        code="RELATION_ON_TOP_OF_FAILED",
                        message="'mug' is not clearly on top of 'table'.",
                        relation_id="mug_on_table",
                        target_id="mug",
                        evidence={"z_gap": 0.5, "overlap_x": 0.3, "overlap_y": 0.3, "support_z": 0.5},
                    )
                ],
            )
            repaired_report = ValidationReport.ok(VerificationMode.DETERMINISTIC, "support relation repaired")
            repair_result = BlenderRunResult(ok=True, message=None, stdout="repair ok\n", raw={})
            with mock.patch.object(
                session.blender,
                "validate_scene",
                side_effect=[failed_report, repaired_report],
            ), mock.patch.object(
                session.blender,
                "get_scene_graph",
                return_value=support_repair_scene_graph(),
            ), mock.patch.object(
                session.blender,
                "execute_code",
                return_value=repair_result,
            ) as execute_code, mock.patch.object(
                session.blender,
                "render_screenshots",
                return_value=[],
            ):
                reports = session._run_validation_pass("initial_round_0")

            root = session.store.root
            repair_plan = json.loads(
                (root / "repairs" / "initial_round_0_static_support_repair.json").read_text(encoding="utf-8")
            )
            repair_script = (root / "code" / "initial_round_0_support_repair.py").read_text(encoding="utf-8")
            persisted_script = (root / "code" / "initial_round_0_scene_with_support_repair.py").read_text(encoding="utf-8")
            execution_log = (root / "logs" / "initial_round_0_support_repair_execution.txt").read_text(encoding="utf-8")
            after_report = json.loads(
                (
                    root / "reports" / "initial_round_0_scene_deterministic_after_support_repair.json"
                ).read_text(encoding="utf-8")
            )

        self.assertTrue(repair_plan["applied"], repair_plan)
        self.assertAlmostEqual(repair_plan["adjustments"][0]["delta"][2], -0.5)
        self.assertIn("LL3M deterministic static support repair", repair_script)
        self.assertIn("LL3M deterministic static support repair", persisted_script)
        self.assertIn("LL3M deterministic static support repair", session.code or "")
        self.assertIn("repair ok", execution_log)
        self.assertTrue(after_report["passed"], after_report)
        execute_code.assert_called_once()
        self.assertTrue(reports[0].passed)
        self.assertIn("RENDER_NO_SCREENSHOTS", {issue.code for report in reports for issue in report.issues})

    def test_animation_contact_repair_script_shifts_consistent_z_gap_keyframes(self) -> None:
        report = ValidationReport.failed(
            VerificationMode.DETERMINISTIC,
            [
                ValidationIssue(
                    code="CONTACT_CONSTRAINT_FLOATING",
                    message="box floating",
                    target_id="box",
                    evidence={"z_gap": 0.04},
                ),
                ValidationIssue(
                    code="CONTACT_CONSTRAINT_FLOATING",
                    message="box floating",
                    target_id="box",
                    evidence={"z_gap": 0.041},
                ),
            ],
        )

        script = _animation_contact_repair_script(report)

        self.assertIn("LL3M deterministic animation contact repair", script)
        self.assertIn('"constant_deltas": {"box": -0.0405}', script)
        self.assertIn("point.co.y += float(dz)", script)

    def test_animation_contact_repair_script_aligns_single_support_keyframes(self) -> None:
        report = ValidationReport.failed(
            VerificationMode.DETERMINISTIC,
            [
                ValidationIssue(
                    code="ANIMATION_PENETRATES_SUPPORT",
                    message="crate penetrates table",
                    target_id="crate",
                    evidence={"target_id": "table", "z_gap": -0.15},
                ),
                ValidationIssue(
                    code="CONTACT_CONSTRAINT_PENETRATION",
                    message="crate intersects table",
                    target_id="crate",
                    evidence={"object_id": "table", "axis": "z", "penetration_depth": 0.1},
                ),
            ],
        )

        script = _animation_contact_repair_script(report)

        self.assertIn('"support_pairs": {"crate": "table"}', script)
        self.assertIn("_ll3m_contact_repair_align_keyed_support", script)
        self.assertIn("support_box[5] - subject_box[2] + 0.001", script)

    def test_animation_contact_repair_skips_multi_support_subjects(self) -> None:
        report = ValidationReport.failed(
            VerificationMode.DETERMINISTIC,
            [
                ValidationIssue(
                    code="CONTACT_CONSTRAINT_SUPPORT_PENETRATION",
                    message="car penetrates ramp",
                    target_id="car",
                    evidence={"object_id": "ramp", "z_gap": -0.06},
                ),
                ValidationIssue(
                    code="CONTACT_CONSTRAINT_FLOATING",
                    message="car floats over platform",
                    target_id="car",
                    evidence={"object_id": "platform", "z_gap": 0.06},
                ),
            ],
        )

        script = _animation_contact_repair_script(report)

        self.assertEqual(script, "")

    def test_animation_stage_writes_repair_artifact_and_appends_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = InteractiveHarnessSession(minimal_config(Path(tmp)), include_animation=True, skip_vision=True, skip_video=True)
            session.store = ArtifactStore.create(Path(tmp))
            with mock.patch.object(session.coder, "generate", return_value="LL3M_METADATA = {}\n"), mock.patch.object(
                session,
                "_execute_validate_refine",
                side_effect=[True, True],
            ), mock.patch.object(session.blender, "get_scene_graph", return_value=bridge_scene_graph()), mock.patch.object(
                session.refiner,
                "add_animation",
                return_value="import bpy\nLL3M_METADATA = {}\n",
            ):
                session._run_two_stage_animation_start(bridge_animation_ir())

            plan = json.loads((session.store.root / "reports" / "animation_path_repair_plan.json").read_text(encoding="utf-8"))
            script = (session.store.root / "code" / "generated_animation_stage.py").read_text(encoding="utf-8")
            repaired_ir = json.loads((session.store.root / "ir_animation_stage_repaired.json").read_text(encoding="utf-8"))

        self.assertTrue(plan["applied"], plan)
        self.assertIn("LL3M deterministic animation path repair", script)
        self.assertIn("_ll3m_repair_recalibrate_keyframes", script)
        self.assertIn("_ll3m_repair_keyframe.get(\"location\"", script)
        support = repaired_ir["animation"]["events"][0]["contact_constraints"][0]
        self.assertEqual((support["start_frame"], support["end_frame"]), (37, 84))

    def test_animation_repair_owns_subject_path_without_appending_static_support_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = InteractiveHarnessSession(minimal_config(Path(tmp)), include_animation=True, skip_vision=True, skip_video=True)
            session.ir = bridge_animation_ir()
            _, animation_plan = repair_animation_ir(bridge_animation_ir(), bridge_scene_graph())
            static_report = ValidationReport.failed(
                VerificationMode.DETERMINISTIC,
                [
                    ValidationIssue(
                        code="RELATION_ON_TOP_OF_FAILED",
                        message="mug is not on table",
                        relation_id="mug_on_table",
                        target_id="mug",
                        evidence={"z_gap": 0.5, "overlap_x": 0.3, "overlap_y": 0.3, "support_z": 0.5},
                    )
                ],
            )
            session._animation_repair_plan = animation_plan
            session._static_support_repair_plan = repair_static_support(
                support_repair_ir(), support_repair_scene_graph(), static_report
            )

            code = session._append_static_support_repair(
                "LL3M_METADATA = {}\n# LL3M deterministic static support repair\nold\n"
            )

        self.assertNotIn("LL3M deterministic static support repair", code)


class BackgroundCommandQueueTest(unittest.TestCase):
    def test_background_command_queue_returns_execute_response(self) -> None:
        class FakeServer:
            def __init__(self) -> None:
                self.command_queue: queue.Queue[tuple[dict[str, str], queue.Queue[dict[str, object]]]] = queue.Queue()

            def execute_command(self, command: dict[str, str]) -> dict[str, object]:
                return {"status": "success", "result": {"ok": True, "echo": command["type"]}}

        server = FakeServer()
        response_queue: queue.Queue[dict[str, object]] = queue.Queue(maxsize=1)
        server.command_queue.put(({"type": "execute_code"}, response_queue))

        processed = run_server_pending_commands(server)

        self.assertEqual(processed, 1)
        self.assertEqual(response_queue.get_nowait(), {"status": "success", "result": {"ok": True, "echo": "execute_code"}})


if __name__ == "__main__":
    unittest.main()
