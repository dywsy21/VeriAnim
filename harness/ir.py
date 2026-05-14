"""Intermediate representation for scene and animation generation.

The IR is intentionally independent from any specific LLM provider. LLMs should
emit JSON that can be mapped into these dataclasses before code generation,
deterministic validation, visual verification, and refinement.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
import json
from typing import Any


Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
RGBA = tuple[float, float, float, float]

IR_VERSION = "0.1"


class Severity(str, Enum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class Importance(str, Enum):
    OPTIONAL = "optional"
    PREFERRED = "preferred"
    REQUIRED = "required"


class ObjectRole(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    BACKGROUND = "background"
    SUPPORT = "support"
    DECORATION = "decoration"
    CAMERA_TARGET = "camera_target"
    COLLIDER = "collider"


class ObjectCategory(str, Enum):
    GENERIC = "generic"
    FURNITURE = "furniture"
    PROP = "prop"
    CHARACTER = "character"
    VEHICLE = "vehicle"
    ARCHITECTURE = "architecture"
    TERRAIN = "terrain"
    LIGHTING = "lighting"
    CAMERA_RIG = "camera_rig"
    EFFECT = "effect"


class RelationType(str, Enum):
    ON_TOP_OF = "on_top_of"
    UNDER = "under"
    INSIDE = "inside"
    CONTAINS = "contains"
    LEFT_OF = "left_of"
    RIGHT_OF = "right_of"
    IN_FRONT_OF = "in_front_of"
    BEHIND = "behind"
    NEAR = "near"
    FAR_FROM = "far_from"
    FACING = "facing"
    ALIGNED_WITH = "aligned_with"
    TOUCHING = "touching"
    NOT_INTERSECTING = "not_intersecting"
    ATTACHED_TO = "attached_to"
    SAME_HEIGHT_AS = "same_height_as"


class EnvironmentType(str, Enum):
    EMPTY = "empty"
    STUDIO = "studio"
    ROOM = "room"
    OUTDOOR = "outdoor"
    SKYBOX = "skybox"
    ABSTRACT = "abstract"
    CUSTOM = "custom"


class LightType(str, Enum):
    AREA = "area"
    POINT = "point"
    SUN = "sun"
    SPOT = "spot"
    HDRI = "hdri"


class CameraViewType(str, Enum):
    FRONT = "front"
    BACK = "back"
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"
    THREE_QUARTER = "three_quarter"
    CLOSE_UP = "close_up"
    RELATION_CLOSE_UP = "relation_close_up"
    FREE = "free"


class RenderEngine(str, Enum):
    EEVEE = "eevee"
    CYCLES = "cycles"
    WORKBENCH = "workbench"


class AnimationAction(str, Enum):
    TRANSLATE = "translate"
    ROTATE = "rotate"
    SCALE = "scale"
    FOLLOW_PATH = "follow_path"
    APPEAR = "appear"
    DISAPPEAR = "disappear"
    MORPH = "morph"
    CAMERA_MOVE = "camera_move"
    CAMERA_ORBIT = "camera_orbit"
    PHYSICS = "physics"
    CUSTOM = "custom"


class Interpolation(str, Enum):
    CONSTANT = "constant"
    LINEAR = "linear"
    EASE_IN = "ease_in"
    EASE_OUT = "ease_out"
    EASE_IN_OUT = "ease_in_out"
    BEZIER = "bezier"


class VerificationMode(str, Enum):
    DETERMINISTIC = "deterministic"
    VISION = "vision"
    VIDEO = "video"
    HUMAN = "human"


@dataclass(slots=True)
class ValidationIssue:
    code: str
    message: str
    severity: Severity = Severity.MAJOR
    target_id: str | None = None
    relation_id: str | None = None
    frame: int | None = None
    suggested_fix: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ValidationReport:
    mode: VerificationMode
    passed: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    summary: str | None = None
    artifacts: list[str] = field(default_factory=list)

    @classmethod
    def ok(cls, mode: VerificationMode, summary: str | None = None) -> "ValidationReport":
        return cls(mode=mode, passed=True, summary=summary)

    @classmethod
    def failed(
        cls,
        mode: VerificationMode,
        issues: list[ValidationIssue],
        summary: str | None = None,
    ) -> "ValidationReport":
        return cls(mode=mode, passed=False, issues=issues, summary=summary)


@dataclass(slots=True)
class SourcePrompt:
    text: str
    negative_text: str | None = None
    image_paths: list[str] = field(default_factory=list)
    user_constraints: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StyleSpec:
    description: str | None = None
    realism: float | None = None
    detail_level: str = "medium"
    color_palette: list[str] = field(default_factory=list)
    material_style: str | None = None


@dataclass(slots=True)
class DimensionSpec:
    size: Vec3 | None = None
    min_size: Vec3 | None = None
    max_size: Vec3 | None = None
    tolerance: float = 0.1


@dataclass(slots=True)
class TransformSpec:
    location: Vec3 | None = None
    rotation_euler: Vec3 | None = None
    scale: Vec3 | None = None


@dataclass(slots=True)
class MaterialSpec:
    id: str
    description: str
    base_color: RGBA | None = None
    metallic: float | None = None
    roughness: float | None = None
    alpha: float | None = None
    texture_hints: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ObjectPartSpec:
    id: str
    description: str
    required: bool = True
    material_id: str | None = None
    expected_count: int | None = None
    dimension: DimensionSpec | None = None


@dataclass(slots=True)
class PlacementSpec:
    transform: TransformSpec = field(default_factory=TransformSpec)
    anchor: str = "origin"
    parent_id: str | None = None
    notes: str | None = None


@dataclass(slots=True)
class ObjectSpec:
    id: str
    description: str
    label: str | None = None
    category: ObjectCategory = ObjectCategory.GENERIC
    role: ObjectRole = ObjectRole.SECONDARY
    importance: Importance = Importance.REQUIRED
    parts: list[ObjectPartSpec] = field(default_factory=list)
    required_features: list[str] = field(default_factory=list)
    optional_features: list[str] = field(default_factory=list)
    forbidden_features: list[str] = field(default_factory=list)
    dimensions: DimensionSpec | None = None
    placement: PlacementSpec = field(default_factory=PlacementSpec)
    material_ids: list[str] = field(default_factory=list)
    generation_notes: str | None = None
    visual_check_prompts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SpatialRelationSpec:
    id: str
    relation_type: RelationType
    subject_id: str
    object_id: str
    description: str | None = None
    required: bool = True
    tolerance: float = 0.05
    min_distance: float | None = None
    max_distance: float | None = None
    offset: Vec3 | None = None
    axis: str | None = None
    visual_priority: Importance = Importance.REQUIRED


@dataclass(slots=True)
class LightSpec:
    id: str
    light_type: LightType
    description: str | None = None
    location: Vec3 | None = None
    rotation_euler: Vec3 | None = None
    energy: float | None = None
    color: RGBA | None = None
    size: float | None = None


@dataclass(slots=True)
class EnvironmentSpec:
    environment_type: EnvironmentType = EnvironmentType.STUDIO
    description: str | None = None
    floor: str | None = None
    walls: str | None = None
    sky: str | None = None
    world_background: str | None = None
    lights: list[LightSpec] = field(default_factory=list)
    ambient_occlusion: bool = True
    notes: str | None = None


@dataclass(slots=True)
class CameraSpec:
    id: str
    view_type: CameraViewType = CameraViewType.THREE_QUARTER
    description: str | None = None
    location: Vec3 | None = None
    look_at: Vec3 | None = None
    target_object_ids: list[str] = field(default_factory=list)
    focal_length_mm: float | None = None
    coverage: str | None = None
    frame_range: tuple[int, int] | None = None


@dataclass(slots=True)
class RenderSpec:
    resolution: tuple[int, int] = (1280, 720)
    engine: RenderEngine = RenderEngine.EEVEE
    samples: int | None = None
    transparent_background: bool = False
    output_dir: str | None = None


@dataclass(slots=True)
class ScreenshotViewSpec:
    id: str
    view_type: CameraViewType
    description: str
    camera_id: str | None = None
    target_object_ids: list[str] = field(default_factory=list)
    relation_ids: list[str] = field(default_factory=list)
    frame: int | None = None
    crop_hint: str | None = None
    required: bool = True


@dataclass(slots=True)
class ScreenshotPlan:
    views: list[ScreenshotViewSpec] = field(default_factory=list)
    render: RenderSpec = field(default_factory=RenderSpec)
    min_required_views: int = 3


@dataclass(slots=True)
class DeterministicCheckSpec:
    id: str
    description: str
    target_ids: list[str] = field(default_factory=list)
    relation_ids: list[str] = field(default_factory=list)
    required: bool = True


@dataclass(slots=True)
class VisualVerifierSpec:
    enabled: bool = True
    model_hint: str | None = None
    required_view_ids: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    pass_criteria: list[str] = field(default_factory=list)
    max_rounds: int = 2


@dataclass(slots=True)
class VideoVerifierSpec:
    enabled: bool = False
    model_hint: str | None = "qwen3.5-omni"
    sampled_frames: list[int] = field(default_factory=list)
    require_preview_video: bool = True
    questions: list[str] = field(default_factory=list)
    pass_criteria: list[str] = field(default_factory=list)
    max_rounds: int = 2


@dataclass(slots=True)
class VerificationPlan:
    deterministic_checks: list[DeterministicCheckSpec] = field(default_factory=list)
    screenshot_plan: ScreenshotPlan = field(default_factory=ScreenshotPlan)
    visual: VisualVerifierSpec = field(default_factory=VisualVerifierSpec)
    video: VideoVerifierSpec = field(default_factory=VideoVerifierSpec)


@dataclass(slots=True)
class KeyframeSpec:
    frame: int
    transform: TransformSpec | None = None
    value: dict[str, Any] = field(default_factory=dict)
    interpolation: Interpolation = Interpolation.EASE_IN_OUT
    description: str | None = None


@dataclass(slots=True)
class MotionPathSpec:
    points: list[Vec3] = field(default_factory=list)
    keyframes: list[KeyframeSpec] = field(default_factory=list)
    path_object_id: str | None = None
    follow_orientation: bool = False


@dataclass(slots=True)
class AnimationEventSpec:
    id: str
    action: AnimationAction
    subject_ids: list[str]
    start_frame: int
    end_frame: int
    description: str
    target_ids: list[str] = field(default_factory=list)
    path: MotionPathSpec | None = None
    start_transform: TransformSpec | None = None
    end_transform: TransformSpec | None = None
    interpolation: Interpolation = Interpolation.EASE_IN_OUT
    required: bool = True
    expected_visual_result: str | None = None
    constraints: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AnimationSpec:
    duration_frames: int
    fps: int = 24
    events: list[AnimationEventSpec] = field(default_factory=list)
    camera_events: list[AnimationEventSpec] = field(default_factory=list)
    loop: bool = False
    render: RenderSpec = field(default_factory=RenderSpec)
    verifier: VideoVerifierSpec = field(default_factory=VideoVerifierSpec)


@dataclass(slots=True)
class SceneSpec:
    objects: list[ObjectSpec]
    relations: list[SpatialRelationSpec] = field(default_factory=list)
    materials: list[MaterialSpec] = field(default_factory=list)
    environment: EnvironmentSpec = field(default_factory=EnvironmentSpec)
    cameras: list[CameraSpec] = field(default_factory=list)
    style: StyleSpec = field(default_factory=StyleSpec)
    verifier: VerificationPlan = field(default_factory=VerificationPlan)
    coordinate_system: str = "Blender default: Z up, right-handed"
    units: str = "meters"


@dataclass(slots=True)
class GenerationIR:
    prompt: SourcePrompt
    scene: SceneSpec
    animation: AnimationSpec | None = None
    version: str = IR_VERSION
    project_id: str | None = None
    notes: str | None = None

    def validate(self) -> ValidationReport:
        issues: list[ValidationIssue] = []
        object_ids = [obj.id for obj in self.scene.objects]
        object_id_set = set(object_ids)

        if len(object_ids) != len(object_id_set):
            issues.append(
                ValidationIssue(
                    code="DUPLICATE_OBJECT_ID",
                    message="Scene object ids must be unique.",
                    severity=Severity.CRITICAL,
                )
            )

        material_ids = {material.id for material in self.scene.materials}
        camera_ids = {camera.id for camera in self.scene.cameras}
        relation_ids = {relation.id for relation in self.scene.relations}

        for obj in self.scene.objects:
            if not obj.id:
                issues.append(
                    ValidationIssue(
                        code="EMPTY_OBJECT_ID",
                        message="Object id cannot be empty.",
                        severity=Severity.CRITICAL,
                    )
                )
            for material_id in obj.material_ids:
                if material_id not in material_ids:
                    issues.append(
                        ValidationIssue(
                            code="UNKNOWN_OBJECT_MATERIAL",
                            message=f"Object '{obj.id}' references unknown material '{material_id}'.",
                            severity=Severity.MAJOR,
                            target_id=obj.id,
                        )
                    )

        for relation in self.scene.relations:
            if relation.subject_id not in object_id_set:
                issues.append(
                    ValidationIssue(
                        code="UNKNOWN_RELATION_SUBJECT",
                        message=f"Relation '{relation.id}' references unknown subject '{relation.subject_id}'.",
                        severity=Severity.CRITICAL,
                        relation_id=relation.id,
                    )
                )
            if relation.object_id not in object_id_set:
                issues.append(
                    ValidationIssue(
                        code="UNKNOWN_RELATION_OBJECT",
                        message=f"Relation '{relation.id}' references unknown object '{relation.object_id}'.",
                        severity=Severity.CRITICAL,
                        relation_id=relation.id,
                    )
                )

        for camera in self.scene.cameras:
            for target_id in camera.target_object_ids:
                if target_id not in object_id_set:
                    issues.append(
                        ValidationIssue(
                            code="UNKNOWN_CAMERA_TARGET",
                            message=f"Camera '{camera.id}' targets unknown object '{target_id}'.",
                            severity=Severity.MAJOR,
                            target_id=target_id,
                        )
                    )

        for view in self.scene.verifier.screenshot_plan.views:
            if view.camera_id and view.camera_id not in camera_ids:
                issues.append(
                    ValidationIssue(
                        code="UNKNOWN_VIEW_CAMERA",
                        message=f"Screenshot view '{view.id}' references unknown camera '{view.camera_id}'.",
                        severity=Severity.MAJOR,
                    )
                )
            for target_id in view.target_object_ids:
                if target_id not in object_id_set:
                    issues.append(
                        ValidationIssue(
                            code="UNKNOWN_VIEW_TARGET",
                            message=f"Screenshot view '{view.id}' targets unknown object '{target_id}'.",
                            severity=Severity.MAJOR,
                            target_id=target_id,
                        )
                    )
            for relation_id in view.relation_ids:
                if relation_id not in relation_ids:
                    issues.append(
                        ValidationIssue(
                            code="UNKNOWN_VIEW_RELATION",
                            message=f"Screenshot view '{view.id}' references unknown relation '{relation_id}'.",
                            severity=Severity.MAJOR,
                            relation_id=relation_id,
                        )
                    )

        if self.animation:
            if self.animation.duration_frames <= 0:
                issues.append(
                    ValidationIssue(
                        code="INVALID_ANIMATION_DURATION",
                        message="Animation duration must be greater than zero.",
                        severity=Severity.CRITICAL,
                    )
                )
            if self.animation.fps <= 0:
                issues.append(
                    ValidationIssue(
                        code="INVALID_ANIMATION_FPS",
                        message="Animation fps must be greater than zero.",
                        severity=Severity.CRITICAL,
                    )
                )
            for event in [*self.animation.events, *self.animation.camera_events]:
                if event.start_frame < 0 or event.end_frame < event.start_frame:
                    issues.append(
                        ValidationIssue(
                            code="INVALID_EVENT_FRAME_RANGE",
                            message=f"Animation event '{event.id}' has an invalid frame range.",
                            severity=Severity.CRITICAL,
                            frame=event.start_frame,
                        )
                    )
                if event.end_frame > self.animation.duration_frames:
                    issues.append(
                        ValidationIssue(
                            code="EVENT_EXCEEDS_DURATION",
                            message=f"Animation event '{event.id}' ends after the animation duration.",
                            severity=Severity.MAJOR,
                            frame=event.end_frame,
                        )
                    )
                for subject_id in event.subject_ids:
                    if subject_id not in object_id_set and event.action not in {
                        AnimationAction.CAMERA_MOVE,
                        AnimationAction.CAMERA_ORBIT,
                    }:
                        issues.append(
                            ValidationIssue(
                                code="UNKNOWN_ANIMATION_SUBJECT",
                                message=f"Animation event '{event.id}' references unknown subject '{subject_id}'.",
                                severity=Severity.CRITICAL,
                                target_id=subject_id,
                            )
                        )
                for target_id in event.target_ids:
                    if target_id not in object_id_set and target_id not in camera_ids:
                        issues.append(
                            ValidationIssue(
                                code="UNKNOWN_ANIMATION_TARGET",
                                message=f"Animation event '{event.id}' references unknown target '{target_id}'.",
                                severity=Severity.MAJOR,
                                target_id=target_id,
                            )
                        )

        return ValidationReport(
            mode=VerificationMode.DETERMINISTIC,
            passed=not issues,
            issues=issues,
            summary="IR structural validation passed." if not issues else "IR structural validation failed.",
        )

    def to_dict(self, *, omit_none: bool = True) -> dict[str, Any]:
        return _to_plain_data(self, omit_none=omit_none)

    def to_json(self, *, indent: int = 2, omit_none: bool = True) -> str:
        return json.dumps(self.to_dict(omit_none=omit_none), indent=indent, sort_keys=True)


def _to_plain_data(value: Any, *, omit_none: bool) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        result: dict[str, Any] = {}
        for item in fields(value):
            item_value = getattr(value, item.name)
            if omit_none and item_value is None:
                continue
            result[item.name] = _to_plain_data(item_value, omit_none=omit_none)
        return result
    if isinstance(value, dict):
        return {
            key: _to_plain_data(item_value, omit_none=omit_none)
            for key, item_value in value.items()
            if not (omit_none and item_value is None)
        }
    if isinstance(value, (list, tuple)):
        return [_to_plain_data(item_value, omit_none=omit_none) for item_value in value]
    return value


def report_to_dict(report: ValidationReport, *, omit_none: bool = True) -> dict[str, Any]:
    return _to_plain_data(report, omit_none=omit_none)


def report_to_json(report: ValidationReport, *, indent: int = 2, omit_none: bool = True) -> str:
    return json.dumps(report_to_dict(report, omit_none=omit_none), indent=indent, sort_keys=True)
