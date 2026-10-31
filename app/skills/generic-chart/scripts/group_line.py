#!/usr/bin/env python3
"""Standalone grouped line chart component with local full-style rendering."""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


COMPONENT_TYPE = "group-line"
PALETTE = ["#4387f5", "#4bc3d5", "#f9730c", "#8b5cf6", "#10b981", "#ef4444", "#64748b", "#f59e0b"]

VISUAL_CHANNEL_MAPPINGS = [
    {
        "semantic": "横轴维度",
        "field": "xField",
        "aliases": ["category", "dimension", "date", "period", "year"],
        "visualChannel": "xAxis.category",
        "required": True,
        "default": "period",
    },
    {
        "semantic": "折线系列",
        "field": "series",
        "aliases": ["lines", "lineSeries", "groups"],
        "visualChannel": "line.position+stroke",
        "required": True,
    },
    {
        "semantic": "左轴范围",
        "field": "leftAxis",
        "visualChannel": "leftAxis.scale",
    },
    {
        "semantic": "右轴范围",
        "field": "rightAxis",
        "visualChannel": "rightAxis.scale",
    },
    {
        "semantic": "完整视觉样式",
        "field": "visualStyle",
        "visualChannel": "layout+axis+grid+line+point+legend+tooltip+overlays+css",
    },
]

DATA_CONTRACT = [
    {"field": "xField", "type": "string", "channel": "xAxis.category"},
    {"field": "series[].field", "type": "number", "channel": "line.position"},
    {"field": "data[]", "type": "array", "required": True},
]


def component_schema() -> dict[str, Any]:
    return {
        "type": COMPONENT_TYPE,
        "name": "分组折线图",
        "visualChannelMappings": VISUAL_CHANNEL_MAPPINGS,
        "dataContract": DATA_CONTRACT,
        "axisSemantics": {"xScale": "category|time|linear", "missingValuePolicy": "legacy|gap", "step-after": "Hold previous value until the next observation", "step-before": "Change at the previous observation"},
        "styleContract": {
            "visualStyle": {
                "container": "background, color, padding, borderRadius, fontFamily, className",
                "title": "visible, text, color, fontSize, fontWeight, lineHeight, margin",
                "chart": "background, minWidth, height, fitContent (boolean; fit iframe to actual chart/axis/legend bounds without extra footer reserve)",
                "margin": "top, right, bottom, left",
                "grid": "visible, count, color, width, dasharray, opacity",
                "xAxis": "visible, color, fontSize, fontWeight, labelEvery, labelAngle, lineColor, lineWidth",
                "yAxis": "position(left|right|both), suffix, digits, color, fontSize, fontWeight, lineColor, lineWidth",
                "leftAxis/rightAxis": "min, max, label/description/title/name/axisLabel, suffix, digits, includeZero, visible",
                "line": "showPoints, strokeWidth, curve(straight|smooth|step-after|step-before), dasharray, opacity, strokeLinecap, strokeLinejoin, colorSplit{x,field,beforeColor,afterColor,beforeLabel,afterLabel}; exact existing x only, original path unchanged",
                "point": "visible, radius, outerRadius, fill, strokeWidth",
                "seriesStyle": "field/series/name/label, color, strokeWidth, opacity, dasharray, showPoints, pointRadius, axis",
                "legend": "visible, position(top|bottom), align, gap, color, fontSize, markerSize, markerShape; legend labels show series names only and strip units/value suffixes",
                "tooltip": "visible, placement(cursor|fixed|topGrid|aboveFirstGrid), background, color, borderRadius, padding, fontSize, maxWidth, arrow, showUnit",
                "hover": "bandColor, bandOpacity",
                "positionLine": "visible, color, opacity, width, dasharray; vertical x-position guide shown on hover",
                "overlays": "referenceLine, selectedBand, thresholdMarkers, pointMarkers; pointMarkers supports labelBox, eventTime, eventTitle, labelPosition(top|bottom), labelPlacement(outside-top reserves a separate lane above plot; all labels retained on mobile), allowCurveOverlap (default false; true permits curve overlap while protecting marked anchors and other cards), labelBackground, borderColor, labelBorderRadius, connectorColor",
                "customCss": "raw CSS appended inside this component",
            }
        },
        "builtInDataSources": [],
    }


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _parse_style_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--style-json must be a JSON object")
    parsed.setdefault("version", 1)
    return parsed


def _strip_code_fence(value: str) -> str:
    text = value.strip()
    fence = re.match(r"^```(?:json|js|javascript)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    return fence.group(1).strip() if fence else text


def _parse_style_code(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    text = _strip_code_fence(value)
    if not text:
        return {}
    for prefix in ("export default", "module.exports =", "const visualStyle =", "let visualStyle =", "var visualStyle ="):
        if text.strip().startswith(prefix):
            text = text.strip()[len(prefix) :].strip()
            break
    if text.startswith("return "):
        text = text[len("return ") :].strip()
    text = text.rstrip(";")
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            parsed.setdefault("version", 1)
            return parsed
    except json.JSONDecodeError:
        pass
    node_script = """
const fs = require('fs');
const source = fs.readFileSync(process.argv[1], 'utf8');
let value;
try {
  value = Function('"use strict"; return (' + source + ');')();
} catch (firstError) {
  value = Function('"use strict";' + source + '; return typeof visualStyle !== "undefined" ? visualStyle : undefined;')();
}
if (!value || Array.isArray(value) || typeof value !== 'object') {
  throw new Error('style code must evaluate to an object');
}
process.stdout.write(JSON.stringify(value));
"""
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as file:
            file.write(text)
            temp_path = Path(file.name)
        result = subprocess.run(["node", "-e", node_script, str(temp_path)], text=True, capture_output=True, timeout=10)
    except FileNotFoundError as exc:
        raise ValueError("--style-code uses JavaScript object syntax but node is not available") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "--style-code must be JSON or a JavaScript object expression")
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        raise ValueError("--style-code must evaluate to an object")
    parsed.setdefault("version", 1)
    return parsed


def _read_style_file(path: str | None) -> str | None:
    if not path:
        return None
    return Path(path).expanduser().read_text(encoding="utf-8")


def _combined_style(style_json: str | None, style_code: str | None, style_file: str | None) -> dict[str, Any]:
    style = _parse_style_json(style_json)
    file_style = _parse_style_code(_read_style_file(style_file))
    code_style = _parse_style_code(style_code)
    if file_style:
        style = _deep_merge(style, file_style)
    if code_style:
        style = _deep_merge(style, code_style)
    if style:
        style.setdefault("version", 1)
    return style


def _extract_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("chartPayload"), dict):
        return payload["chartPayload"]
    root = payload.get("root")
    if isinstance(root, dict) and root.get("type") == "component" and isinstance(root.get("component"), dict):
        return root["component"]
    component = payload.get("component")
    if isinstance(component, dict):
        return component
    return payload


def _is_grouped_line_payload(payload: dict[str, Any]) -> bool:
    chart_type = str(payload.get("chartType") or payload.get("chartName") or payload.get("type") or "").replace("_", "-").lower()
    return chart_type in {"grouped-line", "group-line", "multi-line", "dual-line", COMPONENT_TYPE}


def _coerce_grouped_line_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    if _is_grouped_line_payload(payload):
        normalized = dict(payload)
        normalized["chartType"] = "grouped-line"
        return normalized
    props = payload.get("props") if isinstance(payload.get("props"), dict) else {}
    data = payload.get("data") or props.get("data") or payload.get("rows") or props.get("rows")
    if not isinstance(data, list) or not data:
        return None
    x_field = str(props.get("xField") or payload.get("xField") or "period")
    raw_series = props.get("series") or payload.get("series")
    if isinstance(raw_series, list) and len([item for item in raw_series if isinstance(item, dict)]) >= 2:
        normalized = dict(payload)
        normalized["chartType"] = "grouped-line"
        normalized["data"] = data
        normalized.setdefault("xField", x_field)
        return normalized
    if len(_numeric_fields(data, x_field)) >= 2:
        normalized = dict(payload)
        normalized["chartType"] = "grouped-line"
        normalized["data"] = data
        normalized.setdefault("xField", x_field)
        return normalized
    if _looks_like_tidy_line_data(data, x_field):
        normalized = dict(payload)
        normalized["chartType"] = "grouped-line"
        normalized["data"] = data
        normalized.setdefault("xField", x_field)
        return normalized
    return None


def _numeric_fields(data: list[Any], x_field: str) -> list[str]:
    fields: list[str] = []
    seen: set[str] = set()
    for row in data:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            if key == x_field or key in seen:
                continue
            try:
                float(value)
            except (TypeError, ValueError):
                continue
            fields.append(str(key))
            seen.add(str(key))
    return fields


def _looks_like_tidy_line_data(data: list[Any], x_field: str) -> bool:
    rows = [row for row in data if isinstance(row, dict)]
    if not rows:
        return False
    numeric_fields = _numeric_fields(rows, x_field)
    if len(numeric_fields) != 1:
        return False
    group_field = _detect_tidy_group_field(rows, x_field, numeric_fields[0], None)
    return bool(group_field)


def _detect_tidy_group_field(data: list[dict[str, Any]], x_field: str, value_field: str, preferred: str | None) -> str | None:
    preferred_fields = [
        preferred,
        "seriesField",
        "groupField",
        "categoryField",
        "entityField",
        "nameField",
        "series",
        "group",
        "category",
        "entity",
        "company",
        "ticker",
        "symbol",
        "name",
        "label",
    ]
    fields: list[str] = []
    for field in preferred_fields:
        if field and field not in fields:
            fields.append(field)
    for row in data:
        for field in row.keys():
            if field not in fields:
                fields.append(str(field))
    for field in fields:
        if field in {x_field, value_field}:
            continue
        values = [str(row.get(field)).strip() for row in data if row.get(field) not in (None, "")]
        unique_values = list(dict.fromkeys(values))
        if 2 <= len(unique_values) <= 12 and len(values) >= len(unique_values):
            return field
    return None


def _match_series_for_category(series: list[dict[str, Any]], category: str, index: int) -> dict[str, Any] | None:
    normalized_category = str(category).strip().lower()
    for item in series:
        if str(item.get("field", "")).strip().lower() == normalized_category:
            return item
        if str(item.get("label", "")).strip().lower() == normalized_category:
            return item
    return series[index] if index < len(series) else None


def _maybe_pivot_tidy_data(
    data: list[Any],
    x_field: str,
    series: list[dict[str, Any]],
    source: dict[str, Any],
    props: dict[str, Any],
) -> tuple[list[Any], list[dict[str, Any]]]:
    rows = [row for row in data if isinstance(row, dict)]
    if not rows:
        return data, series
    raw_value_field = props.get("valueField") or source.get("valueField") or props.get("yField") or source.get("yField")
    series_fields = [str(item.get("field") or "") for item in series]
    duplicated_series_field = ""
    if series_fields and len(set(series_fields)) < len(series_fields):
        duplicated_series_field = next((field for field in series_fields if field and series_fields.count(field) > 1), "")
    numeric_fields = _numeric_fields(rows, x_field)
    value_field = str(raw_value_field or duplicated_series_field or "")
    if value_field and value_field not in numeric_fields:
        value_field = ""
    if not value_field and len(numeric_fields) == 1:
        value_field = numeric_fields[0]
    if not value_field:
        return data, series
    has_wide_fields = len(series_fields) == len(set(series_fields)) and all(
        any(number_like(row.get(field)) for row in rows) for field in series_fields if field
    )
    if has_wide_fields and (value_field not in series_fields or (len(series_fields) > 1 and not (props.get("seriesField") or source.get("seriesField") or props.get("groupField") or source.get("groupField")))):
        return data, series
    group_field = _detect_tidy_group_field(
        rows,
        x_field,
        value_field,
        str(props.get("seriesField") or source.get("seriesField") or props.get("groupField") or source.get("groupField") or ""),
    )
    if not group_field:
        return data, series
    category_order: list[str] = []
    wide_by_x: dict[str, dict[str, Any]] = {}
    for row in rows:
        x_value = row.get(x_field)
        category = str(row.get(group_field) or "").strip()
        if x_value in (None, "") or not category:
            continue
        if category not in category_order:
            category_order.append(category)
        x_key = str(x_value)
        wide_row = wide_by_x.setdefault(x_key, {x_field: x_value})
        wide_row[category] = row.get(value_field)
    if len(category_order) < 2 or not wide_by_x:
        return data, series
    next_series: list[dict[str, Any]] = []
    for index, category in enumerate(category_order):
        matched = _match_series_for_category(series, category, index) or {}
        next_series.append(
            {
                **matched,
                "field": category,
                "label": str(matched.get("label") or category),
                "color": str(matched.get("color") or PALETTE[index % len(PALETTE)]),
                "axis": str(matched.get("axis") or "right"),
                "prefix": str(matched.get("prefix") or ""),
                "suffix": str(matched.get("suffix") or ""),
                "digits": matched.get("digits"),
            }
        )
    return list(wide_by_x.values()), next_series


def number_like(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _axis_values(data: list[Any], series: list[dict[str, Any]], axis: str) -> list[float]:
    fields = [str(item.get("field") or "") for item in series if str(item.get("axis") or "right").lower() == axis]
    values: list[float] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        for field in fields:
            value = _finite_number(row.get(field))
            if value is not None:
                values.append(value)
    return values


def _axis_domain_is_implausibly_wide(configured_min: float, configured_max: float, data_min: float, data_max: float) -> bool:
    if configured_max <= configured_min:
        return True
    data_span = max(data_max - data_min, 1e-9)
    configured_span = configured_max - configured_min
    # Model-generated chart payloads sometimes contain hallucinated axis bounds
    # such as max=111 for data whose true max is 9.  A user can still request a
    # somewhat wider domain, but ranges that are several times larger than the
    # actual data destroy readability, so normalize them back to the data.
    if configured_span <= max(data_span * 4, data_span + 8):
        return False
    upper_extra = max(0.0, configured_max - data_max)
    lower_extra = max(0.0, data_min - configured_min)
    return max(upper_extra, lower_extra) > max(data_span * 2, 8)


def _expand_axis_to_data(axis: dict[str, Any], values: list[float]) -> dict[str, Any]:
    if not values:
        return axis
    data_min = min(values)
    data_max = max(values)
    next_axis = dict(axis)
    configured_min = _finite_number(next_axis.get("min"))
    configured_max = _finite_number(next_axis.get("max"))
    if configured_min is not None and configured_max is not None and _axis_domain_is_implausibly_wide(configured_min, configured_max, data_min, data_max):
        configured_min = None
        configured_max = None
        next_axis.pop("min", None)
        next_axis.pop("max", None)
    next_axis["min"] = min(configured_min if configured_min is not None else data_min, data_min)
    next_axis["max"] = max(configured_max if configured_max is not None else data_max, data_max)
    return next_axis


def _percent_semantic_text(*values: Any) -> str:
    return " ".join(str(value or "") for value in values).lower()


def _looks_like_percent_metric(source: dict[str, Any], props: dict[str, Any], series: list[dict[str, Any]], left_axis: dict[str, Any], right_axis: dict[str, Any]) -> bool:
    text = _percent_semantic_text(
        source.get("title"),
        source.get("subtitle"),
        source.get("metric"),
        source.get("metricLabel"),
        source.get("unit"),
        source.get("valueUnit"),
        props.get("title"),
        props.get("unit"),
        left_axis.get("label"),
        left_axis.get("title"),
        left_axis.get("name"),
        left_axis.get("axisLabel"),
        right_axis.get("label"),
        right_axis.get("title"),
        right_axis.get("name"),
        right_axis.get("axisLabel"),
        *[item.get("field") for item in series],
        *[item.get("label") for item in series],
    )
    return bool(
        "%"
        in text
        or "％" in text
        or "pct" in text
        or "percent" in text
        or "percentage" in text
        or "收益率" in text
        or "回报率" in text
        or "涨跌幅" in text
        or "变动率" in text
        or "增长率" in text
    )


def _looks_like_financial_time_series_metric(
    source: dict[str, Any],
    props: dict[str, Any],
    series: list[dict[str, Any]],
    left_axis: dict[str, Any],
    right_axis: dict[str, Any],
) -> bool:
    text = _percent_semantic_text(
        source.get("title"),
        source.get("subtitle"),
        source.get("metric"),
        source.get("metricLabel"),
        source.get("unit"),
        source.get("valueUnit"),
        props.get("title"),
        props.get("unit"),
        left_axis.get("label"),
        left_axis.get("title"),
        left_axis.get("name"),
        left_axis.get("axisLabel"),
        right_axis.get("label"),
        right_axis.get("title"),
        right_axis.get("name"),
        right_axis.get("axisLabel"),
        *[item.get("field") for item in series],
        *[item.get("label") for item in series],
    )
    if any(
        token in text
        for token in (
            "市值",
            "投资",
            "投資",
            "资产",
            "資產",
            "标的",
            "標的",
            "组合",
            "組合",
            "价格",
            "價格",
            "股价",
            "股價",
            "行情",
            "market value",
            "portfolio",
            "investment",
            "asset",
            "price",
            "close",
        )
    ):
        return True
    return any(
        re.fullmatch(r"[A-Z0-9.^=\-]{2,16}(?:\.[A-Z]{1,4})?", str(item.get("field") or "").upper())
        or re.fullmatch(r"[A-Z0-9.^=\-]{2,16}(?:\.[A-Z]{1,4})?", str(item.get("label") or "").upper())
        for item in series
    )


def _apply_percent_units(series: list[dict[str, Any]], left_axis: dict[str, Any], right_axis: dict[str, Any], visual_style: dict[str, Any]) -> None:
    for item in series:
        if not item.get("suffix") and not item.get("unit"):
            item["suffix"] = "%"
    for axis in (left_axis, right_axis):
        if not axis.get("suffix") and not axis.get("unit"):
            axis["suffix"] = "%"
    tooltip = visual_style.setdefault("tooltip", {})
    if isinstance(tooltip, dict):
        tooltip["showUnit"] = True


def _looks_like_date_axis(data: list[Any], x_field: str) -> bool:
    rows = [row for row in data if isinstance(row, dict) and row.get(x_field) not in (None, "")]
    if not rows:
        return False
    sample = rows[: min(len(rows), 20)]
    return all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.get(x_field) or "")) for row in sample)


def _looks_like_relative_day_axis(data: list[Any], x_field: str) -> bool:
    rows = [row for row in data if isinstance(row, dict) and row.get(x_field) not in (None, "")]
    if not rows:
        return False
    if x_field.lower() not in {"day", "days", "relative_day", "relativeday", "window_day", "windowday", "t"}:
        return False
    values: list[float] = []
    for row in rows[: min(len(rows), 120)]:
        value = _finite_number(row.get(x_field))
        if value is None:
            return False
        values.append(value)
    return bool(values) and min(values) <= 1 and max(values) >= min(10, len(values) - 1)


def _axis_sort_key(row: dict[str, Any], x_field: str) -> tuple[int, Any]:
    value = row.get(x_field)
    number = _finite_number(value)
    if number is not None:
        return (0, number)
    return (1, str(value or ""))


def _forward_fill_series_values(data: list[Any], series: list[dict[str, Any]], x_field: str) -> list[Any]:
    date_axis = _looks_like_date_axis(data, x_field)
    relative_day_axis = _looks_like_relative_day_axis(data, x_field)
    if not date_axis and not relative_day_axis:
        return data
    fields = [str(item.get("field") or "") for item in series if item.get("field")]
    if not fields:
        return data
    sorted_rows = sorted(
        [dict(row) for row in data if isinstance(row, dict)],
        key=lambda row: _axis_sort_key(row, x_field),
    )
    last_values: dict[str, Any] = {field: 0 for field in fields} if relative_day_axis else {}
    for row in sorted_rows:
        for field in fields:
            value = row.get(field)
            if number_like(value):
                last_values[field] = value
            elif field in last_values:
                row[field] = last_values[field]
    return sorted_rows


def _normalize_series_item(item: dict[str, Any], index: int, default_axis: str = "right") -> dict[str, Any] | None:
    field = str(item.get("field") or item.get("key") or item.get("name") or "")
    if not field:
        return None
    return {
        "field": field,
        "label": str(item.get("label") or item.get("title") or item.get("name") or field),
        "color": str(item.get("color") or PALETTE[index % len(PALETTE)]),
        "axis": "right" if str(item.get("axis") or default_axis).lower() == "right" else "left",
        "prefix": str(item.get("prefix") or ""),
        "suffix": str(item.get("suffix") or ""),
        "digits": item.get("digits"),
    }


def _series_from_payload(payload: dict[str, Any], props: dict[str, Any], data: list[Any], x_field: str) -> list[dict[str, Any]]:
    raw_series = props.get("series") or payload.get("series")
    series: list[dict[str, Any]] = []
    if isinstance(raw_series, list) and raw_series:
        for index, item in enumerate(raw_series):
            if isinstance(item, dict):
                normalized = _normalize_series_item(item, index)
                if normalized:
                    series.append(normalized)
    if series:
        return series
    left_series = props.get("leftSeries") if isinstance(props.get("leftSeries"), dict) else payload.get("leftSeries")
    right_series = props.get("rightSeries") if isinstance(props.get("rightSeries"), dict) else payload.get("rightSeries")
    for item, axis in ((left_series, "left"), (right_series, "right")):
        if isinstance(item, dict):
            normalized = _normalize_series_item(item, len(series), axis)
            if normalized:
                series.append(normalized)
    if series:
        return series
    return [
        {"field": field, "label": field, "color": PALETTE[index % len(PALETTE)], "axis": "right", "prefix": "", "suffix": "", "digits": None}
        for index, field in enumerate(_numeric_fields(data, x_field))
    ]


def _normalize_views(raw_views: Any, base_left_axis: dict[str, Any], base_right_axis: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw_views, dict):
        return {}
    views = {}
    for key, view in raw_views.items():
        if not isinstance(view, dict) or not isinstance(view.get("data"), list):
            continue
        views[str(key)] = {
            "label": str(view.get("label") or key),
            "data": view["data"],
            "leftAxis": view.get("leftAxis") if isinstance(view.get("leftAxis"), dict) else base_left_axis,
            "rightAxis": view.get("rightAxis") if isinstance(view.get("rightAxis"), dict) else base_right_axis,
        }
    return views


def _normalize_config(payload: dict[str, Any], title: str | None = None, style: dict[str, Any] | None = None) -> dict[str, Any]:
    source = _extract_payload(payload)
    if not isinstance(source, dict):
        raise ValueError("payload must contain a grouped-line/group-line component")
    source = _coerce_grouped_line_payload(source)
    if source is None:
        raise ValueError("payload must contain grouped-line data with at least two numeric series")
    props = source.get("props") if isinstance(source.get("props"), dict) else {}
    data = source.get("data") or props.get("data") or source.get("rows") or props.get("rows") or []
    if not isinstance(data, list):
        raise ValueError("group-line data must be an array")
    x_field = str(props.get("xField") or source.get("xField") or "period")
    series = _series_from_payload(source, props, data, x_field)
    data, series = _maybe_pivot_tidy_data(data, x_field, series, source, props)
    if not series:
        raise ValueError("group-line series must contain at least one numeric field")
    left_axis = props.get("leftAxis") if isinstance(props.get("leftAxis"), dict) else source.get("leftAxis")
    right_axis = props.get("rightAxis") if isinstance(props.get("rightAxis"), dict) else source.get("rightAxis")
    left_axis = left_axis if isinstance(left_axis, dict) else {"digits": 2, "includeZero": False}
    right_axis = right_axis if isinstance(right_axis, dict) else {"digits": 2, "includeZero": False}
    left_axis = _expand_axis_to_data(left_axis, _axis_values(data, series, "left"))
    right_axis = _expand_axis_to_data(right_axis, _axis_values(data, series, "right"))
    visual_style = source.get("visualStyle") if isinstance(source.get("visualStyle"), dict) else props.get("visualStyle")
    visual_style = visual_style if isinstance(visual_style, dict) else {}
    if style:
        visual_style = _deep_merge(visual_style, style)
    y_axis = visual_style.get("yAxis") if isinstance(visual_style.get("yAxis"), dict) else {}
    if y_axis.get("suffix"):
        left_axis = _deep_merge(left_axis, {"suffix": y_axis["suffix"]})
        right_axis = _deep_merge(right_axis, {"suffix": y_axis["suffix"]})
    is_percent_metric = _looks_like_percent_metric(source, props, series, left_axis, right_axis)
    if is_percent_metric:
        _apply_percent_units(series, left_axis, right_axis, visual_style)
    if source.get("missingValuePolicy") != "gap" and (is_percent_metric or _looks_like_financial_time_series_metric(source, props, series, left_axis, right_axis)):
        data = _forward_fill_series_values(data, series, x_field)
    views = _normalize_views(props.get("views") or source.get("views"), left_axis, right_axis)
    default_view = str(props.get("defaultView") or source.get("defaultView") or next(iter(views), ""))
    return {
        "type": COMPONENT_TYPE,
        "chartType": "grouped-line",
        "themeMode": str(
            props.get("themeMode") or props.get("theme") or source.get("themeMode") or source.get("theme") or "light"
        ).lower(),
        "title": str(title or source.get("title") or props.get("title") or "分组折线图"),
        "subtitle": str(source.get("subtitle") or props.get("subtitle") or ""),
        "xField": x_field,
        "xScale": str(source.get("xScale") or props.get("xScale") or "category"),
        "missingValuePolicy": str(source.get("missingValuePolicy") or "legacy"),
        "series": series,
        "leftAxis": left_axis,
        "rightAxis": right_axis,
        "height": int(props.get("height") or source.get("height") or 360),
        "gridCount": int(props.get("gridCount") or source.get("gridCount") or 5),
        "visualStyle": visual_style,
        "data": data,
        "views": views,
        "defaultView": default_view if default_view in views else next(iter(views), ""),
    }


def render_document(payload: dict[str, Any], title: str | None = None, style: dict[str, Any] | None = None) -> str:
    config = _normalize_config(payload, title, style)
    config_json = json.dumps(config, ensure_ascii=False).replace("</", "<\\/")
    page_title = html.escape(config["title"] or "分组折线图")
    contract = html.escape(json.dumps(component_schema(), ensure_ascii=False), quote=True)
    theme_mode = "dark" if config.get("themeMode") == "dark" else "light"
    return f"""<!doctype html>
<html lang="zh-CN" data-theme="{theme_mode}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{page_title}</title>
<script>(() => {{ const theme = new URLSearchParams(location.search).get('theme'); if (theme === 'dark' || theme === 'light') document.documentElement.dataset.theme = theme; }})();</script>
<style>
*{{box-sizing:border-box}}
:root{{--chart-text-primary:#171b22;--chart-text-secondary:#5f6876;--chart-text-muted:#8b94a2;--chart-border-subtle:#edf0f4;--chart-border-default:#a3a8b0;--chart-surface-muted:#f1f1f1;--chart-point-fill:#fff;--chart-tooltip-bg:rgba(59,59,59,.96)}}
:root[data-theme="dark"]{{color-scheme:dark;--chart-text-primary:#f1f5f9;--chart-text-secondary:#c1cad7;--chart-text-muted:#93a0b3;--chart-border-subtle:#2b3544;--chart-border-default:#334052;--chart-surface-muted:#202733;--chart-point-fill:#181e28;--chart-tooltip-bg:#202733}}
html,body{{width:100%;min-height:100%;margin:0;overflow:visible}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;letter-spacing:0;background:transparent;color:var(--chart-text-primary);overflow:visible}}
.group-line-component{{position:relative;width:100%;container-type:inline-size;min-height:0;height:auto;padding:0;background:transparent;border:0;border-radius:0;box-shadow:none;color:var(--chart-text-primary);overflow:visible}}
.chart-title{{min-height:22px;max-height:42px;margin:0;font-size:16px;font-weight:800;line-height:1.25;color:inherit;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}}
.chart-subtitle{{margin:3px 0 0;color:var(--chart-text-secondary);font-size:12px;line-height:1.35}}
.chart-tabs{{position:absolute;z-index:3;top:8px;right:18px;display:flex;align-items:center;gap:4px}}
.chart-tab{{appearance:none;border:0;background:transparent;color:var(--chart-text-secondary);height:34px;min-width:56px;padding:0 12px;border-radius:8px;font:inherit;font-size:13px;cursor:pointer}}
.chart-tab[aria-selected="true"]{{background:var(--chart-surface-muted);color:var(--chart-text-primary);font-weight:800}}
.chart-stage{{position:relative;width:100%;height:calc(clamp(340px,calc(100cqw / 2.8),{config["height"]}px) - 82px);min-height:220px;margin-top:4px;overflow:visible}}
svg{{display:block;width:100%;height:100%;overflow:visible}}
.grid-line{{stroke:var(--chart-border-subtle);stroke-width:1}}
.axis-line{{stroke:var(--chart-border-default);stroke-width:1}}
.axis-label{{fill:var(--chart-text-secondary);font-size:10px;font-weight:400}}
.x-label{{fill:var(--chart-text-secondary);font-size:10px;font-weight:400}}
.line-path{{fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}}
.point-outer{{fill:var(--chart-point-fill);stroke-width:2}}
.point-inner{{stroke-width:0;cursor:crosshair}}
.hover-zone{{fill:rgba(0,0,0,0);cursor:crosshair;pointer-events:all}}
.position-line{{stroke:var(--chart-text-muted);stroke-width:1;pointer-events:none;opacity:0;shape-rendering:crispEdges}}
.overlay-label{{fill:var(--chart-text-secondary);font-size:11px;font-weight:700;paint-order:stroke;stroke:var(--chart-point-fill);stroke-width:4px;stroke-linejoin:round;pointer-events:none}}
.chart-legend{{position:relative;width:100%;min-height:28px;margin:10px 0 0;display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:10px 18px;color:var(--chart-text-secondary);font-size:10px;line-height:1.2;text-align:center;pointer-events:none;overflow:visible}}
.legend-item{{display:flex;align-items:center;gap:7px;white-space:nowrap}}
.legend-dot{{width:8px;height:8px;border-radius:50%;flex:none}}
.chart-tooltip{{position:fixed;z-index:2147483647;display:inline-grid;min-width:0;width:fit-content;max-width:min(220px,calc(100vw - 24px));padding:6px 8px;background:var(--chart-tooltip-bg);color:#fff;border-radius:6px;font-size:10px;line-height:1.35;box-shadow:none;filter:none;pointer-events:none;opacity:0;visibility:hidden;transition:opacity .08s ease;white-space:nowrap}}
.chart-tooltip::after{{content:"";display:var(--tooltip-arrow-display,block);position:absolute;left:var(--arrow-left,50%);top:calc(100% - 1px);transform:translateX(-50%);border-left:5px solid transparent;border-right:5px solid transparent;border-top:6px solid var(--tooltip-bg,rgba(59,59,59,.96))}}
.chart-tooltip.active{{opacity:1;visibility:visible}}
.tooltip-title{{color:#d8d8d8;margin-bottom:3px;font-size:9px;font-weight:400;line-height:1.2}}
.tooltip-body{{display:grid;grid-template-columns:auto auto;column-gap:8px;row-gap:2px;align-items:center;width:max-content}}
.tooltip-row{{display:contents;white-space:nowrap}}
.tooltip-label{{display:flex;align-items:center;gap:5px;color:#f5f5f5;min-width:0}}
.tooltip-dot{{width:7px;height:7px;border-radius:50%;flex:none}}
.tooltip-value{{justify-self:end;text-align:right;margin-left:0;color:#fff;font-weight:650;font-variant-numeric:tabular-nums}}
@media(min-width:1200px){{.chart-stage{{height:{max(220, int(config["height"]) - 82)}px}}}}
@media(max-width:680px){{.group-line-component{{min-height:0;height:auto;padding:0}}.chart-title{{min-height:18px;max-height:42px;font-size:16px;line-height:1.25;padding:0}}.chart-subtitle{{font-size:12px}}.chart-tabs{{top:4px;right:0;gap:2px}}.chart-tab{{height:30px;min-width:44px;padding:0 8px;border-radius:8px;font-size:12px}}.chart-stage{{height:calc(clamp(242px,calc(100cqw / 2.72),306px) - 54px);min-height:188px;margin-top:4px}}.axis-label,.x-label{{font-size:10px;font-weight:400}}.chart-legend{{gap:8px 12px;min-height:0;margin-top:0;font-size:10px;align-content:center}}.legend-item{{gap:5px;max-width:42%;overflow:hidden;text-overflow:ellipsis}}.legend-dot{{width:8px;height:8px}}.chart-tooltip{{min-width:0;width:fit-content;max-width:calc(100vw - 24px);font-size:10px;padding:6px 8px;white-space:normal}}.tooltip-label{{min-width:0}}}}
@media(max-width:680px){{.group-line-component.has-event-marks .chart-stage{{height:344px;min-height:344px}}}}
</style>
</head>
<body>
<section class="group-line-component" data-component-contract="{contract}">
  <h2 class="chart-title"></h2>
  <p class="chart-subtitle"></p>
  <div class="chart-tabs" role="tablist"></div>
  <div class="chart-stage"><svg role="img" aria-label="{page_title}"></svg></div>
  <div class="chart-legend"></div>
  <div class="chart-tooltip"></div>
</section>
<script>
const config = {config_json};
const root = document.querySelector('.group-line-component');
const titleEl = root.querySelector('.chart-title');
const subtitleEl = root.querySelector('.chart-subtitle');
const stage = root.querySelector('.chart-stage');
const svg = root.querySelector('svg');
const tooltip = root.querySelector('.chart-tooltip');
const tabs = root.querySelector('.chart-tabs');
const legend = root.querySelector('.chart-legend');
const NS = 'http://www.w3.org/2000/svg';
const DEFAULT_STYLE = {{
  version: 1,
  container: {{}},
  title: {{ visible: true }},
  subtitle: {{ visible: true }},
  chart: {{}},
  margin: {{ top: 8, right: 0, bottom: 46, left: 24 }},
  grid: {{ visible: true, count: config.gridCount || 5, color: '#e8e8e8', width: 1, dasharray: '', opacity: 1 }},
  xAxis: {{ visible: true, color: '#666', fontSize: 10, fontWeight: 400, labelEvery: 'auto', labelAngle: 0, lineColor: '#a3a8b0', lineWidth: 1 }},
  yAxis: {{ position: 'right', color: '#747474', fontSize: 10, fontWeight: 400, lineColor: '#a3a8b0', lineWidth: 1, digits: 2 }},
  leftAxis: {{ includeZero: false, padding: 0.08 }},
  rightAxis: {{ includeZero: false, padding: 0.08 }},
  line: {{ showPoints: false, strokeWidth: 2, curve: 'straight', opacity: 1, strokeLinecap: 'round', strokeLinejoin: 'round', connectNulls: true }},
  point: {{ visible: true, radius: 4, outerRadius: 0, fill: '#fff', strokeWidth: 2 }},
  legend: {{ visible: true, position: 'bottom', align: 'center', gap: 20, color: '#858585', fontSize: 10, markerSize: 8, markerShape: 'circle' }},
  tooltip: {{ visible: true, placement: 'aboveFirstGrid', offsetY: 0, gap: 8, background: 'rgba(59,59,59,.96)', color: '#fff', borderRadius: 6, padding: '6px 8px', fontSize: 10, maxWidth: 220, arrow: true }},
  hover: {{ bandColor: '#8a8a8a', bandOpacity: 0.65, width: 1 }},
  positionLine: {{ visible: true, color: '#6b7280', opacity: 0.72, width: 1, dasharray: '4 4' }},
  overlays: [],
  seriesStyle: [],
  emphasis: []
}};
function isObject(value) {{ return value && typeof value === 'object' && !Array.isArray(value); }}
function deepMerge(base, patch) {{
  const output = {{ ...(base || {{}}) }};
  Object.entries(patch || {{}}).forEach(([key, value]) => {{
    if (isObject(value) && isObject(output[key])) output[key] = deepMerge(output[key], value);
    else if (value !== undefined) output[key] = value;
  }});
  return output;
}}
const DARK_STYLE = {{
  grid: {{color:'#2b3544'}}, xAxis: {{color:'#c1cad7',lineColor:'#334052'}},
  yAxis: {{color:'#c1cad7',lineColor:'#334052'}}, point: {{fill:'#181e28'}},
  legend: {{color:'#c1cad7'}}, tooltip: {{background:'#202733',color:'#f1f5f9'}},
  hover: {{bandColor:'#c1cad7'}}, positionLine: {{color:'#93a0b3'}}
}};
const isDark = document.documentElement.dataset.theme === 'dark' || config.themeMode === 'dark';
const visual = deepMerge(deepMerge(DEFAULT_STYLE, isDark ? DARK_STYLE : {{}}), config.visualStyle || {{}});
const DARK_COLOR_ALIASES = {{'#4387f5':'#8294ff','#4bc3d5':'#34d3ad','#f9730c':'#f6ad55','#8b5cf6':'#d19dff','#10b981':'#34d3ad','#ef4444':'#ff7188','#64748b':'#93a0b3','#f59e0b':'#f6ad55'}};
const chartColor = color => isDark ? (DARK_COLOR_ALIASES[String(color || '').toLowerCase()] || color) : color;
let activeView = config.defaultView && config.views?.[config.defaultView] ? config.defaultView : Object.keys(config.views || {{}})[0];
let tooltipPortalAckUntil = 0;

function svgEl(name, attrs = {{}}, text = '') {{
  const el = document.createElementNS(NS, name);
  Object.entries(attrs).forEach(([key, value]) => {{
    if (value !== undefined && value !== null) el.setAttribute(key, String(value));
  }});
  if (text !== '') el.textContent = text;
  return el;
}}
function number(value) {{
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}}
function toNumber(value, fallback) {{
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}}
function applyDomStyles(el, styles) {{
  if (!isObject(styles)) return;
  Object.entries(styles).forEach(([key, value]) => {{
    if (value !== undefined && value !== null) el.style[key] = String(value);
  }});
}}
function applyComponentStyles() {{
  try {{
    if (window.frameElement) {{
      window.frameElement.style.background = 'transparent';
      window.frameElement.style.backgroundColor = 'transparent';
      window.frameElement.style.border = '0';
      window.frameElement.style.borderRadius = '0';
      window.frameElement.style.boxShadow = 'none';
      window.frameElement.style.overflow = 'visible';
    }}
  }} catch (e) {{}}
  root.style.minHeight = '0';
  root.style.height = 'auto';
  applyDomStyles(root, visual.container);
  applyDomStyles(stage, visual.chart);
  root.style.background = 'transparent';
  root.style.backgroundColor = 'transparent';
  root.style.border = '0';
  root.style.borderRadius = '0';
  root.style.boxShadow = 'none';
  stage.style.background = 'transparent';
  stage.style.backgroundColor = 'transparent';
  titleEl.textContent = visual.title?.text || config.title || '';
  titleEl.style.display = visual.title?.visible === false ? 'none' : '';
  applyDomStyles(titleEl, visual.title?.style);
  subtitleEl.textContent = visual.subtitle?.text || config.subtitle || '';
  subtitleEl.style.display = (visual.subtitle?.visible === false || !subtitleEl.textContent) ? 'none' : '';
  applyDomStyles(subtitleEl, visual.subtitle?.style);
  legend.style.color = visual.legend.color;
  legend.style.fontSize = `${{toNumber(visual.legend.fontSize, 10)}}px`;
  legend.style.gap = `${{toNumber(visual.legend.gap, 22)}}px`;
  tooltip.style.background = visual.tooltip.background;
  tooltip.style.color = visual.tooltip.color;
  tooltip.style.borderRadius = `${{toNumber(visual.tooltip.borderRadius, 5)}}px`;
  tooltip.style.padding = visual.tooltip.padding || '6px 8px';
  tooltip.style.fontSize = `${{toNumber(visual.tooltip.fontSize, 10)}}px`;
  tooltip.style.maxWidth = `min(${{toNumber(visual.tooltip.maxWidth, 220)}}px, calc(100vw - 24px))`;
  tooltip.style.setProperty('--tooltip-arrow-display', visual.tooltip.arrow === false ? 'none' : 'block');
  tooltip.style.setProperty('--tooltip-bg', visual.tooltip.background);
  if (visual.customCss || visual.css) {{
    let customStyle = document.getElementById('group-line-custom-style');
    if (!customStyle) {{
      customStyle = document.createElement('style');
      customStyle.id = 'group-line-custom-style';
      document.head.appendChild(customStyle);
    }}
    customStyle.textContent = String(visual.customCss || visual.css || '');
  }}
}}
function parentTooltipContext() {{
  try {{
    if (!window.frameElement || !window.parent?.document) {{
      return {{ doc: document, win: window, offsetLeft: 0, offsetTop: 0, frameRect: {{left: 0, top: 0}} }};
    }}
    let currentWindow = window;
    let targetWindow = window.parent;
    let offsetLeft = 0;
    let offsetTop = 0;
    let depth = 0;
    while (currentWindow.frameElement && targetWindow?.document && depth < 8) {{
      const frameRect = currentWindow.frameElement.getBoundingClientRect();
      offsetLeft += frameRect.left;
      offsetTop += frameRect.top;
      currentWindow = targetWindow;
      if (!currentWindow.frameElement || !currentWindow.parent || currentWindow.parent === currentWindow) break;
      targetWindow = currentWindow.parent;
      depth += 1;
    }}
    return {{ doc: currentWindow.document, win: currentWindow, offsetLeft, offsetTop, frameRect: {{left: offsetLeft, top: offsetTop}} }};
  }} catch (error) {{
    return null;
  }}
}}
function chartEmbedKey() {{
  try {{
    const frame = window.frameElement;
    if (!frame) {{
      if (!root.dataset.chartEmbed) root.dataset.chartEmbed = `chart-${{Math.random().toString(36).slice(2, 10)}}`;
      return root.dataset.chartEmbed;
    }}
    if (!frame.dataset.chartEmbed) frame.dataset.chartEmbed = `chart-${{Math.random().toString(36).slice(2, 10)}}`;
    return frame.dataset.chartEmbed;
  }} catch (error) {{
    return 'active';
  }}
}}
function ensureParentTooltipStyle(doc) {{
  let style = doc.getElementById('group-line-tooltip-portal-style');
  if (!style) {{
    style = doc.createElement('style');
    style.id = 'group-line-tooltip-portal-style';
    doc.head.appendChild(style);
  }}
  style.textContent = `
    .group-line-tooltip-portal{{position:fixed;z-index:2147483647;display:inline-grid;min-width:0;width:fit-content;max-width:min(220px,calc(100vw - 24px));padding:6px 8px;background:rgba(59,59,59,.96);color:#fff;border-radius:6px;font:10px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;pointer-events:none;opacity:0;visibility:hidden;box-shadow:none;filter:none;white-space:normal;transition:opacity .08s ease}}
    .group-line-tooltip-portal.active{{opacity:1;visibility:visible}}
    .group-line-tooltip-portal .tooltip-title{{color:#d8d8d8;margin-bottom:3px;font-size:9px;font-weight:400;line-height:1.2}}
    .group-line-tooltip-portal .tooltip-body{{display:grid;grid-template-columns:auto auto;column-gap:8px;row-gap:2px;align-items:center;width:max-content}}
    .group-line-tooltip-portal .tooltip-row{{display:contents}}
    .group-line-tooltip-portal .tooltip-label{{display:flex;align-items:center;gap:5px;color:#f5f5f5;min-width:0}}
    .group-line-tooltip-portal .tooltip-dot{{width:7px;height:7px;border-radius:50%;flex:none}}
    .group-line-tooltip-portal .tooltip-value{{justify-self:end;text-align:right;margin-left:0;color:#fff;font-weight:650;font-variant-numeric:tabular-nums}}
    .group-line-tooltip-portal .tooltip-arrow{{display:var(--tooltip-arrow-display,block);position:absolute;left:var(--arrow-left,50%);top:calc(100% - 1px);transform:translateX(-50%);width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;border-top:6px solid var(--tooltip-bg,rgba(59,59,59,.96))}}
  `;
}}
function parentTooltipElement(ctx) {{
  ensureParentTooltipStyle(ctx.doc);
  const id = 'group-line-tooltip-portal-' + chartEmbedKey();
  let element = ctx.doc.getElementById(id);
  if (!element) {{
    element = ctx.doc.createElement('div');
    element.id = id;
    element.className = 'group-line-tooltip-portal';
    ctx.doc.body.appendChild(element);
  }}
  element.style.background = visual.tooltip.background;
  element.style.color = visual.tooltip.color;
  element.style.borderRadius = `${{toNumber(visual.tooltip.borderRadius, 6)}}px`;
  element.style.padding = visual.tooltip.padding || '6px 8px';
  element.style.fontSize = `${{toNumber(visual.tooltip.fontSize, 10)}}px`;
  element.style.maxWidth = `min(${{toNumber(visual.tooltip.maxWidth, 220)}}px, calc(100vw - 24px))`;
  element.style.setProperty('--tooltip-bg', visual.tooltip.background);
  return element;
}}
function postTooltipPortal(action, payload = {{}}) {{
  try {{
    if (!window.parent || window.parent === window) return false;
    window.parent.postMessage({{
      type: 'group-line-tooltip-portal',
      action,
      chartEmbed: chartEmbedKey(),
      ...payload
    }}, '*');
    return true;
  }} catch (error) {{
    return false;
  }}
}}
window.addEventListener('message', event => {{
  if (event.data?.type !== 'group-line-tooltip-portal-ack') return;
  tooltipPortalAckUntil = Date.now() + 250;
  if (event.data.action === 'show') tooltip.classList.remove('active');
}});
function styleForSeries(series) {{
  const candidates = [
    ...(Array.isArray(visual.seriesStyle) ? visual.seriesStyle : []),
    ...(Array.isArray(visual.emphasis) ? visual.emphasis : [])
  ];
  return candidates.find(item => {{
    if (!isObject(item)) return false;
    const keys = [item.field, item.series, item.name, item.label].filter(Boolean).map(String);
    return keys.includes(String(series.field)) || keys.includes(String(series.label));
  }}) || {{}};
}}
function styledSeries() {{
  return (config.series || []).map((series, index) => {{
    const style = styleForSeries(series);
    return {{
      ...series,
      axis: style.axis || series.axis || 'left',
      label: style.label || series.label || series.field,
      color: chartColor(style.color || series.color || {json.dumps(PALETTE)}[index % {len(PALETTE)}]),
      strokeWidth: toNumber(style.strokeWidth, toNumber(visual.line.strokeWidth, 2.4)),
      opacity: toNumber(style.opacity, toNumber(visual.line.opacity, 1)),
      dasharray: style.dasharray || visual.line.dasharray || '',
      showPoints: style.showPoints ?? visual.line.showPoints,
      pointRadius: toNumber(style.pointRadius ?? style.radius, toNumber(visual.point.radius, 3.2)),
      outerRadius: toNumber(style.outerRadius, toNumber(visual.point.outerRadius, 5))
    }};
  }});
}}
function axisConfig(side) {{
  const base = side === 'right' ? config.rightAxis : config.leftAxis;
  const patch = side === 'right' ? visual.rightAxis : visual.leftAxis;
  return deepMerge(deepMerge(base || {{}}, visual.yAxis || {{}}), patch || {{}});
}}
function axisForSeries(item) {{
  return axisConfig(item?.axis === 'right' ? 'right' : 'left');
}}
function formatterLocale() {{
  const localeText = [
    config.locale,
    config.language,
    config.title,
    config.subtitle,
    config.yLabel,
    ...(config.series || []).map(item => item.label || item.name || item.field || '')
  ].filter(Boolean).join(' ');
  return /[\u4e00-\u9fff]/.test(localeText) ? 'zh-CN'
    : /[\u3040-\u30ff]/.test(localeText) ? 'ja-JP'
    : /[\uac00-\ud7af]/.test(localeText) ? 'ko-KR'
    : 'en-US';
}}
function compactUnitCandidates(locale) {{
  if (locale === 'ja-JP') return [['兆', 1e12], ['億', 1e8], ['万', 1e4]];
  if (locale === 'ko-KR') return [['조', 1e12], ['억', 1e8], ['만', 1e4]];
  if (locale === 'zh-CN') return [['万亿', 1e12], ['亿', 1e8], ['万', 1e4]];
  return [['T', 1e12], ['B', 1e9], ['M', 1e6], ['K', 1e3]];
}}
function axisCompactOptions(values, axis = {{}}, options = {{}}) {{
  if (options.showUnit !== false && (axis.prefix || axis.suffix)) return {{ compact: false }};
  const finite = values.map(Number).filter(Number.isFinite);
  if (!finite.length || !finite.every(value => Math.abs(value) >= 1e4)) return {{ compact: false }};
  const candidates = compactUnitCandidates(formatterLocale());
  const matched = candidates.find(([, threshold]) => finite.every(value => Math.abs(value) >= threshold));
  return matched ? {{ compactUnit: matched[0], compactDivisor: matched[1] }} : {{ compact: false }};
}}
function valueFormatter(value, axis, series = {{}}, options = {{}}) {{
  const digits = Number.isFinite(Number(series.digits)) ? Number(series.digits)
    : Number.isFinite(Number(axis.digits)) ? Number(axis.digits)
    : 2;
  const prefix = options.showUnit === false ? '' : (series.prefix || axis.prefix || '');
  const suffix = options.showUnit === false ? '' : (series.suffix || axis.suffix || '');
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '';
  if (numeric === 0) return prefix + '0.00' + suffix;
  const locale = formatterLocale();
  const abs = Math.abs(numeric);
  let scaled = numeric;
  let compactUnit = '';
  if (!suffix) {{
    if (options.compactUnit && Number.isFinite(Number(options.compactDivisor)) && Number(options.compactDivisor) > 1) {{
      compactUnit = String(options.compactUnit);
      scaled = numeric / Number(options.compactDivisor);
    }} else if (options.compact !== false) {{
      const matched = compactUnitCandidates(locale).find(([, threshold]) => abs >= Math.max(threshold, 1e4));
      if (matched) {{ compactUnit = matched[0]; scaled = numeric / matched[1]; }}
    }}
  }}
  const safeDigits = Math.max(2, digits);
  return prefix + scaled.toLocaleString(locale, {{ minimumFractionDigits: safeDigits, maximumFractionDigits: safeDigits }}) + compactUnit + suffix;
}}
function uniqueFormattedTicks(ticks, axis) {{
  const compactOptions = axisCompactOptions(ticks, axis);
  const formatted = ticks.map(value => valueFormatter(value, axis, {{}}, compactOptions));
  if (new Set(formatted).size === formatted.length) return formatted;
  const expandedAxis = {{ ...(axis || {{}}) }};
  for (let digits = 2; digits <= 4; digits += 1) {{
    expandedAxis.digits = digits;
    const retry = ticks.map(value => valueFormatter(value, expandedAxis, {{}}, compactOptions));
    if (new Set(retry).size === retry.length) return retry;
  }}
  return ticks.map(value => String(Number(value).toPrecision(4)) + (axis?.suffix || ''));
}}
function estimateAxisWidth(labels) {{
  const widthFor = label => Array.from(String(label || '')).reduce((total, char) => {{
    if (/[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]/.test(char)) return total + 11;
    if (/[A-Z]/.test(char)) return total + 7;
    if (/[-0-9.%,+]/.test(char)) return total + 6;
    return total + 6.5;
  }}, 4);
  return Math.max(28, Math.min(78, Math.ceil(Math.max(...labels.map(widthFor), 0)) + 1));
}}
function measureAxisWidth(labels, fontSize, fontWeight) {{
  try {{
    const canvas = measureAxisWidth.canvas || (measureAxisWidth.canvas = document.createElement('canvas'));
    const ctx = canvas.getContext('2d');
    ctx.font = `${{fontWeight || 400}} ${{toNumber(fontSize, 10)}}px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif`;
    const widths = labels.map(label => ctx.measureText(String(label || '')).width);
    return Math.max(0, Math.ceil(Math.max(...widths, 0)));
  }} catch (error) {{
    return estimateAxisWidth(labels);
  }}
}}
function tooltipMeta(series = {{}}) {{
  const rawLabel = String(series.label || series.name || series.field || '');
  const explicitUnit = series.suffix || series.unit || '';
  const match = rawLabel.match(/^(.*?)\\s*[（(]\\s*([^()（）]+?)\\s*[）)]\\s*$/);
  return {{
    label: match ? match[1].trim() : rawLabel,
    unit: explicitUnit || (match ? match[2].trim() : '')
  }};
}}
function displaySeriesLabel(series = {{}}) {{
  const label = tooltipMeta(series).label || String(series.label || series.name || series.field || '');
  return label
    .replace(/\\s*[：:]\\s*[-+]?[\\d,]+(?:\\.\\d+)?\\s*[%％]?\\s*$/u, '')
    .replace(/\\s+[-+]?[\\d,]+(?:\\.\\d+)?\\s*(?:万亿|万|亿|万元|亿元|千元|元|K|M|B|T|%)?\\s*$/iu, '')
    .trim() || label;
}}
function tooltipValue(value, axis, series = {{}}, compactOptions = {{}}) {{
  const meta = tooltipMeta(series);
  return valueFormatter(value, axis, {{ ...series, suffix: meta.unit || series.suffix || series.unit || '' }}, {{ ...compactOptions, showUnit: visual.tooltip.showUnit }});
}}
function axisLegendText(axis, axisSeries = []) {{
  const explicit = axis?.description || axis?.label || axis?.title || axis?.name || axis?.axisLabel;
  if (explicit) return String(explicit);
  return '';
}}
function axisDomainIsImplausiblyWide(configuredMin, configuredMax, dataMin, dataMax) {{
  if (!Number.isFinite(configuredMin) || !Number.isFinite(configuredMax)) return false;
  if (configuredMax <= configuredMin) return true;
  const dataSpan = Math.max(dataMax - dataMin, 1e-9);
  const configuredSpan = configuredMax - configuredMin;
  if (configuredSpan <= Math.max(dataSpan * 4, dataSpan + 8)) return false;
  const upperExtra = Math.max(0, configuredMax - dataMax);
  const lowerExtra = Math.max(0, dataMin - configuredMin);
  return Math.max(upperExtra, lowerExtra) > Math.max(dataSpan * 2, 8);
}}
function extent(values, axis) {{
  const valid = values.filter(value => Number.isFinite(value));
  const dataMin = valid.length ? Math.min(...valid) : 0;
  const dataMax = valid.length ? Math.max(...valid) : 1;
  const rawMin = Number(axis.min);
  const rawMax = Number(axis.max);
  const ignoreConfiguredDomain = axisDomainIsImplausiblyWide(rawMin, rawMax, dataMin, dataMax);
  let min = ignoreConfiguredDomain ? NaN : rawMin;
  let max = ignoreConfiguredDomain ? NaN : rawMax;
  if (Number.isFinite(min)) min = Math.min(min, dataMin);
  else min = dataMin;
  if (Number.isFinite(max)) max = Math.max(max, dataMax);
  else max = dataMax;
  if (axis.includeZero !== false) {{
    if (ignoreConfiguredDomain || !Number.isFinite(Number(axis.min))) min = Math.min(0, min);
    if (ignoreConfiguredDomain || !Number.isFinite(Number(axis.max))) max = Math.max(0, max);
  }}
  if (min === max) {{ min -= 1; max += 1; }}
  const pad = toNumber(axis.padding, 0);
  if (pad) {{
    const span = max - min;
    min -= span * pad;
    max += span * pad;
  }}
  return {{ min, max }};
}}
function tickValues(range, count) {{
  const safeCount = Math.max(2, Math.round(count || 5));
  return Array.from({{ length: safeCount }}, (_, index) => range.max - (range.max - range.min) * index / (safeCount - 1));
}}
function gridRatios(count) {{
  const safeCount = Math.max(2, Math.round(count || 5));
  return Array.from({{ length: safeCount }}, (_, index) => index / (safeCount - 1));
}}
function valueAtRatio(range, ratio) {{
  return range.max - (range.max - range.min) * ratio;
}}
function shouldShowXLabel(index, total, plotW) {{
  const every = visual.xAxis.labelEvery;
  if (every === false || every === 0) return false;
  if (Number.isFinite(Number(every)) && Number(every) > 0) return index % Number(every) === 0;
  if (total <= 3) return true;
  return index === 0 || index === Math.floor((total - 1) / 2) || index === total - 1;
}}
function xLabelAnchor(index, total) {{
  if (index === 0) return 'start';
  if (index === total - 1) return 'end';
  return 'middle';
}}
function xLabelPosition(index, total, centerX, margin, plotW) {{
  if (index === 0) return margin.left;
  if (index === total - 1) return margin.left + plotW;
  return centerX;
}}
function applyView(viewKey) {{
  if (!config.views?.[viewKey]) return;
  activeView = viewKey;
  const view = config.views[viewKey];
  config.data = view.data;
  config.leftAxis = view.leftAxis || config.leftAxis;
  config.rightAxis = view.rightAxis || config.rightAxis;
  tabs.querySelectorAll('.chart-tab').forEach(tab => tab.setAttribute('aria-selected', String(tab.dataset.value === viewKey)));
  hideTooltip();
  render();
}}
Object.entries(config.views || {{}}).forEach(([key, view]) => {{
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'chart-tab';
  button.role = 'tab';
  button.dataset.value = key;
  button.textContent = view.label;
  button.setAttribute('aria-selected', String(key === activeView));
  button.addEventListener('click', () => applyView(key));
  tabs.appendChild(button);
}});
if (activeView) {{
  const view = config.views[activeView];
  config.data = view.data;
  config.leftAxis = view.leftAxis || config.leftAxis;
  config.rightAxis = view.rightAxis || config.rightAxis;
}}
function pathFromPoints(points, curve) {{
  const valid = points.filter(Boolean);
  if (!valid.length) return '';
  if (curve === 'step-after' || curve === 'step-before') {{
    let d = '', previous = null;
    for (const point of points) {{
      if (!point) {{ if (config.missingValuePolicy === 'gap' || visual.line.connectNulls === false) previous = null; continue; }}
      if (!previous) d += ` M ${{point[0]}} ${{point[1]}}`;
      else if (curve === 'step-after') d += ` H ${{point[0]}} V ${{point[1]}}`;
      else d += ` V ${{point[1]}} H ${{point[0]}}`;
      previous = point;
    }}
    return d.trim();
  }}
  if (config.missingValuePolicy !== 'gap' && visual.line.connectNulls !== false && curve !== 'smooth') {{
    return valid.map((point, index) => `${{index === 0 ? 'M' : 'L'}} ${{point[0]}} ${{point[1]}}`).join(' ');
  }}
  if (curve === 'smooth' && config.missingValuePolicy === 'gap' && points.some(point => !point)) {{
    const segments = []; let current = [];
    for (const point of points) {{ if (point) current.push(point); else if (current.length) {{ segments.push(current); current = []; }} }}
    if (current.length) segments.push(current);
    return segments.map(segment => pathFromPoints(segment, curve)).join(' ');
  }}
  if (curve !== 'smooth' || valid.length < 3) {{
    return points.map((point, index) => point ? `${{index === 0 || !points[index - 1] ? 'M' : 'L'}} ${{point[0]}} ${{point[1]}}` : '').join(' ');
  }}
  let d = `M ${{valid[0][0]}} ${{valid[0][1]}}`;
  for (let index = 1; index < valid.length; index += 1) {{
    const prev = valid[index - 1];
    const current = valid[index];
    const midX = (prev[0] + current[0]) / 2;
    d += ` C ${{midX}} ${{prev[1]}}, ${{midX}} ${{current[1]}}, ${{current[0]}} ${{current[1]}}`;
  }}
  return d;
}}
function drawMarker(parent, x, y, overlay, color) {{
  const shape = String(overlay.shape || overlay.symbol || 'circle').toLowerCase();
  const radius = toNumber(overlay.radius, 4.2);
  if (shape === 'rect' || shape === 'square') {{
    const width = toNumber(overlay.width, radius * 2.4);
    const height = toNumber(overlay.height, shape === 'square' ? width : radius * 2);
    parent.appendChild(svgEl('rect', {{ x: x - width / 2, y: y - height / 2, width, height, rx: toNumber(overlay.borderRadius, 2), fill: color, stroke: overlay.stroke || '#fff', 'stroke-width': toNumber(overlay.strokeWidth, 1.5) }}));
    return;
  }}
  if (shape === 'star') {{
    const points = [];
    for (let index = 0; index < 10; index += 1) {{
      const angle = -Math.PI / 2 + index * Math.PI / 5;
      const r = index % 2 === 0 ? radius * 1.35 : radius * 0.58;
      points.push(`${{x + Math.cos(angle) * r}},${{y + Math.sin(angle) * r}}`);
    }}
    parent.appendChild(svgEl('polygon', {{ points: points.join(' '), fill: color, stroke: overlay.stroke || '#fff', 'stroke-width': toNumber(overlay.strokeWidth, 1.5) }}));
    return;
  }}
  parent.appendChild(svgEl('circle', {{ cx: x, cy: y, r: radius, fill: color, stroke: overlay.stroke || '#fff', 'stroke-width': toNumber(overlay.strokeWidth, 1.5) }}));
}}
let eventLabelLayout = {{points:[], segments:[], boxes:[]}};
function eventTextWidth(text, size, weight) {{
  const canvas=eventTextWidth.canvas||(eventTextWidth.canvas=document.createElement('canvas'));
  const ctx=canvas.getContext('2d');
  ctx.font=`${{weight}} ${{size}}px ${{getComputedStyle(root).fontFamily}}`;
  return ctx.measureText(String(text||'')).width;
}}
function eventTextLines(text, maxWidth, size, weight) {{
  const tokens=String(text||'').match(/[-+]?\\d+(?:\\.\\d+)?%?|[A-Za-z]+|./gu)||[];
  const lines=[];let line='';
  for(const token of tokens){{
    if(line && eventTextWidth(line+token,size,weight)>maxWidth){{lines.push(line);line='';}}
    line+=token;
  }}
  if(line)lines.push(line);
  return lines.length?lines:[''];
}}
function segmentHitsBox(a,b,r) {{
  let lo=0,hi=1;const dx=b[0]-a[0],dy=b[1]-a[1];
  for(const [p,q] of [[-dx,a[0]-r.x],[dx,r.x+r.w-a[0]],[-dy,a[1]-r.y],[dy,r.y+r.h-a[1]]]){{
    if(p===0){{if(q<0)return false;continue;}}
    const t=q/p;if(p<0)lo=Math.max(lo,t);else hi=Math.min(hi,t);
    if(lo>hi)return false;
  }}
  return true;
}}
function eventLabelNaturalWidth(overlay,dateText,titleText,availableWidth) {{
  const fontSize=toNumber(overlay.fontSize,11),dateFontSize=toNumber(overlay.dateFontSize,Math.max(9,fontSize-1));
  const weight=toNumber(overlay.titleFontWeight,600),dateWeight=toNumber(overlay.dateFontWeight,500);
  const padding=toNumber(overlay.labelPaddingX,8);
  const cap=Math.max(40,Math.min(toNumber(overlay.labelMaxWidth,210),availableWidth));
  const lines=eventTextLines(titleText,cap-2*padding,fontSize,weight),dates=eventTextLines(dateText,cap-2*padding,dateFontSize,dateWeight);
  return Math.min(cap,Math.max(Math.min(toNumber(overlay.labelMinWidth,108),cap),2*padding+Math.max(...lines.map(t=>eventTextWidth(t,fontSize,weight)),...dates.map(t=>eventTextWidth(t,dateFontSize,dateWeight)))));
}}
function eventLabelNaturalHeight(overlay,dateText,titleText,width) {{
  const fontSize=toNumber(overlay.fontSize,11),dateFontSize=toNumber(overlay.dateFontSize,Math.max(9,fontSize-1));
  const weight=toNumber(overlay.titleFontWeight,600),dateWeight=toNumber(overlay.dateFontWeight,500);
  const textWidth=width-2*toNumber(overlay.labelPaddingX,8);
  return 2*toNumber(overlay.labelPaddingY,6)+toNumber(overlay.labelLineGap,4)+eventTextLines(dateText,textWidth,dateFontSize,dateWeight).length*dateFontSize*1.3+eventTextLines(titleText,textWidth,fontSize,weight).length*fontSize*1.3;
}}
function drawEventLabel(parent,x,y,overlay,color,dateText,titleText,bounds) {{
  const inset=overlay.mobileGrid?8:12;
  bounds={{left:bounds.left+inset,right:bounds.right-inset,top:bounds.top+inset,bottom:bounds.bottom-inset}};
  const fontSize=toNumber(overlay.fontSize,11),dateFontSize=toNumber(overlay.dateFontSize,Math.max(9,fontSize-1));
  const weight=toNumber(overlay.titleFontWeight,600),dateWeight=toNumber(overlay.dateFontWeight,500);
  const px=toNumber(overlay.labelPaddingX,8),py=toNumber(overlay.labelPaddingY,6),lineGap=toNumber(overlay.labelLineGap,4);
  const cap=Math.max(40,Math.min(toNumber(overlay.timelineWidth,toNumber(overlay.labelMaxWidth,210)),bounds.right-bounds.left));
  const lines=eventTextLines(titleText,cap-2*px,fontSize,weight);
  const dates=eventTextLines(dateText,cap-2*px,dateFontSize,dateWeight);
  const width=Math.min(cap,Math.max(overlay.timelineWidth?cap:Math.min(toNumber(overlay.labelMinWidth,108),cap),2*px+Math.max(...lines.map(t=>eventTextWidth(t,fontSize,weight)),...dates.map(t=>eventTextWidth(t,dateFontSize,dateWeight)))));
  const titleHeight=fontSize*1.3,dateHeight=dateFontSize*1.3;
  const height=2*py+dates.length*dateHeight+lineGap+lines.length*titleHeight;
  const gap=toNumber(overlay.labelGap,12),preferBelow=overlay.labelPosition==='bottom';
  // Prefer the exact-date anchor; search both axes when it would cover data.
  const anchorWidth=Math.min(bounds.right-bounds.left,toNumber(overlay.timelineWidth,210));
  const center=Math.max(bounds.left+anchorWidth/2,Math.min(bounds.right-anchorWidth/2,x));
  const anchorX=Math.max(bounds.left,Math.min(bounds.right-width,center-width/2));
  const horizontal=[anchorX];
  for(let x=bounds.left;x<=bounds.right-width;x+=12)horizontal.push(x);
  horizontal.push(bounds.right-width);
  const rowHeight=toNumber(overlay.timelineRowHeight,height);
  const candidates=[];
  for(let by=bounds.top;by<=bounds.bottom-rowHeight;by+=4)candidates.push(by);
  if(!candidates.length)candidates.push(bounds.top);
  let best=null;
  for(const bx of horizontal)for(const candidate of candidates){{
    if(overlay.allowCurveOverlap && Math.abs(bx-anchorX)>width*.65)continue;
    const by=Math.max(bounds.top,Math.min(bounds.bottom-height,candidate));
    const below=by+height/2>y;
    const r={{x:bx,y:by,w:width,h:height}},padded={{x:bx-4,y:by-4,w:width+8,h:height+8}};
    let score=Math.abs(bx+width/2-x)*(overlay.allowCurveOverlap ? .10 : .06)+Math.abs(by+height/2-y)*(overlay.allowCurveOverlap ? .008 : .03)+(below===preferBelow?0:2);
    for(const old of eventLabelLayout.boxes){{const area=Math.max(0,Math.min(bx+width+4,old.x+old.w)-Math.max(bx-4,old.x))*Math.max(0,Math.min(by+height+4,old.y+old.h)-Math.max(by-4,old.y));if(area)score+=100000000+area;}}
    for(const point of (overlay.allowCurveOverlap?eventLabelLayout.anchorPoints||[]:eventLabelLayout.points))if(point[0]>=padded.x&&point[0]<=padded.x+padded.w&&point[1]>=padded.y&&point[1]<=padded.y+padded.h)score+=100000;
    for(const [a,b] of eventLabelLayout.segments)if(segmentHitsBox(a,b,padded)){{
      // Allowed overlap remains a soft cost: prefer vertical gaps over hiding the curve.
      score+=overlay.allowCurveOverlap?Math.hypot(b[0]-a[0],b[1]-a[1])*2:10000;
    }}
    if(!best||score<best.score)best={{...r,score}};
  }}
  if(overlay.outsideBox)best={{x:overlay.outsideBox.x,y:overlay.outsideBox.y,w:width,h:height}};
  eventLabelLayout.boxes.push(best);
  const below=best.y+height/2>y,anchorY=below?best.y:best.y+height;
  (eventLabelLayout.connectorLayer || parent).appendChild(svgEl('line',{{x1:x,y1:y+(below?1:-1)*toNumber(overlay.radius,4.2),x2:Math.max(best.x+px,Math.min(best.x+width-px,x)),y2:anchorY,stroke:overlay.connectorColor||overlay.borderColor||color,'stroke-width':toNumber(overlay.connectorWidth,1),opacity:toNumber(overlay.connectorOpacity,.8),'pointer-events':'none'}}));
  const group=svgEl('g',{{class:'event-label-box','data-placement':overlay.labelPlacement||'inside','pointer-events':'none'}});
  group.appendChild(svgEl('rect',{{x:best.x,y:best.y,width,height,rx:toNumber(overlay.labelBorderRadius,4),fill:overlay.labelBackground||'var(--chart-point-fill)',stroke:overlay.borderColor||'#d0d5dd','stroke-width':toNumber(overlay.labelBorderWidth,1),opacity:toNumber(overlay.labelOpacity,.98)}}));
  dates.forEach((t,i)=>group.appendChild(svgEl('text',{{x:best.x+px,y:best.y+py+dateFontSize+i*dateHeight,fill:overlay.dateColor||'var(--chart-text-secondary)','font-size':dateFontSize,'font-weight':dateWeight}},t)));
  lines.forEach((t,i)=>group.appendChild(svgEl('text',{{x:best.x+px,y:best.y+py+dates.length*dateHeight+lineGap+fontSize+i*titleHeight,fill:overlay.titleColor||overlay.labelColor||'var(--chart-text-primary)','font-size':fontSize,'font-weight':weight}},t)));
  parent.appendChild(group);
}}
function drawOutsideTopEventLabels(events,parent) {{
  if(!events.length){{stage.style.marginTop='';return;}}
  const ordered=[...events].sort((a,b)=>a[1]-b[1]);
  const bounds=ordered[0][7],gap=8,available=Math.max(40,bounds.right-bounds.left-24);
  const columns=Math.max(1,Math.min(ordered.length,Math.floor((available+gap)/(108+gap))));
  const natural=Math.max(...ordered.map(args=>eventLabelNaturalWidth(args[3],args[5],args[6],available)));
  const cardWidth=Math.min(natural,(available-gap*(columns-1))/columns);
  const rowHeights=[];
  ordered.forEach((args,i)=>{{const row=Math.floor(i/columns);rowHeights[row]=Math.max(rowHeights[row]||0,eventLabelNaturalHeight(args[3],args[5],args[6],cardWidth));}});
  const laneHeight=rowHeights.reduce((sum,h)=>sum+h,0)+gap*Math.max(0,rowHeights.length-1)+24;
  stage.style.marginTop=`${{4+laneHeight}}px`;
  const groupWidth=columns*cardWidth+gap*(columns-1);
  const startX=Math.max(bounds.left+12,Math.min(bounds.right-12-groupWidth,ordered[0][1]-cardWidth/2));
  ordered.forEach((event,i)=>{{
    const args=[...event],row=Math.floor(i/columns),col=i%columns;
    const y=-laneHeight+12+rowHeights.slice(0,row).reduce((sum,h)=>sum+h+gap,0);
    args[3]={{...args[3],timelineWidth:cardWidth,outsideBox:{{x:startX+col*(cardWidth+gap),y}}}};
    // The entire card is above SVG y=0, outside every curve/grid coordinate.
    drawEventLabel(...args);
  }});
}}
function shouldShowLineLegend(series){{
  return visual.legend.visible!==false && (series.length>1 || config.visualStyle?.legend?.visible===true);
}}
function eventMarkColor(overlay,lineColor) {{
  const hue=color=>{{
    let raw=String(color||'').replace('#','');if(raw.length===3)raw=[...raw].map(c=>c+c).join('');
    if(!/^[0-9a-f]{{6}}$/i.test(raw))return null;
    const [r,g,b]=[0,2,4].map(i=>parseInt(raw.slice(i,i+2),16)/255),max=Math.max(r,g,b),min=Math.min(r,g,b),d=max-min;
    if(d<.12)return null;
    return (((max===r?(g-b)/d:max===g?(b-r)/d+2:(r-g)/d+4)*60)%360+360)%360;
  }};
  const lineHue=hue(lineColor),given=overlay.color,markHue=hue(given);
  if(given && String(given).toLowerCase()!==String(lineColor).toLowerCase() && (lineHue===null||markHue===null||Math.min(Math.abs(lineHue-markHue),360-Math.abs(lineHue-markHue))>=40))return given;
  return lineHue!==null && lineHue>=160 && lineHue<=310?'#f97316':'#8b5cf6';
}}
let mobileEventKey='';
function drawMobileEventLabels(events,svg) {{
  if(!events.length)return;
  const ordered=[...events].sort((a,b)=>a[1]-b[1]),keyOf=e=>`${{e[5]}}|${{e[6]}}`;
  let active=ordered.findIndex(e=>keyOf(e)===mobileEventKey);
  if(active<0)active=ordered.length-1;
  const labels=svgEl('g',{{class:'mobile-event-labels','pointer-events':'none'}});
  svg.appendChild(labels);
  const rings=[],hits=[];
  const select=index=>{{
    mobileEventKey=keyOf(ordered[index]);
    labels.replaceChildren();eventLabelLayout.connectorLayer.replaceChildren();eventLabelLayout.boxes=[];
    const chosen=[...new Set([ordered.length-1,index,...ordered.map((_,i)=>i).reverse()])].slice(0,3);
    const selected=chosen.map(i=>{{
      const args=[...ordered[i]];args[0]=labels;
      const labelWidth=Math.min(108,Math.max(64,(args[7].right-args[7].left-24)/3-8));
      args[3]={{...args[3],mobileGrid:true,timelineWidth:labelWidth,labelMinWidth:labelWidth,labelMaxWidth:labelWidth,fontSize:11,dateFontSize:10,borderColor:i===index?args[4]:undefined}};
      return args;
    }}).sort((a,b)=>a[1]-b[1]);
    const rowHeight=Math.max(...selected.map(args=>eventLabelNaturalHeight(args[3],args[5],args[6],args[3].timelineWidth)));
    selected.forEach(args=>{{args[3].timelineRowHeight=rowHeight;drawEventLabel(...args);}});
    rings.forEach((ring,i)=>ring.setAttribute('opacity',i===index?'1':'0'));
    hits.forEach((hit,i)=>hit.setAttribute('aria-pressed',String(i===index)));
    postChartHeight();
  }};
  ordered.forEach((event,index)=>{{
    const [,x,y,overlay,color,dateText,titleText]=event;
    const ring=svgEl('circle',{{cx:x,cy:y,r:toNumber(overlay.radius,4.2)+3,fill:'none',stroke:color,'stroke-width':2,'pointer-events':'none',class:'mobile-event-ring'}});
    const hit=svgEl('circle',{{cx:x,cy:y,r:14,fill:'transparent','pointer-events':'all',class:'mobile-event-hit',role:'button',tabindex:0,'aria-label':`${{dateText}} ${{titleText}}`}});
    hit.addEventListener('pointerdown',event=>{{event.stopPropagation();hideTooltip();select(index);}});
    hit.addEventListener('click',event=>{{event.stopPropagation();select(index);}});
    hit.addEventListener('keydown',event=>{{if(event.key==='Enter'||event.key===' '){{event.preventDefault();select(index);}}}});
    rings.push(ring);hits.push(hit);svg.append(ring,hit);
  }});
  select(active);
}}
function drawSvgLegend(series, width, height, topY = null) {{
  const split = visual.line.colorSplit;
  if (isObject(split) && config.data.some(row=>String(row[config.xField])===String(split.x))) {{
    series=series.flatMap(item=>(!split.field||split.field===item.field)?[
      {{...item,label:split.beforeLabel||item.label,color:split.beforeColor||item.color}},
      {{...item,label:split.afterLabel||item.label,color:split.afterColor||item.color}}
    ]:[item]);
  }}
  if (!shouldShowLineLegend(series)) {{
    legend.style.display = 'none';
    return;
  }}
  legend.style.display = 'none';
  const group = svgEl('g', {{ class: 'svg-legend', 'font-family': 'inherit' }});
  const isNarrow = width < 680;
  const markerSize = toNumber(visual.legend.markerSize, 8);
  const fontSize = toNumber(visual.legend.fontSize, 10);
  const itemGap = isNarrow ? Math.max(14, toNumber(visual.legend.gap, 20) - 6) : toNumber(visual.legend.gap, 20);
  const rowGap = Math.max(isNarrow ? 15 : 18, fontSize + (isNarrow ? 5 : 7));
  const sidePad = isNarrow ? 18 : 28;
  const maxRowWidth = Math.max(120, width - sidePad * 2);
  const maxItemTextWidth = isNarrow ? Math.max(70, Math.min(156, maxRowWidth / Math.min(2, Math.max(1, series.length)) - markerSize - 13)) : Math.min(300, Math.max(180, maxRowWidth / Math.min(2, Math.max(1, series.length)) - markerSize - itemGap - 12));
  const isWideChar = char => /[\u1100-\u115f\u2329\u232a\u2e80-\u303e\u3040-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe10-\ufe19\ufe30-\ufe6f\uff00-\uff60\uffe0-\uffe6]/.test(char);
  const measureLabel = label => Array.from(String(label || '')).reduce((sum, char) => sum + fontSize * (isWideChar(char) ? 1 : 0.56), 0);
  const trimLabel = label => {{
    const text = String(label || '');
    if (measureLabel(text) <= maxItemTextWidth) return text;
    const chars = Array.from(text);
    let width = fontSize;
    let output = '';
    for (const char of chars) {{
      const nextWidth = width + fontSize * (isWideChar(char) ? 1 : 0.56);
      if (nextWidth > maxItemTextWidth) break;
      output += char;
      width = nextWidth;
    }}
    return (output || chars.slice(0, 2).join('')) + '…';
  }};
  const items = series.map(item => {{
    const label = String(item.label || item.field || '');
    const displayLabel = trimLabel(displaySeriesLabel(item));
    const textWidth = Math.min(maxItemTextWidth, Math.max(24, measureLabel(displayLabel)));
    return {{ item, label, displayLabel, width: markerSize + 7 + textWidth + 2 }};
  }});
  const rows = [[]];
  let rowWidth = 0;
  items.forEach(entry => {{
    const nextWidth = rowWidth ? rowWidth + itemGap + entry.width : entry.width;
    if (rows.at(-1).length && nextWidth > maxRowWidth) {{
      rows.push([entry]);
      rowWidth = entry.width;
    }} else {{
      rows.at(-1).push(entry);
      rowWidth = nextWidth;
    }}
  }});
  const legendHeight = (rows.length - 1) * rowGap;
  const firstRowY = Number.isFinite(Number(topY)) ? Number(topY) + fontSize : height - (isNarrow ? 10 : 15) - legendHeight;
  rows.forEach((row, rowIndex) => {{
    const totalWidth = row.reduce((sum, entry) => sum + entry.width, 0) + Math.max(0, row.length - 1) * itemGap;
    let cursorX = (width - totalWidth) / 2;
    if (visual.legend.align === 'left') cursorX = sidePad;
    if (visual.legend.align === 'right') cursorX = width - totalWidth - sidePad;
    cursorX = Math.max(sidePad, cursorX);
    const y = firstRowY + rowIndex * rowGap;
    row.forEach(entry => {{
      group.appendChild(svgEl('circle', {{ cx: cursorX + markerSize / 2, cy: y - markerSize / 2 + 1, r: markerSize / 2, fill: entry.item.color }}));
      const text = svgEl('text', {{ x: cursorX + markerSize + 7, y: y, fill: visual.legend.color, 'font-size': fontSize, 'font-weight': 400 }}, entry.displayLabel);
      if (entry.displayLabel !== entry.label) text.appendChild(svgEl('title', {{}}, entry.label));
      group.appendChild(text);
      cursorX += entry.width + itemGap;
    }});
  }});
  svg.appendChild(group);
}}
function render() {{
  applyComponentStyles();
  svg.innerHTML = '';
  legend.innerHTML = '';
  const width = Math.max(svg.clientWidth || 900, toNumber(visual.chart.minWidth, 360));
  root.classList.toggle('has-event-marks', (Array.isArray(visual.overlays)?visual.overlays:[]).some(item=>(item.labelBox||item.eventTitle||item.eventTime)&&item.labelPlacement!=='outside-top'));
  const height = Math.max(svg.clientHeight || (toNumber(visual.chart.height, config.height) - 58), 160);
  svg.setAttribute('viewBox', `0 0 ${{width}} ${{height}}`);
  const isMobile = width < 680;
  const safeInset = 12;
  const rows = Array.isArray(config.data) ? config.data : [];
  const series = styledSeries();
  eventLabelLayout = {{points:[],segments:[],boxes:[]}};
  const pendingEventLabels = [];
  if (!rows.length || !series.length) return;
  const leftAxis = axisConfig('left');
  const rightAxis = axisConfig('right');
  const yAxisPosition = String(visual.yAxis.position || 'right').toLowerCase();
  const leftSeries = series.filter(item => item.axis !== 'right');
  const rightSeries = series.filter(item => item.axis === 'right');
  const leftVisibleExplicit = Object.prototype.hasOwnProperty.call(leftAxis || {{}}, 'visible');
  const rightVisibleExplicit = Object.prototype.hasOwnProperty.call(rightAxis || {{}}, 'visible');
  const showLeftAxis = leftVisibleExplicit
    ? leftAxis.visible !== false
    : (leftSeries.length > 0 && ['left', 'both'].includes(yAxisPosition));
  const showRightAxis = rightVisibleExplicit
    ? rightAxis.visible !== false
    : (rightSeries.length > 0 ? ['right', 'both'].includes(yAxisPosition) : ['right', 'both'].includes(yAxisPosition));
  const leftExtent = leftSeries.length ? extent(leftSeries.flatMap(item => rows.map(row => number(row[item.field])).filter(value => value !== null)), leftAxis) : null;
  const rightExtent = rightSeries.length ? extent(rightSeries.flatMap(item => rows.map(row => number(row[item.field])).filter(value => value !== null)), rightAxis) : (leftExtent || extent([], rightAxis));
  const primaryAxisSide = showLeftAxis && leftExtent ? 'left' : 'right';
  const primaryExtent = primaryAxisSide === 'left' ? leftExtent : rightExtent;
  const primaryAxis = primaryAxisSide === 'left' ? leftAxis : rightAxis;
  const gridCount = Math.max(2, Math.round(toNumber(visual.grid.count, config.gridCount || 5)));
  const gridStops = gridRatios(gridCount);
  const ticks = gridStops.map(ratio => valueAtRatio(primaryExtent, ratio));
  const leftTickValues = gridStops.map(ratio => primaryAxisSide === 'left' || !leftExtent ? valueAtRatio(primaryExtent, ratio) : valueAtRatio(leftExtent, ratio));
  const rightTickValues = gridStops.map(ratio => primaryAxisSide === 'right' ? valueAtRatio(primaryExtent, ratio) : valueAtRatio(rightExtent, ratio));
  const leftTickLabels = uniqueFormattedTicks(leftTickValues, leftAxis);
  const rightTickLabels = uniqueFormattedTicks(rightTickValues, rightAxis);
  const baseMargin = width >= 1200
    ? {{ top: 8, right: 112, bottom: 46, left: 24 }}
    : isMobile ? {{ top: 8, right: 18, bottom: 30, left: 18 }} : visual.margin;
  const margin = {{
    top: toNumber(baseMargin.top, 8),
    right: toNumber(baseMargin.right, 0),
    bottom: toNumber(baseMargin.bottom, 46),
    left: toNumber(baseMargin.left, 24)
  }};
  margin.top = Math.max(0, Number(margin.top) || 0);
  ['right','bottom','left'].forEach(key => margin[key] = Math.max(safeInset, Number(margin[key]) || safeInset));
  const leftAxisLegend = showLeftAxis ? axisLegendText(leftAxis, leftSeries) : '';
  const rightAxisLegend = showRightAxis ? axisLegendText(rightAxis, rightSeries) : '';
  const rightAxisGap = isMobile ? 8 : 10;
  const leftTickWidth = showLeftAxis ? measureAxisWidth(leftTickLabels, visual.yAxis.fontSize, visual.yAxis.fontWeight) : 0;
  const rightTickWidth = showRightAxis ? measureAxisWidth(rightTickLabels, visual.yAxis.fontSize, visual.yAxis.fontWeight) : 0;
  if (showLeftAxis) margin.left = Math.max(margin.left, leftTickWidth + (isMobile ? 6 : 10));
  if (!showLeftAxis) margin.left = safeInset;
  if (showRightAxis) margin.right = Math.max(0, rightTickWidth + rightAxisGap);
  if (leftAxisLegend || rightAxisLegend) {{
    const axisFontSize = toNumber(visual.yAxis.fontSize, 12);
    margin.top = Math.max(margin.top, axisFontSize * 2 + 12);
  }}
  if (shouldShowLineLegend(series)) {{
    const estimatedLegendRows = isMobile && series.length > 2 ? 2 : 1;
    const xAxisFontSize = toNumber(visual.xAxis.fontSize, 10);
    const legendFontSize = toNumber(visual.legend.fontSize, 10);
    const legendRowGap = Math.max(isMobile ? 15 : 18, legendFontSize + (isMobile ? 5 : 7));
    margin.bottom = Math.max(margin.bottom, xAxisFontSize + 6 + legendFontSize + Math.max(0, estimatedLegendRows - 1) * legendRowGap + 8);
  }}
  const plotW = Math.max(isMobile ? 96 : 140, width - margin.left - margin.right);
  const plotH = Math.max(isMobile ? 96 : 110, height - margin.top - margin.bottom);
  const rightAxisX = margin.left + plotW + rightAxisGap + rightTickWidth;
  const rightAxisLegendX = rightAxisX;
  const yLeft = value => {{
    const range = leftExtent || rightExtent;
    return margin.top + (range.max - value) / (range.max - range.min) * plotH;
  }};
  const yRight = value => margin.top + (rightExtent.max - value) / (rightExtent.max - rightExtent.min) * plotH;
  const yFor = item => item.axis === 'right' ? yRight : yLeft;
  const axisFor = item => item.axis === 'right' ? rightAxis : leftAxis;
  const pointCount = Math.max(rows.length, 1);
  const stepX = pointCount > 1 ? plotW / (pointCount - 1) : 0;
  const xValues = config.xScale === 'time' ? rows.map(row => typeof row[config.xField] === 'number' ? row[config.xField] : Date.parse(row[config.xField]))
    : config.xScale === 'linear' ? rows.map(row => Number(row[config.xField])) : null;
  if (xValues && (xValues.some(value => !Number.isFinite(value)) || xValues.some((value,index) => index && value < xValues[index-1]))) throw new Error('Continuous x values must be valid and sorted');
  const firstX = xValues?.[0], lastX = xValues?.at(-1);
  const centerX = index => xValues ? lastX > firstX ? margin.left + (xValues[index]-firstX)/(lastX-firstX)*plotW : margin.left + plotW/2
    : pointCount > 1 ? margin.left + stepX * index : margin.left + plotW / 2;
  const hoverStartX = index => {{
    if (pointCount <= 1) return margin.left;
    if (index <= 0) return margin.left;
    return (centerX(index - 1) + centerX(index)) / 2;
  }};
  const hoverEndX = index => {{
    if (pointCount <= 1) return margin.left + plotW;
    if (index >= pointCount - 1) return margin.left + plotW;
    return (centerX(index) + centerX(index + 1)) / 2;
  }};
  const hoverWidthFor = index => Math.max(1, hoverEndX(index) - hoverStartX(index));
  const xByValue = value => {{
    const index = rows.findIndex(row => String(row[config.xField]) === String(value));
    return index >= 0 ? centerX(index) : null;
  }};
  gridStops.forEach((ratio, index) => {{
    const value = ticks[index];
    const y = margin.top + ratio * plotH;
    if (visual.grid.visible !== false) {{
      svg.appendChild(svgEl('line', {{ x1: margin.left, y1: y, x2: margin.left + plotW, y2: y, class: 'grid-line', stroke: visual.grid.color, 'stroke-width': visual.grid.width, 'stroke-dasharray': visual.grid.dasharray || '', opacity: visual.grid.opacity }}));
    }}
    if (showLeftAxis) {{
      svg.appendChild(svgEl('text', {{ x: margin.left - 12, y: y + 4, class: 'axis-label', 'text-anchor': 'end', fill: visual.yAxis.color, 'font-size': visual.yAxis.fontSize, 'font-weight': visual.yAxis.fontWeight }}, leftTickLabels[index] ?? valueFormatter(leftTickValues[index], leftAxis)));
    }}
    if (showRightAxis) {{
      svg.appendChild(svgEl('text', {{ x: rightAxisX, y: y + 4, class: 'axis-label', 'text-anchor': 'end', fill: visual.yAxis.color, 'font-size': visual.yAxis.fontSize, 'font-weight': visual.yAxis.fontWeight }}, rightTickLabels[index] ?? ''));
    }}
  }});
  if (visual.xAxis.visible !== false) {{
    const axisY = margin.top + plotH;
    svg.appendChild(svgEl('line', {{ x1: margin.left, y1: axisY, x2: margin.left + plotW, y2: axisY, class: 'axis-line', stroke: visual.xAxis.lineColor, 'stroke-width': visual.xAxis.lineWidth }}));
  }}
  if (showLeftAxis) {{
    if (leftAxisLegend) {{
      const axisFontSize = toNumber(visual.yAxis.fontSize, 12);
      svg.appendChild(svgEl('text', {{ x: margin.left - 12, y: margin.top - axisFontSize - 6, class: 'axis-label y-axis-legend', 'text-anchor': 'end', fill: visual.yAxis.color, 'font-size': visual.yAxis.fontSize, 'font-weight': visual.yAxis.fontWeight }}, leftAxisLegend));
    }}
  }}
  if (showRightAxis) {{
    if (rightAxisLegend) {{
      const axisFontSize = toNumber(visual.yAxis.fontSize, 12);
      svg.appendChild(svgEl('text', {{ x: rightAxisLegendX, y: margin.top - axisFontSize - 6, class: 'axis-label y-axis-legend', 'text-anchor': 'end', fill: visual.yAxis.color, 'font-size': visual.yAxis.fontSize, 'font-weight': visual.yAxis.fontWeight }}, rightAxisLegend));
    }}
  }}
  if (visual.xAxis.visible !== false) {{
    const xAxisLabelY = margin.top + plotH + toNumber(visual.xAxis.fontSize, 10) + 6;
    rows.forEach((row, index) => {{
      if (!shouldShowXLabel(index, rows.length, plotW)) return;
      const x = centerX(index);
      const label = String(row[config.xField] ?? '');
      const textX = xLabelPosition(index, rows.length, x, margin, plotW);
      const textAnchor = xLabelAnchor(index, rows.length);
      const text = svgEl('text', {{ x: textX, y: xAxisLabelY, class: 'x-label', 'text-anchor': textAnchor, fill: visual.xAxis.color, 'font-size': visual.xAxis.fontSize, 'font-weight': visual.xAxis.fontWeight, transform: visual.xAxis.labelAngle ? `rotate(${{visual.xAxis.labelAngle}} ${{textX}} ${{xAxisLabelY}})` : '' }}, label);
      svg.appendChild(text);
    }});
  }}
  const legendTopY = visual.xAxis.visible !== false
    ? margin.top + plotH + toNumber(visual.xAxis.fontSize, 10) + 12
    : margin.top + plotH + 6;
  (Array.isArray(visual.overlays) ? visual.overlays : []).filter(item => isObject(item) && item.type === 'selectedBand').forEach(overlay => {{
    const fromX = Number.isFinite(Number(overlay.fromIndex)) ? centerX(Number(overlay.fromIndex)) : xByValue(overlay.from ?? overlay.start);
    const toX = Number.isFinite(Number(overlay.toIndex)) ? centerX(Number(overlay.toIndex)) : xByValue(overlay.to ?? overlay.end);
    if (fromX === null || toX === null) return;
    const x = Math.min(fromX, toX);
    const w = Math.max(1, Math.abs(toX - fromX));
    svg.appendChild(svgEl('rect', {{ x, y: margin.top, width: w, height: plotH, fill: overlay.color || '#f1f5f9', opacity: toNumber(overlay.opacity, 0.72) }}));
  }});
  const positionLine = svgEl('line', {{ x1: margin.left, y1: margin.top, x2: margin.left, y2: margin.top + plotH, class: 'position-line', stroke: visual.positionLine.color, 'stroke-width': toNumber(visual.positionLine.width, 1), 'stroke-dasharray': visual.positionLine.dasharray || '', opacity: 0 }});
  series.forEach(item => {{
    const points = rows.map((row, index) => {{
      const value = number(row[item.field]);
      return value === null ? null : [centerX(index), yFor(item)(value), value, row, index];
    }});
    eventLabelLayout.points.push(...points.filter(Boolean));
    for(let i=1;i<points.length;i++) if(points[i-1]&&points[i]) eventLabelLayout.segments.push([points[i-1],points[i]]);
    let lineColor=item.color;
    const split=visual.line.colorSplit;
    if(isObject(split)&&(!split.field||split.field===item.field)) {{
      const splitIndex=rows.findIndex(row=>String(row[config.xField])===String(split.x));
      if(splitIndex>=0&&points[splitIndex]) {{
        const id='line-split-'+series.indexOf(item);
        const defs=svgEl('defs',{{}}),gradient=svgEl('linearGradient',{{id,gradientUnits:'userSpaceOnUse',x1:margin.left,x2:margin.left+plotW,y1:0,y2:0}});
        const offset=String(Math.max(0,Math.min(1,(centerX(splitIndex)-margin.left)/plotW)));
        gradient.appendChild(svgEl('stop',{{offset,'stop-color':split.beforeColor||item.color}}));
        gradient.appendChild(svgEl('stop',{{offset,'stop-color':split.afterColor||item.color}}));
        defs.appendChild(gradient);svg.appendChild(defs);lineColor=`url(#${{id}})`;
      }}
    }}
    const path = svgEl('path', {{
      d: pathFromPoints(points, visual.line.curve),
      stroke: lineColor,
      class: 'line-path',
      'stroke-width': item.strokeWidth,
      'stroke-dasharray': item.dasharray,
      'stroke-linecap': visual.line.strokeLinecap,
      'stroke-linejoin': visual.line.strokeLinejoin,
      opacity: item.opacity
    }});
    svg.appendChild(path);
    const pointVisible = visual.point.visible !== false && (item.showPoints === true || visual.line.showPoints === true);
    if (pointVisible) {{
      points.forEach(point => {{
        if (!point) return;
        svg.appendChild(svgEl('circle', {{ cx: point[0], cy: point[1], r: item.outerRadius, class: 'point-outer', fill: visual.point.fill, stroke: item.color, 'stroke-width': visual.point.strokeWidth }}));
        svg.appendChild(svgEl('circle', {{ cx: point[0], cy: point[1], r: item.pointRadius, class: 'point-inner', fill: item.color }}));
      }});
    }}
  }});
  svg.appendChild(positionLine);
  (Array.isArray(visual.overlays) ? visual.overlays : []).filter(item => isObject(item) && item.type === 'referenceLine').forEach(overlay => {{
    const axis = String(overlay.axis || 'left') === 'right' ? rightAxis : leftAxis;
    const yScale = String(overlay.axis || 'left') === 'right' ? yRight : yLeft;
    const value = number(overlay.value);
    if (value === null) return;
    const y = yScale(value);
    svg.appendChild(svgEl('line', {{ x1: margin.left, y1: y, x2: margin.left + plotW, y2: y, stroke: overlay.color || '#94a3b8', 'stroke-width': toNumber(overlay.width, 1.4), 'stroke-dasharray': overlay.dasharray || '4 4', opacity: toNumber(overlay.opacity, 1) }}));
    if (overlay.label) {{
      svg.appendChild(svgEl('text', {{ x: margin.left + plotW - 4, y: y - 6, 'text-anchor': 'end', class: 'overlay-label', fill: overlay.labelColor || overlay.color || '#475467', 'font-size': toNumber(overlay.fontSize, 11) }}, String(overlay.label).replace('{{value}}', valueFormatter(value, axis))));
    }}
  }});
  (Array.isArray(visual.overlays) ? visual.overlays : []).filter(item => isObject(item) && ['thresholdMarkers', 'pointMarkers'].includes(item.type)).forEach(overlay => {{
    const sourceMetric = overlay.sourceMetric || overlay.metric || overlay.field;
    const targetField = overlay.targetField || overlay.targetMetric || sourceMetric || series[0]?.field;
    const targetSeries = series.find(item => item.field === targetField) || series[0];
    const threshold = number(overlay.threshold);
    let shown = 0;
    rows.forEach((row, index) => {{
      if (shown >= toNumber(overlay.maxLabels ?? overlay.maxPoints, Infinity)) return;
      const sourceValue = number(row[sourceMetric]);
      const targetValue = number(row[targetField]);
      if (targetValue === null || !targetSeries) return;
      let match = true;
      if (overlay.type === 'thresholdMarkers' && threshold !== null) {{
        const direction = overlay.direction === 'below' ? 'below' : 'above';
        match = sourceValue !== null && (direction === 'below' ? sourceValue <= threshold : sourceValue >= threshold);
      }}
      if (overlay.type === 'pointMarkers' && (overlay.x !== undefined || overlay.value !== undefined || overlay.index !== undefined)) {{
        match = (overlay.index !== undefined && Number(overlay.index) === index)
          || (overlay.x !== undefined && String(row[config.xField]) === String(overlay.x))
          || (overlay.value !== undefined && String(row[config.xField]) === String(overlay.value));
      }}
      if (!match) return;
      const x = centerX(index);
      const y = yFor(targetSeries)(targetValue);
      const color = eventMarkColor(overlay, targetSeries.color);
      const eventOverlay = {{...overlay,connectorColor:eventMarkColor({{color:overlay.connectorColor||color}},targetSeries.color)}};
      drawMarker(svg, x, y, overlay, color);
      const label = overlay.eventTitle || overlay.eventLabel || overlay.pointLabel || overlay.label;
      if (label || overlay.labelTemplate || overlay.labelBox) {{
        const formatOverlayText = value => String(value ?? '')
          .replaceAll('{{x}}', String(row[config.xField] ?? ''))
          .replaceAll('{{date}}', String(row.date ?? row[config.xField] ?? ''))
          .replaceAll('{{value}}', valueFormatter(targetValue, axisFor(targetSeries), targetSeries))
          .replaceAll('{{markerValue}}', sourceValue === null ? '' : String(sourceValue));
        const text = formatOverlayText(overlay.labelTemplate || label || '');
        if (overlay.labelBox || overlay.eventTime || overlay.eventTitle) {{
          pendingEventLabels.push([
            svg, x, y, eventOverlay, color,
            formatOverlayText(overlay.eventTime || overlay.dateLabel || '{{date}}'),
            text,
            {{ left: margin.left, right: margin.left + plotW, top: margin.top, bottom: margin.top + plotH }}
          ]);
        }} else {{
          svg.appendChild(svgEl('text', {{ x: x + toNumber(overlay.labelDx, 6), y: y - toNumber(overlay.labelDy, 8), class: 'overlay-label', fill: overlay.labelColor || color, 'font-size': toNumber(overlay.fontSize, 11) }}, text));
        }}
      }}
      shown += 1;
    }});
  }});
  eventLabelLayout.connectorLayer = svgEl('g', {{class:'event-label-connectors','pointer-events':'none'}});
  svg.appendChild(eventLabelLayout.connectorLayer);
  eventLabelLayout.anchorPoints=pendingEventLabels.map(args=>[args[1],args[2]]);
  const inlineEvents=pendingEventLabels.filter(args=>args[3].labelPlacement!=='outside-top');
  drawOutsideTopEventLabels(pendingEventLabels.filter(args=>args[3].labelPlacement==='outside-top'),svg);
  if (!isMobile && inlineEvents.length) {{
    const labelWidth=Math.max(...inlineEvents.map(args=>eventLabelNaturalWidth(args[3],args[5],args[6],args[7].right-args[7].left-24)));
    const rowHeight=Math.max(...inlineEvents.map(args=>eventLabelNaturalHeight(args[3],args[5],args[6],labelWidth)));
    inlineEvents.sort((a,b)=>a[1]-b[1]).forEach(args=>{{
      args[3]={{...args[3],timelineWidth:labelWidth,timelineRowHeight:rowHeight}};
      drawEventLabel(...args);
    }});
  }}
  rows.forEach((row, index) => {{
    const x = hoverStartX(index);
    const hitWidth = hoverWidthFor(index);
    const hit = svgEl('rect', {{ x, y: margin.top, width: hitWidth, height: plotH, class: 'hover-zone', fill: 'rgba(0,0,0,0)', 'pointer-events': 'all' }});
    hit.addEventListener('mouseenter', () => showTooltip(row, index, centerX(index), series, positionLine, margin, plotH, plotW));
    hit.addEventListener('mousemove', event => showTooltip(row, index, centerX(index), series, positionLine, margin, plotH, plotW, event));
    hit.addEventListener('mouseleave', hideTooltip);
    svg.appendChild(hit);
  }});
  if (isMobile) drawMobileEventLabels(inlineEvents, svg);
  drawSvgLegend(series, width, height, legendTopY);
}}
function tooltipBounds(anchorX, tooltipWidth, viewportLeft = 0, viewportRight = window.innerWidth, safeDistance = 0) {{
  const arrowInset = 0;
  const minLeft = viewportLeft + safeDistance;
  const maxLeft = Math.max(minLeft, viewportRight - tooltipWidth - safeDistance);
  const centeredLeft = anchorX - tooltipWidth / 2;
  const left = Number.isFinite(maxLeft) ? Math.max(minLeft, Math.min(maxLeft, centeredLeft)) : Math.max(minLeft, centeredLeft);
  const arrowLeft = Math.min(tooltipWidth - arrowInset, Math.max(arrowInset, anchorX - left));
  return {{ left, arrowLeft }};
}}
function tooltipArrowDisplay(bounds, tooltipWidth) {{
  const arrowHalfWidth = 5;
  const arrowLeft = Number(bounds?.arrowLeft);
  if (!Number.isFinite(arrowLeft) || !Number.isFinite(tooltipWidth) || tooltipWidth <= 0) return 'none';
  return arrowLeft < arrowHalfWidth || arrowLeft > tooltipWidth - arrowHalfWidth ? 'none' : 'block';
}}
function titleRectInContext(ctx) {{
  const title = root.querySelector('.chart-title');
  if (!title) return null;
  const range = document.createRange();
  range.selectNodeContents(title);
  const rect = range.getBoundingClientRect();
  return {{ left: ctx.offsetLeft + rect.left, right: ctx.offsetLeft + rect.right, top: ctx.offsetTop + rect.top, bottom: ctx.offsetTop + rect.bottom }};
}}
function avoidTitleOverlap(bounds, tooltipWidth, tooltipHeight, top, titleRect, anchorX, viewportLeft, viewportRight, safeDistance = 0) {{
  if (!titleRect) return bounds;
  const tooltipRect = {{ left: bounds.left, right: bounds.left + tooltipWidth, top, bottom: top + tooltipHeight }};
  const overlaps = tooltipRect.left < titleRect.right && tooltipRect.right > titleRect.left && tooltipRect.top < titleRect.bottom && tooltipRect.bottom > titleRect.top;
  if (!overlaps) return bounds;
  const aboveTitleTop = titleRect.top - tooltipHeight - safeDistance;
  if (aboveTitleTop >= safeDistance) {{
    const arrowInset = 0;
    return {{ ...bounds, top: aboveTitleTop, arrowLeft: Math.min(tooltipWidth - arrowInset, Math.max(arrowInset, anchorX - bounds.left)) }};
  }}
  const arrowInset = 0;
  const minLeft = viewportLeft + safeDistance;
  const maxLeft = viewportRight - tooltipWidth - safeDistance;
  const candidates = [titleRect.right + safeDistance, titleRect.left - tooltipWidth - safeDistance].filter(value => (
    value >= minLeft &&
    value <= maxLeft &&
    anchorX >= value + arrowInset &&
    anchorX <= value + tooltipWidth - arrowInset
  ));
  if (!candidates.length) return bounds;
  const left = candidates.reduce((best, value) => Math.abs(value - bounds.left) < Math.abs(best - bounds.left) ? value : best, candidates[0]);
  const clampedLeft = Math.max(minLeft, Math.min(maxLeft, left));
  return {{ ...bounds, left: clampedLeft, arrowLeft: Math.min(tooltipWidth - arrowInset, Math.max(arrowInset, anchorX - clampedLeft)) }};
}}
function svgScreenX(svgX, contextOffsetLeft = 0) {{
  const rect = svg.getBoundingClientRect();
  const viewBox = svg.viewBox.baseVal;
  const scaleX = viewBox.width ? rect.width / viewBox.width : 1;
  return contextOffsetLeft + rect.left + (svgX - viewBox.x) * scaleX;
}}
function svgScreenY(svgY, contextOffsetTop = 0) {{
  const rect = svg.getBoundingClientRect();
  const viewBox = svg.viewBox.baseVal;
  const scaleY = viewBox.height ? rect.height / viewBox.height : 1;
  return contextOffsetTop + rect.top + (svgY - viewBox.y) * scaleY;
}}
function showTooltip(row, index, centerX, series, positionLine, margin, plotH, plotW, event) {{
  const lineVisible = visual.positionLine.visible !== false;
  positionLine.setAttribute('x1', Math.round(centerX));
  positionLine.setAttribute('x2', Math.round(centerX));
  positionLine.setAttribute('y1', margin.top);
  positionLine.setAttribute('y2', margin.top + plotH);
  positionLine.setAttribute('stroke', visual.positionLine.color || visual.hover.bandColor);
  positionLine.setAttribute('stroke-width', Math.max(1, toNumber(visual.positionLine.width, visual.hover.width || 1)));
  positionLine.setAttribute('stroke-dasharray', visual.positionLine.dasharray || '');
  positionLine.style.opacity = String(lineVisible ? toNumber(visual.positionLine.opacity, visual.hover.bandOpacity || 0.65) : 0);
  if (visual.tooltip.visible === false) return;
  const tooltipCompactOptions = axisCompactOptions(
    series.map(item => row[item.field]),
    {{}},
    {{ showUnit: visual.tooltip.showUnit }}
  );
  const tooltipHtml = `<div class="tooltip-title">${{row[config.xField] ?? ''}}</div><div class="tooltip-body">` + series.map(item => {{
    const axis = axisForSeries(item);
    const meta = tooltipMeta(item);
    return `<div class="tooltip-row"><span class="tooltip-label"><i class="tooltip-dot" style="background:${{item.color}}"></i>${{meta.label}}</span><span class="tooltip-value">${{tooltipValue(row[item.field], axis, item, tooltipCompactOptions)}}</span></div>`;
  }}).join('') + '</div>';
  const stageRect = stage.getBoundingClientRect();
  const ctx = parentTooltipContext();
  const viewportPad = 0;
  const tooltipGap = toNumber(visual.tooltip.gap, 12);
  const offsetY = toNumber(visual.tooltip.offsetY, 0);
  const plotLeft = svgScreenX(margin.left);
  const plotRight = svgScreenX(margin.left + plotW);
  const anchorScreenX = svgScreenX(centerX);
  const gridScreenTop = svgScreenY(margin.top);

  if (ctx) {{
    tooltip.classList.remove('active');
    const portal = parentTooltipElement(ctx);
    portal.innerHTML = tooltipHtml + (visual.tooltip.arrow === false ? '' : '<span class="tooltip-arrow"></span>');
    const parentWidth = ctx.doc.defaultView?.innerWidth || ctx.doc.documentElement.clientWidth || 0;
    const absoluteAnchorX = svgScreenX(centerX, ctx.offsetLeft);
    const absolutePlotLeft = svgScreenX(margin.left, ctx.offsetLeft);
    const absolutePlotRight = svgScreenX(margin.left + plotW, ctx.offsetLeft);
    const availablePlotWidth = Math.max(120, absolutePlotRight - absolutePlotLeft);
    portal.style.minWidth = '0';
    portal.style.width = 'fit-content';
    portal.style.maxWidth = `${{Math.min(toNumber(visual.tooltip.maxWidth, 220), availablePlotWidth)}}px`;
    const firstGridTop = svgScreenY(margin.top, ctx.offsetTop);
    let bounds = tooltipBounds(absoluteAnchorX, portal.offsetWidth, 0, parentWidth, viewportPad);
    const top = firstGridTop - portal.offsetHeight - tooltipGap + offsetY;
    if (portal.offsetWidth > 0 && parentWidth > 0) {{
      portal.style.left = `${{bounds.left}}px`;
      portal.style.top = `${{bounds.top ?? top}}px`;
      portal.style.setProperty('--arrow-left', `${{bounds.arrowLeft}}px`);
      portal.style.setProperty('--tooltip-arrow-display', tooltipArrowDisplay(bounds, portal.offsetWidth));
      portal.classList.add('active');
      postChartHeight();
      return;
    }}
  }}

  if (window.parent && window.parent !== window) {{
    tooltip.classList.remove('active');
    const rootRect = root.getBoundingClientRect();
    postTooltipPortal('show', {{
      html: tooltipHtml + (visual.tooltip.arrow === false ? '' : '<span class="tooltip-arrow"></span>'),
      anchorX: centerX,
      anchorScreenX,
      stageLeft: stageRect.left,
      stageTop: stageRect.top,
      rootLeft: rootRect.left,
      rootRight: rootRect.right,
      gridTop: margin.top,
      gridScreenTop,
      plotScreenLeft: plotLeft,
      plotScreenRight: plotRight,
      gap: tooltipGap,
      offsetY,
      maxWidth: toNumber(visual.tooltip.maxWidth, 220),
      background: visual.tooltip.background,
      color: visual.tooltip.color,
      borderRadius: toNumber(visual.tooltip.borderRadius, 6),
      padding: visual.tooltip.padding || '6px 8px',
      fontSize: toNumber(visual.tooltip.fontSize, 10),
    }});
    const fallbackTime = Date.now();
    setTimeout(() => {{
      if (tooltipPortalAckUntil >= fallbackTime) return;
      tooltip.innerHTML = tooltipHtml;
      tooltip.classList.add('active');
      const availablePlotWidth = Math.max(120, plotRight - plotLeft);
      tooltip.style.minWidth = '0';
      tooltip.style.width = 'fit-content';
      tooltip.style.maxWidth = `${{Math.min(toNumber(visual.tooltip.maxWidth, 220), availablePlotWidth)}}px`;
      const bounds = tooltipBounds(anchorScreenX, tooltip.offsetWidth, 0, window.innerWidth, viewportPad);
      const top = gridScreenTop - tooltip.offsetHeight - tooltipGap + offsetY;
      tooltip.style.left = `${{bounds.left}}px`;
      tooltip.style.top = `${{top}}px`;
      tooltip.style.setProperty('--arrow-left', `${{bounds.arrowLeft}}px`);
      tooltip.style.setProperty('--tooltip-arrow-display', tooltipArrowDisplay(bounds, tooltip.offsetWidth));
      postChartHeight();
    }}, 60);
    postChartHeight();
    return;
  }}

  tooltip.innerHTML = tooltipHtml;
  tooltip.classList.add('active');
  const availablePlotWidth = Math.max(120, plotRight - plotLeft);
  tooltip.style.minWidth = '0';
  tooltip.style.width = 'fit-content';
  tooltip.style.maxWidth = `${{Math.min(toNumber(visual.tooltip.maxWidth, 220), availablePlotWidth)}}px`;
  const bounds = tooltipBounds(anchorScreenX, tooltip.offsetWidth, 0, window.innerWidth, viewportPad);
  const top = gridScreenTop - tooltip.offsetHeight - tooltipGap + offsetY;
  tooltip.style.left = `${{bounds.left}}px`;
  tooltip.style.top = `${{top}}px`;
  tooltip.style.setProperty('--arrow-left', `${{bounds.arrowLeft}}px`);
  tooltip.style.setProperty('--tooltip-arrow-display', tooltipArrowDisplay(bounds, tooltip.offsetWidth));
  postChartHeight();
}}
function hideTooltip() {{
  tooltip.classList.remove('active');
  const ctx = parentTooltipContext();
  const portal = ctx ? parentTooltipElement(ctx) : null;
  if (portal) portal.classList.remove('active');
  postTooltipPortal('hide');
  svg.querySelectorAll('.position-line').forEach(el => el.style.opacity = '0');
  postChartHeight();
}}
function postChartHeight() {{
  const rootRect = root.getBoundingClientRect();
  const stageHeight = Math.ceil(stage.getBoundingClientRect().height || stage.offsetHeight || 0);
  const legendRect = legend.getBoundingClientRect();
  const rootBottom = Math.max(root.getBoundingClientRect().bottom || 0, legendRect.bottom || 0);
  const contentHeight = Math.ceil(Math.max(root.scrollHeight || 0, root.offsetHeight || 0, rootBottom - rootRect.top));
  const minHeight = visual.chart.fitContent === true ? stageHeight : Math.max(240, stageHeight + 56);
  const localTooltipBottom = tooltip.classList.contains('active') ? tooltip.getBoundingClientRect().bottom - rootRect.top + 12 : 0;
  // SVG text may extend beyond its CSS box; include visible tick/legend/mark bounds.
  const svgBottom=Math.max(svg.getBoundingClientRect().bottom,...[...svg.querySelectorAll('text,.event-label-box')].map(el=>el.getBoundingClientRect().bottom));
  const height = Math.ceil(Math.max(minHeight, contentHeight, localTooltipBottom, svgBottom-rootRect.top) + 2);
  try {{
    if (window.frameElement) {{
      window.frameElement.style.height = `${{height}}px`;
      window.frameElement.style.minHeight = `${{minHeight}}px`;
    }}
  }} catch (e) {{}}
  try {{ parent.postMessage({{type:'analysis-planner-chart-height', height}}, '*'); }} catch (e) {{}}
}}
window.addEventListener('resize', () => {{ render(); postChartHeight(); }});
applyComponentStyles();
render();
postChartHeight();
setTimeout(postChartHeight, 80);
setTimeout(postChartHeight, 300);
setTimeout(postChartHeight, 900);
</script>
{(Path(__file__).resolve().parents[1] / "runtime/mobile_interactions.html").read_text(encoding="utf-8")}
</body>
</html>"""


def _default_payload(title: str | None = None) -> dict[str, Any]:
    return {
        "chartType": "grouped-line",
        "title": title or "分组折线图",
        "xField": "period",
        "series": [
            {"field": "current", "label": "Current", "color": "#4387f5"},
            {"field": "previous", "label": "Previous", "color": "#4bc3d5"},
        ],
        "data": [
            {"period": "Q1", "current": 32, "previous": 24},
            {"period": "Q2", "current": 46, "previous": 35},
            {"period": "Q3", "current": 41, "previous": 38},
            {"period": "Q4", "current": 58, "previous": 44},
        ],
    }


def _load_payload(path: str | None, data_json: str | None, data: str | None, title: str | None) -> dict[str, Any]:
    if path:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    elif data_json:
        payload = json.loads(data_json)
    elif data:
        payload = {"chartType": "grouped-line", "title": title or "", "data": json.loads(data)}
    else:
        payload = _default_payload(title)
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if title:
        payload["title"] = title
    return payload


def _write_payload_output(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the grouped line component.")
    parser.add_argument("--code")
    parser.add_argument("--title")
    parser.add_argument("--payload", help="Path to component JSON payload.")
    parser.add_argument("--data")
    parser.add_argument("--data-json")
    parser.add_argument("--style-json")
    parser.add_argument("--style-code", help="Model-generated JSON/JavaScript object merged into visualStyle.")
    parser.add_argument("--style-file", help="Path to a model-generated JSON/JavaScript visualStyle snippet.")
    parser.add_argument("--output", help="Output HTML path.")
    parser.add_argument("--payload-output")
    parser.add_argument("--schema", action="store_true", help="Print component schema.")
    args, _ = parser.parse_known_args()
    if args.schema:
        print(json.dumps(component_schema(), ensure_ascii=False, indent=2))
        return 0
    if not args.output:
        raise SystemExit("--output is required.")
    style = _combined_style(args.style_json, args.style_code, args.style_file)
    payload = _load_payload(args.payload, args.data_json, args.data, args.title)
    normalized = _normalize_config(payload, args.title, style)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_document(payload, args.title, style), encoding="utf-8")
    _write_payload_output(args.payload_output, normalized)
    print(json.dumps({"ok": True, "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
