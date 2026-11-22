# Design

## Context

见 proposal.md「Why」。本设计只记录落地这个 change 所需的技术现状与约束：

- **CaaS 已有 registry+driver 范式**：`mcp_server/registry.py` 的 `DRIVERS` 映射 +
  `registry.json` 分类型条目，加生成方式＝一条条目 + 一个 driver，派发核心零改动。信源层
  直接照这个形状做，不发明新范式。
- **信源提供方已是 shared-instance MCP 面**：fd-text-core（`papers_*`/`gov_*`/`news_*`）、
  fd-legal-search-mcp（`law_*`/`wenshu_*`/…）、fd-zhesi-reference（`concept_lookup` 等）
  都是 `/fd-*` 路径的托管 MCP。CaaS 车道只需 HTTP 调用它们，不搬检索代码。
- **provenance 已有工作区共享契约**：`openspec/specs/text-query-surface` 规定 `source`/
  `stable_id`/`as_of` 三元组，fd-text-core 与 fd-legal-search 都在实现它。实论
  `search.py::Provenance` 是无 spec 背书的超集（多 `coverage`/`metadata_only`/
  `matched_terms`/`authorization`）。
- **实论 verify 引擎现状**：`fd-shilun/server/fd_shilun_api/verify/` 是 FastAPI 路由背后的
  纯 Python 引擎（`verify_text` / `build_report`），**不是 MCP 工具**。这是 P3 的真实增量。
- **doc 渲染底座**：`tools/doc_render.py` 已支持模板目录
  `templates/doc/<id>/ = template.json + style.css`，md/html → chromium print。严肃版式＝
  新增模板目录 + 锚点渲染，不改内核分发骨架。
- **ADR-0004**：生成面可托管，发布面永不托管；编排知识以技能承载、不进工具面。

## Goals / Non-Goals

**Goals**
- 让 CaaS 的信源层与生成层同构（registry+driver），加域不改核心。
- 让「引用」成为物料库一等对象，核验复用实论引擎而非另写。
- 严肃版式复用既有渲染底座，只加模板与锚点渲染。

**Non-Goals**
- 不搬 fd-shilun 的门面/公文专用逻辑（A 案：实论保留门面，仅后续单独 change 改调基座）。
- 不把写作编排做成工具；写作留在 skill。
- 不改 `caas-delivery` 投递信封四字段、不改 `material-store` 既有资产语义。
- 不内建任何采集通道。

## Decisions

### D1 信源层照 generation registry 做兄弟文件，不做新的派发机制
新增 `mcp_server/sources.json`（车道：domain → {provider, endpoint, tools, driver}）与
`registry.py` 内并列的 `SOURCE_DRIVERS` 映射。`source_search` 按域查表、调 driver。
**备选**：给 CaaS 单独做一套 plugin 加载器 —— 否决，理由是与既有 registry 重复，违背
「形态/契约只有一个实现点」。
- 新域 = 一条 `sources.json` 条目 + 一个 driver 函数；核心 `source_search` 不动。

### D2 provenance 以工作区共享契约为准，实论超集在独立 change 里升进契约
CaaS 车道返回的每条命中携带 `source`/`stable_id`/`as_of`，并 MAY 携带实论的覆盖度超集字段。
**超集字段的「唯一真源」收敛到 `openspec/specs/text-query-surface`，由工作区根那条独立
delta 完成**（本 change 只消费，不重定义）。**备选**：CaaS 自己再定一份 provenance ——
否决，那正是要消除的第四份平行形状。

### D3 引用作为物料库新资产型 + citations 表，不改既有 asset 语义
新增 citations 记录（绑定 `asset_id`、`source`/`stable_id`/`as_of`、`status`），按 owner
隔离，与既有 assets/variants 血缘并用但不改其列语义。`[[cite:<id>]]` 锚点是正文与记录的
绑定键。**备选**：把引用塞进 asset.metadata JSON —— 否决，检索/核验/按 owner 过滤都需要
可查询列，JSON blob 会让核验闸变成全表扫描。

### D4 核验闸复用实论引擎，先给引擎套一层可调用面
`serious-text-citation` 要求「复用不另造」。实论引擎现在是 FastAPI 内部模块；最小可行路径是
给它包一层可被 CaaS 调用的面（MCP 工具或 HTTP 端点，二选一在 tasks 里定），CaaS 侧只做
薄客户端。**这条是工作量增量，不是零成本接线**——design 必须点名它。

### D5 严肃版式＝doc 模板族 + 锚点渲染，内核零改动
`templates/doc/{gongwen-gb9704,academic-gbt7714,academic-apa,report}/` 各含
`template.json`+`style.css`；`doc_render.py` 增一个「扫 `[[cite:id]]` → 查 citations →
按格式生成参考文献 + 正文回引」的前处理步骤。解不出的锚点按「截断」处理（与
serious-text-citation 一致）。

### D6 render 前与 deliver 前各一道核验闸
render 前 preflight 拦截确定不通过（宁删勿假）；deliver 前 gate 确认引用已定稿
（通过或待核）。两闸读同一份 citations.status，不各算一遍。

## Risks / Trade-offs

- **实论引擎不是 MCP 工具** → P3 需先套可调用面，估算为一独立 task；若走 HTTP 直连需处理
  鉴权与出口策略（对外部学术源的代理口径已在 fd-shilun 部署里存在）。
- **超集字段若不在工作区契约先落** → CaaS 与实论会各留一份 provenance，正是要防的坑。缓解：
  provenance 收敛（工作区根 delta）作为 P1 前置，先于 CaaS 消费。
- **免费/受限信源被上游拦** → 车道空结果必带原因，UI/skill 需据原因区分「没有」与「上游坏」。
- **版面契约（GB/T 9704）细节多** → 首批只承诺版心/字号/行距/页码等可判定项，逐条落 scenario
  测试；不在本 change 追求全量排版合规。
- **锚点截断会改正文** → 「截断」作用于最终成稿而非用户源稿，需保证用户原稿不被静默改写。

## Migration Plan

1. **P1（工作区根）**：provenance 超集升进 `text-query-surface`。先合，后 CaaS 消费。
2. **P2（fd-caas）**：sources.json + SOURCE_DRIVERS + `source_search` + citations 表；
   加性、不改既有工具行为，可独立上线（无 cite 的旧成稿不受影响）。
3. **P3（fd-caas + fd-shilun）**：核验引擎套可调用面 → 两闸接线 → 严肃版式模板族 →
   锚点渲染。**回滚**：每条按镜像 tag 级回退；citations 表为新表，回退只需停用车道与闸，
   不回滚既有资产。

## Open Questions

- 核验引擎对 CaaS 的暴露形态：MCP 工具 vs HTTP 端点？（不改变 spec 的行为契约，tasks 里定）
- citations 的 `status` 是否要随每次核验留历史版本，还是只存当前态？（可先只存当前态）
- 实论侧改调 CaaS 基座的对接 change 何时开（本 change 不含，属后续）。
