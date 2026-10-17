# 模型 Provider 接入范围与能力边界

记录日期：2026-10-17（北京时间）  
状态：官方资料调研与扩展范围；实现进展以第 1 节及独立验收文档为准。

## 1. 目标与当前基线

用户要求后续 Provider 尽量覆盖市场上常见的大模型。项目采用“统一教学调用契约 → 协议适配器 → 厂商配置 → 具体模型能力注册”的组织方式，减少新增厂商时对陪学、圆桌、我教 AI、规划业务的改动。

本文的厂商调研只阅读公开官方资料，没有为比较厂商调用付费模型或读取密钥。下文将已核实的接口事实、建议的工程实现、仍需账号验证的事项分别列出。接口会变化，正式接入时应重新核对官方文档及账号控制台。

现有 `app/llm/client.py` 是遗留同步 HTTPX OpenAI 兼容调用实现，具备文本调用、提示词约束 JSON、Pydantic 校验及测试假实现；默认超时 180 秒。它可作为迁移参考，也不证明已支持下面每家厂商。正式学习流程目前仍使用固定开发内容与模板。

同期实现进展：已新增 `app/llm/provider.py` 异步统一文本接口、Fake Provider、DeepSeek/通用 Chat Completions 适配及 `probe.py`。22 项单测通过，并在用户另行授权下，完成 DeepSeek 官方服务的一次固定开发文本连通：`deepseek-flash`，最大输出 128 token，无重试，实际记录 19 输入 / 1 输出 token。此次不使用学生数据，也未接入学生运行链路。通用兼容路线当前仅通过模拟测试，Claude/Gemini 原生适配未实现；细节见 [39 号验收文档](../进度/39-统一Provider首版与DeepSeek开发连通验收.md)。

“**当前未接真实模型，不自动理解课程正文或评定教学效果**”在正式学生功能中仍成立：独立连通探测不等于陪学接入，更不等于教学有效性验证。外部调用进入正式流程之前，需要先完成长运行任务、事务边界、取消恢复、用量与预算控制。

## 2. 已核实的厂商与服务接入方式

表中的地址是官方示例或路径形态，不是本项目已启用的地址。所有厂商的账号开通、所在地区、可用模型与能力均需真实接入时确认。不把某型号的能力推广为全厂商能力，也不固定“最新型号”和价格。

| 厂商/服务 | 官方资料核实到的协议与鉴权 | 关键差异及本项目处理建议 | 官方一手资料 |
|---|---|---|---|
| OpenAI | 提供 Responses 与 Chat Completions；API Key 使用 `Authorization: Bearer …`。 | OpenAI 官方建议新项目考虑 Responses；保留独立 Responses 适配器和兼容 Chat 适配器。二者输入、事件、工具与结果结构不应混用；参数支持按具体模型区分。 | [API 概览](https://developers.openai.com/api/reference/overview)、[Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) |
| Anthropic Claude | 原生 Messages：`https://api.anthropic.com/v1/messages`；官方示例使用 `x-api-key` 与 `anthropic-version`。 | 独立 Messages 适配器；处理内容块、顶层 system、工具结果与流事件，不将其强行当成 Chat choices。API 版本应进入配置快照。 | [Messages API](https://platform.claude.com/docs/en/api/messages/create) |
| Google Gemini | 官方提供 Gemini API 及 OpenAI 兼容入口，兼容示例 base URL 为 `https://generativelanguage.googleapis.com/v1beta/openai/`，使用 Gemini API Key；原生 API 示例使用 `x-goog-api-key`。 | 基础对话可走兼容层，原生扩展另建 Gemini 适配器；原生与兼容接口的鉴权、内容块和工具参数分开处理。Key 类型和账号权限以接入时官方要求为准。 | [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai?hl=en)、[API keys](https://ai.google.dev/gemini-api/docs/api-key?hl=en) |
| DeepSeek | 官方首次调用指南列出 OpenAI 兼容入口 `https://api.deepseek.com`，并列出 Anthropic 兼容方式；API Key 鉴权。 | 优先核对 Chat 兼容基础文本；思考模式、reasoning 字段、工具调用和 JSON 能力逐型号登记。官方页面本轮搜索索引可读取，但部分正文抓取不稳定，接入时需重新核对选定路径和请求体。 | [官方首次调用指南](https://api-docs.deepseek.com/guides/codex)、[官方 JSON/工具能力说明](https://www.deepseek.com/en/news/api-upgrade/) |
| 阿里云百炼 / Qwen | 官方明确支持 OpenAI Chat 兼容接口，配置 API Key、base URL、model。当前文档示例按业务空间和地域配置 endpoint。 | endpoint、业务空间、地域与 Key 一起保存；官方说明地域 Key 与 endpoint 必须匹配。不能将北京、新加坡等区域视为可任意互换；本地开源 Qwen 另按实际 Ollama/vLLM 服务注册。 | [OpenAI Chat 接口兼容](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope) |
| 智谱 GLM | 对话补全示例为 `https://open.bigmodel.cn/api/paas/v4/chat/completions`，使用标准 Bearer API Key，消息/choices 结构可作为 Chat 兼容接入依据。 | 独立厂商配置，普通 API 与其他套餐、国际服务分别核对；reasoning、工具和多模态仅按所选型号及 endpoint 开放。不得沿用旧客户端默认 GLM 就宣称完成真实接入。 | [官方对话补全参考](https://docs.bigmodel.cn/api-reference/模型-api/对话补全) |
| 豆包 / 火山方舟 | 官方数据面示例 base URL 为 `https://ark.cn-beijing.volces.com/api/v3`，支持 Chat 与 Responses；API Key 可使用 Bearer，另有 Access Key 鉴权方式。 | 区分推理数据面与资源管理管控面；按官方指定的 model ID/推理接入点使用。普通模型 API 与 Coding Plan 等专用 endpoint、Key 不能混用。 | [Base URL 与鉴权](https://docs.volcengine.com/docs/ark/base-url-and-authentication?lang=en)、[快速入门](https://docs.volcengine.com/docs/ark/quick-start?lang=en) |
| Kimi / 月之暗面 | 官方国际站快速入门含 OpenAI Chat SDK 示例，base URL 为 `https://api.moonshot.ai/v1`、Bearer API Key；页面同时列出 Responses/Anthropic Messages 接入方向。 | 分开登记协议与区域，不自动把国际示例换成国内地址；国内账号 endpoint 本轮中文站抓取超时，待重新核对。思考模式、工具历史和结构化输出按对应官方页面及型号确认。 | [官方快速入门](https://platform.kimi.ai/docs/overview)、[原中文文档入口，待复核](https://platform.moonshot.cn/docs/guide/start-using-kimi-api) |
| 百度千帆 | 官方 SDK 文档使用 OpenAI Client、API Key 和 `https://qianfan.baidubce.com/v2` 调用 Chat。 | 选当前账号对应的 V2 普通模型接口；旧版 access token 路线、应用调用所需 appid、专属套餐地址分别配置，不混作同一接口。 | [Python SDK 调用 Chat](https://cloud.baidu.com/doc/qianfan-docs/s/Fm9l6ocai) |
| 腾讯混元 / TokenHub | 混元官方已有 OpenAI Chat 兼容入口，旧示例为 `https://api.hunyuan.cloud.tencent.com/v1`、Bearer API Key。官方明确提示相关功能逐步迁移 TokenHub，新开服务应查看新平台。 | 旧混元兼容配置可保留迁移入口；新账号优先核对 TokenHub 的地区、endpoint、Key 与型号。云 API 的 SecretId/SecretKey 与兼容 API Key 不混用。TokenHub 具体路径和实际授权需接入时确认。 | [混元兼容调用及迁移提示](https://cloud.tencent.com/document/product/1729/111007)、[鉴权凭证区别](https://cloud.tencent.com/document/product/1729/97730)、[TokenHub 混元调用指南](https://cloud.tencent.com/document/product/1823/132252) |
| MiniMax | 官方提供 OpenAI SDK 与 Anthropic SDK 接入文档；当前中国站 OpenAI 示例 base URL 为 `https://api.minimax.cn/v1`，使用 API Key。 | 两种协议分别注册，国内/国际地址分别核对。官方列出部分兼容参数会被忽略；思考字段和工具历史需要专门映射，不能只保留正文而丢失协议所需状态。 | [OpenAI SDK](https://platform.minimax.cn/docs/api-reference/text-openai-api)、[Anthropic SDK](https://platform.minimax.cn/docs/api-reference/text-anthropic-api) |
| SiliconFlow / 硅基流动 | 官方 Chat Completions 参考使用 Bearer API Key，提供 stream、tools、response_format 等字段。 | 这是托管平台，模型 ID、版本、能力和授权以平台为准；同名开源模型在硅基流动与原厂直连属于不同注册项。不能从“请求字段存在”推断所有托管模型都支持该字段。 | [官方 Chat Completions 参考](https://docs.siliconflow.cn/docs/api/chat-completions-post) |
| Ollama 本地服务 | 官方有原生及 OpenAI 兼容 API；兼容示例 `http://localhost:11434/v1/`，示例中的 SDK api_key 是占位，服务会忽略。 | 按已安装服务版本和实际加载模型声明能力。内网开放需由部署层落实访问控制，不能把占位 Key 当成有效身份验证；本地模型名称不等于云端同名 API 的完整能力。 | [官方 OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility) |
| vLLM 本地/内网服务 | 官方提供 OpenAI 兼容 HTTP server，可配置 API Key；Chat 依赖适合的模型与 chat template。 | 固定服务版本、模型和模板，核对工具解析器及结构化输出配置。官方说明 API Key 并不保护服务所有路径，部署时需要限定暴露面；不与项目现有 8000 端口冲突。 | [官方 OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/) |

### 2.1 可扩展的云平台入口

以下纳入架构扩展范围，不作为首期全部实现的承诺：

| 服务 | 已核实事实与差异 | 官方资料 |
|---|---|---|
| Azure OpenAI | 官方支持 API Key 或 Microsoft Entra ID 方式；API 请求中的 model 通常填写资源中的部署名称，不能直接假定等同原始模型 ID。配置需要资源 endpoint、部署与凭证方式。 | [切换 OpenAI 与 Azure OpenAI endpoint](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/how-to/switching-endpoints) |
| Amazon Bedrock | Converse / ConverseStream 通过 bedrock-runtime 提供一致消息调用；需要对应 InvokeModel/流式权限。适配需保留 AWS 区域、身份与模型/资源标识。具体凭证链和选择的访问方式待接入时确认。 | [Converse API](https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html) |

其他厂商和模型服务可按相同流程新增。未开展官方核实的服务不能在界面标记为“已支持”或默认启用。

## 3. 工程建议：协议、厂商、模型分开管理

以下为完整目标设计；当前已完成的基础文本接口与独立探测见第 1 节，其余不视为已有实现。

### 3.1 协议适配器

建议划分为：`fake`、`openai_chat`、`openai_responses`、`anthropic_messages`；需要原生能力时再加入 `gemini_native`、`bedrock_converse` 等。

业务只提交统一的消息、任务类型、允许工具、输出契约、超时与预算约束。适配器负责协议转换，厂商配置负责 endpoint 与凭证引用；LangGraph 继续负责教学流程及人工关口，按需使用 LangChain 的模型集成。不能仅因 LangChain 有集成类，就认为本项目已经通过该厂商的工程验收。

统一结果至少保留正文、经验证的结构化结果、工具请求、结束原因、用量、远端请求 ID、实际模型标识以及不确定状态。协议所需的消息状态单独管理，不把厂商的 reasoning 内容直接展示给学生，也不作为学生成长证据。

### 3.2 厂商配置

配置建议包含 provider ID、协议、受允许的 endpoint、区域/业务空间、模型或部署 ID、凭证引用、超时、并发与预算、允许发送的数据范围。密钥仅在服务端解析，前端只能读取脱敏配置状态。

教师和学生页面引用已审批的逻辑模型配置，不直接接收任意 base URL。自定义兼容 endpoint 也必须由管理员核对、保存并明确启用；避免请求地址和鉴权头由学生输入决定。变更配置应有版本和审计，不能静默将一个项目的调用切到另一厂商或地区。

### 3.3 具体模型能力注册

注册粒度为 **厂商 + 协议 + endpoint + 区域 + 模型/部署 + 配置版本**。相同模型名称经由不同托管平台访问时应分开验收。

| 能力 | 声明与默认规则 |
|---|---|
| `text` | 普通文本响应；通过契约与授权检查后才启用真实调用 |
| `stream` | 是否支持增量正文及中断；流事件必须按协议归一化 |
| `tool_calls` | 工具请求、结果回传、并行能力分别核对；默认关闭未知项 |
| `structured_output` | 区分 `none`、`json_object`、`json_schema`；“提示词要求 JSON”不能冒充原生 schema 约束 |
| `vision` | 按型号确认图片内容格式、限制及数据外发许可；默认关闭 |
| `usage` | 记录可获得的输入、输出、缓存/思考等计量；缺失项为未知，不能当成 0 |
| `reasoning_controls` | 支持的字段、枚举、是否可关闭按模型配置，禁止统一硬塞参数 |
| `remote_cancel` | 区分服务端支持取消与本机停止等待；不能承诺断开连接即停止计费 |

未知能力默认关闭。发现模型列表不代表已发现能力；文档声称支持、离线适配测试通过、真实账号验收通过，应显示为不同状态。禁止将一次成功文本请求自动升级为“所有能力可用”。

## 4. 首版实施与验收顺序建议

1. 统一请求/结果/错误契约、能力注册、配置校验，补齐 Fake Provider 的成功、超时、限流、输出错误和未知用量场景。
2. 升级运行器：短事务领取任务并冻结输入；事务外调用；短事务校验运行版本、取消状态及租约后提交结果。租约续期、崩溃恢复、重复提交与预算预留需要一起设计。
3. 完成 Chat 兼容基础适配，通过本地 HTTP stub 验证各配置所需 URL、鉴权、错误与响应映射。离线通过只表示适配代码可验证，不能替代真实账号验收。
4. 按实际业务需要实现 Responses、Messages 等适配。对兼容层不支持的特性明确拒绝或走显式选择的适配路线；不静默忽略教学流程依赖的关键参数。
5. 已按用户指定完成 DeepSeek 独立探测；正式概率陪学仍需确认持续调用的型号/配置、预算和学生数据范围，并先完成运行器改造。无需等所有厂商均真实开通后才验证这条完整链路。
6. 后续逐家完成真实验收。每新增模型至少确认基础文本、流事件、结构化结果、工具边界、超时/重试、用量与取消行为中实际启用的部分。

请求超时后远端是否执行成功可能未知。重试不能保证只产生一次远端调用或费用；本地只提交一次学习结果与远端只计费一次是不同保证。重试策略、未知费用预留和补偿记录需要显式管理。

## 5. 尚待用户选择与真实账号验证

- 用户已指定 DeepSeek 并授权一次独立开发探测；正式样板持续调用的具体配置、预算上限和外发数据范围仍需确认，不能沿用一次探测授权无限调用。
- 哪些学生原文、题目、项目证据、课程资料允许发给该服务；当前未授权按此清单自动向所有厂商发送数据。
- 选定账号的模型权限、并发/速率、可用地域、套餐和数据条款。
- 每个拟启用能力的实际返回、字段差异、错误码、流式用量、工具历史和输出约束。
- 原生功能与兼容入口之间的差异；国内/国际地址及 Key 是否对应。
- GLM、Kimi、DeepSeek 等文档抓取不稳定的页面，在实施时再次核对；腾讯新账号确认 TokenHub 路线。

目前仅 DeepSeek 完成独立真实连通；其余厂商为规划范围，没有逐家真实账号验收，也没有让模型自动判定学习成效。正式学习流程接入完成后仍要保留数学验证器、人工复核、课程审核和教学效果验证，Provider 的可用性不能替代这些工作。
