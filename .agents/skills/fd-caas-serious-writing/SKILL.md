---
name: fd-caas-serious-writing
description: >
  严肃文本写作知识技能(纯知识,无脚本):论文 / 公文 / 研究报告三族的选题、结构、证据纪律,
  以及「每个论断都要有出处」的引用编排方法。写作在对话里完成——本 skill 提供模板与纪律,
  定稿后自驱下游 CaaS 工具链:sources_search 找真来源 → citation_record 记引用 →
  render_document(template=…,citations=…,cite_format=…) 出带编号参考文献的成稿。
  触发场景:用户说"写一篇论文/综述/开题报告"、"写一份公文(通知/请示/函/意见)"、
  "写一份研究报告/尽调备忘录"、"给这个结论配上出处"、或 /fd-caas-serious-writing。
  策划完不想停在文本、要直接出成稿的,由本 skill 串下游 MCP 工具。
compatibility: 纯知识技能,无脚本无依赖;下游调用 CaaS MCP 工具面(user/source_search、citation_record、citation_list、citation_gate、render_document、render_still、templates_list)。
---

# CaaS 严肃写作——知识技能

> 「严肃文本」= 每个论断都要能追到出处。本 skill 是三族（论文 / 公文 / 研究报告）的
> 选题、结构、证据纪律与引用编排知识。**写作是对话里的技能，不是工具**——工具面只给
> 确定性能力（找来源、记引用、出稿），编排知识在这里。

## 铁律（三族通用）

1. **先找源，后落笔**。不要凭记忆写"某某研究证明"。论断要有 `stable_id` 才写下去。
2. **引用是一等对象**：每用一个来源，先 `citation_record(asset_id, source, stable_id, as_of, ref_meta)`，
   拿到 `id`，正文里用 `[[cite:<id>]]` 锚点。
3. **宁删勿假**：出稿前必过 `citation_gate(text, asset_id)`——解不出或被判 blocked 的锚点会被截除，
   保留的是「通过」或「待核」。不要把 `待核` 当「已证」。
4. **来源覆盖要如实**：中文文献走开放元数据兜底时，provenance 会带 `coverage=partial`；据此在正文里
   说明"该结论资料有限"，不要写成定论。

## 工作方式

1. **确认族与体裁**：论文 / 公文（文种？）/ 研究报告；受众与投递面。
2. **找来源**：按域调 `source_search(domain, query)`——
   - 论文族：`papers`（→ fd-text-core，OpenAlex/arXiv）、`philosophy`（哲思参照系）
   - 公文族：`gov`（部委公文语料，用于文种模板与范式依据）
   - 研究报告族：`legal` / `news` / `papers` 按需
   每条命中带 `source/stable_id/as_of`；空结果带 `reason`（断了就说断了，不要编）。
3. **记引用**：`citation_record(...)` 落库拿 id。
4. **写正文**：按下方各族结构写，断言处挂 `[[cite:<id>]]`。
5. **出稿**：`render_document(source, format="pdf", template=<版式>, citations=<该 asset 的引用>, cite_format=<gbt7714|apa>)`。
   版式：公文=`gongwen-gb9704`；论文=`academic-gbt7714` 或 `academic-apa`；报告=`report`。
6. **交稿前**：`citation_gate(text, asset_id)` 复核 cleaned_text 与待核清单，如实向用户说明哪些是"待核"。

## 论文族结构

- 摘要（问题 / 方法 / 结论，结论必须能在正文找到出处）
- 引言：研究缺口（引 2–3 条实证支撑"缺口存在"）
- 相关工作：每条工作一句 + 引用
- 方法 / 论证：核心论断逐条挂引用
- 结论与局限
- 参考文献（由 `render_document` 自动按 cite_format 生成，不要手写）

## 公文族结构

- 版式必须用 `gongwen-gb9704`（版心/字号/行距/页码为可判定项）
- 文种结构从 `gov` 语料归纳（通知/请示/函/意见各有范式），不要凭空造格式
- 凡引用"上级文件/法规"必须指到 `gov` 语料的具体条目（stable_id）
- 无出处的口径 → 标"待核"，不得写成"根据有关规定"这类无源断言

## 研究报告族结构

- 摘要与结论先行（结论必须有数据/来源支撑）
- 分节正文，关键数字挂引用或说明来源与口径
- 图表用 `render_still(kind="chart")`，图表标题注明数据来源
- 参考文献（cite_format 同论文）

## 与工具面的边界

- 工具面**没有**"写作"工具：写作就是你现在在做的事。
- 工具面**有**：`source_search`（找源）、`citation_record`/`citation_list`/`citation_gate`（引用与闸）、
  `render_document`（出稿）、`render_still`（配图）、`templates_list`（看版式）。
- 找源断了（`reason=unreachable/quota`）就如实告诉用户"该源当前不可达"，不要用臆造内容补位。
