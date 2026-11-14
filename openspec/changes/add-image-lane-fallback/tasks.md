## 1. 代码：图像产物形态

- [x] 1.1 `scripts/_ciyuan_image.py`：`data[]` 解析从「只认 `b64_json`」改为
      「`b64_json` 优先、缺失时取 `url`」；url 分支下载到 `output_paths[i]`
- [x] 1.2 url 下载的硬约束：仅 http/https、连接/读取超时、体积上限（默认与
      车道既有产物体积同量级）、`Content-Type` 与图像魔数双重校验；
      任一不符抛错并保留上游 URL 的主机名（不含查询串，避免签名泄漏）
- [x] 1.3 响应保持既有形状（`ok/provider/model/images[].local_path/file_size_bytes`），
      MUST NOT 把上游 URL 直接回给调用者
- [x] 1.4 单测（`tests/` 既有风格）：b64 形态回归、url 形态落盘、url 返回 HTML 失败、
      url 超体积失败、`data` 为空失败

## 2. 换上游（数据，不出片）

- [ ] 2.1 取一条可用的图像上游凭证：组 19 `fed-workbuddy-media`（acct 57，
      `grok-imagine-image`，`/v1/images/generations`）；确认该分组是否允许
      VAAS 所在账号使用（组 19 `is_exclusive=f`，但密钥属 `federation@` 用户，
      需决定是新建一把 VAAS 专用 key 还是复用）
- [ ] 2.2 写入 vault `vaas/pool/wanxing/image`：`{provider: ciyuan, base_url: <词元网关>,
      api_key: <组19 key>, model: grok-imagine-image}`；保留一份旧记录以便回滚
- [ ] 2.3 用真实 `generate_image` 打一发（不是 `registry_list_generators` 读状态），
      以「产物落盘 + 形状合规」为通过判据

## 3. 台账与镜像

- [ ] 3.1 实调通过后改 `mcp-server/mcp_server/lane_health.json`：
      `image.ciyuan` → `active`，`last_verified` 写当日，`reason` 写明生效的
      provider/model 与分组；`video.ciyuan` 保持 `down`（同一被封账号未解）
- [ ] 3.2 出片并滚到 china-<node-a>（`VAAS_HOSTED=1` 那台）；判据 = 容器内
      `lane_health.json` 内容 + 公网 `registry_list_generators` 回读
- [ ] 3.3 滚版后复跑一次 `generate_image`，确认走的是新车道

## 4. 结构成因与台账订正

- [x] 4.1 `docs/debt-ledger.md`：DEBT-LANE-9-01 补记结构成因「组 9 只有一个上游账号、
      `fallback_group_id` 为空 ⇒ 单点」，并记下组 19 这条可用替代车道与其成本（2K/$0.02）
- [x] 4.2 补一条「车道状态自证循环」的说明：`down` 会阻断上游探测，`last_verified`
      不会自己前进；复验是人的动作，不是系统行为
- [ ] 4.3 视频车道 `seedance-ciyuan` 的处置单独写清：与图像同源（acct 40），
      随账号替换/解封一起恢复，不在本 change 内先行翻状态

## 5. 验证

- [x] 5.1 `openspec validate add-image-lane-fallback --strict` 通过
- [x] 5.2 单测全绿；`scripts/_ciyuan_image.py` 对新形态有红证（先让用例在旧代码上失败）
- [ ] 5.3 现网回读：`registry_list_generators` 的 `image.ciyuan` 为 active 且
      `last_verified` 是本次实调日期

## 6. 复验探针（把手工配方变成工具）
- [x] 6.1 `scripts/probe_image_lane.py`：复用真实 `_ciyuan_image.generate`（不是平行实现），
      输出三态 `restored` / `still-blocked` / `cannot-judge`，退出码 0/1/2
- [x] 6.2 判据方向：**4xx 一律 `cannot-judge`**（401 key 无效、400 模型不受支持都指向
      我们自己的配置）；只有 200+HTML、5xx、`data[]` 空或形态不合规才算 `still-blocked`；
      网络不通单独记 `network`，不许当成「上游仍封着」
- [x] 6.3 安全：key 只从环境变量读，不外泄到输出；报错只带主机名不带查询串
- [x] 6.4 离线用例 9 条（三态各自成立 + 互不冒充 + key 不出现在输出里）；真打
      `router.finddatatech.cloud` 冒烟一次（无效 key → `credential_rejected`，不消耗额度）
- [ ] 6.5 拿到可用 key 后跑一次真复验，把结论回填 `docs/debt-ledger.md` 的
      DEBT-LANE-9-01-REVERIFY 行（前置=任务 2 的凭证决定）

## 7. 台账陈旧度（本轮新增）

- [x] 7.1 `mcp-server/mcp_server/lane_health.py`：台账条目自带陈旧度——
      `DEFAULT_STALE_AFTER_DAYS = 7`，支持条目级 `stale_after_days` 覆盖；
      `lane_health()` 视图新增 `age_days` / `stale`，随 `annotate()` 一路到发现面
- [x] 7.2 判不出不许当新鲜：时间戳缺失或不可解析 → `stale: null`（不是 `false`）
- [x] 7.3 8 条离线用例（新鲜/超期/条目级覆盖/坏日期/缺日期/未知车道/发现面带出/`lane_down` 不受影响）
- [x] 7.4 用真实台账复核：`voice.ciyuan` 为 `active` 但已 8 天未复验 → `stale: true`
      （**这正是陈旧 `active` 的形态：它会被当成「能用」推荐出去**）；
      `image/video.ciyuan` 3 天 → 未超期，不误报
- [ ] 7.5 复验周期是否要按车道类型再分档（小时级更短）、以及发现面展示文案，
      留给实体检后定；当前只做「标出来」不做「自动降级」
