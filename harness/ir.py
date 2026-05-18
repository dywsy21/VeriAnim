"""Intermediate representation for scene and animation generation.

The IR is intentionally independent from any specific LLM provider. LLMs should
emit JSON that can be mapped into these dataclasses before code generation,
deterministic validation, visual verification, and refinement.
"""

from __future__ import annotations

import copy
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


class GenerationStageType(str, Enum):
    STATIC_SCENE = "static_scene"
    ANIMATION_EXTENSION = "animation_extension"


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
class TextureSourceSpec:
    source: str = "freestocktextures"
    title: str | None = None
    page_url: str | None = None
    image_url: str | None = None
    download_url: str | None = None
    local_path: str | None = None
    license: str | None = "CC0"
    tags: list[str] = field(default_factory=list)
    approved_by_vision: bool = False
    vision_summary: str | None = None


@dataclass(slots=True)
class MaterialSpec:
    id: str = "material"
    description: str = "material"
    base_color: RGBA | None = None
    metallic: float | None = None
    roughness: float | None = None
    alpha: float | None = None
    texture_hints: list[str] = field(default_factory=list)
    needs_texture: bool = False
    texture_query: str | None = None
    texture_source: TextureSourceSpec | None = None


@dataclass(slots=True)
class ObjectPartSpec:
    id: str = "part"
    description: str = "object part"
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
    id: str = "light"
    light_type: LightType = LightType.AREA
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
    id: str = "camera"
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
    id: str = "view"
    view_type: CameraViewType = CameraViewType.THREE_QUARTER
    description: str = "scene view"
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
    id: str = "check"
    description: str = "deterministic check"
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
    max_rounds: int = 6


@dataclass(slots=True)
class VideoVerifierSpec:
    enabled: bool = False
    model_hint: str | None = "qwen3.5-omni"
    sampled_frames: list[int] = field(default_factory=list)
    require_preview_video: bool = True
    questions: list[str] = field(default_factory=list)
    pass_criteria: list[str] = field(default_factory=list)
    max_rounds: int = 6


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
class PipelineStageSpec:
    id: str
    stage_type: GenerationStageType
    description: str
    depends_on: list[str] = field(default_factory=list)
    freezes_scene_geometry: bool = False
    verifier_modes: list[VerificationMode] = field(default_factory=list)


@dataclass(slots=True)
class GenerationIR:
    prompt: SourcePrompt
    scene: SceneSpec
    animation: AnimationSpec | None = None
    stages: list[PipelineStageSpec] = field(default_factory=list)
    version: str = IR_VERSION
    project_id: str | None = None
    notes: str | None = None

    def ensure_progressive_stages(self) -> None:
        """Populate the canonical static-scene -> animation stage plan."""

        if not self.animation:
            self.stages = [
                PipelineStageSpec(
                    id="static_scene",
                    stage_type=GenerationStageType.STATIC_SCENE,
                    description="Generate and verify the static scene baseline.",
                    verifier_modes=[VerificationMode.DETERMINISTIC, VerificationMode.VISION],
                )
            ]
            return
        self.stages = [
            PipelineStageSpec(
                id="static_scene",
                stage_type=GenerationStageType.STATIC_SCENE,
                description="Generate and verify the static scene baseline without animation data.",
                verifier_modes=[VerificationMode.DETERMINISTIC, VerificationMode.VISION],
            ),
            PipelineStageSpec(
                id="animation_extension",
                stage_type=GenerationStageType.ANIMATION_EXTENSION,
                description="Add animation on top of the validated static scene baseline.",
                depends_on=["static_scene"],
                freezes_scene_geometry=True,
                verifier_modes=[VerificationMode.DETERMINISTIC, VerificationMode.VISION, VerificationMode.VIDEO],
            ),
        ]

    def static_scene_projection(self) -> "GenerationIR":
        """Return a static-only IR projection for the first pipeline stage.

        The original prompt may contain motion language. The scene-stage prompt
        is therefore synthesized from SceneSpec so code generation cannot infer
        animation from leftover natural language.
        """

        projected = copy.deepcopy(self)
        projected.animation = None
        projected.prompt = SourcePrompt(
            text=_static_scene_prompt(projected.scene),
            negative_text=self.prompt.negative_text,
            image_paths=list(self.prompt.image_paths),
            user_constraints=[
                "Static scene baseline only; do not create keyframes, drivers, animated materials, frame ranges, or animated visibility.",
                "Represent objects in a neutral pose that makes required contacts and spatial relations visible.",
            ],
        )
        projected.stages = [
            PipelineStageSpec(
                id="static_scene",
                stage_type=GenerationStageType.STATIC_SCENE,
                description="Generate and verify the static scene baseline without animation data.",
                verifier_modes=[VerificationMode.DETERMINISTIC, VerificationMode.VISION],
            )
        ]
        return projected

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
            animation_events = [*self.animation.events, *self.animation.camera_events]
            camera_actions = {AnimationAction.CAMERA_MOVE, AnimationAction.CAMERA_ORBIT}

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
            if not animation_events:
                issues.append(
                    ValidationIssue(
                        code="NO_ANIMATION_EVENTS",
                        message="AnimationSpec must contain at least one event or camera_event.",
                        severity=Severity.CRITICAL,
                    )
                )
            if self.animation.verifier.enabled:
                if not self.animation.verifier.questions:
                    issues.append(
                        ValidationIssue(
                            code="MISSING_VIDEO_VERIFIER_QUESTIONS",
                            message="Enabled video verification requires at least one temporal question.",
                            severity=Severity.MAJOR,
                        )
                    )
                if not self.animation.verifier.pass_criteria:
                    issues.append(
                        ValidationIssue(
                            code="MISSING_VIDEO_VERIFIER_PASS_CRITERIA",
                            message="Enabled video verification requires pass criteria.",
                            severity=Severity.MAJOR,
                        )
                    )
            for frame in self.animation.verifier.sampled_frames:
                if frame < 1 or frame > self.animation.duration_frames:
                    issues.append(
                        ValidationIssue(
                            code="INVALID_SAMPLED_FRAME",
                            message=f"Sampled frame '{frame}' is outside the animation range.",
                            severity=Severity.MAJOR,
                            frame=frame,
                        )
                    )

            for event in self.animation.events:
                if event.action in camera_actions:
                    issues.append(
                        ValidationIssue(
                            code="CAMERA_ACTION_IN_OBJECT_EVENTS",
                            message=f"Camera action event '{event.id}' should be placed in camera_events.",
                            severity=Severity.MAJOR,
                            frame=event.start_frame,
                        )
                    )
                _validate_animation_event(
                    event,
                    issues=issues,
                    object_id_set=object_id_set,
                    camera_ids=camera_ids,
                    duration_frames=self.animation.duration_frames,
                    sampled_frames=self.animation.verifier.sampled_frames,
                    is_camera_event=False,
                )

            for event in self.animation.camera_events:
                if event.action not in camera_actions:
                    issues.append(
                        ValidationIssue(
                            code="NON_CAMERA_ACTION_IN_CAMERA_EVENTS",
                            message=f"Non-camera event '{event.id}' should be placed in events.",
                            severity=Severity.MAJOR,
                            frame=event.start_frame,
                        )
                    )
                _validate_animation_event(
                    event,
                    issues=issues,
                    object_id_set=object_id_set,
                    camera_ids=camera_ids,
                    duration_frames=self.animation.duration_frames,
                    sampled_frames=self.animation.verifier.sampled_frames,
                    is_camera_event=True,
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


def _validate_animation_event(
    event: AnimationEventSpec,
    *,
    issues: list[ValidationIssue],
    object_id_set: set[str],
    camera_ids: set[str],
    duration_frames: int,
    sampled_frames: list[int],
    is_camera_event: bool,
) -> None:
    camera_actions = {AnimationAction.CAMERA_MOVE, AnimationAction.CAMERA_ORBIT}
    if not event.id:
        issues.append(
            ValidationIssue(
                code="EMPTY_ANIMATION_EVENT_ID",
                message="Animation event id cannot be empty.",
                severity=Severity.CRITICAL,
                frame=event.start_frame,
            )
        )
    if not event.subject_ids:
        issues.append(
            ValidationIssue(
                code="MISSING_ANIMATION_SUBJECTS",
                message=f"Animation event '{event.id}' must reference at least one subject.",
                severity=Severity.CRITICAL,
                frame=event.start_frame,
            )
        )
    if not (event.description or "").strip():
        issues.append(
            ValidationIssue(
                code="MISSING_EVENT_DESCRIPTION",
                message=f"Animation event '{event.id}' needs a semantic description.",
                severity=Severity.MAJOR,
                frame=event.start_frame,
            )
        )
    if event.required and not (event.expected_visual_result or "").strip():
        issues.append(
            ValidationIssue(
                code="MISSING_EXPECTED_VISUAL_RESULT",
                message=f"Required animation event '{event.id}' needs expected_visual_result for video verification.",
                severity=Severity.MAJOR,
                frame=event.start_frame,
            )
        )

    if event.start_frame < 1 or event.end_frame <= event.start_frame:
        issues.append(
            ValidationIssue(
                code="INVALID_EVENT_FRAME_RANGE",
                message=f"Animation event '{event.id}' must have start_frame >= 1 and end_frame > start_frame.",
                severity=Severity.CRITICAL,
                frame=event.start_frame,
            )
        )
    if event.end_frame > duration_frames:
        issues.append(
            ValidationIssue(
                code="EVENT_EXCEEDS_DURATION",
                message=f"Animation event '{event.id}' ends after the animation duration.",
                severity=Severity.MAJOR,
                frame=event.end_frame,
            )
        )

    for subject_id in event.subject_ids:
        if event.action in camera_actions or is_camera_event:
            if subject_id not in camera_ids:
                issues.append(
                    ValidationIssue(
                        code="UNKNOWN_CAMERA_ANIMATION_SUBJECT",
                        message=f"Camera animation event '{event.id}' references unknown camera '{subject_id}'.",
                        severity=Severity.CRITICAL,
                        target_id=subject_id,
                    )
                )
        elif subject_id not in object_id_set:
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

    if event.start_frame >= 1 and event.end_frame <= duration_frames and event.end_frame > event.start_frame:
        missing_samples = _missing_sampled_frames(event, sampled_frames)
        if missing_samples:
            issues.append(
                ValidationIssue(
                    code="MISSING_ANIMATION_SAMPLE_FRAMES",
                    message=f"Animation event '{event.id}' sampled_frames must cover start, middle, and end states.",
                    severity=Severity.MAJOR,
                    frame=event.start_frame,
                    evidence={"missing": missing_samples, "sampled_frames": sampled_frames},
                )
            )

    _validate_motion_path(event, issues)
    _validate_action_requirements(event, issues)


def _missing_sampled_frames(event: AnimationEventSpec, sampled_frames: list[int]) -> list[str]:
    samples = set(sampled_frames)
    missing: list[str] = []
    if event.start_frame not in samples:
        missing.append("start")
    if event.end_frame - event.start_frame > 1 and not any(event.start_frame < frame < event.end_frame for frame in samples):
        missing.append("middle")
    if event.end_frame not in samples:
        missing.append("end")
    return missing


def _static_scene_prompt(scene: SceneSpec) -> str:
    object_lines = [
        f"- {obj.id}: {obj.description}"
        for obj in scene.objects
    ]
    relation_lines = [
        f"- {relation.id}: {relation.subject_id} {relation.relation_type.value} {relation.object_id}"
        + (f" ({relation.description})" if relation.description else "")
        for relation in scene.relations
    ]
    material_lines = [
        f"- {material.id}: {material.description}"
        for material in scene.materials
    ]
    camera_lines = [
        f"- {camera.id}: {camera.description or camera.coverage or camera.view_type.value}"
        for camera in scene.cameras
    ]
    sections = [
        "Generate the static scene baseline described by this SceneSpec projection.",
        "Do not animate anything in this stage.",
        "Objects:",
        *object_lines,
    ]
    if relation_lines:
        sections.extend(["Required spatial relations:", *relation_lines])
    if material_lines:
        sections.extend(["Materials:", *material_lines])
    if camera_lines:
        sections.extend(["Cameras:", *camera_lines])
    if scene.environment.description:
        sections.extend(["Environment:", scene.environment.description])
    sections.append("All objects should be placed in a neutral representative pose suitable for later animation.")
    return "\n".join(sections)


def _validate_motion_path(event: AnimationEventSpec, issues: list[ValidationIssue]) -> None:
    if not event.path:
        return
    for index, point in enumerate(event.path.points):
        if len(point) != 3:
            issues.append(
                ValidationIssue(
                    code="INVALID_PATH_POINT",
                    message=f"Animation event '{event.id}' path point {index} must be a 3D coordinate.",
                    severity=Severity.MAJOR,
                    frame=event.start_frame,
                )
            )
    for keyframe in event.path.keyframes:
        if keyframe.frame < event.start_frame or keyframe.frame > event.end_frame:
            issues.append(
                ValidationIssue(
                    code="PATH_KEYFRAME_OUT_OF_RANGE",
                    message=f"Animation event '{event.id}' keyframe {keyframe.frame} is outside its event frame range.",
                    severity=Severity.MAJOR,
                    frame=keyframe.frame,
                )
            )


def _validate_action_requirements(event: AnimationEventSpec, issues: list[ValidationIssue]) -> None:
    transform_field_by_action = {
        AnimationAction.TRANSLATE: "location",
        AnimationAction.ROTATE: "rotation_euler",
        AnimationAction.SCALE: "scale",
        AnimationAction.CAMERA_MOVE: "location",
        AnimationAction.CAMERA_ORBIT: "location",
    }
    explicit_actions = {
        AnimationAction.TRANSLATE,
        AnimationAction.ROTATE,
        AnimationAction.SCALE,
        AnimationAction.FOLLOW_PATH,
        AnimationAction.APPEAR,
        AnimationAction.DISAPPEAR,
        AnimationAction.CAMERA_MOVE,
        AnimationAction.CAMERA_ORBIT,
    }
    if event.action not in explicit_actions:
        issues.append(
            ValidationIssue(
                code="UNVERIFIABLE_ANIMATION_ACTION",
                message=(
                    f"Animation event '{event.id}' uses action '{event.action.value}', which is not part of "
                    "the repeatably verifiable action subset. Use translate, rotate, scale, follow_path, "
                    "appear, disappear, camera_move, or camera_orbit."
                ),
                severity=Severity.MAJOR,
                frame=event.start_frame,
            )
        )
        return

    if event.action == AnimationAction.FOLLOW_PATH:
        if not event.path or (len(event.path.points) < 2 and len(event.path.keyframes) < 2):
            issues.append(
                ValidationIssue(
                    code="MISSING_FOLLOW_PATH",
                    message=f"follow_path event '{event.id}' requires at least two path points or two path keyframes.",
                    severity=Severity.MAJOR,
                    frame=event.start_frame,
                )
            )
        if not _transform_has(event.start_transform, "location") or not _transform_has(event.end_transform, "location"):
            issues.append(
                ValidationIssue(
                    code="MISSING_ANIMATION_TRANSFORM",
                    message=f"follow_path event '{event.id}' requires start_transform.location and end_transform.location.",
                    severity=Severity.MAJOR,
                    frame=event.start_frame,
                )
            )
        if not _has_intermediate_state(event):
            issues.append(_missing_middle_keyframe_issue(event))
        return

    if event.action in {AnimationAction.APPEAR, AnimationAction.DISAPPEAR}:
        if not _has_visibility_transition(event):
            issues.append(
                ValidationIssue(
                    code="MISSING_VISIBILITY_KEYFRAMES",
                    message=(
                        f"{event.action.value} event '{event.id}' requires start and end path.keyframes "
                        "with value.visible, value.hide_viewport, value.hide_render, or value.alpha."
                    ),
                    severity=Severity.MAJOR,
                    frame=event.start_frame,
                )
            )
        return

    field_name = transform_field_by_action[event.action]
    if not _transform_has(event.start_transform, field_name) or not _transform_has(event.end_transform, field_name):
        issues.append(
            ValidationIssue(
                code="MISSING_ANIMATION_TRANSFORM",
                message=(
                    f"{event.action.value} event '{event.id}' requires start_transform.{field_name} "
                    f"and end_transform.{field_name}."
                ),
                severity=Severity.MAJOR,
                frame=event.start_frame,
            )
        )
    if event.action == AnimationAction.CAMERA_ORBIT and not event.target_ids:
        issues.append(
            ValidationIssue(
                code="MISSING_CAMERA_ORBIT_TARGET",
                message=f"camera_orbit event '{event.id}' must target the object or scene center being orbited.",
                severity=Severity.MAJOR,
                frame=event.start_frame,
            )
        )
    if not _has_intermediate_state(event):
        issues.append(_missing_middle_keyframe_issue(event))


def _transform_has(transform: TransformSpec | None, field_name: str) -> bool:
    value = getattr(transform, field_name, None) if transform else None
    return value is not None and len(value) == 3


def _has_intermediate_state(event: AnimationEventSpec) -> bool:
    if not event.path:
        return False
    if event.action in {AnimationAction.FOLLOW_PATH, AnimationAction.CAMERA_ORBIT} and len(event.path.points) >= 3:
        return True
    field_name = {
        AnimationAction.TRANSLATE: "location",
        AnimationAction.ROTATE: "rotation_euler",
        AnimationAction.SCALE: "scale",
        AnimationAction.FOLLOW_PATH: "location",
        AnimationAction.CAMERA_MOVE: "location",
        AnimationAction.CAMERA_ORBIT: "location",
    }.get(event.action)
    for keyframe in event.path.keyframes:
        if not (event.start_frame < keyframe.frame < event.end_frame):
            continue
        if field_name is None:
            return True
        if keyframe.transform and _transform_has(keyframe.transform, field_name):
            return True
    return False


def _missing_middle_keyframe_issue(event: AnimationEventSpec) -> ValidationIssue:
    return ValidationIssue(
        code="MISSING_INTERMEDIATE_KEYFRAME",
        message=f"Animation event '{event.id}' requires at least one explicit intermediate state between start and end.",
        severity=Severity.MAJOR,
        frame=event.start_frame,
    )


def _has_visibility_transition(event: AnimationEventSpec) -> bool:
    if not event.path:
        return False
    start = False
    end = False
    for keyframe in event.path.keyframes:
        if not _keyframe_has_visibility_value(keyframe):
            continue
        if keyframe.frame == event.start_frame:
            start = True
        if keyframe.frame == event.end_frame:
            end = True
    return start and end


def _keyframe_has_visibility_value(keyframe: KeyframeSpec) -> bool:
    return any(key in keyframe.value for key in ("visible", "hide_viewport", "hide_render", "alpha"))


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
