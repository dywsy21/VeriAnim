import socket
import json
from .headless import execute_headless_blender, should_use_headless

HOST = 'localhost'
PORT = 8888

"""
BlenderClient: Minimal client for communicating with the Blender LL3M server.

Available functions:
- BlenderClient.get_scene_info(host=..., port=...):
    Retrieve basic information about the current Blender scene.
- BlenderClient.get_object_info(obj_name, host=..., port=...):
    Retrieve information about a specific object in the Blender scene.
- BlenderClient.execute_code(code, host=..., port=...):
    Execute arbitrary Python code in the Blender server context.

All functions handle connection and closure internally, and return a dictionary with the result or error.
"""

class BlenderClient:
    """
    Minimal client for communicating with the Blender LL3M server.
    Provides static methods for common commands, each handling connection and closure internally.
    """
    def __init__(self, host=HOST, port=PORT):
        """
        Initialize a BlenderClient instance.
        :param host: Hostname or IP address of the Blender server.
        :param port: Port number of the Blender server.
        """
        self.host = host
        self.port = port
        self.sock = None

    def connect(self):
        """
        Establish a socket connection to the Blender server.
        """
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))

    def close(self):
        """
        Close the socket connection if open.
        """
        if self.sock:
            self.sock.close()
            self.sock = None

    def send_command(self, command) -> dict[str, str]:
        """
        Send a command (dict) to the Blender server and receive the response.
        :param command: Dictionary representing the command to send.
        :return: Response from the server as a dictionary, or error dict on failure.
        """
        if not self.sock:
            raise RuntimeError("Not connected")
        try:
            self.sock.sendall(json.dumps(command).encode('utf-8'))
            data = b''
            while True:
                chunk = self.sock.recv(8192)
                if not chunk:
                    break
                data += chunk
                try:
                    response = json.loads(data.decode('utf-8'))
                    return response
                except json.JSONDecodeError:
                    continue
            return None
        except Exception as e:
            print(f"Error during send_command: {e}")
            return {"status": "error", "message": str(e)}

    @staticmethod
    def _send(type_name: str, params: dict | None = None, host=HOST, port=PORT):
        client = BlenderClient(host, port)
        try:
            client.connect()
            command = {"type": type_name}
            if params is not None:
                command["params"] = params
            return client.send_command(command)
        except Exception as e:
            print(f"Exception in {type_name}: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            client.close()

    @staticmethod
    def get_scene_info(host=HOST, port=PORT):
        """
        Retrieve basic information about the current Blender scene.
        Handles connection and closure internally.
        :param host: Hostname or IP address of the Blender server.
        :param port: Port number of the Blender server.
        :return: Scene info as a dictionary, or error dict on failure.
        """
        client = BlenderClient(host, port)
        try:
            client.connect()
            resp = client.send_command({"type": "get_scene_info"})
            return resp
        except Exception as e:
            print(f"Exception in get_scene_info: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            client.close()

    @staticmethod
    def get_object_info(obj_name, host=HOST, port=PORT):
        """
        Retrieve information about a specific object in the Blender scene.
        Handles connection and closure internally.
        :param obj_name: Name of the object to query.
        :param host: Hostname or IP address of the Blender server.
        :param port: Port number of the Blender server.
        :return: Object info as a dictionary, or error dict on failure.
        """
        client = BlenderClient(host, port)
        try:
            client.connect()
            resp = client.send_command({"type": "get_object_info", "params": {"name": obj_name}})
            return resp
        except Exception as e:
            print(f"Exception in get_object_info: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            client.close()

    @staticmethod
    def get_scene_graph(include_hidden: bool = False, evaluated: bool = False, host=HOST, port=PORT):
        return BlenderClient._send(
            "get_scene_graph",
            {"include_hidden": bool(include_hidden), "evaluated": bool(evaluated)},
            host,
            port,
        )

    @staticmethod
    def get_object_bbox(name_or_id: str, evaluated: bool = True, host=HOST, port=PORT):
        return BlenderClient._send(
            "get_object_bbox",
            {"id": name_or_id, "evaluated": bool(evaluated)},
            host,
            port,
        )

    @staticmethod
    def get_material_info(name: str | None = None, host=HOST, port=PORT):
        params = {"name": name} if name else {}
        return BlenderClient._send("get_material_info", params, host, port)

    @staticmethod
    def get_camera_view_report(camera: str | None = None, target_ids: list[str] | None = None, host=HOST, port=PORT):
        return BlenderClient._send(
            "get_camera_view_report",
            {"camera": camera, "target_ids": target_ids or []},
            host,
            port,
        )

    @staticmethod
    def get_animation_info(object_ids: list[str] | None = None, host=HOST, port=PORT):
        return BlenderClient._send("get_animation_info", {"object_ids": object_ids or []}, host, port)

    @staticmethod
    def sample_object_transforms(object_ids: list[str] | None = None, frames: list[int] | None = None, host=HOST, port=PORT):
        return BlenderClient._send(
            "sample_object_transforms",
            {"object_ids": object_ids or [], "frames": frames or []},
            host,
            port,
        )

    @staticmethod
    def run_validation(ir: dict, include_scene: bool = True, include_animation: bool | None = None, host=HOST, port=PORT):
        params = {"ir": ir, "include_scene": bool(include_scene)}
        if include_animation is not None:
            params["include_animation"] = bool(include_animation)
        return BlenderClient._send("run_validation", params, host, port)

    @staticmethod
    def render_view_plan(views: list[dict], output_dir: str, width: int = 1280, height: int = 720, host=HOST, port=PORT):
        return BlenderClient._send(
            "render_view_plan",
            {"views": views, "output_dir": output_dir, "width": int(width), "height": int(height)},
            host,
            port,
        )

    @staticmethod
    def render_animation_preview(
        output_dir: str,
        frames: list[int] | None = None,
        width: int = 1280,
        height: int = 720,
        render_video: bool = False,
        target_object_ids: list[str] | None = None,
        host=HOST,
        port=PORT,
    ):
        return BlenderClient._send(
            "render_animation_preview",
            {
                "output_dir": output_dir,
                "frames": frames or [],
                "width": int(width),
                "height": int(height),
                "render_video": bool(render_video),
                "target_object_ids": target_object_ids or [],
            },
            host,
            port,
        )

    @staticmethod
    def save_scene_copy(filepath: str, pack: bool = True, host=HOST, port=PORT):
        """
        Ask the Blender addon to save a copy of the current scene to a .blend file.
        :param filepath: Target path for the .blend file
        :param pack: Whether to pack external assets into the .blend
        :return: Response dict with saved status and filepath/message
        """
        client = BlenderClient(host, port)
        try:
            client.connect()
            resp = client.send_command({
                "type": "save_scene_copy",
                "params": {"filepath": filepath, "pack": bool(pack)}
            })
            return resp
        except Exception as e:
            print(f"Exception in save_scene_copy: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            client.close()

    @staticmethod
    def execute_code(code, host=HOST, port=PORT, expects_render=False, headless_enabled=True, headless_timeout=300, fallback_to_socket=True):
        """
        Execute arbitrary Python code in the Blender server context.
        Handles connection and closure internally.
        :param code: Python code (string) to execute in Blender.
        :param host: Hostname or IP address of the Blender server.
        :param port: Port number of the Blender server.
        :param expects_render: Flag indicating if this is rendering code.
        :param headless_enabled: Whether headless execution is enabled.
        :param headless_timeout: Timeout for headless execution in seconds.
        :param fallback_to_socket: Whether to fallback to socket execution if headless fails.
        :return: Execution result as a dictionary, or error dict on failure.
        """
        # Determine execution method
        use_headless = should_use_headless(code, expects_render, headless_enabled)
        
        if use_headless:
            print(f"[BlenderClient] Using headless execution for rendering code")
            result = execute_headless_blender(code, headless_timeout)
            
            # If headless failed and fallback is enabled, try socket execution
            if result.get("status") == "error" and fallback_to_socket:
                print(f"[BlenderClient] Headless execution failed, falling back to socket execution")
                return BlenderClient.execute_code_socket(code, host, port)
            
            return result
        else:
            # Use regular socket execution for non-rendering code
            return BlenderClient.execute_code_socket(code, host, port)
    
    @staticmethod
    def execute_code_socket(code, host=HOST, port=PORT):
        """
        Execute code using socket communication (original method).
        :param code: Python code (string) to execute in Blender.
        :param host: Hostname or IP address of the Blender server.
        :param port: Port number of the Blender server.
        :return: Execution result as a dictionary, or error dict on failure.
        """
        client = BlenderClient(host, port)
        try:
            client.connect()
            resp = client.send_command({"type": "execute_code", "params": {"code": code}})
            return resp
        except Exception as e:
            print(f"Exception in execute_code_socket: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            client.close()
