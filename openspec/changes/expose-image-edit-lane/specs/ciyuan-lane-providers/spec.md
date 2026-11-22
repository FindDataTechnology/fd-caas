## MODIFIED Requirements

### Requirement: 图像车道生成与模型白名单

图像生成 SHALL 以 OpenAI 图像协议调用词元 `POST /v1/images/generations`，并仅使用实测
返回合规 JSON 产物的模型；返回非 JSON（如 HTML）的模型 MUST NOT 作为默认或推荐值。

带源图的图像编辑 SHALL 以同一协议的 `POST /v1/images/edits` 调用，并与生成共用
同一套产物校验。一次编辑请求的源图数量（源图 + 补充参考图）MUST NOT 超过上游声明的上限；
超出 SHALL 在调用前拒绝并指明上限，MUST NOT 截断后静默提交。

编辑产物 MUST 可与源图区分：落盘字节与源图完全相同 SHALL 判为失败，
MUST NOT 作为成功产物写入账本或返回给调用方。

#### Scenario: 图像生成成功落盘

- **WHEN** 调用者请求文生图且凭证为 ciyuan 档
- **THEN** 系统请求 `/v1/images/generations` 并解析响应的 `data[].b64_json`
- **AND** 解码后的图像字节按 VAAS 命名约定写入产物目录
- **AND** 响应返回产物相对路径列表，形状与既有图像响应一致

#### Scenario: 图像车道返回非 JSON 应答

- **WHEN** 词元图像车道对某模型返回 `Content-Type: text/html` 或不可解析的响应体
- **THEN** 该次生成失败并明确报错（说明该模型应答形状不合规）
- **AND** MUST NOT 从 HTML 中猜测或提取图片 URL 充当产物

#### Scenario: 默认图像模型可服务

- **WHEN** 使用 ciyuan 档凭证且调用者未指定模型
- **THEN** 系统使用默认模型 `gemini-3-pro-image`（2026-10-16 实测返回合规 b64_json）

#### Scenario: 带源图的编辑返回可区分产物

- **WHEN** 调用者提交一张源图与一条修改指令
- **THEN** 系统请求 `/v1/images/edits`，产物按与生成相同的魔数与体积校验落盘
- **AND** 产物字节与源图不同；若完全相同则该次调用判失败并说明上游未执行编辑

#### Scenario: 源图数量超上限

- **WHEN** 源图与补充参考图合计超过上游声明的上限（Grok 媒体车道为 3）
- **THEN** 系统在发起调用前拒绝，错误信息指明上限与实际数量
- **AND** MUST NOT 丢弃多余图像后继续提交

#### Scenario: 编辑车道无可用后端

- **WHEN** 编辑车道健康状态为 `down`
- **THEN** 编辑调用以车道不可用失败返回，附原因类别与最后验证时间
- **AND** MUST NOT 静默回退为一次文生图调用
