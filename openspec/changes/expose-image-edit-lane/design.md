# Design

## Context

现状（见 proposal.md - Why）：`generate_image()` 只有 `prompt/size/output_format/n/slug`
五参，驱动 `generators/image.py → scripts/litellm-bridge.py image → _ciyuan_image.py`，
全部指向 `/v1/images/generations`。`generators/image.py` 里保留了一行注释说明
reference_image 从未接线。词元侧 `/v1/images/edits` 已存在（sub2api
`handler/grok_media.go`：`GrokMediaEndpointImagesEdits`；`service/grok_media.go`
`grokMediaMaxEditSourceImages = 3`），并支持把官方 image 对象规范化为 `image_url`
形状（`prepareGrokMediaForwardBody`）。

车道事实（`lane_health.json`，2026-11-15）：
- `image.grok` active（实调 1,500,323 B PNG 1024x1024，真记账）
- `image.ciyuan` active（但披露「请求 grok-imagine-image、hunyuan-image-alpha 承做」）
- 视频车道本线不涉及。

约束：
1. `pricing.json` 的 `generate_image` 仍为 `0.0`，`_load_pricing(strict)` 只拦空表，不拦零值。
2. 上游 `n>1` 只有 ciyuan 档认（`N_SUPPORTED_PROVIDERS = {"ciyuan"}`）。
3. 车道闸是 fail-closed 的：`lane.health=down` 时请求到不了上游，`last_verified` 不会自愈。

## Goals / Non-Goals

**Goals**
- 让「带源图的精细化修改」成为一等出口：独立工具、独立车道、独立计价、独立健康状态。
- 编辑全程可回溯：产物 → 源资产的血缘单向可查。
- 探针可在没有人工判断的前提下判定「上游真的编辑了」而不是回显原图。

**Non-Goals**
- 视频生成 / 视频编辑 / 视频延长（不做，理由见 proposal「不做」段）。
- 自托管推理、LoRA 训练、模型微调。
- mask 的绘制 UI（VAAS 只收参数并披露上游是否尊重）。
- 多图批量编辑的事务语义（部分成功即部分落账，不做回滚）。

## Decisions

### D1. 独立工具 `edit_image`，不在 `generate_image` 上加 `source` 参数

**选择**：新增工具名，注册表 image 类型下多一条目。

**为什么**：同一 name 的重放键语义必须唯一。若 `generate_image(source=None)` 与
`generate_image(source=X)` 都叫 `generate_image`，那么 slug 派生、计费单价、
错误分类、车道健康这四件事全部要在同一处做二分叉——而它们在上游是两条不同端点。
四处分叉必有一处漏掉，最可能漏的是计费（一次编辑按文生图单价收费）。

**备选**：在 `generate_image` 加可选 `source`。省一个工具名，但把上面的分叉成本转嫁
给每个下游消费方；且 `N_SUPPORTED_PROVIDERS` 这类上游差异表需要变形成二维。

### D2. 驱动层走新分支，而不是绕开 `litellm-bridge` 直连

**选择**：`_ciyuan_image.py` 增加 `edit()`，`litellm-bridge.py` 的 `image` 子命令
增加 `--source/--mask/--instruction`，`generators/image.py` 加 `edit()` 入口。

**为什么**：既有三条链（volcengine 直连、ciyuan、grok）共用同一桥接与同一安全包封
（魔数、体积上限、超时、URL 白名单）。绕开桥接等于复制一份安全包封，两份必然漂。

**备选**：新写 `_ciyuan_image_edit.py`。只在这一层就必须重新实现魔数与体积校验——
这正是 `add-image-lane-fallback` 记录过的「同一车道两种形态、两套信任级别」的教训来源。

### D3. 编辑产物必须与源图字节不同，作为硬判据

**选择**：驱动层在落盘前比较编辑产物与源图字节；完全相同即失败。

**为什么**：这是「200 + 回显原图」的唯一机器判据。`image.ciyuan` 已经出过
「请求名与承做名不一致」的实祸，编辑端点的承做方更可能根本不支持编辑而选择静默返回原图。
没有这条闸，"车道 active" 就可能是假的。

**限制（写进代码注释）**：字节相同能判死「原图回显」，但判不了「几乎没改」。
探针另加像素差阈值作为诊断量，但不作为运行时的硬失败条件——
上游对极保守的指令（如"轻微提亮"）返回近似图是合法的。

### D4. 车道粒度：`image_edit.ciyuan` / `image_edit.grok` 两条独立车道

**选择**：与 `image.ciyuan` / `image.grok` 分开记。

**为什么**：spec `generation-registry`「方式可发现性」已把「down 车道不得呈现为可用」
写成硬约束。若共用一个车道名，编辑端点被封会连带把文生图判死（或反之），
而两者的上游端点、账号可用性、被封概率都不同。分开是最低成本的正确做法。

### D5. 计价与拒启

**选择**：`pricing.json` 的 `generate_image` / `edit_image` 填实价；
`_load_pricing(strict=True)` 增加「存在零价条目即拒启」。

**为什么**：现网 `image.grok` active 但 `generate_image` 单价 0.0 ⇒ 每出一张图
我们付上游、收客户 0。目前靠"没人用"侥幸没爆。编辑一旦接上剧创就是持续消耗，
零价必须在这一版之前堵死。

**风险**：真实牌价我没法在仓内查到（`pricing.json` 注释说按上游牌价核对）。
实现时以词元后台/合同价为准填入，并写入 `_comment` 标注取值日期与来源；
**这一项必须在 apply 之前由人补真值**，仓内不猜。

### D6. 探针优先，先于任何工具代码

**选择**：任务序列第一条就是扩 `probe_image_lane.py --edits`。

**为什么**：`DEBT-LANE-9-01-REVERIFY` 的教训是「车道闸 fail-closed 会让状态永远不自愈」。
接入一个新端点前先拿一次真产物，是把"存在 ≠ 可用"变成可判定事实的最低成本方式。
探针判 `cannot-judge`（凭据/网络/参数问题）时，语义是「先修我们的配置」，
不是「上游不行」——这个区分必须体现在退出码与报告字段上。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| 编辑端点只能返原图（承做模型不支持编辑） | D3 硬判据 + D6 探针先行；探针判不过就不排期、不宣传 |
| mask 被上游忽略 | design 层定为可选增强；响应 warnings 如实披露；UI 不承诺局部改动 |
| `n>1` 在 edits 上语义未知 | 驱动层循环 n 次、逐张校验；warnings 披露「多次调用而非上游多图」 |
| 零价计价被漏改 | D5 加拒启闸；并加一条启动自检用例（零价表必须拒启） |
| 多图编辑部分失败 | 已落盘的算成功、未落盘的报失败；不做回滚，warnings 列失败清单 |

## Migration Plan

1. 探针通过 → 记 `image_edit.*` 车道为 active 并写 `last_verified`。
2. 工具上线但默认不宣传：`registry.list_generators` 会自然带出新条目。
3. 计价真值填入后重启 hosted 实例（`_load_pricing(strict=True)` 在启动路径上）。
4. 回滚：删注册表条目即可（工具面消失）；车道台账回写 down 亦阻断调用；
   pricing 回滚只影响计费不影响出片。

## Open Questions

- 词元 `/v1/images/edits` 是否接受应用户端资产 id 直接引用（免去每次上传源图字节）？
  当前设计按「传字节或 URL」实现，若上游支持 id 引用可在不改规格的前提下优化传输。
- 编辑产物是否应自动继承源资产的 `metadata`（如角色绑定）？当前按不继承处理
  （新资产独立），留待剧创侧需求明确后再定。
