---
name: "通用图表"
description: 通用图表渲染技能，适用于用户提供的 JSON、CSV-like 数据或上游 payload。用于生成标准或 Aime 风格图表，包括柱状图、折线图、分组折线图、分组柱状图、柱折图、饼图、矩形图、动态折线、动态柱状、动态气泡、脑图、流程图、旅程图和时间线。
---

# Generic Chart

Use this skill to render reusable chart HTML from structured data or an upstream chart payload.

## Mentora 文件交接

使用宿主 `app/skill_runner.py execute --script <具体脚本.py>`。标准折线用 line_chart.py，分组折线 group_line.py，柱状 bar_chart.py，分组柱 grouped_bar.py，柱折 bar_line.py，饼图 pie.py；根据数据自主选择。没有 generic_chart.py 统一脚本，也没有 `contract --skill`；契约入口是 `app/skill_runner.py contract --script <具体脚本.py>`。

上游已有 JSON 文件时，普通字段重命名、日期解析和排序直接写入小型 payload 的 `dataSource`，不要转抄整组数据、也不必为简单字段映射另写 Python。`hostExtensions.dataSource` 在每个对应 schema 中提供宿主契约：path 是 outputs/ 下文件；recordsPath 是选择数组的键路径（如 ["series"]）；fields 将图表字段映射到源字段；dateFormats 可解析日期；sortBy 可显式排序。其他图表选型、数值字段、单位和样式仍由你决定；聚合等真正分析变换使用已有计算技能，不把模型手写数字当作源数据。

例如折线 payload 可以使用 `{"xField":"date","yField":"rate","series":[{"field":"rate","label":"Rate","axis":"right"}],"xScale":"time","dataSource":{"path":"outputs/data/result.json","recordsPath":["series"],"fields":{"date":"macro_observation_period","rate":"v"},"dateFormats":{"date":"%Y%m%d"},"sortBy":"date"}}`。这只是字段映射示例，不选择业务数据或固定图表样式。单独使用组件 CLI 时仍提供展开后的 data/rows；宿主负责 dataSource 扩展和源值校验。

## CLI 生成顺序

生成任何 CLI 调用前必须按这个顺序推理：

1. 先根据用户问句判断目标图表类型和候选脚本。
2. 再打开 `schemas/{script_stem}.schema.json`，读取该脚本的 `semanticChannels`、`payloadShape`、`modelInstructions` 和支持的参数契约。
3. 然后结合用户问句、上游 observation 或用户提供的数据，把字段映射到 schema 定义的视觉通道。
4. 最后生成 CLI：只传该脚本支持的参数；如果需要 `--data-json` 或 `--payload`，必须符合 schema 的 `payloadShape`。

不要先凭脚本名或示例数据生成参数，再事后套 schema。schema 是参数和 payload 生成的约束来源。

## Scripts

Scripts live in `scripts/`.

- `bar_line.py`: dual-axis bar-line chart; accepts `--payload`, `--data-json`, `--title`, `--style-json`, `--style-code`, `--style-file`, `--output`, `--payload-output`, `--schema`.
- `bar_chart.py`: single-series bar chart; accepts `--payload`, `--data-json`, `--title`, `--output`, `--payload-output`, `--schema`.
- `line_chart.py`: single-series line chart backed by the `group_line.py` renderer; accepts `--payload`, `--data-json`, `--data`, `--title`, `--style-json`, `--style-code`, `--style-file`, `--output`, `--payload-output`, `--schema`.
- `group_line.py`: grouped or multi-series line chart; accepts `--payload`, `--data-json`, `--data`, `--title`, `--style-json`, `--style-code`, `--style-file`, `--output`, `--payload-output`, `--schema`.
- `grouped_bar.py`: grouped bar chart; accepts `--payload`, `--data-json`, `--title`, `--output`, `--payload-output`, `--schema`.
- `waterfall.py`: waterfall chart; accepts `--payload`, `--output`, `--schema`.
- `dynamic_line.py`, `dynamic_bar.py`, `dynamic_bubble.py`: Aime dynamic chart adapters.
- `mind_map.py`, `flowchart.py`, `journey.py`, `timeline.py`: Aime logic/structure chart adapters.
- `pie.py`: standalone component renderer.
- `double_ring_pie.py`: double-ring pie renderer for two values sharing one baseline; accepts `--payload`, `--output`, `--schema`.
- 通用 K 线：使用 Aime Chart 的 `kline` / `kline_basic` payload；保留通用 `fieldMapping` 数据契约，但必须复用 `business-chart-recall/scripts/kline.py` 的业务 K 线渲染内核。不要另写 K 线 HTML、CSS 或交互，以保证股票、ETF、数字币、期权等资产的通用 K 线与业务 K 线外观一致。
- `treemap.py`: treemap renderer for category share and hierarchy; accepts `--payload`, `--data-json`, `--title`, `--output`, `--payload-output`, `--schema`.

## Routing Notes

Financial direction charts must carry the source market in every payload as `financialMarket`: `CN` for A-shares, `HK` for Hong Kong, `US` for US securities. Carry this from the user's request and verified data source even when the title only says “全市场” or a date. CN/HK use red-up/green-down; US uses green-up/red-down. Use `var(--sv-price-up)` / `var(--sv-price-down)` rather than hardcoded red/green. A financial-direction payload with no resolvable market is rejected instead of silently assuming US colors; correct the payload and retry. Do not infer market from language, frequency counts, or the signs of bins. For distributions, sign comes from the X-axis bins, never the nonnegative bar heights.

Always prefer a concrete script for the requested chart type. Do not route through a generic adapter.

The application may use JEV to preselect a primary renderer and at most one distinct supporting renderer from the generic and business-chart catalogs. Treat that result as a bounded visualization candidate set, not as permission to skip schema validation, invent data, or force a second chart. The generative planner must still select required upstream data/transformation steps and read every selected renderer's schema before creating CLI arguments.

JEV may also provide a `visualEncoding` recommendation derived from the request and each renderer schema's `semanticChannels`, `visualChannelMappings`, `modelInstructions`, and `payloadShape`. Use it to decide whether the answer should emphasize latest-vs-history, trend/inflection, growth rate, cross-sectional comparison, composition/contribution, driver-outcome dual axes, verified event overlays, distribution/outliers, flows/relationships, scenarios/uncertainty, or price-volume technical confirmation. This recommendation is a semantic objective rather than a payload: verify that the selected schema supports the required channels, calculate only evidence-backed transformations, and preserve any chart form explicitly requested by the user.

Before generating `--data-json` or `--payload`, read the matching file in `schemas/` and use it as the data contract. Map raw fields through `semanticChannels`, then build the final payload according to `payloadShape`. If the schema and the user request conflict, keep the user-requested chart type but adapt the data shape to the schema, or choose a more suitable script whose schema matches the request.

## 多语言字段显示要求

图表 payload 中的原始数据字段名可以保持稳定的机器字段，例如 `revenue`, `net_income`, `date`, `value`，但所有读者可见的字段名称必须匹配用户语言类型。

- 根据用户问题的主要语言生成 `title`、`series[].label`、`columnSeries[].label`、`lineSeries[].label`、图例、tooltip 字段名、轴标题、节点/分组名称等显示文本。
- 中文用户使用中文字段名，例如“营收”“净利润”“每股收益”；英文用户使用英文字段名，例如 “Revenue”, “Net income”, “EPS”；繁体中文、日文、韩文、泰文等也要使用对应语言。
- 不要把上游英文机器字段直接暴露给用户作为显示名称；只有在金融缩写或品牌名本身通用时才保留原文，例如 EPS、EBIT、EBITDA、TTM、NVDA。
- 如果数据行中的 key 是英文，仍应通过系列 `label` 或对应 payload display 字段提供本地化名称；不要为了本地化而把数据 key 改成不稳定的自然语言字段。
- 当用户混合语言提问时，以用户用于表达分析需求的主要语言为准；如果用户明确指定输出语言，优先遵守指定语言。

## 数量级格式化要求

图表轴标签、tooltip、动态图表浮动标签等读者可见数值在使用紧凑数量级时，必须根据用户输入的主要语言选择单位，并且从 `10000` 开始才做数量级转换。

- 中文使用 `万`、`亿`、`万亿`；日文使用 `万`、`億`、`兆`；韩文使用 `만`、`억`、`조`。
- 英文及其他拉丁语言使用 `K`、`M`、`B`、`T`，但阈值仍从 `10000` 开始；例如 `1000` 保持 `1,000`，`10000` 可显示为 `10K`。
- 不要把 `1000` 到 `9999` 自动转换为 `k/K`。
- 如果字段、系列、坐标轴或 payload 已明确给出 `%` 等后缀，保留显式单位，不做会改变语义的数量级换算。
- 对同一条 Y 轴的刻度，必须整条轴统一判断数量级转换：只有所有 tick 值都达到同一数量级转换阈值时才统一转换；只要有任一 tick 不符合阈值，就整条轴都不做数量级转换，避免同轴刻度出现 `9500` 与 `1.2万` 这类混排。
- 对 tooltip/悬浮图例中同一时点的多系列数值，也必须整组统一判断数量级转换：只有该 tooltip 内全部系列值都满足同一数量级阈值时才统一转换；否则全部保持原始数量级。

Relevant schema files include:

- `bar_line.schema.json`
- `bar_chart.schema.json`
- `line_chart.schema.json`
- `group_line.schema.json`
- `grouped_bar.schema.json`
- `pie.schema.json`
- `waterfall.schema.json`
- `double_ring_pie.schema.json`
- `treemap.schema.json`
- `dynamic_line.schema.json`
- `dynamic_bar.schema.json`
- `dynamic_bubble.schema.json`
- `mind_map.schema.json`
- `flowchart.schema.json`
- `timeline.schema.json`
- `journey.schema.json`

For grouped line requests, pass chart data through `group_line.py`. It accepts either explicit `chartType: grouped-line` payloads or looser payloads with `data`/`rows` plus two or more numeric series.

For ordinary grouped line examples, prefer a single right-side y-axis:

- Set every `series[].axis` to `"right"`.
- Set `leftAxis.visible` to `false`.
- Set `rightAxis.visible` to `true` and put the metric name in `rightAxis.label`.
- Set `visualStyle.yAxis.position` to `"right"`.
- Do not set `series[].suffix`, `leftAxis.suffix`, or `rightAxis.suffix` unless the user explicitly asks visible values to show units. This includes `%`, `万人`, `万元`, and similar units.
- Keep legend and tooltip values unit-free by default; if needed, use `visualStyle.tooltip.showUnit: false`.
- Use a left axis or dual axes only when the user explicitly asks for it, or when multiple metrics truly require split scales.

For single line requests, use `line_chart.py`, which normalizes the payload to one grouped-line series and delegates rendering to `group_line.py`. Apply the same defaults as grouped line charts:

- Use `series[0].axis: "right"`, `leftAxis.visible: false`, `rightAxis.visible: true`, and `visualStyle.yAxis.position: "right"`.
- Put the metric name in `rightAxis.label`.
- Do not set `series[0].suffix`, `axis.suffix`, or `rightAxis.suffix` unless the user explicitly asks visible values to show units.
- Keep `visualStyle.tooltip.showUnit: false` and `visualStyle.positionLine.visible: true` by default.
- Y-axis tick and tooltip compact-unit conversion follows `group_line.py`: convert only when all values in that axis or tooltip group meet the same magnitude threshold.

For verified events that should be anchored to a line point, use `visualStyle.overlays.pointMarkers`. A framed two-line event label is enabled with `labelBox: true`; put the first row in `eventTime` and the second row in `eventTitle`. Use `labelPosition: "top"` or `"bottom"` to control placement. Event overlays must not override the base line chart's default height, margins, curve, line width, point visibility, palette, axes, legend, or tooltip styling unless the user explicitly requests those visual changes. By default the event marker and connector inherit the target series color, while the card uses the component's neutral border and typography. The renderer keeps the card inside the plot horizontally and flips a top card below the point when the available top space is insufficient. Do not add an event overlay unless its date, title, and point association are supported by the input data or an explicitly verified source.

Only pass style arguments when the user asks for visual changes. Preserve the default renderer style for ordinary chart requests.

## Tooltip 独立能力

`bar_line.py`、`line_chart.py`、`group_line.py`、`bar_chart.py` 和 `grouped_bar.py` 的 tooltip 逻辑内置在组件脚本中。组件在 standalone HTML、普通 DOM 或同源 iframe 中会自动把 tooltip portal/style 注入到当前页面或最外层可访问宿主页面，按首根网格线上方定位，业务前端不需要额外写 tooltip listener。

组件不会为了 tooltip 改变图表网格布局或静态预留顶部空白；tooltip 只作为浮层按首根网格线上方定位。

tooltip 的纵向锚点必须来自绘图区首根网格线，而不是标题、段落或组件顶部。脚本只允许在左右方向做视口 clamp，避免横向出屏；不要因为与 `.chart-title` 或上方说明文字重叠，就把 tooltip 改定位到标题上方。若首根网格线上方空间不足，可由宿主 portal 做运行时翻转到首根网格线下方；不要通过静态预留间距或标题避让来改变图表布局。

如果组件运行在跨域 iframe、`file://` opaque origin iframe，或任何浏览器禁止访问父页面 DOM 的环境中，iframe 内脚本无法把 DOM 绘制到 iframe 外部；此时仅靠组件脚本无法突破宿主裁剪，只能退回 iframe 内部 tooltip，或由宿主页面提供 postMessage portal bridge，或改用非 iframe/同源方式嵌入。

分组柱状图的 `gridCount` 表示横线总数，0 轴计入其中且不重复绘制；默认共 5 根。跨零时将零轴作为等间距刻度之一；微小异号值仅保留数据空间，不再额外增加紧邻零轴的刻度线。


饼图 `pie.py` 高度随标题、圆环和底部图例内容自适应，不预留固定最小高度或图例下留白。不展示右上角期间标记；旧 payload 的 badgeLabel/yearLabel/periodLabel 仅兼容读取，不渲染。需要说明期间时放在模型生成的标题或副标题。

折线按事件分色可用 `visualStyle.line.colorSplit={x,field,beforeColor,afterColor,beforeLabel,afterLabel}`，x 必须精确匹配数据点，只改变原折线的颜色与图例，不拆分/补齐价格、不改基础样式；日期不存在时保留原线并由业务层说明。标记点继续用 pointMarkers。

不允许 Mark 卡片遮挡折线时，pointMarkers 使用 `labelPlacement: "outside-top"`。标签在绘图区上方独立区域排列，连接精确数据点，窄屏全部保留并换行；不改变绘图区曲线、坐标、网格或基本样式。

当用户允许标记卡片适度遮挡曲线时，pointMarkers可设置 `allowCurveOverlap: true`；保留正常卡片字号、上下内边距和行距，仍避免覆盖其他卡片与标记锚点。默认false，维持原避让行为。

## Data-driven numeric geometry

Standard quantitative charts use the concrete deterministic chart scripts; the Agent chooses the chart type, fields and style. Never manually author SVG point coordinates from numbers. Line/group-line support `xScale: time|linear|category`, `visualStyle.line.curve: straight|smooth|step-after|step-before`, and `missingValuePolicy: gap` for source-faithful missing observations. For irregular dates use sorted ISO dates/epoch milliseconds and xScale=time, preserving horizontal time spacing. A step-after line holds each value until the next observation; it does not imply missing periods were independently observed.

In the Mentora host, `skill_runner.py execute` also accepts a payload with `dataSource` containing a local data path and field mappings; read its returned host contract for this file-handoff extension. The host validates the rendered data against that source and returns a chart validation manifest. Independent CLI use still accepts ordinary data/rows payloads. StreamViz may arrange verified chart artifacts; it must not redraw their data paths.

## Missing-value grouped bars

For user-requested hatched placeholders, `grouped_bar.py` natively accepts `barPlaceholders` (read its schema): exact target series/category, same-row `referenceSeries`, localized status label and optional hatch color. Preserve missing target data as null; never copy the estimate into it. The hatch is visual geometry only, and tooltip/legend expose the missing status. Missing reference or existing target is rejected. Use the native renderer rather than custom HTML or StreamViz for this feature. Chart-markup calls the semantic mark `bar-placeholder` and compiles it to this array.

## Mind maps from files

`mind_map.py --content-file outputs/data/map.md` renders an authored UTF-8 Markdown file directly. For a large structured dataset, use a small `--payload` with `dataSource` instead: read the mind_map schema for recordsPath, groupBy and labelFields, or treePath for a `{name, children}` tree. The renderer deterministically includes every mapped row, escapes source labels and records source SHA-256/count; the model chooses mappings without transcribing the full dataset. This creates a source-backed map, not an automatic analytical summary. Use an existing transformation skill for computed/grouped metrics. Generated HTML embeds the resolved content and requires no source-file fetch when opened. The canvas supports top-right fullscreen on desktop and mobile.
