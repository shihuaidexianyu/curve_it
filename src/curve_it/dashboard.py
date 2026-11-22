"""Reusable panel definitions and viewport sizing shared by dashboard renderers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil

from .annotations import Annotation, validate_annotations
from .metrics import format_value


@dataclass(frozen=True)
class Panel:
    key: str
    title: str
    unit: str = ""
    direction: int = 0  # 1 means higher is better, -1 means lower is better.
    annotations: tuple[Annotation, ...] = ()

    def __post_init__(self) -> None:
        if self.annotations != ():
            object.__setattr__(
                self,
                "annotations",
                validate_annotations(self.annotations, path=f"panel[{self.key!r}].annotations", unit=self.unit),
            )

    def format(self, value: float) -> str:
        if self.unit == "%":
            return f"{value:.1%}"
        text = format_value(value)
        return f"{text} {self.unit}" if self.unit else text


PanelSpecs = Sequence[Panel | tuple[str, str]]


def panel_specs(panels: PanelSpecs) -> list[Panel]:
    """Accept original ``(key, title)`` callers alongside the structured panel API."""
    return [panel if isinstance(panel, Panel) else Panel(*panel) for panel in panels]


@dataclass(frozen=True)
class GridLayout:
    columns: int
    cell_width: int
    plot_height: int = 0
    page_size: int = 0
    gap: int = 2
    extra_plot_rows: int = 0

    @property
    def compact(self) -> bool:
        return self.plot_height == 0


def fit_terminal(count: int, width: int, available_rows: int | None) -> GridLayout:
    """Size charts for static terminal output."""
    columns = 2 if count > 1 and width >= 82 else 1
    gap = 4 if columns > 1 else 2
    cell_width = (width - gap * (columns - 1)) // columns
    if not count or width < 26 or (available_rows is not None and available_rows < 5):
        return GridLayout(1, width, page_size=max(1, count if available_rows is None else available_rows))
    if available_rows is None:
        return GridLayout(columns, cell_width, plot_height=9, page_size=count, gap=gap)

    # Each chart needs a title and x-axis, plus one row of separation between chart rows.
    rows = min(2, ceil(count / columns), max(1, (available_rows + 1) // 12))
    plot_height = min(16, (available_rows + 1) // rows - 3)
    return GridLayout(columns, cell_width, plot_height, columns * rows, gap=gap)


def fit_live(width: int, available_rows: int, *, focused: bool) -> GridLayout:
    """Keep page boundaries stable when a live terminal is resized."""
    page_size = 1 if focused else 4
    columns = 2 if not focused and width >= 52 else 1
    gap = 4 if columns > 1 else 2
    cell_width = (width - gap * (columns - 1)) // columns
    rows = page_size // columns
    # A plot needs a header, an x-axis and at least two plotting rows.
    plot_height = (available_rows - rows + 1) // rows - 2
    if plot_height < 2 or cell_width < 24:
        plot_height = 0
    return GridLayout(columns, cell_width, plot_height, page_size, gap=gap)


def fit_dense(count: int, width: int, available_rows: int, *, ascii_only: bool = False) -> GridLayout:
    """Use spare rows to increase detail after fitting the most charts in the viewport."""
    columns = max(1, min(count, 4, (width + 2) // 35))
    cell_width = (width - 2 * (columns - 1)) // columns
    chart_rows = max(1, ceil(count / columns))
    max_height = 1 if ascii_only else 2
    plot_height = min(max_height, max(0, available_rows // chart_rows - 1))
    page_size = columns * max(1, available_rows // (plot_height + 1))
    spare_rows = available_rows - chart_rows * (plot_height + 1)
    extra_plot_rows = max(0, spare_rows) if plot_height < max_height else 0
    return GridLayout(columns, cell_width, plot_height, page_size, extra_plot_rows=extra_plot_rows)
