"""Dataclass serde helpers for the harness IR."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
import types
from typing import Any, Union, get_args, get_origin, get_type_hints


LEGACY_RIGID_IR = "legacy_rigid_ir"
EXTENSION_IR = "extension_ir"
INVALID_IR = "invalid_ir"


class IRDecodeError(ValueError):
    """Raised when LLM JSON cannot be mapped into the IR."""


def detect_ir_format(value: Any) -> str:
    """Classify raw IR JSON before consumers decode it."""

    if not isinstance(value, dict):
        return INVALID_IR
    if not isinstance(value.get("prompt"), dict) or not isinstance(value.get("scene"), dict):
        return INVALID_IR
    if "extension" in value and value.get("extension") is not None:
        return EXTENSION_IR if isinstance(value.get("extension"), dict) else INVALID_IR
    return LEGACY_RIGID_IR


def bridge_legacy_rigid_intent(value: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal rigid extension view for a legacy rigid IR payload."""

    if detect_ir_format(value) != LEGACY_RIGID_IR:
        raise IRDecodeError("Invalid bridge/comparison payload: expected legacy rigid IR.")
    scene = value.get("scene") or {}
    animation = value.get("animation") or {}
    object_ids = [
        item.get("id")
        for item in scene.get("objects", [])
        if isinstance(item, dict) and item.get("id")
    ]
    event_object_ids: list[str] = []
    for event in [*(animation.get("events") or []), *(animation.get("camera_events") or [])]:
        if not isinstance(event, dict):
            continue
        event_object_ids.extend(str(item) for item in event.get("subject_ids", []) if item)
        event_object_ids.extend(str(item) for item in event.get("target_ids", []) if item)
    rigid_ids = list(dict.fromkeys(event_object_ids or object_ids))
    return {
        "families": [
            {
                "id": "rigid",
                "family_type": "rigid",
                "description": "Legacy rigid animation intent bridged from GenerationIR.animation.",
                "required_probe_ids": ["rigid_video"],
                "insufficient_probe_types": ["bbox"],
            }
        ],
        "verification_probes": [
            {
                "id": "rigid_video",
                "probe_type": "video",
                "target_ids": ["rigid"],
                "required": True,
                "pass_criteria": "Rigid animation intent remains visible over sampled frames.",
                "evidence_path": "reports/*_animation_video.json",
            }
        ],
        "rigid_specs": [
            {
                "id": "legacy_rigid_intent",
                "family_id": "rigid",
                "object_ids": rigid_ids,
                "probe_ids": ["rigid_video"],
            }
        ],
    }


def from_dict(cls: type[Any], value: Any, *, strict: bool = True) -> Any:
    """Recursively build dataclasses/enums from plain JSON-compatible data."""
    return _convert(cls, value, strict=strict, path=_type_name(cls))


def _convert(expected_type: Any, value: Any, *, strict: bool, path: str) -> Any:
    if value is None:
        if strict and not _allows_none(expected_type):
            raise IRDecodeError(f"Unexpected null at {path}.")
        return None

    origin = get_origin(expected_type)
    args = get_args(expected_type)

    if origin in {Union, types.UnionType}:
        return _convert_union(args, value, strict=strict, path=path)

    if origin is None and hasattr(expected_type, "__origin__"):
        origin = expected_type.__origin__
        args = getattr(expected_type, "__args__", ())

    if origin is list:
        if not isinstance(value, list):
            raise IRDecodeError(f"Expected list at {path}, got {type(value).__name__}.")
        inner = args[0] if args else Any
        return [_convert(inner, item, strict=strict, path=f"{path}[{index}]") for index, item in enumerate(value)]

    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            raise IRDecodeError(f"Expected array at {path}, got {type(value).__name__}.")
        inner_types = args
        if len(inner_types) == 2 and inner_types[1] is Ellipsis:
            return tuple(
                _convert(inner_types[0], item, strict=strict, path=f"{path}[{index}]")
                for index, item in enumerate(value)
            )
        if inner_types and len(value) != len(inner_types):
            raise IRDecodeError(f"Expected {len(inner_types)} items at {path}, got {len(value)}.")
        return tuple(
            _convert(inner_types[index], item, strict=strict, path=f"{path}[{index}]")
            if index < len(inner_types)
            else item
            for index, item in enumerate(value)
        )

    if origin is dict:
        if not isinstance(value, dict):
            raise IRDecodeError(f"Expected object at {path}, got {type(value).__name__}.")
        key_type = args[0] if args else Any
        value_type = args[1] if len(args) > 1 else Any
        return {
            _convert(key_type, item_key, strict=strict, path=f"{path}.<key>"): _convert(
                value_type,
                item_value,
                strict=strict,
                path=f"{path}.{item_key}",
            )
            for item_key, item_value in value.items()
        }

    if expected_type is Any:
        return value

    if isinstance(expected_type, type) and issubclass(expected_type, Enum):
        try:
            return expected_type(value)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in expected_type)
            raise IRDecodeError(
                f"Invalid enum value '{value}' for {expected_type.__name__} at {path}. Allowed: {allowed}"
            ) from exc

    if isinstance(expected_type, type) and is_dataclass(expected_type):
        if not isinstance(value, dict):
            raise IRDecodeError(f"Expected object for {expected_type.__name__} at {path}, got {type(value).__name__}.")
        hints = get_type_hints(expected_type)
        kwargs: dict[str, Any] = {}
        field_map = {field.name: field for field in fields(expected_type)}
        unknown = sorted(set(value) - set(field_map))
        if strict and unknown:
            names = ", ".join(unknown)
            raise IRDecodeError(f"Unknown field(s) for {expected_type.__name__} at {path}: {names}")
        for name in field_map:
            if name in value:
                kwargs[name] = _convert(hints.get(name, Any), value[name], strict=strict, path=f"{path}.{name}")
        try:
            return expected_type(**kwargs)
        except TypeError as exc:
            raise IRDecodeError(f"Invalid object for {expected_type.__name__} at {path}: {exc}") from exc

    if expected_type is bool:
        return _convert_bool(value, strict=strict, path=path)
    if expected_type is str:
        if isinstance(value, str):
            return value
        if strict:
            raise IRDecodeError(f"Expected string at {path}, got {type(value).__name__}.")
        return str(value)
    if expected_type is int:
        return _convert_int(value, strict=strict, path=path)
    if expected_type is float:
        return _convert_float(value, strict=strict, path=path)

    return value


def _convert_union(args: tuple[Any, ...], value: Any, *, strict: bool, path: str) -> Any:
    errors: list[Exception] = []
    for option in args:
        if option is type(None):
            continue
        try:
            return _convert(option, value, strict=strict, path=path)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise errors[0]
    return value


def _convert_bool(value: Any, *, strict: bool, path: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", "none", "null", ""}:
        return False
    if strict:
        raise IRDecodeError(f"Expected boolean at {path}, got {value!r}.")
    return bool(value)


def _convert_int(value: Any, *, strict: bool, path: str) -> int:
    if isinstance(value, bool):
        if strict:
            raise IRDecodeError(f"Expected integer at {path}, got bool.")
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text and re_match_int(text):
            return int(text)
    if strict:
        raise IRDecodeError(f"Expected integer at {path}, got {value!r}.")
    return int(value)


def _convert_float(value: Any, *, strict: bool, path: str) -> float:
    if isinstance(value, bool):
        if strict:
            raise IRDecodeError(f"Expected number at {path}, got bool.")
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    if strict:
        raise IRDecodeError(f"Expected number at {path}, got {value!r}.")
    return float(value)


def re_match_int(value: str) -> bool:
    return value.startswith("-") and value[1:].isdigit() or value.isdigit()


def _allows_none(expected_type: Any) -> bool:
    if expected_type is Any:
        return True
    origin = get_origin(expected_type)
    args = get_args(expected_type)
    if origin in {Union, types.UnionType}:
        return any(option is type(None) for option in args)
    return False


def _type_name(value: Any) -> str:
    return getattr(value, "__name__", str(value))
