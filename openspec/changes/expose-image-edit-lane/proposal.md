# Proposal

> ## 探针未通过（2026-11-21）：本 change 停在任务 1.5
>
> `probe_image_lane.py --edits` 与对照组 `--model grok-imagine-image`（generations）
> **同时**返回 HTTP 503；`/v1/models`、站点根路径、甚至**无效 key** 也都是 503
> （0.13s 返回，带 SafeLine 的 `sl-session` cookie）。
>
> 根因不是词元上游，是承载它的节点：**`cheap-7` (100.64.0.14) 自
> 2026-11-21T15:53:01Z 起 kubelet 停止上报**（`tailscale status` 显示
> `cheap7 … relay "cs"; offline`，ICMP/SSH 全丢）。sub2api 主 pod 由 Running 变
> **Pending**：PVC `sub2api-data-3` 是 local-path，PV 的 `nodeAffinity` 钉死 cheap-7
> ⇒ `0/7 nodes are available`。同挂该节点的 platform / falkordb / apisix /
> zhesi-api 一并不可达。
>
> 判据类别 = **`cannot-judge`**（基础设施故障），**不是** `still-blocked`。
> 按 `DEBT-LANE-9-01-REVERIFY` 的口径，这类结论 MUST NOT 被读成「上游仍封着」。
>
> **已完成且不依赖上游的部分**（任务 2/3/4/5 的代码面）：驱动 `edit()`、
> 源图上限、回显判据、bridge 分流、`edit_image` 工具与注册表、车道台账、
> 零价拒启闸、8 条新测试、562 条测试全绿。**未做**：真产物验证（1.1–1.4）、
> 出片上生产（6.x）。
>
> **解除条件**：cheap-7 恢复（或 sub2api 迁到别的节点）后重跑
> `probe_image_lane.py --edits`，`restored` 才允许继续 6.x。

## Why

词元网关早已开通 `POST /v1/images/edits`（sub2api 路由与 Grok 媒体端点都在役，最多 3 张源图），
但 VAAS 从未接这条出口：`generate_image()` 只收 `prompt/size/n/slug`，`generators/image.py`
的注释写着「reference_image (img2img) was never wired through the bridge」。
于是我们买到的「编辑」能力一次也没被用过，落盘的每一张图都是一次性抽卡——
不满意只能换提示词重抽，改一件外套等于重做整幅画面。

剧创那一侧的结构化产物（角色 model sheet、场景/道具 art bible）本就是要锁外观的，
可下游只把它们拼成字符串塞进 prompt，图与图之间没有任何共享锚点。
**能不能编辑图，决定了剧创的一致性是可证的还是靠文字猜的。**

现在做的理由：图车道已 active（`image.grok` 11-15、`image.ciyuan` 11-14 均实调销账），
视频车道暂不参与（编排类视频工具已充分，本线不做），
把有限的动作集中在「图片精细化修改」这一条上，收益最直接、验证最快。

## What Changes

- 新增生成方式 `edit_image`（type=image），调度到 `/v1/images/edits`；入参含
  `source`（必填，单张源图 asset 或路径）、`instruction`（必填，修改指令）、
  `mask`（可选，局部编辑区域图）、`references`（可选，最多 2 张补充参考图，
  与 source 合计不超过上游的 3 张上限）、`n`（可选）、`slug`（可选，重放键）。
- 编辑产物记入资产账本，并用 `parent_id` 指向源图 → 第几次改、从哪张改，全链可回溯
  （沿用 `material-store` 已有的血缘字段，不新增表）。
- `generators/image.py` 的驱动层加 edits 分支：`_ciyuan_image.py` 增加 edits 调用与
  同等级的魔数/体积/超时校验；返回形状与 `generate_image` 一致，另带 `source_asset_id`。
- 车道台账新增 `image_edit.ciyuan` / `image_edit.grok` 两条；`down` 时调用按 `lane_down`
  拒绝，MUST NOT 静默回退到文生图（静默回退 = 用户以为改了、其实抽了张新的）。
- 计价表补真价：`generate_image` / `edit_image` 不再是 `0.0`；hosted 校验从「表为空即拒启」
  加严为「存在零价车道即拒启」。
- 探针：扩 `scripts/probe_image_lane.py`，新增 `--edits` 模式，产物判据含
  「与源图字节不同且局部像素差超阈值」——否则说明上游 200 返回的是原图。
- **不做**：视频生成、视频编辑、视频延长（本线不参与）；不做 LoRA 训练/自托管推理；
  不做 mask 的画笔 UI（VAAS 只提供参数面与校验，UI 在消费方）。

## Capabilities

### New Capabilities

（无——本 change 只扩既有的两条能力，不引入新的系统行为边界。）

### Modified Capabilities

- `ciyuan-lane-providers`: 「图像车道生成与模型白名单」从只覆盖 `generations` 扩到同时覆盖
  `edits`，并把「编辑产物必须可与源图区分」写成硬判据。
- `generation-registry`: 「生成方式注册」新增 `edit_image` 条目与 image 类型多方式并存的约束；
  「方式可发现性」要求编辑车道同样携带健康状态，down 不得呈现为可用。

## Impact

- **代码**
  - `mcp-server/mcp_server/tools/generate.py`：新增 `edit_image()` 工具与文档串。
  - `mcp-server/mcp_server/generators/image.py`：edits 分支（不再以「无 E2E 路径」为由拒绝）。
  - `mcp-server/mcp_server/registry.py`：`image` 类型下注册 `edit_image`。
  - `scripts/litellm-bridge.py`：`image` 子命令增加 `--source/--mask/--instruction`，
    或在 `cmd_image` 内按是否给 `--source` 分流到 edits 端点。
  - `scripts/_ciyuan_image.py`：edits 请求构造与响应解析（multipart 与 JSON 两种形状都要认）。
  - `mcp-server/mcp_server/lane_health.json`：两条 `image_edit.*` 车道。
  - `mcp-server/pricing.json`：真价；`main.py` 的 `_load_pricing(strict=True)` 增加零价判定。
  - `scripts/probe_image_lane.py`：`--edits` 探针 + 差异判据。
- **接口**：全部加性。`generate_image` 签名不变；新增独立工具名 `edit_image`，
  避免在同一工具上做行为分叉（有 source 就编辑、没 source 就生成会让重放键语义含糊）。
- **依赖**：无新增。edits 请求仍走 stdlib HTTP（与 `_ciyuan_image` 现状一致），
  multipart 由标准库拼装。
- **风险**
  - 最大风险是**上游 200 返回原图**：`image.ciyuan` 已发生过「请求 grok-imagine-image、
    实际 hunyuan-image-alpha 承做」的漂移，编辑端点的承做方完全可能不支持编辑。
    探针的差异判据是这条的唯一挡板；判不出差异时只允许记 `cannot-judge`，
    MUST NOT 据此把车道判为 active。
  - 上游 `n>1` 只有 ciyuan 档认（`N_SUPPORTED_PROVIDERS`），edits 是否同样如此未知；
    实现按「驱动层循环 n 次、并在 warnings 披露」处理，不假设上游多图。
  - mask 是否被上游真正尊重未经实测。设计上把 mask 定为**可选增强**：
    缺少时按整图编辑，UI MUST NOT 承诺局部改动一定被遵守。
