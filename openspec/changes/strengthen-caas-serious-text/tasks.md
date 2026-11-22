# Tasks

## 前置依赖（跨仓，另一个 OpenSpec change）

- **P1 provenance 契约收敛**（工作区根 `openspec/changes/extend-provenance-coverage-contract`）：
  把实论 `Provenance` 的覆盖度超集字段升进 `openspec/specs/text-query-surface` 的
  「每条结果 SHALL 带 provenance 契约」要求。**须先合，P2 才消费**——否则 CaaS 与实论各留
  一份 provenance，正是要防的第四份平行形状。

## 1. 信源层（source-lane）

- [x] 1.1 新增 `mcp-server/mcp_server/sources.json`，声明首批车道
  （papers/gov/news→fd-text-core、legal→fd-legal-search-mcp、philosophy→fd-zhesi-reference），
  每条含 domain/provider/endpoint/tools/driver。验证：文件可被加载、schema 校验通过。
- [x] 1.2 新增与 registry 同构的 `sources.py`（`SOURCE_DRIVERS` 映射 +
  `source_search(domain, query)` 派发）；未注册域报错并回显可用域。验证：单测「未知域报错」+
  「已知域走对应 driver」。
- [x] 1.3 实现各车道 driver 为**适配器**：HTTP 调用对应信源 MCP 面，归一为统一 provenance
  （`source`/`stable_id`/`as_of`）。验证：mock 信源面返回，断言每条命中带三元组。
- [x] 1.4 空结果与上游异常必带原因。验证：单测「上游失联→reason=unreachable，非空列表静默」。
- [x] 1.5 托管计量与租户隔离：source_search 记入租户用量；凭据不落日志/响应。验证：托管模式
  单测断言用量行 + 响应体不含凭据。

## 2. 引用一等对象 + 核验闸（serious-text-citation）

- [x] 2.1 物料库新增 citations 表（绑定 asset_id、provenance、status、owner）。
  验证：`vaas-init-db` 幂等建表；既有库启动无副作用（复用 material-store 自举口径）。
- [x] 2.2 引用回查与 owner 隔离。验证：单测「按 id 回查命中」+「跨租户访问被拒」。
- [ ] 2.3 给实论 verify 引擎套一层可调用面（默认 MCP 工具，与车道同构；
  若改 HTTP 端点需处理鉴权与出口）。验证：CaaS 侧薄客户端能调通、返回判定与依据。
  **注：这条是工作量增量——引擎现为 FastAPI 内部模块，非现成 MCP 工具。**
  **范围裁定（A 案）**：给引擎套可调用面 = 改 fd-shilun 代码；本 change proposal 明确
  「不改 fd-shilun 代码，实论侧对接是后续独立 change」。故本任务**顺延到实论对接 change**
  （fd-shilun 侧新增 `verify` 可调用面 + CaaS 薄客户端），不在此 change 内完成。CaaS 侧已就绪：
  `citation_record` 落 status、`citation_gate` 按 status 三态处置，只差上游判定来源。
- [x] 2.4 锚点截断：保存/渲染前逐个 `[[cite:<id>]]` 对照 citations，解不出即从最终成稿截除，
  不改用户源稿。验证：单测「含假锚点→成稿无该锚点、源稿不变」。
- [x] 2.5 render 前 preflight 闸 + deliver 前 gate：确定不通过即拦；无法确定标「待核」保留、
  不计通过。验证：单测「假引用被拦」+「无全文→pending 保留且不计通过」。
  （render 侧闸已落：`_render_citations` 对 blocked/未知锚点不渲染编号，测试覆盖；
  `citation_gate` 工具提供 cleaned_text + 三态；deliver 侧闸由投递面在发送前调
  `citation_gate` 确认无 blocked——属投递面接线，见备注。）

## 3. 严肃版式 + 锚点渲染（presentation-plane）

- [x] 3.1 新增模板族 `templates/doc/{gongwen-gb9704,academic-gbt7714,academic-apa,report}/`
  （各 template.json + style.css），复用既有字体栈与品牌主题。验证：`templates_list(domain=doc)`
  列出四个新版式、未知版式报错列清单。
- [x] 3.2 `doc_render.py` 增「扫锚点→查 citations→按格式生成编号参考文献+正文回引」前处理。
  验证：单测「GB/T 7714 与 APA 各出正确列表、正文回引同号」。
- [x] 3.3 解不出的锚点不渲染出处。验证：单测「假锚点→不产出编号、不报假出处」。
- [x] 3.4 端到端冒烟：一份含真实引用的源经 `render_document(template=gongwen-gb9704)` 出稿，
  参考文献编号与正文回引一致、版心/字号/行距/页码可判定项符合。验证：冒烟探针产出 pdf。

## 4. 写作技能（不进工具面）

- [x] 4.1 新增严肃文本写作知识技能（各族选题/结构/证据纪律），照 `fd-vaas-brainstorm` 纯知识
  形态，驱动下游 `source_search`→渲染→核验。验证：技能内不引用任何编排类 MCP 工具、
  托管工具清单不含写作工具（对照 unified-mcp-interface 断言）。

## 5. 集成验证

- [x] 5.1 托管面工具清单断言：含 `source_*` 与核验入口、不含写作编排工具。
  验证：`test_hosted_surface` 扩一条断言。
- [x] 5.2 三阶段回滚演练：停用车道与两闸后，既有生成/渲染/发布路径行为与改动前一致
  （citations 新表不影响旧成稿）。验证：既有测试全绿 + 一份无 cite 旧稿照常出片。

## Workflow follow-up

- 本 change 落地后，另开实论侧对接 change（fd-shilun 改调 CaaS 基座；A 案不改门面，只换
  检索/引用库/版式的内部实现）。
- 完成后按 openspec archive 流程归档（注意 REMOVED 段须交给 archive CLI 处理，不手改主 spec）。
