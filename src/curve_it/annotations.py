"""Chart annotations for Python callers and JSON dashboard configurations."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path


@dataclass(frozen=True)
class ReferenceLine:
    value: float
    label: str


@dataclass(frozen=True)
class ReferenceBand:
    lower: float
    upper: float
    label: str


@dataclass(frozen=True)
class PointLabel:
    iteration: int
    value: float
    label: str


Annotation = ReferenceLine | ReferenceBand | PointLabel


@dataclass(frozen=True)
class AnnotationIssue:
    path: str
    message: str


class AnnotationError(ValueError):
    def __init__(self, issues: Sequence[AnnotationIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(
            "Invalid chart annotations:\n" + "\n".join(f"  {issue.path}: {issue.message}" for issue in self.issues)
        )


_TYPES = {"line": ReferenceLine, "band": ReferenceBand, "point": PointLabel}
_FIELDS = {
    ReferenceLine: ("value", "label"),
    ReferenceBand: ("lower", "upper", "label"),
    PointLabel: ("iteration", "value", "label"),
}


def validate_annotations(
    annotations: object, *, path: str = "annotations", unit: str = ""
) -> tuple[Annotation, ...]:
    """Validate Python objects or JSON-style mappings and collect all field errors."""
    if not isinstance(annotations, (list, tuple)):
        raise AnnotationError([AnnotationIssue(path, "expected a list of lines, bands or points")])
    issues = []
    validated = []
    for index, item in enumerate(annotations):
        location = f"{path}[{index}]"
        start = len(issues)
        if isinstance(item, Mapping):
            kind = item.get("type")
            cls = _TYPES.get(kind) if isinstance(kind, str) else None
            if cls is None:
                issues.append(AnnotationIssue(f"{location}.type", f"expected 'line', 'band' or 'point', got {kind!r}"))
                continue
            fields = _FIELDS[cls]
            for key in item.keys() - {"type", *fields}:
                issues.append(AnnotationIssue(f"{location}.{key}", "unknown field"))
            for field in fields:
                if field not in item:
                    issues.append(AnnotationIssue(f"{location}.{field}", "required field is missing"))
            values = {field: item.get(field) for field in fields if field in item}
        elif type(item) in _FIELDS:
            cls = type(item)
            values = vars(item)
        else:
            issues.append(AnnotationIssue(location, f"expected an annotation object, got {type(item).__name__}"))
            continue
        for field, value in values.items():
            field_path = f"{location}.{field}"
            if field == "label":
                if not isinstance(value, str) or not value.strip() or not value.isprintable():
                    issues.append(AnnotationIssue(field_path, "expected non-empty printable text on one line"))
            elif field == "iteration":
                if type(value) is not int or value < 0:
                    issues.append(AnnotationIssue(field_path, f"expected a non-negative integer, got {value!r}"))
            elif not _finite(value):
                issues.append(
                    AnnotationIssue(field_path, f"expected a finite number in the metric's raw units, got {value!r}")
                )
            elif unit == "%" and not 0 <= value <= 1:
                issues.append(AnnotationIssue(field_path, f"expected a ratio in [0, 1] (0.90 means 90%), got {value!r}"))
        if len(issues) == start:
            if cls is ReferenceBand and values["lower"] >= values["upper"]:
                issues.append(AnnotationIssue(f"{location}.upper", f"must exceed lower ({values['lower']!r})"))
            else:
                validated.append(cls(**values))
    if issues:
        raise AnnotationError(issues)
    return tuple(validated)


def _finite(value: object) -> bool:
    if type(value) not in (float, int):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def load_annotations(path: str | Path) -> dict[str, tuple[Annotation, ...]]:
    """Load an object keyed by exact log metric names."""
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise AnnotationError([AnnotationIssue(f"{source}:{error.lineno}:{error.colno}", error.msg)]) from error
    if not isinstance(data, dict):
        raise AnnotationError([AnnotationIssue(str(source), "expected a JSON object keyed by log metric names")])
    issues = []
    result = {}
    for key, items in data.items():
        location = f"{source}[{key!r}]"
        if not key.strip() or not key.isprintable():
            issues.append(AnnotationIssue(location, "expected a non-empty printable metric name"))
        try:
            result[key] = validate_annotations(items, path=location)
        except AnnotationError as error:
            issues.extend(error.issues)
    if issues:
        raise AnnotationError(issues)
    return result


def validate_annotation_keys(annotations: Mapping[str, object], metric_keys: Iterable[str]) -> None:
    """Check metric names against a log snapshot and suggest likely spelling corrections."""
    known = set(metric_keys)
    issues = []
    for key in sorted(annotations.keys() - known):
        matches = get_close_matches(key, sorted(known), n=1)
        hint = f". Did you mean {matches[0]!r}?" if matches else ". Use --list to check the metric name"
        issues.append(AnnotationIssue(f"annotations[{key!r}]", f"metric is absent from this log{hint}"))
    if issues:
        raise AnnotationError(issues)


def visible_annotations(annotations: Sequence[Annotation], start: int, stop: int) -> tuple[Annotation, ...]:
    return tuple(item for item in annotations if not isinstance(item, PointLabel) or start <= item.iteration <= stop)


def annotation_values(annotations: Sequence[Annotation]) -> list[float]:
    return [
        value
        for item in annotations
        for value in ((item.lower, item.upper) if isinstance(item, ReferenceBand) else (item.value,))
    ]
