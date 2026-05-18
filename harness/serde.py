"""Dataclass serde helpers for the harness IR."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
import types
from typing import Any, Union, get_args, get_origin, get_type_hints


class IRDecodeError(ValueError):
    """Raised when LLM JSON cannot be mapped into the IR."""


def from_dict(cls: type[Any], value: Any) -> Any:
    """Recursively build dataclasses/enums from plain JSON-compatible data."""
    return _convert(cls, value)


def _convert(expected_type: Any, value: Any) -> Any:
    if value is None:
        return None

    origin = get_origin(expected_type)
    args = get_args(expected_type)

    if origin in {Union, types.UnionType}:
        return _convert_union(args, value)

    if origin is None and hasattr(expected_type, "__origin__"):
        origin = expected_type.__origin__
        args = getattr(expected_type, "__args__", ())

    if origin is list:
        inner = args[0] if args else Any
        return [_convert(inner, item) for item in value]

    if origin is tuple:
        inner_types = args
        if len(inner_types) == 2 and inner_types[1] is Ellipsis:
            return tuple(_convert(inner_types[0], item) for item in value)
        return tuple(
            _convert(inner_types[index], item) if index < len(inner_types) else item
            for index, item in enumerate(value)
        )

    if origin is dict:
        key_type = args[0] if args else Any
        value_type = args[1] if len(args) > 1 else Any
        return {
            _convert(key_type, item_key): _convert(value_type, item_value)
            for item_key, item_value in value.items()
        }

    if expected_type is Any:
        return value

    if isinstance(expected_type, type) and issubclass(expected_type, Enum):
        try:
            return expected_type(value)
        except ValueError as exc:
            allowed = ", ".join(item.value for item in expected_type)
            raise IRDecodeError(f"Invalid enum value '{value}' for {expected_type.__name__}. Allowed: {allowed}") from exc

    if isinstance(expected_type, type) and is_dataclass(expected_type):
        if not isinstance(value, dict):
            raise IRDecodeError(f"Expected object for {expected_type.__name__}, got {type(value).__name__}.")
        hints = get_type_hints(expected_type)
        kwargs: dict[str, Any] = {}
        known = {field.name for field in fields(expected_type)}
        for name in known:
            if name in value:
                kwargs[name] = _convert(hints.get(name, Any), value[name])
        return expected_type(**kwargs)

    try:
        if expected_type is bool:
            return _convert_bool(value)
        if expected_type in {str, int, float}:
            return expected_type(value)
    except (TypeError, ValueError):
        return value

    return value


def _convert_union(args: tuple[Any, ...], value: Any) -> Any:
    errors: list[Exception] = []
    for option in args:
        if option is type(None):
            continue
        try:
            return _convert(option, value)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise errors[0]
    return value


def _convert_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off", "none", "null", ""}:
        return False
    return bool(value)
