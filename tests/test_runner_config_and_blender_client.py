from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from blender.client import BlenderClient
from harness.blender_runtime import BlenderRuntime
from harness.config import HarnessConfig
from harness.preflight import format_issue, has_errors, run_preflight
from harness.runner import _runner_lock


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


if __name__ == "__main__":
    unittest.main()
