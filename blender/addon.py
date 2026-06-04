# Blender VeriAnim Addon
# Socket server for code execution, scene inspection, validation, and renders.
import bpy
import math
import socket
import threading
import json
import traceback
import io
import os
import queue
import sys
from contextlib import redirect_stderr, redirect_stdout
from mathutils import Vector
from bpy.props import IntProperty, BoolProperty

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from blender.verianim_utils import configure_render

bl_info = {
    "name": "VeriAnim Blender",
    "author": "Sining Lu",
    "version": (3, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > VeriAnim",
    "description": "Socket server for VeriAnim code execution, scene inspection, validation, and rendering",
    "category": "Interface",
}

HOST = 'localhost'
PORT = 8888

class VeriAnimAgentServer:
    def __init__(self, host=HOST, port=PORT):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None
        self.command_queue = queue.Queue()

    def start(self):
        if self.running and self.socket:
            print("Server already running")
            return True
        self.running = True
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            self.server_thread = threading.Thread(target=self._server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()
            print(f"VeriAnimAgentServer started on {self.host}:{self.port}")
            return True
        except Exception as e:
            print(f"Failed to start server: {e}")
            self.stop()
            return False

    def stop(self):
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None
        if self.server_thread:
            try:
                if self.server_thread.is_alive():
                    self.server_thread.join(timeout=1.0)
            except:
                pass
            self.server_thread = None
        print("VeriAnimAgentServer stopped")

    def _server_loop(self):
        print("Server thread started")
        self.socket.settimeout(1.0)
        while self.running:
            try:
                try:
                    client, address = self.socket.accept()
                    print(f"Connected to client: {address}")
                    client_thread = threading.Thread(target=self._handle_client, args=(client,))
                    client_thread.daemon = True
                    client_thread.start()
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"Error accepting connection: {e}")
            except Exception as e:
                print(f"Error in server loop: {e}")
                if not self.running:
                    break
        print("Server thread stopped")

    def _handle_client(self, client):
        print("Client handler started")
        buffer = b''
        try:
            while self.running:
                try:
                    data = client.recv(8192)
                    if not data:
                        print("Client disconnected")
                        break
                    buffer += data
                    try:
                        command = json.loads(buffer.decode('utf-8'))
                        buffer = b''
                        if getattr(bpy.app, "background", False):
                            response_queue = queue.Queue(maxsize=1)
                            self.command_queue.put((command, response_queue))
                            try:
                                response = response_queue.get(timeout=3600)
                            except queue.Empty:
                                response = {"status": "error", "message": "Timed out waiting for Blender command execution"}
                            self._send_response(client, response)
                            continue
                        def execute_wrapper():
                            try:
                                response = self.execute_command(command)
                                self._send_response(client, response)
                            except Exception as e:
                                print(f"Error executing command: {e}")
                                traceback.print_exc()
                                try:
                                    error_response = {"status": "error", "message": str(e)}
                                    self._send_response(client, error_response)
                                except:
                                    pass
                            return None
                        bpy.app.timers.register(execute_wrapper, first_interval=0.0)
                    except json.JSONDecodeError:
                        pass
                except Exception as e:
                    print(f"Error receiving data: {e}")
                    break
        except Exception as e:
            print(f"Error in client handler: {e}")
        finally:
            try:
                client.close()
            except:
                pass
            print("Client handler stopped")

    def _send_response(self, client, response):
        response_json = json.dumps(response)
        try:
            client.sendall(response_json.encode('utf-8'))
        except:
            print("Failed to send response - client disconnected")

    def process_pending_commands(self, limit=100):
        processed = 0
        while processed < limit:
            try:
                command, response_queue = self.command_queue.get_nowait()
            except queue.Empty:
                break
            try:
                response = self.execute_command(command)
            except Exception as e:
                traceback.print_exc()
                response = {"status": "error", "message": str(e)}
            response_queue.put(response)
            processed += 1
        return processed

    def execute_command(self, command):
        try:
            cmd_type = command.get("type")
            params = command.get("params", {})
            if cmd_type == "get_scene_info":
                return {"status": "success", "result": self.get_scene_info()}
            elif cmd_type == "get_object_info":
                return {"status": "success", "result": self.get_object_info(params.get("name"))}
            elif cmd_type == "get_scene_graph":
                return {"status": "success", "result": self.get_scene_graph(params)}
            elif cmd_type == "get_object_bbox":
                return {"status": "success", "result": self.get_object_bbox(params)}
            elif cmd_type == "get_material_info":
                return {"status": "success", "result": self.get_material_info(params)}
            elif cmd_type == "get_camera_view_report":
                return {"status": "success", "result": self.get_camera_view_report(params)}
            elif cmd_type == "get_animation_info":
                return {"status": "success", "result": self.get_animation_info(params)}
            elif cmd_type == "sample_object_transforms":
                return {"status": "success", "result": self.sample_object_transforms(params)}
            elif cmd_type == "run_validation":
                return {"status": "success", "result": self.run_validation(params)}
            elif cmd_type == "render_view_plan":
                return {"status": "success", "result": self.render_view_plan(params)}
            elif cmd_type == "render_animation_preview":
                return {"status": "success", "result": self.render_animation_preview(params)}
            elif cmd_type == "execute_code":
                return {"status": "success", "result": self.execute_code(params.get("code"))}
            elif cmd_type == "save_scene_copy":
                return {"status": "success", "result": self.save_scene_copy(params)}
            else:
                return {"status": "error", "message": f"Unknown command type: {cmd_type}"}
        except Exception as e:
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def get_scene_info(self):
        scene = bpy.context.scene
        return {
            "name": scene.name,
            "object_count": len(scene.objects),
            "objects": [obj.name for obj in scene.objects]
        }

    def _find_object(self, name_or_id):
        if not name_or_id:
            return None
        obj = bpy.data.objects.get(name_or_id)
        if obj:
            return obj
        for obj in bpy.data.objects:
            if obj.get("verianim_id") == name_or_id:
                return obj
        for obj in bpy.data.objects:
            if obj.name.startswith(str(name_or_id)):
                return obj
        return None

    def _find_objects(self, name_or_id):
        if not name_or_id:
            return []
        matches = []
        exact = bpy.data.objects.get(name_or_id)
        if exact:
            matches.append(exact)
        for obj in bpy.data.objects:
            if obj not in matches and obj.get("verianim_id") == name_or_id:
                matches.append(obj)
        for obj in bpy.data.objects:
            if obj not in matches and obj.name.startswith(str(name_or_id)):
                matches.append(obj)
        return matches

    def _object_identity(self, obj):
        return {
            "name": obj.name,
            "verianim_id": obj.get("verianim_id"),
            "verianim_role": obj.get("verianim_role"),
            "verianim_part": obj.get("verianim_part"),
            "type": obj.type,
        }

    def _world_bbox(self, obj, evaluated=False):
        target = obj
        if evaluated:
            try:
                depsgraph = bpy.context.evaluated_depsgraph_get()
                target = obj.evaluated_get(depsgraph)
            except Exception:
                target = obj
        if not getattr(target, "bound_box", None):
            return None
        corners = [target.matrix_world @ Vector(corner) for corner in target.bound_box]
        if not corners:
            return None
        min_v = Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners)))
        max_v = Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners)))
        center = (min_v + max_v) * 0.5
        size = max_v - min_v
        return {
            "corners": [list(v) for v in corners],
            "min": list(min_v),
            "max": list(max_v),
            "center": list(center),
            "size": list(size),
        }

    def _aggregate_bbox(self, objects, evaluated=False):
        corners = []
        for obj in objects:
            if obj.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META"}:
                continue
            bbox = self._world_bbox(obj, evaluated=evaluated)
            if bbox:
                corners.extend(Vector(corner) for corner in bbox["corners"])
        if not corners:
            return None
        min_v = Vector((min(v.x for v in corners), min(v.y for v in corners), min(v.z for v in corners)))
        max_v = Vector((max(v.x for v in corners), max(v.y for v in corners), max(v.z for v in corners)))
        center = (min_v + max_v) * 0.5
        size = max_v - min_v
        return {
            "corners": [list(v) for v in corners],
            "min": list(min_v),
            "max": list(max_v),
            "center": list(center),
            "size": list(size),
        }

    def _object_summary(self, obj, include_bbox=True, evaluated=False):
        data = {
            **self._object_identity(obj),
            "location": list(obj.location),
            "rotation_euler": list(obj.rotation_euler),
            "scale": list(obj.scale),
            "matrix_world": [list(row) for row in obj.matrix_world],
            "parent": obj.parent.name if obj.parent else None,
            "children": [child.name for child in obj.children],
            "visible_get": bool(obj.visible_get()),
            "hide_viewport": bool(obj.hide_viewport),
            "hide_render": bool(obj.hide_render),
            "materials": [],
            "mesh": None,
        }
        if hasattr(obj.data, "materials"):
            data["materials"] = [mat.name if mat else None for mat in obj.data.materials]
        if obj.type == "MESH" and obj.data:
            data["mesh"] = {
                "vertices": len(obj.data.vertices),
                "edges": len(obj.data.edges),
                "polygons": len(obj.data.polygons),
            }
        if include_bbox:
            data["bbox"] = self._world_bbox(obj, evaluated=evaluated)
        return data

    def get_object_info(self, name):
        obj = self._find_object(name)
        if not obj:
            return {"error": f"Object not found: {name}"}
        return self._object_summary(obj)

    def get_scene_graph(self, params):
        include_hidden = bool(params.get("include_hidden", False))
        evaluated = bool(params.get("evaluated", False))
        objects = []
        for obj in bpy.context.scene.objects:
            if not include_hidden and (obj.hide_viewport or obj.hide_render):
                continue
            objects.append(self._object_summary(obj, include_bbox=True, evaluated=evaluated))
        return {
            "scene": {
                "name": bpy.context.scene.name,
                "frame_current": bpy.context.scene.frame_current,
                "frame_start": bpy.context.scene.frame_start,
                "frame_end": bpy.context.scene.frame_end,
                "fps": bpy.context.scene.render.fps,
                "camera": bpy.context.scene.camera.name if bpy.context.scene.camera else None,
            },
            "collections": [
                {
                    "name": col.name,
                    "objects": [obj.name for obj in col.objects],
                    "children": [child.name for child in col.children],
                }
                for col in bpy.data.collections
            ],
            "objects": objects,
            "materials": [self._material_summary(mat) for mat in bpy.data.materials],
        }

    def get_object_bbox(self, params):
        name_or_id = params.get("name") or params.get("id")
        objects = self._find_objects(name_or_id)
        if not objects:
            return {"found": False, "message": "Object not found"}
        return {
            "found": True,
            "objects": [self._object_identity(obj) for obj in objects],
            "bbox": self._aggregate_bbox(objects, evaluated=bool(params.get("evaluated", False))),
        }

    def _material_summary(self, mat):
        data = {
            "name": mat.name,
            "diffuse_color": list(mat.diffuse_color),
            "use_nodes": bool(mat.use_nodes),
            "users": mat.users,
            "node_names": [],
        }
        if mat.use_nodes and mat.node_tree:
            data["node_names"] = [node.name for node in mat.node_tree.nodes]
        return data

    def get_material_info(self, params):
        name = params.get("name")
        if name:
            mat = bpy.data.materials.get(name)
            if not mat:
                return {"found": False, "message": f"Material not found: {name}"}
            return {"found": True, "material": self._material_summary(mat)}
        return {"materials": [self._material_summary(mat) for mat in bpy.data.materials]}

    def get_camera_view_report(self, params):
        camera = self._find_object(params.get("camera")) if params.get("camera") else bpy.context.scene.camera
        if not camera or camera.type != "CAMERA":
            return {"ok": False, "message": "No camera found"}
        target_ids = params.get("target_ids") or []
        targets = [self._find_object(item) for item in target_ids]
        targets = [obj for obj in targets if obj]
        if not targets:
            targets = [obj for obj in bpy.context.scene.objects if obj.type in {"MESH", "CURVE", "EMPTY"}]
        try:
            from bpy_extras.object_utils import world_to_camera_view
        except Exception as e:
            return {"ok": False, "message": str(e)}
        depsgraph = bpy.context.evaluated_depsgraph_get()
        objects = []
        for obj in targets:
            bbox = self._world_bbox(obj, evaluated=True)
            if not bbox:
                continue
            coords = [world_to_camera_view(bpy.context.scene, camera, Vector(corner)) for corner in bbox["corners"]]
            in_view = any(0 <= co.x <= 1 and 0 <= co.y <= 1 and co.z >= 0 for co in coords)
            objects.append({
                **self._object_identity(obj),
                "in_view": bool(in_view),
                "projected": [[co.x, co.y, co.z] for co in coords],
            })
        return {"ok": True, "camera": self._object_summary(camera, include_bbox=False), "objects": objects}

    def get_animation_info(self, params):
        object_ids = params.get("object_ids") or []
        objects = [self._find_object(item) for item in object_ids] if object_ids else list(bpy.context.scene.objects)
        objects = [obj for obj in objects if obj]
        result = {
            "scene": {
                "frame_start": bpy.context.scene.frame_start,
                "frame_end": bpy.context.scene.frame_end,
                "fps": bpy.context.scene.render.fps,
            },
            "objects": [],
        }
        for obj in objects:
            action = obj.animation_data.action if obj.animation_data else None
            fcurves = []
            if action:
                for fc in action.fcurves:
                    fcurves.append({
                        "data_path": fc.data_path,
                        "array_index": fc.array_index,
                        "range": list(fc.range()),
                        "keyframes": [
                            {
                                "frame": key.co.x,
                                "value": key.co.y,
                                "interpolation": key.interpolation,
                            }
                            for key in fc.keyframe_points
                        ],
                    })
            result["objects"].append({
                **self._object_identity(obj),
                "has_animation_data": bool(obj.animation_data),
                "action": action.name if action else None,
                "fcurves": fcurves,
            })
        return result

    def sample_object_transforms(self, params):
        object_ids = params.get("object_ids") or []
        frames = [int(frame) for frame in (params.get("frames") or [bpy.context.scene.frame_current])]
        objects = [self._find_object(item) for item in object_ids] if object_ids else list(bpy.context.scene.objects)
        objects = [obj for obj in objects if obj]
        original_frame = bpy.context.scene.frame_current
        samples = {}
        try:
            for frame in frames:
                bpy.context.scene.frame_set(frame)
                frame_samples = []
                for obj in objects:
                    frame_samples.append({
                        **self._object_identity(obj),
                        "location": list(obj.matrix_world.translation),
                        "rotation_euler": list(obj.rotation_euler),
                        "scale": list(obj.scale),
                        "bbox": self._world_bbox(obj, evaluated=True),
                    })
                samples[str(frame)] = frame_samples
        finally:
            bpy.context.scene.frame_set(original_frame)
        return {"frames": frames, "samples": samples}

    def run_validation(self, params):
        ir = params.get("ir") or {}
        scene_spec = ir.get("scene") or {}
        include_scene = bool(params.get("include_scene", True))
        include_animation = bool(params.get("include_animation", bool(ir.get("animation"))))
        issues = []

        def issue(code, message, severity="major", target_id=None, relation_id=None, frame=None, evidence=None):
            issues.append({
                "code": code,
                "message": message,
                "severity": severity,
                "target_id": target_id,
                "relation_id": relation_id,
                "frame": frame,
                "evidence": evidence or {},
            })

        objects = {}
        for spec in scene_spec.get("objects", []):
            object_id = spec.get("id")
            matches = self._find_objects(object_id)
            if not matches and include_scene:
                issue("MISSING_OBJECT", f"Object '{object_id}' was not created.", "critical", target_id=object_id)
                continue
            if not matches:
                continue
            objects[object_id] = matches
            mesh_parts = [obj for obj in matches if obj.type == "MESH"]
            if include_scene:
                if mesh_parts and all(len(obj.data.vertices) == 0 for obj in mesh_parts):
                    issue("EMPTY_MESH", f"Object '{object_id}' has no mesh vertices.", "critical", target_id=object_id)
                if spec.get("material_ids") and mesh_parts and not any(obj.data.materials for obj in mesh_parts):
                    issue("MISSING_MATERIAL", f"Object '{object_id}' has no material assigned.", "major", target_id=object_id)

        if include_scene:
            for relation in scene_spec.get("relations", []):
                self._validate_relation(relation, objects, issue)

        if include_scene and scene_spec.get("cameras") and not bpy.context.scene.camera:
            issue("MISSING_ACTIVE_CAMERA", "Scene has camera specs but no active camera.", "major")

        animation = ir.get("animation") if include_animation else None
        trace = {}
        if animation:
            self._validate_animation(animation, trace, issue)

        return {
            "passed": not issues,
            "issues": issues,
            "summary": "Validation passed." if not issues else "Validation found issues.",
            "trace": trace,
        }

    def _validate_relation(self, relation, objects, issue):
        relation_id = relation.get("id")
        subject_id = relation.get("subject_id")
        object_id = relation.get("object_id")
        subj = objects.get(subject_id)
        obj = objects.get(object_id)
        if not subj or not obj:
            return
        sb = self._aggregate_bbox(subj, evaluated=True)
        ob = self._aggregate_bbox(obj, evaluated=True)
        if not sb or not ob:
            issue("MISSING_BBOX", "Could not compute relation bounding boxes.", target_id=subject_id, relation_id=relation_id)
            return
        sc = Vector(sb["center"])
        oc = Vector(ob["center"])
        smin, smax = Vector(sb["min"]), Vector(sb["max"])
        omin, omax = Vector(ob["min"]), Vector(ob["max"])
        tol = float(relation.get("tolerance", 0.05))
        rtype = relation.get("relation_type")
        if rtype == "on_top_of":
            overlap_x = min(smax.x, omax.x) - max(smin.x, omin.x)
            overlap_y = min(smax.y, omax.y) - max(smin.y, omin.y)
            z_gap = abs(smin.z - omax.z)
            if overlap_x <= 0 or overlap_y <= 0 or z_gap > max(tol, 0.12):
                issue("RELATION_ON_TOP_OF_FAILED", f"'{subject_id}' is not clearly on top of '{object_id}'.", "major", subject_id, relation_id, evidence={"z_gap": z_gap, "overlap_x": overlap_x, "overlap_y": overlap_y})
        elif rtype == "left_of" and not (sc.x < oc.x - tol):
            issue("RELATION_LEFT_OF_FAILED", f"'{subject_id}' is not left of '{object_id}'.", "major", subject_id, relation_id, evidence={"subject_x": sc.x, "object_x": oc.x})
        elif rtype == "right_of" and not (sc.x > oc.x + tol):
            issue("RELATION_RIGHT_OF_FAILED", f"'{subject_id}' is not right of '{object_id}'.", "major", subject_id, relation_id, evidence={"subject_x": sc.x, "object_x": oc.x})
        elif rtype == "near":
            max_dist = float(relation.get("max_distance") or 2.0)
            dist = (sc - oc).length
            if dist > max_dist:
                issue("RELATION_NEAR_FAILED", f"'{subject_id}' is too far from '{object_id}'.", "major", subject_id, relation_id, evidence={"distance": dist, "max_distance": max_dist})
        elif rtype == "not_intersecting":
            overlap = (
                min(smax.x, omax.x) - max(smin.x, omin.x),
                min(smax.y, omax.y) - max(smin.y, omin.y),
                min(smax.z, omax.z) - max(smin.z, omin.z),
            )
            if all(value > tol for value in overlap):
                issue("RELATION_INTERSECTION_FAILED", f"'{subject_id}' appears to intersect '{object_id}'.", "major", subject_id, relation_id, evidence={"overlap": overlap})

    def _validate_animation(self, animation, trace, issue):
        duration = int(animation.get("duration_frames") or 0)
        if duration > 0 and bpy.context.scene.frame_end < duration:
            issue("FRAME_END_TOO_SHORT", "Scene frame_end is shorter than AnimationSpec duration.", "major", evidence={"frame_end": bpy.context.scene.frame_end, "duration": duration})
        events = list(animation.get("events", [])) + list(animation.get("camera_events", []))
        for event in events:
            frames = sorted(set([
                int(event.get("start_frame", 1)),
                int((event.get("start_frame", 1) + event.get("end_frame", 1)) / 2),
                int(event.get("end_frame", 1)),
            ]))
            for subject_id in event.get("subject_ids", []):
                obj = self._find_object(subject_id)
                if not obj:
                    issue("MISSING_ANIMATED_OBJECT", f"Animated object '{subject_id}' was not found.", "critical", target_id=subject_id)
                    continue
                if event.get("action") not in {"camera_move", "camera_orbit"} and not obj.animation_data:
                    issue("MISSING_ANIMATION_DATA", f"Animated object '{subject_id}' has no animation_data.", "major", target_id=subject_id)
                trace.setdefault(subject_id, [])
                original_frame = bpy.context.scene.frame_current
                try:
                    for frame in frames:
                        bpy.context.scene.frame_set(frame)
                        trace[subject_id].append({
                            "frame": frame,
                            "location": list(obj.matrix_world.translation),
                            "rotation_euler": list(obj.rotation_euler),
                            "scale": list(obj.scale),
                        })
                finally:
                    bpy.context.scene.frame_set(original_frame)

    def _bbox_for_objects(self, objects):
        points = []
        for obj in objects:
            bbox = self._world_bbox(obj, evaluated=True)
            if bbox:
                points.extend(Vector(corner) for corner in bbox["corners"])
        if not points:
            points = [Vector((0.0, 0.0, 0.0))]
        min_v = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
        max_v = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
        return min_v, max_v

    def _look_at(self, camera, target):
        direction = target - camera.location
        camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    def _camera_offset(self, view_type, radius):
        if view_type == "front":
            return Vector((0, -radius, radius * 0.35))
        if view_type == "back":
            return Vector((0, radius, radius * 0.35))
        if view_type == "left":
            return Vector((-radius, 0, radius * 0.35))
        if view_type == "right":
            return Vector((radius, 0, radius * 0.35))
        if view_type == "top":
            return Vector((0, 0, radius))
        if view_type == "bottom":
            return Vector((0, 0, -radius))
        return Vector((radius * 0.8, -radius * 0.8, radius * 0.55))

    def _ensure_render_settings(self, width, height):
        scene = bpy.context.scene
        configure_render(scene, width=int(width), height=int(height), engine="workbench")

    def render_view_plan(self, params):
        views = params.get("views") or []
        output_dir = params.get("output_dir")
        width = int(params.get("width", 1280))
        height = int(params.get("height", 720))
        if not output_dir:
            return {"rendered": False, "message": "Missing output_dir", "paths": []}
        os.makedirs(output_dir, exist_ok=True)
        self._ensure_render_settings(width, height)
        scene = bpy.context.scene
        original_frame = scene.frame_current
        all_objects = [obj for obj in scene.objects if obj.type in {"MESH", "CURVE", "EMPTY"} and not obj.name.startswith("verianim_render_camera")]
        paths = []
        try:
            for view in views:
                frame = view.get("frame")
                if frame is not None:
                    scene.frame_set(int(frame))
                targets = [self._find_object(item) for item in view.get("target_object_ids", [])]
                targets = [obj for obj in targets if obj] or all_objects
                min_v, max_v = self._bbox_for_objects(targets)
                center = (min_v + max_v) * 0.5
                radius = max((max_v - min_v).length * 1.4, 3.0)
                view_id = str(view.get("id") or f"view_{len(paths) + 1}")
                cam_data = bpy.data.cameras.new(f"verianim_render_camera_{view_id}_data")
                cam = bpy.data.objects.new(f"verianim_render_camera_{view_id}", cam_data)
                scene.collection.objects.link(cam)
                cam.location = center + self._camera_offset(view.get("view_type", "three_quarter"), radius)
                self._look_at(cam, center)
                cam_data.lens = float(view.get("focal_length_mm") or 35)
                scene.camera = cam
                path = os.path.join(output_dir, f"{view_id}.png").replace("\\", "/")
                scene.render.filepath = path
                bpy.ops.render.render(write_still=True)
                paths.append(path)
        finally:
            scene.frame_set(original_frame)
        return {"rendered": True, "paths": paths}

    def render_animation_preview(self, params):
        output_dir = params.get("output_dir")
        frames = [int(frame) for frame in (params.get("frames") or [])]
        width = int(params.get("width", 1280))
        height = int(params.get("height", 720))
        render_video = bool(params.get("render_video", False))
        if not output_dir:
            return {"rendered": False, "message": "Missing output_dir", "paths": [], "video": None}
        if not frames:
            scene = bpy.context.scene
            frames = sorted(set([scene.frame_start, int((scene.frame_start + scene.frame_end) / 2), scene.frame_end]))
        views = [
            {"id": f"frame_{frame:04d}", "view_type": "three_quarter", "frame": frame, "target_object_ids": params.get("target_object_ids") or []}
            for frame in frames
        ]
        sampled = self.render_view_plan({"views": views, "output_dir": output_dir, "width": width, "height": height})
        video_path = None
        if render_video:
            try:
                scene = bpy.context.scene
                self._ensure_render_settings(width, height)
                scene.render.filepath = os.path.join(output_dir, "preview.mp4").replace("\\", "/")
                scene.render.image_settings.file_format = "FFMPEG"
                scene.render.ffmpeg.format = "MPEG4"
                bpy.ops.render.render(animation=True)
                video_path = scene.render.filepath
            except Exception as e:
                return {**sampled, "video": video_path, "video_error": str(e)}
        return {**sampled, "video": video_path, "frames": frames}

    def execute_code(self, code):
        namespace = {"__name__": "__main__", "bpy": bpy}
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        try:
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                exec(code, namespace)
        except Exception as exc:
            tb = traceback.format_exc()
            return {
                "executed": False,
                "ok": False,
                "stdout": stdout_buffer.getvalue(),
                "stderr": stderr_buffer.getvalue(),
                "traceback": tb,
                "message": str(exc),
                "result": stdout_buffer.getvalue(),
            }
        return {
            "executed": True,
            "ok": True,
            "stdout": stdout_buffer.getvalue(),
            "stderr": stderr_buffer.getvalue(),
            "traceback": None,
            "message": None,
            "result": stdout_buffer.getvalue(),
        }

    def save_scene_copy(self, params):
        """
        Save a copy of the current scene to a .blend file without changing the user's current file binding.
        Optionally pack all external assets into the .blend file for portability.
        Expected params:
          - filepath: target path for the .blend file
          - pack: bool (default True) whether to pack external assets
        """
        filepath = params.get("filepath")
        pack = bool(params.get("pack", True))
        if not filepath:
            return {"saved": False, "message": "Missing 'filepath' parameter"}
        # Ensure directory exists
        import os
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        # Pack assets if requested
        if pack:
            try:
                bpy.ops.file.pack_all()
            except Exception as e:
                print(f"[VeriAnim Addon] pack_all failed: {e}")
        # Save as copy so current file path is not changed
        try:
            bpy.ops.wm.save_as_mainfile(filepath=filepath, copy=True)
            return {"saved": True, "filepath": filepath}
        except Exception as e:
            return {"saved": False, "message": str(e)}

# --- Blender UI Panel and Operators ---``
class BLENDERCUSTOMAGENT_OT_StartServer(bpy.types.Operator):
    bl_idname = "blendercustomagent.start_server"
    bl_label = "Start VeriAnim Server"
    bl_description = "Start the Blender VeriAnim socket server"

    def execute(self, context):
        scene = context.scene
        existing = getattr(bpy.types, "blendercustomagent_server", None)
        if existing and getattr(existing, "port", None) != scene.blendercustomagent_port:
            existing.stop()
            del bpy.types.blendercustomagent_server
            existing = None
        if not existing:
            bpy.types.blendercustomagent_server = VeriAnimAgentServer(port=scene.blendercustomagent_port)
        if bpy.types.blendercustomagent_server.start():
            scene.blendercustomagent_server_running = True
            self.report({'INFO'}, f"VeriAnim Server started on port {scene.blendercustomagent_port}")
            return {'FINISHED'}
        scene.blendercustomagent_server_running = False
        self.report({'ERROR'}, f"VeriAnim Server failed to start on port {scene.blendercustomagent_port}; check Blender console")
        return {'CANCELLED'}

class BLENDERCUSTOMAGENT_OT_StopServer(bpy.types.Operator):
    bl_idname = "blendercustomagent.stop_server"
    bl_label = "Stop VeriAnim Server"
    bl_description = "Stop the Blender VeriAnim socket server"

    def execute(self, context):
        scene = context.scene
        if hasattr(bpy.types, "blendercustomagent_server") and bpy.types.blendercustomagent_server:
            bpy.types.blendercustomagent_server.stop()
            del bpy.types.blendercustomagent_server
        scene.blendercustomagent_server_running = False
        self.report({'INFO'}, "VeriAnim Server stopped!")
        return {'FINISHED'}

class BLENDERCUSTOMAGENT_PT_Panel(bpy.types.Panel):
    bl_label = "Blender VeriAnim"
    bl_idname = "BLENDERVERIANIMAGENT_PT_Panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'VeriAnim'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "blendercustomagent_port")
        if not scene.blendercustomagent_server_running:
            layout.operator("blendercustomagent.start_server", text="Start VeriAnim Server")
        else:
            layout.operator("blendercustomagent.stop_server", text="Stop VeriAnim Server")
            layout.label(text=f"Running on port {scene.blendercustomagent_port}")

# --- Registration ---
classes = [
    BLENDERCUSTOMAGENT_OT_StartServer,
    BLENDERCUSTOMAGENT_OT_StopServer,
    BLENDERCUSTOMAGENT_PT_Panel,
]

def register():
    bpy.types.Scene.blendercustomagent_port = IntProperty(
        name="Port",
        description="Port for the VeriAnim server",
        default=8888,
        min=1024,
        max=65535
    )
    bpy.types.Scene.blendercustomagent_server_running = BoolProperty(
        name="Server Running",
        default=False
    )
    for cls in classes:
        bpy.utils.register_class(cls)
    print("Blender VeriAnim addon registered")

def unregister():
    if hasattr(bpy.types, "blendercustomagent_server") and bpy.types.blendercustomagent_server:
        bpy.types.blendercustomagent_server.stop()
        del bpy.types.blendercustomagent_server
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.blendercustomagent_port
    del bpy.types.Scene.blendercustomagent_server_running
    print("Blender VeriAnim addon unregistered")

if __name__ == "__main__":
    register() 
