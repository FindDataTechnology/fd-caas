# 加强 CaaS 基座：让它能产出严肃文本

## Why

CaaS 今天能把需求变成素材并排得好看，但流水线的第一段是**想象**——brainstorm 由 LLM
凭空起稿，没有信源。所以它出得了短视频和图文，出不了论文、公文、研究报告这类**每个论断
都得有出处**的严肃文本。实论（fd-shilun）已经把信源消费、引用核验、公文排版做出来了，
但那套能力困在一个单独产品里、且自造了一份无 spec 背书的 provenance 超集。

把「严肃文本」做成 CaaS 基座的一族可扩展能力，既让 CaaS 第一次能产出有出处的文本，又把
三份并行的 provenance 形状收敛成一份（工作区 `text-query-surface` 已有该契约，实论的超集
需升进去）。现在做，是因为信源提供方（fd-text-core / fd-legal-search-mcp /
fd-zhesi-reference）都已是 shared-instance MCP 面，CaaS 只需做适配器、不必搬任何检索代码。

## What Changes

- **新增信源层**：CaaS 长出与 generation registry 同构的**信源车道注册表**
  （`sources.json` + `SOURCE_DRIVERS`）。车道的 driver 是**纯适配器**——HTTP 调用现有
  信源 MCP 面，CaaS 仍是编排层，不自己爬、不新增爬取仓。
  - 首批车道：`papers`/`gov`/`news`（→ fd-text-core）、`legal`（→ fd-legal-search-mcp）、
    `philosophy`（→ fd-zhesi-reference）。新车道 = 一条 registry 条目 + 一个 driver。
  - 统一 provenance（`source`/`stable_id`/`as_of`，沿用 `text-query-surface` 契约，含实论
    的覆盖度超集字段）；空结果必带原因。
- **引用成为一等对象**：物料库新增 citation 记录（归属资产、按 owner 隔离），正文以
  `[[cite:<id>]]` 锚点引用；status 取 `resolvable|authentic|supported|pending|blocked`。
- **核验闸**：出稿前（render 前）与投递前（deliver 前）各一道闸，**调用 fd-shilun 既有
  verify 引擎**（不重写）——不可解/元数据不符/已撤稿 → 拦下不出稿；无法核实 → 标「待核」，
  不计通过，也不静默删。
- **严肃版式**：`render_document` 增模板族
  （`gongwen-gb9704` / `academic-gbt7714` / `academic-apa` / `report`），把
  `[[cite:<id>]]` 按选定格式渲染成编号参考文献。同一 render 内核，多套版式。
- **写作仍是 skill**：各族的选题/结构/证据纪律作为纯知识技能新增（`fd-vaas-brainstorm`
  的同侪），**不进 hosted 工具面**——遵守 CaaS「编排知识不以 MCP 工具暴露」与 ADR-0004。
- **实论改为消费基座**（A 案）：fd-shilun 保留门面与公文专用逻辑，检索/引用库/版式改用
  CaaS 基座，不再维护第二套。本 change 不改 fd-shilun 代码，只由 CaaS 侧提供基座；实论
  侧的对接是后续独立 change。

## Capabilities

### New Capabilities
- `source-lane`: CaaS 信源车道——按域派发检索到外部信源 MCP 面，统一 provenance 与空结果
  原因；车道可注册扩展，核心零改动。与 generation-registry 对称。
- `serious-text-citation`: 严肃文本的引用与核验——citation 作为物料库一等对象（owner 隔离、
  绑定资产）、`[[cite:id]]` 锚点、三级核验闸（可解/真实/支持）、分级处置（拦下/待核）。

### Modified Capabilities
- `presentation-plane`: `render_document` 增严肃版式模板族，并渲染 `[[cite:id]]` 为编号
  参考文献——同一渲染底座与品牌主题不变，新增版式与引用渲染行为。
- `unified-mcp-interface`: 托管工具面新增 `source_*` 命名空间（可托管、可计量）与核验入口；
  写作类知识不进工具面（沿用既有编排知识边界）。

## Impact

- **新增代码（fd-caas）**：`mcp_server/sources.json` + `SOURCE_DRIVERS`（registry.py 兄弟）；
  `tools/source.py`（source_search 适配器）；物料库 citations 表与资产型；核验闸接线点
  （render 前 preflight、deliver 前 gate）；`templates/doc/{gongwen-gb9704,academic-gbt7714,
  academic-apa,report}`；doc_render 增 cite 渲染。
- **依赖（只调用不改）**：fd-text-core、fd-legal-search-mcp、fd-zhesi-reference 的 MCP 面；
  fd-shilun 的 verify 引擎。
- **跨仓契约收敛（独立 change，工作区根）**：`openspec/specs/text-query-surface` 的 provenance
  要求升入实论覆盖度超集，供 CaaS 信源车道与三家共同读。
- **不改**：`caas-delivery` 投递信封契约（核验结论只作为 citation.status 随内容携带，不改
  信封四字段）；`material-store` 既有资产/血缘模型（citation 为新增类型，不改既有语义）。
- **工作量风险（须在 design 点名）**：fd-shilun 的 verify 引擎目前是 FastAPI 路由，不是 MCP
  工具；CaaS「直接调不重写」可能需先给它套一层可调用的面，这是 P3 的真实增量。
