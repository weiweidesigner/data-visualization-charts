#!/usr/bin/env python3
"""Standalone mind-map renderer with inline styles."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from pathlib import Path
from typing import Any


def unwrap_chart_payload(payload: dict[str, Any]) -> dict[str, Any]:
    chart_payload = payload.get("chartPayload")
    if not isinstance(chart_payload, dict):
        return payload
    unwrapped = dict(chart_payload)
    for key in ("title", "theme", "themeMode", "theme_mode"):
        if key in payload and key not in unwrapped:
            unwrapped[key] = payload[key]
    return unwrapped


def _field(record: Any, path: Any) -> Any:
    keys = path if isinstance(path, list) else str(path).split('.')
    value = record
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"Mind-map source field not found: {path!r}")
        value = value[key]
    return value


def _plain_label(value: Any) -> str:
    text = '—' if value is None else str(value)
    text = html.escape(' '.join(text.splitlines()), quote=False)
    return re.sub(r'([\\`*_[\]{}()#+!|])', r'\\\1', text)


def resolve_file_content(payload: dict[str, Any], base: Path, content_file: str | None = None) -> dict[str, Any]:
    """Turn explicit local file mappings into Markdown without an LLM or network."""
    source = payload.get('dataSource')
    if source is None and not content_file:
        return payload
    if source is not None and content_file:
        raise ValueError('Use dataSource or --content-file, not both')
    if markdown_from_payload(payload):
        raise ValueError('Use file content or inline Markdown, not both')
    path_text = content_file if content_file else source.get('path') if isinstance(source, dict) else None
    if not isinstance(path_text, str) or not path_text or '://' in path_text:
        raise ValueError('Mind-map file source requires a local path')
    path = Path(path_text).expanduser()
    if not path.is_absolute(): path = base / path
    path = path.resolve(); raw = path.read_bytes(); text = raw.decode('utf-8-sig')
    count = None
    if content_file:
        markdown = text
    else:
        document = json.loads(text)
        if 'treePath' in source:
            tree = document
            for key in source['treePath']: tree = tree[key]
            lines = []; count = 0
            def visit(node, depth=0):
                nonlocal count
                if not isinstance(node, dict) or not isinstance(node.get('name'), str) or not node['name'].strip():
                    raise ValueError('Tree nodes require nonempty name strings and optional children arrays')
                count += 1
                lines.append(('# ' if depth == 0 else '  ' * (depth-1) + '- ') + _plain_label(node['name']))
                children = node.get('children', [])
                if not isinstance(children, list): raise ValueError('Tree children must be an array')
                for child in children: visit(child, depth+1)
            visit(tree)
        else:
            rows = document
            for key in source.get('recordsPath', []): rows = rows[key]
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise ValueError('recordsPath must select an array of objects')
            fields = source.get('labelFields')
            if not isinstance(fields, list) or not fields:
                raise ValueError('Row sources require explicit labelFields')
            groups = source.get('groupBy', [])
            if not isinstance(groups, list): raise ValueError('groupBy must be an array')
            title = payload.get('title')
            if not isinstance(title, str) or not title.strip(): raise ValueError('Row sources require a root title')
            branches = {}; count = len(rows)
            for row in rows:
                branch = branches
                for spec in groups:
                    spec = {'field': spec} if isinstance(spec, str) else spec
                    value = _field(row, spec['field'])
                    label = (spec.get('labels') or {}).get(str(value), value)
                    branch = branch.setdefault(str(label) if label is not None else '—', {})
                parts = []
                for spec in fields:
                    spec = {'field': spec} if isinstance(spec, str) else spec
                    if not isinstance(spec, dict) or 'field' not in spec: raise ValueError('labelFields require field mappings')
                    value = _field(row, spec['field'])
                    parts.append(str(spec.get('label') or '') + (': ' if spec.get('label') else '') + ('—' if value is None else str(value)) + (str(spec.get('suffix') or '') if value is not None else ''))
                branch.setdefault(None, []).append(' · '.join(_plain_label(part) for part in parts))
            lines = ['# ' + _plain_label(title)]
            def emit(branch, depth=0):
                for label, children in branch.items():
                    if label is None:
                        lines.extend('  ' * depth + '- ' + text for text in children)
                    else:
                        lines.append('  ' * depth + '- ' + _plain_label(label)); emit(children, depth+1)
            emit(branches)
        markdown = '\n'.join(lines)
    if not markdown.strip(): raise ValueError('Mind-map source is empty')
    result = dict(payload); result.pop('dataSource', None); result['data'] = {'content': markdown}
    result['_fileSource'] = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
                             'recordCount': count, 'kind': 'markdown-file' if content_file else 'json-file',
                             'mapping': source}
    return result


def load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload:
        payload = json.loads(Path(args.payload).expanduser().read_text(encoding="utf-8"))
    elif args.data_json:
        payload = json.loads(args.data_json)
    else:
        payload = {}
    if not isinstance(payload, dict):
        payload = {"data": payload}
    payload = unwrap_chart_payload(payload)
    if args.data:
        payload["data"] = json.loads(args.data)
    if args.title and payload.get('dataSource'): payload['title'] = args.title
    payload = resolve_file_content(payload, Path.cwd(), getattr(args, 'content_file', None))
    if args.title and not (generic_mind_map_title(args.title) and payload_title_from_content(payload)):
        payload["title"] = args.title
    if not payload.get("title"):
        root_title = payload_title_from_content(payload)
        if root_title:
            payload["title"] = root_title
    theme = normalize_theme(args.theme_mode or args.theme or ("dark" if args.dark else None) or theme_from_payload(payload))
    payload["theme"] = theme
    payload["themeMode"] = theme
    return payload


def markdown_from_payload(payload: dict[str, Any]) -> str:
    payload = unwrap_chart_payload(payload)
    for key in ("content", "markdown", "text"):
        if isinstance(payload.get(key), str):
            return str(payload.get(key) or "")
    data = payload.get("data")
    if isinstance(data, dict):
        return str(data.get("content") or data.get("markdown") or data.get("text") or "")
    if isinstance(data, str):
        return data
    return ""


def title_from_markdown(markdown: str) -> str:
    for line in str(markdown or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def payload_title_from_content(payload: dict[str, Any]) -> str:
    return title_from_markdown(markdown_from_payload(payload))


def generic_mind_map_title(title: Any) -> bool:
    normalized = re.sub(r"[\s#：:|｜_-]+", "", str(title or "").strip().lower())
    return normalized in {
        "",
        "思维导图",
        "思維導圖",
        "脑图",
        "腦圖",
        "mindmap",
        "summary",
        "marketsummary",
        "markethotspots",
        "今日市场热点",
        "今日市場熱點",
    }


def normalize_theme(value: Any = None) -> str:
    text = str(value or "").strip().lower()
    if text in {"dark", "night", "black", "暗夜", "暗色", "深色", "黑夜"}:
        return "dark"
    return "light"


def theme_from_payload(payload: dict[str, Any]) -> str:
    for key in ("themeMode", "theme", "theme_mode"):
        if key in payload:
            return normalize_theme(payload.get(key))
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("themeMode", "theme", "theme_mode"):
            if key in data:
                return normalize_theme(data.get(key))
    return "light"


def build_html(payload: dict[str, Any], theme: str | None = None) -> str:
    markdown = markdown_from_payload(payload).strip()
    if not markdown:
        raise ValueError("mind_map.py requires markdown content in payload.data.content/markdown/text; content must be generated before rendering.")
    title = str(payload.get("title") or title_from_markdown(markdown) or "思维导图")
    safe_title = html.escape(title)
    markdown_json = json.dumps(markdown, ensure_ascii=False).replace("</", "<\\/")
    theme_name = normalize_theme(theme or theme_from_payload(payload))
    body_class = "theme-dark" if theme_name == "dark" else "theme-light"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover" />
<title>{safe_title}</title>
<style>
:root{{--mm-fullscreen-bg:#ffffff;--mm-page-bg:transparent;--mm-bg:transparent;--mm-text:#111827;--mm-muted:#4b5563;--mm-border:rgba(17,24,39,.12);--mm-button-bg:rgba(255,255,255,.92);--mm-shadow:0 8px 22px rgba(17,24,39,.12);--mm-node-bg:transparent;--mm-link-opacity:.88}}
body.theme-dark{{--mm-fullscreen-bg:#101010;--mm-page-bg:transparent;--mm-bg:transparent;--mm-text:#eef4f8;--mm-muted:#9aa6b5;--mm-border:rgba(148,163,184,.22);--mm-button-bg:rgba(15,23,32,.9);--mm-shadow:0 10px 26px rgba(0,0,0,.28);--mm-node-bg:transparent;--mm-link-opacity:.72}}
html,body{{margin:0;padding:0;width:100%;height:auto;min-height:0;background:var(--mm-page-bg);color:var(--mm-text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;overflow:hidden}}
#container{{position:relative;width:100%;height:min(52.5vw,520px);min-height:320px;background:transparent;border-radius:0;overflow:hidden}}
#fullscreenBtn,#exitFullscreenBtn{{position:absolute;z-index:20;display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border:1px solid var(--mm-border);border-radius:6px;background:var(--mm-button-bg);color:var(--mm-text);box-shadow:var(--mm-shadow);cursor:pointer;-webkit-tap-highlight-color:transparent}}
#fullscreenBtn{{right:14px;top:18px}}
#exitFullscreenBtn{{right:18px;top:max(14px,env(safe-area-inset-top));display:none}}
#fullscreenBtn svg,#exitFullscreenBtn svg{{display:block;width:13px;height:13px;stroke:currentColor;stroke-width:2;fill:none;stroke-linecap:round;stroke-linejoin:round}}
#mindMapZoom{{position:absolute;z-index:30;right:12px;bottom:12px;display:none;gap:4px;padding:4px;border:1px solid var(--mm-border);border-radius:10px;background:var(--mm-button-bg)}}#mindMapZoom button{{width:30px;height:30px;border:0;border-radius:6px;background:transparent;color:var(--mm-text);font:600 18px/1 sans-serif;cursor:pointer}}#mindMapZoom button:focus-visible{{outline:2px solid var(--mm-muted);outline-offset:1px}}
pre.markmap{{position:absolute;inset:0;margin:0;padding:0;background:transparent;white-space:pre}}
svg.markmap{{width:100%!important;height:100%!important;touch-action:auto!important}}
svg.markmap text{{fill:var(--mm-text)!important;opacity:1!important;font-weight:600}}
.markmap-foreign,.markmap-foreign *{{color:var(--mm-text)!important;opacity:1!important}}
svg.markmap .link{{stroke-opacity:var(--mm-link-opacity)!important}}
svg.markmap .node circle{{fill:var(--mm-node-bg);stroke-width:2.5px}}
@media(max-width:720px){{
  #container{{height:calc(100vw * .525);min-height:220px;max-height:none}}
  #fullscreenBtn{{display:inline-flex;top:9px;right:12px}}
  svg.markmap text{{font-size:13px!important}}
}}

html:fullscreen,html:-webkit-full-screen,body.is-fullscreen{{width:100vw;height:100vh;background:var(--mm-fullscreen-bg);overflow:hidden}}
body.is-fullscreen #mindMapZoom{{display:flex}}
#fullscreenBtn:hover,#exitFullscreenBtn:hover{{border-color:var(--mm-muted)}}
#fullscreenBtn:focus-visible,#exitFullscreenBtn:focus-visible{{outline:2px solid var(--mm-muted);outline-offset:2px}}
body.is-fullscreen #container{{position:fixed;inset:0;width:100vw;height:100vh;min-height:0;border-radius:0;z-index:9999}}
body.is-fullscreen #fullscreenBtn{{display:none}}
body.is-fullscreen #exitFullscreenBtn{{display:inline-flex}}
body.is-fullscreen svg.markmap text{{font-size:13px!important}}
@media(max-width:720px) and (orientation:portrait){{
  body.is-fullscreen #container{{width:100vh;height:100vw;left:50%;top:50%;transform:translate(-50%,-50%) rotate(90deg);transform-origin:center center}}
}}
body.is-fullscreen.is-forced-landscape #container{{width:100vh;height:100vw;left:50%;top:50%;right:auto;bottom:auto;transform:translate(-50%,-50%) rotate(90deg);transform-origin:center center}}
</style>
</head>
<body class="{body_class}">
<div id="container" data-mind-map-container="true" data-mind-map-version="3">
  <button id="fullscreenBtn" type="button" aria-label="全屏浏览" title="全屏浏览">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5"></path><path d="M16 3h5v5"></path><path d="M21 16v5h-5"></path><path d="M3 16v5h5"></path></svg>
  </button>
  <button id="exitFullscreenBtn" type="button" aria-label="退出全屏" title="退出全屏">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3v6H3"></path><path d="M15 3v6h6"></path><path d="M21 15h-6v6"></path><path d="M3 15h6v6"></path></svg>
  </button>
<div id="mindMapZoom" role="group" aria-label="画布缩放"><button type="button" data-map-zoom="out" aria-label="缩小画布" title="缩小">−</button><button type="button" data-map-zoom="fit" aria-label="适应画布" title="适应画布">⤢</button><button type="button" data-map-zoom="in" aria-label="放大画布" title="放大">+</button></div>
  <pre class="markmap" id="mm"></pre>
</div>
<script>
document.getElementById("mm").textContent = {markdown_json};
const container = document.getElementById("container");
const fullscreenBtn = document.getElementById("fullscreenBtn");
const exitFullscreenBtn = document.getElementById("exitFullscreenBtn");

let mindMapInteracted = false;
function applyMindMapTransform(svg, group, tx, ty, scale){{
  group.setAttribute("transform", `translate(${{tx}},${{ty}}) scale(${{scale}})`);
  // D3's next held drag must start from the same transform as the visible group.
  if (svg.__zoom) svg.__zoom = new svg.__zoom.constructor(scale, tx, ty);
}}
function zoomMindMap(factor, clientX, clientY){{
  const svg=document.querySelector("svg.markmap"), group=svg?.querySelector("g");
  if(!svg||!group)return;
  const matrix=group.transform.baseVal.consolidate()?.matrix;
  if(!matrix)return;
  const rect=svg.getBoundingClientRect(),point=svg.createSVGPoint();point.x=clientX==null?rect.left+rect.width/2:clientX;point.y=clientY==null?rect.top+rect.height/2:clientY;const local=point.matrixTransform(svg.getScreenCTM().inverse()),cx=local.x,cy=local.y;
  const scale=Math.max(.1,Math.min(8,matrix.a*factor)),ratio=scale/matrix.a;
  mindMapInteracted=true;
  applyMindMapTransform(svg,group,cx-(cx-matrix.e)*ratio,cy-(cy-matrix.f)*ratio,scale);
}}
document.querySelectorAll("[data-map-zoom]").forEach(button=>button.addEventListener("click",()=>{{
  if(button.dataset.mapZoom==="fit"){{mindMapInteracted=false;refitMindMap();}}
  else zoomMindMap(button.dataset.mapZoom==="in"?1.25:.8);
}}));
container.addEventListener("wheel",event=>{{
  if(!event.target.closest?.("svg.markmap"))return;
  event.stopImmediatePropagation();
  if(event.ctrlKey||event.metaKey){{event.preventDefault();zoomMindMap(Math.exp(-event.deltaY*.01),event.clientX,event.clientY);}}
}},{{capture:true,passive:false}});
let heldTouch=null,holdTimer=null;
const releaseHeldTouch=()=>{{clearTimeout(holdTimer);heldTouch=null;}};
container.addEventListener('touchstart',event=>{{
  if(event.touches.length!==1){{releaseHeldTouch();return;}}
  if(!event.target.closest?.('svg.markmap'))return;
  const t=event.touches[0];heldTouch={{x:t.clientX,y:t.clientY,active:document.body.classList.contains('is-fullscreen')}};
  holdTimer=setTimeout(()=>{{if(heldTouch)heldTouch.active=true;}},300);
  // A quick single-finger swipe scrolls the page; held drag moves the map.
  event.stopImmediatePropagation();
}},{{capture:true,passive:true}});
container.addEventListener('touchmove',event=>{{
  if(event.touches.length!==1||!heldTouch)return;
  const t=event.touches[0],dx=t.clientX-heldTouch.x,dy=t.clientY-heldTouch.y;
  event.stopImmediatePropagation();
  if(!heldTouch.active){{if(Math.hypot(dx,dy)>8)releaseHeldTouch();return;}}
  const svg=document.querySelector('svg.markmap'),group=svg?.querySelector('g'),matrix=group?.transform.baseVal.consolidate()?.matrix;
  if(!matrix)return;
  event.preventDefault();mindMapInteracted=true;
  const rect=svg.getBoundingClientRect(),box=svg.viewBox.baseVal;
  applyMindMapTransform(svg,group,matrix.e+dx*(box.width?box.width/rect.width:1),matrix.f+dy*(box.height?box.height/rect.height:1),matrix.a);
  heldTouch.x=t.clientX;heldTouch.y=t.clientY;
}},{{capture:true,passive:false}});
container.addEventListener('touchend',releaseHeldTouch);
container.addEventListener('touchcancel',()=>{{releaseHeldTouch();pinchDistance=0;}});

let pinchDistance=0;
container.addEventListener("touchstart",event=>{{
  if(event.touches.length!==2)return;
  pinchDistance=Math.hypot(event.touches[0].clientX-event.touches[1].clientX,event.touches[0].clientY-event.touches[1].clientY);
  event.preventDefault();event.stopImmediatePropagation();
}},{{capture:true,passive:false}});
container.addEventListener("touchmove",event=>{{
  if(event.touches.length!==2||!pinchDistance)return;
  const a=event.touches[0],b=event.touches[1],distance=Math.hypot(a.clientX-b.clientX,a.clientY-b.clientY);
  event.preventDefault();event.stopImmediatePropagation();zoomMindMap(distance/pinchDistance,(a.clientX+b.clientX)/2,(a.clientY+b.clientY)/2);pinchDistance=distance;
}},{{capture:true,passive:false}});
container.addEventListener("touchend",()=>{{pinchDistance=0;}});

function forceMindMapTextStyle(){{
  const styles = getComputedStyle(document.body);
  const textColor = styles.getPropertyValue("--mm-text").trim() || "#111827";
  const nodeBg = styles.getPropertyValue("--mm-node-bg").trim() || "#ffffff";
  document.querySelectorAll("svg.markmap text").forEach(el=>{{el.style.fill=textColor;el.style.opacity="1";el.style.fontWeight="600";}});
  document.querySelectorAll("svg.markmap .markmap-foreign, svg.markmap .markmap-foreign *").forEach(el=>{{el.style.color=textColor;el.style.opacity="1";}});
  document.querySelectorAll("svg.markmap .node circle").forEach(el=>{{el.style.fill=nodeBg;}});
}}
setTimeout(forceMindMapTextStyle,500);setTimeout(forceMindMapTextStyle,1200);setTimeout(forceMindMapTextStyle,2200);
function sendMindMapHeight(){{
  if (document.body.classList.contains("is-fullscreen")) return;
  const height = Math.ceil(container ? container.getBoundingClientRect().height : document.body.scrollHeight || 320);
  try {{ parent.postMessage({{type:"analysis-planner-chart-height", height}}, "*"); }} catch (e) {{}}
}}
function refitMindMap(){{
  updateFullscreenLayout();
  forceMindMapTextStyle();
  const svg = document.querySelector("svg.markmap");
  if (svg && svg.__markmap && typeof svg.__markmap.fit === "function") {{
    try {{ svg.__markmap.fit(); }} catch (e) {{}}
  }}
  if (document.body.classList.contains("is-fullscreen")) {{
    requestAnimationFrame(fillMindMapFullscreen);
    setTimeout(fillMindMapFullscreen, 80);
  }} else {{
    requestAnimationFrame(fitMindMapBelowTitle);
    setTimeout(fitMindMapBelowTitle, 80);
  }}
  sendMindMapHeight();
}}
function fitMindMapBelowTitle(){{
  const svg = document.querySelector("svg.markmap");
  const group = svg ? svg.querySelector("g") : null;
  if (!svg || !group || !container) return;
  const width = Math.max(1, container.clientWidth || svg.clientWidth || window.innerWidth);
  const height = Math.max(1, container.clientHeight || svg.clientHeight || 320);
  let box;
  try {{ box = group.getBBox(); }} catch (e) {{ return; }}
  if (!box || box.width <= 0 || box.height <= 0) return;
  const padX = 0;
  const padTop = 48;
  const padBottom = 8;
  const usableW = Math.max(1, width - padX * 2);
  const usableH = Math.max(1, height - padTop - padBottom);
  const scale = Math.max(0.1, Math.min(usableW / box.width, usableH / box.height));
  const tx = padX + (usableW - box.width * scale) / 2 - box.x * scale;
  const renderedH = box.height * scale;
  const centeredY = padTop + Math.max(0, usableH - renderedH) / 2;
  const maxY = Math.max(padTop, height - padBottom - renderedH);
  const ty = Math.min(maxY, centeredY) - box.y * scale;
  applyMindMapTransform(svg, group, tx, ty, scale);
}}
function fillMindMapFullscreen(){{
  const svg = document.querySelector("svg.markmap");
  const group = svg ? svg.querySelector("g") : null;
  if (!svg || !group || !container) return;
  const width = Math.max(1, container.clientWidth || svg.clientWidth || window.innerWidth);
  const height = Math.max(1, container.clientHeight || svg.clientHeight || window.innerHeight);
  let box;
  try {{ box = group.getBBox(); }} catch (e) {{ return; }}
  if (!box || box.width <= 0 || box.height <= 0) return;
  const padX = 0;
  const padTop = 48;
  const padBottom = 48;
  const usableW = Math.max(1, width - padX * 2);
  const usableH = Math.max(1, height - padTop - padBottom);
  const scale = Math.max(0.1, Math.min(usableW / box.width, usableH / box.height));
  const tx = padX + (usableW - box.width * scale) / 2 - box.x * scale;
  const ty = padTop + (usableH - box.height * scale) / 2 - box.y * scale;
  applyMindMapTransform(svg, group, tx, ty, scale);
}}
function updateFullscreenLayout(){{
  const active = document.body.classList.contains("is-fullscreen");
  const needsForcedLandscape = active && window.innerHeight > window.innerWidth;
  document.body.classList.toggle("is-forced-landscape", needsForcedLandscape);
}}
function lockLandscape(){{
  try {{
    const orientation = screen.orientation;
    if (orientation && typeof orientation.lock === "function") {{
      orientation.lock("landscape").catch(()=>{{}});
    }}
  }} catch (e) {{}}
}}
function unlockOrientation(){{
  try {{
    const orientation = screen.orientation;
    if (orientation && typeof orientation.unlock === "function") orientation.unlock();
  }} catch (e) {{}}
}}
async function enterFullscreen(){{
  document.body.classList.add("is-fullscreen");
  updateFullscreenLayout();
  const fullscreenTarget = document.documentElement || document.body;
  try {{
    if (fullscreenTarget.requestFullscreen) await fullscreenTarget.requestFullscreen();
    else if (fullscreenTarget.webkitRequestFullscreen) fullscreenTarget.webkitRequestFullscreen();
  }} catch (e) {{}}
  lockLandscape();
  setTimeout(refitMindMap,80);
  setTimeout(refitMindMap,320);
}}
function exitFullscreen(){{
  document.body.classList.remove("is-fullscreen");
  document.body.classList.remove("is-forced-landscape");
  unlockOrientation();
  try {{
    if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen();
    else if (document.webkitFullscreenElement && document.webkitExitFullscreen) document.webkitExitFullscreen();
  }} catch (e) {{}}
  setTimeout(refitMindMap,80);
  setTimeout(refitMindMap,320);
  setTimeout(refitMindMap,720);
}}
document.addEventListener("keydown",event=>{{if(event.key==="Escape"&&document.body.classList.contains("is-fullscreen"))exitFullscreen();}});
fullscreenBtn.addEventListener("click", enterFullscreen);
exitFullscreenBtn.addEventListener("click", exitFullscreen);
document.addEventListener("fullscreenchange", () => {{
  if (!document.fullscreenElement) {{
    document.body.classList.remove("is-fullscreen");
    document.body.classList.remove("is-forced-landscape");
    unlockOrientation();
    refitMindMap();
    setTimeout(refitMindMap,260);
    setTimeout(refitMindMap,720);
  }}
}});
document.addEventListener("webkitfullscreenchange", () => {{
  if (!document.webkitFullscreenElement) {{
    document.body.classList.remove("is-fullscreen");
    document.body.classList.remove("is-forced-landscape");
    unlockOrientation();
    refitMindMap();
    setTimeout(refitMindMap,260);
    setTimeout(refitMindMap,720);
  }}
}});
window.addEventListener("load", refitMindMap);
window.addEventListener("resize", refitMindMap);
window.addEventListener("orientationchange", () => {{ setTimeout(refitMindMap,120); setTimeout(refitMindMap,420); }});
[300,900,1800].forEach(delay=>setTimeout(()=>{{if(!mindMapInteracted)refitMindMap();}},delay));
if ("ResizeObserver" in window) {{
  try {{ new ResizeObserver(refitMindMap).observe(container); }} catch (e) {{}}
}}
</script>
<script src="https://cdn.jsdelivr.net/npm/markmap-autoloader@0.18.12"></script>
{(Path(__file__).resolve().parents[1] / "runtime/mobile_interactions.html").read_text(encoding="utf-8")}
</body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code")
    parser.add_argument("--title")
    parser.add_argument("--payload")
    parser.add_argument("--data-json")
    parser.add_argument("--content-file", help="UTF-8 Markdown file; or use payload.dataSource for mapped JSON rows/trees")
    parser.add_argument("--data")
    parser.add_argument("--output", required=True)
    parser.add_argument("--payload-output")
    parser.add_argument("--theme", help="Theme mode: light or dark. Unknown/default values fall back to light.")
    parser.add_argument("--theme-mode", "--themeMode", dest="theme_mode", help="Theme mode alias. Unknown/default values fall back to light.")
    parser.add_argument("--dark", action="store_true", help="Render with dark theme.")
    args, _ = parser.parse_known_args()
    payload = load_payload(args)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        html_text = build_html(payload, theme=payload.get("themeMode"))
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    output.write_text(html_text, encoding="utf-8")
    if args.payload_output:
        Path(args.payload_output).expanduser().resolve().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output), "chartName": "mind_map", "fileSource": payload.get("_fileSource")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
