"""Graph helpers for the :mod:`moa.commands.pr_stats` command."""

from __future__ import annotations

import pathlib
import re
from datetime import date
from html import escape
from typing import Any

DARK_THEME_SVG_CSS = """<style>
@media (prefers-color-scheme: dark){
.bg { fill: #0d1117; }
.label { fill: #e6edf3; }
.axis { stroke: #8b949e; }
.bar { fill: #79c0ff; }
}
</style>"""
SVG_LABEL_CHAR_WIDTH = 7
SVG_AXIS_MARGIN = 20
SVG_AXIS_TOP = 40
SVG_Y_AXIS_LABEL_X = 20
SVG_X_AXIS_LABEL_Y_OFFSET = 24
SVG_X_AXIS_LABEL_ROTATION = -15
SVG_BAR_MIN_WIDTH = 600
SVG_BAR_X_AXIS_LABEL_Y = 350
SVG_HORIZONTAL_BAR_HEIGHT = 24
SVG_HORIZONTAL_BAR_GAP = 18
SVG_HORIZONTAL_BAR_PLOT_WIDTH = 280
SVG_HORIZONTAL_BAR_VALUE_PADDING = 20


def week_label_first_day(value: str) -> str:
    """Returns the Monday date for an ISO week label.

    The function expects a label formatted as ``YYYY-Www`` and returns the
    corresponding first day of that week in ``YYYY-MM-DD`` format.
    If *value* does not match the expected format or cannot be converted,
    the input value is returned unchanged.
    """
    match = re.fullmatch(r"(\d{4})-W(\d{2})", value)
    if not match:
        return value
    year = int(match.group(1))
    week = int(match.group(2))
    try:
        return date.fromisocalendar(year, week, 1).isoformat()
    except ValueError:
        return value


def compute_moving_average(values: list[float], window: int = 10) -> list[float | None]:
    """Computes a simple moving average sequence.

    :param values: Ordered numeric values to average.
    :param window: Number of points per averaging window.
    :return: A list the same length as *values*, with ``None`` for entries
        before enough values are available to fill one full window.
    """
    result: list[float | None] = []
    for i, _ in enumerate(values):
        if i + 1 < window:
            result.append(None)
        else:
            result.append(sum(values[i + 1 - window : i + 1]) / window)
    return result


def save_job_duration_line_graph(
    path: pathlib.Path,
    series: list[dict[str, Any]],
    title: str,
    moving_avg_window: int = 10,
    x_axis_label: str = "Completion date",
    y_axis_label: str = "Duration (minutes)",
) -> None:
    """Saves a job-duration SVG line chart.

    The chart plots one point per entry in *series* using ``duration_seconds``
    converted to minutes and uses the ``completed_at`` date portion for
    x-axis labels. When enough values are available, a moving-average line
    (``moving_avg_window``) is rendered in addition to the raw series.
    """
    width = 800
    height = 420
    left = 70
    right = 20
    top = 40
    bottom = 120
    plot_w = width - left - right
    plot_h = height - top - bottom

    n = len(series)
    if n == 0:
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
            f"{DARK_THEME_SVG_CSS}"
            f'<rect class="bg" x="0" y="0" width="{width}" height="{height}" fill="#fff"/>'
            f'<text x="{width / 2}" y="28" text-anchor="middle" font-size="18" '
            f'class="label" fill="#111">{escape(title)}</text>'
            f'<text x="{width / 2}" y="{top + plot_h / 2}" text-anchor="middle" '
            'class="label" fill="#888">No data</text>'
            "</svg>"
        )
        path.write_text(svg, encoding="utf-8")
        return

    values = [float(pt.get("duration_seconds", 0)) / 60 for pt in series]
    x_labels_raw = [str(pt.get("completed_at", ""))[:10] for pt in series]
    max_val = max(values) if values else 1
    if max_val == 0:
        max_val = 1

    def format_tick(v: float) -> str:
        return f"{v:.1f}".rstrip("0").rstrip(".")

    def x_pos(i: int) -> float:
        return left + (i * plot_w / (n - 1) if n > 1 else plot_w / 2)

    def y_pos(v: float) -> float:
        return top + plot_h - (v / max_val) * plot_h

    points_str = " ".join(f"{x_pos(i):.1f},{y_pos(v):.1f}" for i, v in enumerate(values))
    avg = compute_moving_average([float(v) for v in values], moving_avg_window)
    avg_pairs = [(x_pos(i), y_pos(v)) for i, v in enumerate(avg) if v is not None]
    avg_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in avg_pairs)

    step = max(1, n // 8)
    x_label_elems = []
    for i in range(0, n, step):
        x = x_pos(i)
        x_label_elems.append(
            f'<text x="{x:.1f}" y="{top + plot_h + 20}" text-anchor="end" '
            f'transform="rotate({SVG_X_AXIS_LABEL_ROTATION} {x:.1f} {top + plot_h + 20})" '
            f'class="label" fill="#111" font-size="11">{escape(x_labels_raw[i])}</text>'
        )

    y_tick_elems = []
    for tick_i in range(5):
        tick_val = max_val * tick_i / 4
        y = y_pos(tick_val)
        y_tick_elems.append(
            f'<line x1="{left - 5}" y1="{y:.1f}" x2="{left}" y2="{y:.1f}" '
            f'class="axis" stroke="#000"/>'
            f'<text x="{left - 8}" y="{y:.1f}" text-anchor="end" '
            f'dominant-baseline="middle" class="label" fill="#111" '
            f'font-size="11">{format_tick(tick_val)}</text>'
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" '
            f'stroke="#ddd" stroke-dasharray="4,4"/>'
        )

    legend_x = width - right - 130
    legend_y = top + 10
    legend_elems = (
        f'<line x1="{legend_x}" y1="{legend_y}" x2="{legend_x + 20}" y2="{legend_y}" '
        f'stroke="#4e79a7" stroke-width="2"/>'
        f'<text x="{legend_x + 25}" y="{legend_y + 4}" font-size="11" '
        f'class="label" fill="#111">duration</text>'
    )
    if avg_str:
        legend_elems += (
            f'<line x1="{legend_x}" y1="{legend_y + 18}" '
            f'x2="{legend_x + 20}" y2="{legend_y + 18}" '
            f'stroke="#e05c5c" stroke-width="2" stroke-dasharray="5,3"/>'
            f'<text x="{legend_x + 25}" y="{legend_y + 22}" font-size="11" '
            f'class="label" fill="#111">avg-{moving_avg_window}</text>'
        )

    dots = "".join(
        f'<circle cx="{x_pos(i):.1f}" cy="{y_pos(v):.1f}" r="3" fill="#4e79a7"/>'
        for i, v in enumerate(values)
    )
    avg_line = (
        f'<polyline points="{avg_str}" fill="none" stroke="#e05c5c" '
        f'stroke-width="2" stroke-dasharray="5,3"/>'
        if avg_str
        else ""
    )
    x_axis_label_elem = (
        f'<text x="{left + plot_w / 2:.1f}" '
        f'y="{height - SVG_X_AXIS_LABEL_Y_OFFSET}" text-anchor="middle" '
        f'class="label" fill="#111" font-size="12">{escape(x_axis_label)}</text>'
    )
    y_axis_label_elem = (
        f'<text x="{SVG_Y_AXIS_LABEL_X}" y="{top + plot_h / 2:.1f}" text-anchor="middle" '
        f'transform="rotate(-90 {SVG_Y_AXIS_LABEL_X} {top + plot_h / 2:.1f})" '
        f'class="label" fill="#111" font-size="12">{escape(y_axis_label)}</text>'
    )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
        f"{DARK_THEME_SVG_CSS}"
        f'<rect class="bg" x="0" y="0" width="{width}" height="{height}" fill="#fff"/>'
        f'<text x="{width / 2}" y="28" text-anchor="middle" font-size="18" '
        f'class="label" fill="#111">{escape(title)}</text>'
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" '
        f'class="axis" stroke="#000"/>'
        f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" '
        f'class="axis" stroke="#000"/>'
        + "".join(y_tick_elems)
        + f'<polyline points="{points_str}" fill="none" stroke="#4e79a7" stroke-width="2"/>'
        + dots
        + avg_line
        + "".join(x_label_elems)
        + legend_elems
        + x_axis_label_elem
        + y_axis_label_elem
        + "</svg>"
    )
    path.write_text(svg, encoding="utf-8")


def save_graphs_html_report(
    path: pathlib.Path, repo: str, graphs: list[tuple[str, pathlib.Path]]
) -> None:
    """Builds an HTML report that embeds SVG graph files inline.

    :param path: Destination HTML file.
    :param repo: Repository label displayed in the page title.
    :param graphs: Ordered ``(title, svg_path)`` pairs to include as sections.
    """
    title = f"PR stats graphs for {repo}"
    section_chunks = []
    for graph_title, graph_path in graphs:
        svg_content = graph_path.read_text(encoding="utf-8")
        section_chunks.append(
            "\n".join(
                [
                    "<section>",
                    f"<h2>{escape(graph_title)}</h2>",
                    svg_content,
                    "</section>",
                ]
            )
        )
    sections = "\n".join(section_chunks)
    html = "\n".join(
        [
            "<!DOCTYPE html>",
            "<html>",
            "<head>",
            "<meta charset='utf-8'>",
            f"<title>{escape(title)}</title>",
            "<style>",
            "body{font-family:Arial,sans-serif;margin:20px;}",
            "svg{max-width:100%;height:auto;}",
            "section{margin:24px 0;}",
            "h2{margin-bottom:8px;}",
            "</style>",
            "</head>",
            "<body>",
            f"<h1>{escape(title)}</h1>",
            sections,
            "</body>",
            "</html>",
        ]
    )
    path.write_text(html, encoding="utf-8")


def save_bar_graph(
    path: pathlib.Path,
    values: dict[str, int],
    title: str,
    x_axis_label: str | None = None,
    y_axis_label: str | None = None,
    bar_labels: dict[str, str] | None = None,
    horizontal: bool = False,
) -> None:
    """Saves a themed SVG bar chart.

    :param path: Destination SVG path.
    :param values: Mapping of category label to numeric value.
    :param title: Graph title.
    :param x_axis_label: Optional x-axis label.
    :param y_axis_label: Optional y-axis label.
    :param bar_labels: Optional secondary labels rendered near each bar.
    :param horizontal: When ``True``, renders a horizontal bar chart;
        otherwise renders a vertical bar chart.
    """
    if not values:
        values = {"none": 0}
    max_value = max(values.values()) if values else 0
    max_label_len = max(map(len, values), default=0)
    bar_width = 80
    gap = 40
    left = max(60, 20 + max_label_len * SVG_LABEL_CHAR_WIDTH)
    baseline = 300
    width = max(SVG_BAR_MIN_WIDTH, left + len(values) * (bar_width + gap) + SVG_AXIS_MARGIN)
    y_axis_label_y = (SVG_AXIS_TOP + baseline) / 2
    scale = 200 / max_value if max_value else 0
    bars = []
    labels = []
    if horizontal:
        bar_height = SVG_HORIZONTAL_BAR_HEIGHT
        y_gap = SVG_HORIZONTAL_BAR_GAP
        top = 60
        bottom = top + len(values) * (bar_height + y_gap)
        width = max(SVG_BAR_MIN_WIDTH, left + SVG_HORIZONTAL_BAR_PLOT_WIDTH + SVG_AXIS_MARGIN)
        graph_right = width - SVG_AXIS_MARGIN - SVG_HORIZONTAL_BAR_VALUE_PADDING
        scale = (graph_right - left) / max_value if max_value else 0
        y_axis_label_y = (top + bottom) / 2
        for i, (label, value) in enumerate(values.items()):
            y = top + i * (bar_height + y_gap)
            bar_len = int(value * scale) if max_value else 0
            bars.append(
                f'<rect x="{left}" y="{y}" width="{bar_len}" height="{bar_height}" '
                'class="bar" fill="#4e79a7"/>'
            )
            labels.append(
                f'<text x="{left - 8}" y="{y + bar_height / 2 + 4}" text-anchor="end" '
                'class="label" fill="#111">'
                f"{escape(label)}</text>"
            )
            labels.append(
                f'<text x="{left + bar_len + 6}" y="{y + bar_height / 2 + 4}" '
                'text-anchor="start" class="label" fill="#111">'
                f"{value}</text>"
            )
            extra_label = bar_labels.get(label) if bar_labels else None
            if extra_label:
                labels.append(
                    f'<text x="{left + bar_len + 6}" y="{y + bar_height / 2 + 18}" '
                    'text-anchor="start" class="label" fill="#888" font-size="11">'
                    f"{escape(extra_label)}</text>"
                )
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{bottom + 50}">'
            f"{DARK_THEME_SVG_CSS}"
            f'<rect class="bg" x="0" y="0" width="{width}" height="{bottom + 50}" fill="#fff"/>'
            f'<text x="{width/2}" y="28" text-anchor="middle" font-size="18" '
            f'class="label" fill="#111">{escape(title)}</text>'
            f'<line x1="{left}" y1="{SVG_AXIS_TOP}" '
            f'x2="{left}" y2="{bottom}" class="axis" stroke="#000"/>'
            f'<line x1="{left}" y1="{bottom}" '
            f'x2="{graph_right}" y2="{bottom}" class="axis" stroke="#000"/>'
            + "".join(bars)
            + "".join(labels)
            + (
                f'<text x="{left + (graph_right - left) / 2:.1f}" '
                f'y="{bottom + SVG_X_AXIS_LABEL_Y_OFFSET}" '
                f'text-anchor="middle" '
                f'class="label" fill="#111" font-size="12">{escape(x_axis_label)}</text>'
                if x_axis_label
                else ""
            )
            + (
                f'<text x="{SVG_Y_AXIS_LABEL_X}" y="{y_axis_label_y:.1f}" '
                f'text-anchor="middle" '
                f'transform="rotate(-90 {SVG_Y_AXIS_LABEL_X} {y_axis_label_y:.1f})" '
                f'class="label" fill="#111" font-size="12">{escape(y_axis_label)}</text>'
                if y_axis_label
                else ""
            )
            + "</svg>"
        )
    else:
        for i, (label, value) in enumerate(values.items()):
            x = left + i * (bar_width + gap)
            height = int(value * scale) if max_value else 0
            y = baseline - height
            bars.append(
                f'<rect x="{x}" y="{y}" width="{bar_width}" height="{height}" '
                'class="bar" fill="#4e79a7"/>'
            )
            labels.append(
                f'<text x="{x + bar_width / 2}" y="{baseline + 20}" text-anchor="end" '
                f'transform="rotate({SVG_X_AXIS_LABEL_ROTATION} '
                f'{x + bar_width / 2} {baseline + 20})" '
                'class="label" fill="#111">'
                f"{escape(label)}</text>"
            )
            extra_label = bar_labels.get(label) if bar_labels else None
            value_y = y - 18 if extra_label else y - 6
            labels.append(
                f'<text x="{x + bar_width / 2}" y="{value_y}" '
                'text-anchor="middle" class="label" fill="#111">'
                f"{value}</text>"
            )
            if extra_label:
                labels.append(
                    f'<text x="{x + bar_width / 2}" y="{y - 6}" '
                    'text-anchor="middle" class="label" fill="#888" font-size="11">'
                    f"{escape(extra_label)}</text>"
                )
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="360">'
            f"{DARK_THEME_SVG_CSS}"
            f'<rect class="bg" x="0" y="0" width="{width}" height="360" fill="#fff"/>'
            f'<text x="{width/2}" y="28" text-anchor="middle" font-size="18" '
            f'class="label" fill="#111">{escape(title)}</text>'
            f'<line x1="{left - SVG_AXIS_MARGIN}" y1="{SVG_AXIS_TOP}" '
            f'x2="{left - SVG_AXIS_MARGIN}" y2="{baseline}" '
            'class="axis" stroke="#000"/>'
            f'<line x1="{left - SVG_AXIS_MARGIN}" y1="{baseline}" '
            f'x2="{width - SVG_AXIS_MARGIN}" y2="{baseline}" class="axis" stroke="#000"/>'
            + "".join(bars)
            + "".join(labels)
            + (
                f'<text x="{left + (width - left - SVG_AXIS_MARGIN) / 2:.1f}" '
                f'y="{SVG_BAR_X_AXIS_LABEL_Y}" '
                f'text-anchor="middle" '
                f'class="label" fill="#111" font-size="12">{escape(x_axis_label)}</text>'
                if x_axis_label
                else ""
            )
            + (
                f'<text x="{SVG_Y_AXIS_LABEL_X}" y="{y_axis_label_y:.1f}" '
                f'text-anchor="middle" '
                f'transform="rotate(-90 {SVG_Y_AXIS_LABEL_X} {y_axis_label_y:.1f})" '
                f'class="label" fill="#111" font-size="12">{escape(y_axis_label)}</text>'
                if y_axis_label
                else ""
            )
            + "</svg>"
        )
    path.write_text(svg, encoding="utf-8")
