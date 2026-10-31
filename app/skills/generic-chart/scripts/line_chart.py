#!/usr/bin/env python3
"""Single-line chart adapter backed by the grouped-line renderer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import group_line


COMPONENT_TYPE = "line"


def component_schema() -> dict[str, Any]:
    return {
        "type": COMPONENT_TYPE,
        "name": "折线图",
        "implementation": {
            "renderer": "line_chart.py",
            "source": "adapter over group_line.py",
            "seriesLimit": 1,
            "notes": "Normalizes line payloads to one grouped-line series, then delegates rendering to group_line.py so axis, legend, tooltip, positionLine, spacing, and visualStyle behavior stay identical.",
        },
        "visualChannelMappings": [
            {
                "semantic": "横轴维度",
                "field": "xField",
                "aliases": ["category", "dimension", "date", "period", "year"],
                "visualChannel": "xAxis.category",
                "required": True,
                "default": "period",
            },
            {
                "semantic": "折线数值",
                "field": "yField or series[0].field",
                "aliases": ["line", "lineSeries", "yField", "value"],
                "visualChannel": "line.position",
                "required": True,
            },
            {
                "semantic": "右轴范围",
                "field": "rightAxis or axis",
                "visualChannel": "rightAxis.scale",
            },
            {
                "semantic": "完整视觉样式",
                "field": "visualStyle",
                "visualChannel": "layout+axis+grid+line+point+legend+tooltip+positionLine+overlays+css",
            },
        ],
        "dataContract": [
            {"field": "xField", "type": "string", "channel": "xAxis.category"},
            {"field": "yField or series[0].field", "type": "number", "channel": "line.position"},
            {"field": "data[]", "type": "array", "required": True},
        ],
        "payloadShape": {
            "type": "line",
            "title": "string",
            "xField": "string",
            "xScale": "category|time|linear",
            "missingValuePolicy": "legacy|gap",
            "yField": "string",
            "series": [{"field": "string", "label": "string", "axis": "right"}],
            "leftAxis": {"visible": False},
            "rightAxis": {"label": "string", "digits": "number", "includeZero": "boolean", "visible": True},
            "visualStyle": {
                "yAxis": {"position": "right"},
                "tooltip": {"showUnit": False},
                "positionLine": {"visible": True, "color": "string", "opacity": "number", "width": "number", "dasharray": "string"},
            },
            "data": "array<object>",
        },
        "styleContract": group_line.component_schema()["styleContract"],
        "modelInstructions": [
            "Use only when the x dimension has meaningful order.",
            "Sort x chronologically before rendering.",
            "Provide exactly one numeric series.",
            "Use the right y-axis by default: set series[0].axis='right', leftAxis.visible=false, rightAxis.visible=true, and visualStyle.yAxis.position='right'.",
            "Put the metric name in rightAxis.label. Do not set series[0].suffix or rightAxis.suffix unless the user explicitly asks visible values to show units.",
            "Y-axis tick and tooltip compact-unit conversion is inherited from group_line.py and is applied only when the whole axis or tooltip group satisfies the same magnitude threshold.",
            "Tooltip boundary behavior is inherited from group_line.py: clamp to both viewport edges, compute anchors from rendered SVG screen coordinates on small screens, and hide the arrow when it would sit on the tooltip edge.",
            "Right-side y-axis descriptions align to the same right edge as y-axis tick labels and may extend leftward into the chart area instead of reserving extra right margin.",
        ],
    }


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


def _numeric_fields(data: list[Any], x_field: str) -> list[str]:
    fields: list[str] = []
    seen: set[str] = set()
    for row in data:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            key = str(key)
            if key == x_field or key in seen:
                continue
            try:
                float(value)
            except (TypeError, ValueError):
                continue
            fields.append(key)
            seen.add(key)
    return fields


def _first_series(source: dict[str, Any], props: dict[str, Any], data: list[Any], x_field: str) -> dict[str, Any]:
    raw_series = props.get("series") or source.get("series")
    if isinstance(raw_series, list):
        for item in raw_series:
            if isinstance(item, dict) and (item.get("field") or item.get("key") or item.get("valueField")):
                field = str(item.get("field") or item.get("key") or item.get("valueField"))
                return {
                    "field": field,
                    "label": str(item.get("label") or item.get("title") or item.get("name") or field),
                    "color": item.get("color"),
                    "axis": "right",
                    "prefix": str(item.get("prefix") or ""),
                    "digits": item.get("digits"),
                }
    y_field = str(props.get("yField") or source.get("yField") or source.get("valueField") or "")
    if not y_field:
        numeric_fields = _numeric_fields(data, x_field)
        if numeric_fields:
            y_field = numeric_fields[0]
    if not y_field:
        raise ValueError("line data must contain yField, series[0].field, or at least one numeric field")
    label = source.get("yLabel") or props.get("yLabel") or source.get("measureLabel") or props.get("measureLabel") or y_field
    return {"field": y_field, "label": str(label), "axis": "right"}


def _normalize_line_payload(payload: dict[str, Any], title: str | None = None, style: dict[str, Any] | None = None) -> dict[str, Any]:
    source = _extract_payload(payload)
    if not isinstance(source, dict):
        raise ValueError("payload must contain a line component")
    props = source.get("props") if isinstance(source.get("props"), dict) else {}
    data = source.get("data") or props.get("data") or source.get("rows") or props.get("rows") or []
    if not isinstance(data, list):
        raise ValueError("line data must be an array")
    x_field = str(props.get("xField") or source.get("xField") or "period")
    series = _first_series(source, props, data, x_field)

    axis = source.get("axis") if isinstance(source.get("axis"), dict) else props.get("axis")
    right_axis = props.get("rightAxis") if isinstance(props.get("rightAxis"), dict) else source.get("rightAxis")
    if not isinstance(right_axis, dict):
        right_axis = axis if isinstance(axis, dict) else {}
    right_axis = {
        **right_axis,
        "label": str(right_axis.get("label") or right_axis.get("title") or right_axis.get("name") or source.get("yLabel") or props.get("yLabel") or series["label"]),
        "visible": True,
    }
    right_axis.pop("suffix", None)

    visual_style = source.get("visualStyle") if isinstance(source.get("visualStyle"), dict) else props.get("visualStyle")
    visual_style = visual_style if isinstance(visual_style, dict) else {}
    visual_style = group_line._deep_merge(visual_style, style or {})
    visual_style = group_line._deep_merge(
        {
            "yAxis": {"position": "right"},
            "tooltip": {"showUnit": False},
            "positionLine": {"visible": True},
        },
        visual_style,
    )

    normalized = {
        "type": "group-line",
        "chartType": "grouped-line",
        "themeMode": str(
            props.get("themeMode") or props.get("theme") or source.get("themeMode") or source.get("theme") or "light"
        ).lower(),
        "title": str(title or source.get("title") or props.get("title") or "折线图"),
        "subtitle": str(source.get("subtitle") or props.get("subtitle") or ""),
        "xField": x_field,
        "xScale": str(source.get("xScale") or props.get("xScale") or "category"),
        "missingValuePolicy": str(source.get("missingValuePolicy") or "legacy"),
        "data": data,
        "series": [series],
        "leftAxis": {"visible": False},
        "rightAxis": right_axis,
        "height": int(props.get("height") or source.get("height") or 360),
        "gridCount": int(props.get("gridCount") or source.get("gridCount") or 5),
        "visualStyle": visual_style,
    }
    return normalized


def _load_payload(path: str | None, data_json: str | None, data: str | None, title: str | None) -> dict[str, Any]:
    if path:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    elif data_json:
        payload = json.loads(data_json)
    elif data:
        payload = {"type": "line", "title": title or "", "data": json.loads(data)}
    else:
        payload = {
            "type": "line",
            "title": title or "折线图",
            "xField": "period",
            "yField": "value",
            "series": [{"field": "value", "label": "数值", "axis": "right"}],
            "data": [
                {"period": "Q1", "value": 32},
                {"period": "Q2", "value": 46},
                {"period": "Q3", "value": 41},
                {"period": "Q4", "value": 58},
            ],
        }
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
    parser = argparse.ArgumentParser(description="Render the single-line component.")
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
    style = group_line._combined_style(args.style_json, args.style_code, args.style_file)
    payload = _load_payload(args.payload, args.data_json, args.data, args.title)
    normalized = _normalize_line_payload(payload, args.title, style)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(group_line.render_document(normalized, args.title, {}), encoding="utf-8")
    _write_payload_output(args.payload_output, normalized)
    print(json.dumps({"ok": True, "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
