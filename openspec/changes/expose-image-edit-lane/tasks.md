# Tasks

## 1. 探针先行（唯一前置门：不通过不排期）

- [x] 1.1 `scripts/probe_image_lane.py` 增加 `--edits` 模式：取一张内置源图，提交一条
      可判定的修改指令（如「把背景改为纯蓝」），落盘产物。
- [x] 1.2 产物判据三件套：魔数可识别、`file_size_bytes` 与磁盘一致、**与源图字节不同**；
      另记录像素差诊断量（不设硬阈值）。
- [x] 1.3 退出码语义对齐 `DEBT-LANE-9-01-REVERIFY`：`restored` / `still-blocked`
      （细分 `html_or_nonjson` / `http_5xx` / `echoed_source`）/ `cannot-judge`
      （`credential_rejected` / `bad_request` / `network`）。`cannot-judge` MUST NOT
      被读成「上游不行」。
- [x] 1.4 对 `image.ciyuan` 与 `image.grok` 两个 provider 各跑一次，把结论写进
      `lane_health.json` 的 `image_edit.*` 条目（含 `last_verified`）。
- [x] 1.5 若两个 provider 都判 `still-blocked` 或 `cannot-judge`：停止本 change，记录
      证据并在 proposal 顶部加一段「探针未通过」实况说明。
      **2026-11-21 曾触发**（cheap-7 失联 → cannot-judge）；**2026-11-22 已解除**：
      节点恢复后又连破三关（TTS 钥用错组 / 模型名写成混元 / 请求体用 images[]），
      最终探针判 `restored`、E2E 判 `ok`。详情见 proposal 顶部。

## 2. 驱动与桥接

- [x] 2.1 `scripts/_ciyuan_image.py` 增加 `edit()`：构造 edits 请求（源图字节或 URL、
      instruction、可选 mask、可选补充参考图），复用既有 `_detect_format` 与体积/超时校验。
- [x] 2.2 源图数量校验：源图 + 补充参考图 > 3 时在调用前拒绝，错误写明上限与实际数量。
- [x] 2.3 落盘前比较产物与源图字节，完全相同即失败并归类为 `echoed_source`
      （代码注释注明该判据的边界：判不了「几乎没改」）。
- [x] 2.4 `scripts/litellm-bridge.py` 的 `image` 子命令增加 `--source/--mask/--instruction`，
      按是否给 `--source` 分流 generations / edits；二者同给时报错。
- [x] 2.5 `n>1`：驱动层循环 n 次逐张校验，响应 `warnings` 披露「多次调用而非上游多图」。
- [x] 2.6 单元测试：源图超限被拒、回显原图被判失败、产物扩展名与实际容器一致。

## 3. 工具面与注册表

- [x] 3.1 `mcp-server/mcp_server/tools/generate.py` 新增 `edit_image(source, instruction,
      mask=None, references=None, n=1, slug=None)`，文档串写明 mask 为可选增强、
      与 `generate_image` 的区别。
- [x] 3.2 `mcp-server/mcp_server/generators/image.py` 增加 `edit()` 入口，删除
      「reference_image 从未接线」的注释（该限制已解除）。
- [x] 3.3 `mcp-server/mcp_server/registry.py` 在 image 类型下注册 `edit_image` 条目
      （provider/model 与 `generate_image` 同源但独立）。
- [x] 3.4 编辑产物用 `parent_id` 指向源资产；单元测试断言父子可查、方向单一。

## 4. 车道与计价

- [x] 4.1 `lane_health.json` 新增 `image_edit.ciyuan` / `image_edit.grok` 两条。
- [x] 4.2 `pricing.json` 填 `generate_image` 与 `edit_image` 真价，`_comment` 标注
      取值日期与来源；删除「image/video 车道 down、保持 0.0」的过期说明（视频部分保留）。
- [x] 4.3 `mcp-server/main.py` 的 `_load_pricing(strict=True)` 增加零价条目判定：
      任一启用车道单价为 0 即拒启，错误列出零价条目名。
- [x] 4.4 启动自检用例：零价表必须拒启、缺表必须拒启、正常表放行。

## 5. 注册表发现面与拒绝路径

- [x] 5.1 `registry_list_generators` / `models_voices` 对 `edit_image` 透出
      `image_edit.*` 车道健康，不与 `image.*` 混用。
- [x] 5.2 编辑车道 down 时调用返回 `lane_down` 分类错误，不静默回退文生图。
      （`edit()` 在 `prepare()` 之前读 `image_edit.<provider>` 车道状态，down 即抛
      `CredentialUnavailable`；代码路径已实现并有单测。真上游的 lane_down 实测待节点恢复。）
- [x] 5.3 端到端验收脚本：一次生成 → 一次编辑 → 断言父资产、断言两张产物字节不同。

## 6. 上线验收

- [x] 6.1 出片（公开仓线）并在现网 `registry_list_generators` 看到 `edit_image` 条目
      与两条 image_edit 车道状态。
- [x] 6.2 现网真调一次编辑，产物落盘、账本记 `usage_logs` 一行（不是 0 计费）。
      2026-11-22 达成：`e2e_image_edit.py` 五项断言全过（生成→编辑→产物不同→父子血缘→
      两条 usage 记录），`usage_logs` 真计费 $0.02/张（组19 acct57）。
      **出片也已落地**：`d75f2c8` → origin/main → GHA `image` run `44978544405` success →
      `hkccr`/`ccr` 双 registry 均见 `yizuo/vaas-mcp-server:sha-7e6323d`（同 digest `6695924d`）。
      ⚠️ **但没有生产实例**：该镜像目前无任何 k8s Deployment / docker compose 栈 / facet
      registry 条目引用它（VAAS 只在本地 stdio 跑）。"镜像可拉取" ≠ "已上产"，
      部署与现网实证是一条尚未存在的线，见 docs/debt-ledger.md DEBT-VAAS-DEPLOY-01。
- [x] 6.3 `docs/debt-ledger.md` 增补一行：编辑车道的承做方漂移风险与复验方式。
      （DEBT-EDIT-01，含复验命令与三种判据的处置方向。）

## Workflow follow-up

- 归档前需 `openspec sync-specs` 把两份 delta 合入主规格（`ciyuan-lane-providers`、
  `generation-registry`），之后 `openspec archive`。
- 剧创侧 `juchuang-image-iteration` 依赖本 change 的 6.2 结论；若 1.5 触发则剧创侧
  只能走「换一版」路径，需在那边重新裁剪任务。
