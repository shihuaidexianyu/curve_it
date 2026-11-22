"""Local charts for reinforcement-learning console logs."""

from .annotations import (
    AnnotationError,
    PointLabel,
    ReferenceBand,
    ReferenceLine,
    load_annotations,
    validate_annotation_keys,
    validate_annotations,
)
from .dashboard import Panel
from .log import LogReader, ParsedLog
from .notifications import Notification, NotificationError, Notifier, notifier_from_env
from .profiles import DashboardProfile, select_panels
from .reports import ReportSchedule, training_report
from .terminal import DashboardFrame, render_dashboard

__all__ = [
    "AnnotationError",
    "PointLabel",
    "ReferenceBand",
    "ReferenceLine",
    "load_annotations",
    "validate_annotation_keys",
    "validate_annotations",
    "DashboardFrame",
    "DashboardProfile",
    "LogReader",
    "Notification",
    "NotificationError",
    "Notifier",
    "notifier_from_env",
    "Panel",
    "ParsedLog",
    "ReportSchedule",
    "render_dashboard",
    "select_panels",
    "training_report",
]
