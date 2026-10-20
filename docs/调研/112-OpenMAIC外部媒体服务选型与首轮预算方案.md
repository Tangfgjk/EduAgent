# OpenMAIC 外部媒体服务选型与首轮预算方案

核对时间：2026-10-19，Asia/Shanghai。性质：服务选型、预算建议与接入约束；不是实现报告或真实调用验收。本文只读取固定源码及官方公开文档，未读取密钥、未调用真实付费 API。

**未获预算确认，不发真实调用。** 用户要求完整图片、语音、视频课堂，外部媒体生成确属必要。已有 DeepSeek 仅用于文本，不能生成本文媒体；当前没有新媒体 key。旧首轮“仅文本生成”预算已关闭，不复用、不重开。本方案是一笔待单独确认的新媒体预算。

## 1. 推荐结论

推荐统一使用**阿里云百炼华北2（北京）**：图片 `qwen-image-2.0-2026-03-03`，中文配音 `qwen3-tts-flash`（系统音色 Cherry），真实视频 `wan2.2-t2v-plus`（480P、5秒、无声）。优先采用现有中文教学稿，视频中的旁白使用独立配音轨。图片用日期快照避免滚动别名变更；TTS 的内建型号是滚动别名，执行前记录官方对应快照及实际型号。

这是基于统一地域、人民币按张/字符/秒计价及现有适配范围作出的工程建议，尚未通过真实课堂验证。图片 2.0 系列官方支持中英文提示、文字渲染与图像生成；TTS 支持中文和混合语言，单语可指定 Chinese；Wan2.2 生成真正动态画面，原生固定5秒。因此无需为了满足时长而购买更长视频。依据：[图片接口](https://help.aliyun.com/zh/model-studio/qwen-image-api)、[TTS 接口](https://help.aliyun.com/zh/model-studio/qwen-tts-api)、[Wan 接口](https://help.aliyun.com/zh/model-studio/legacy-wan-text-to-video-api-reference)。

**关键接入缺口：固定 OpenMAIC 并没有 Wan provider。** 图片与 TTS 已有内建适配；Wan 需补充独立适配和代理预算治理，不能直接在 HappyHorse 项中替换模型名称后声称可用。若坚持完全使用现成 provider，火山组合是备选，但需重新核价并确认凭据。

## 2. 固定源码核对

本机目录 `.local/services/OpenMAIC-v1.2.0` 的 `git rev-parse HEAD` 为 `a9da60b3b4adb429369c0da13d5637a904ead2b0`。以下全部以该提交为准，而非 main 或服务商最新产品目录。

| 固定源码 | 核对结果与影响 |
| --- | --- |
| [image-providers.ts](https://github.com/THU-MAIC/OpenMAIC/blob/a9da60b3b4adb429369c0da13d5637a904ead2b0/lib/media/image-providers.ts) | `qwen-image` 包含所荐图片快照；另有 Seedream 等；菜单枚举不代表账号已开通。 |
| [qwen-image-adapter.ts](https://github.com/THU-MAIC/OpenMAIC/blob/a9da60b3b4adb429369c0da13d5637a904ead2b0/lib/media/adapters/qwen-image-adapter.ts) | 同步 POST，`input.messages`，size 为 `宽*高`；默认尺寸1024×576，`prompt_extend:true`、`watermark:false`，未显式发送 n。首轮代理须锁定合法尺寸、单张输出与张数，不依赖默认值。其“连接测试”也是 POST，不能视为完全无成本授权。 |
| [video-providers.ts](https://github.com/THU-MAIC/OpenMAIC/blob/a9da60b3b4adb429369c0da13d5637a904ead2b0/lib/media/video-providers.ts) | 有 Seedance、Kling、Veo、MiniMax、Grok、HappyHorse、OpenRouter；没有 Wan。选项归一化会把不支持的时长替换为 provider 第一项，代理必须验证最终发出的参数，防止规格意外变化。 |
| [happyhorse-adapter.ts](https://github.com/THU-MAIC/OpenMAIC/blob/a9da60b3b4adb429369c0da13d5637a904ead2b0/lib/media/adapters/happyhorse-adapter.ts) | 百炼异步提交/查询范式可借鉴，但参数使用 resolution、ratio，不能作为 Wan 的正式适配。HappyHorse 本次未完成账号可用性和价格核对，不列入获批调用白名单。 |
| [audio/constants.ts](https://github.com/THU-MAIC/OpenMAIC/blob/a9da60b3b4adb429369c0da13d5637a904ead2b0/lib/audio/constants.ts)、[tts-providers.ts](https://github.com/THU-MAIC/OpenMAIC/blob/a9da60b3b4adb429369c0da13d5637a904ead2b0/lib/audio/tts-providers.ts) | 有 `qwen-tts`，默认 `qwen3-tts-flash` / Cherry；普通合成发送 Chinese，下载返回音频 URL；另有豆包TTS。首轮只用系统音色，禁止声音复刻和注册声音请求。 |

上游已有部分媒体请求与任务恢复抽象，不等于本项目已具备人民币硬上限；下文均是待落地的准入条件。

## 3. 模型、接口、地域与凭据

| 能力 | 推荐配置 | 请求与凭据要求 |
| --- | --- | --- |
| 图片 | `qwen-image-2.0-2026-03-03`；1280×720；n=1 | POST `/api/v1/services/aigc/multimodal-generation/generation`，`input.messages`；北京百炼 API Key，Bearer；执行前确认账号模型开通和业务空间权限。 |
| 配音 | `qwen3-tts-flash`；Cherry；Chinese；普通非流式合成 | 同路径，`input.text/voice/language_type`；固定上游 TTS baseUrl 含 `/api/v1`，避免重复拼接；北京 Key。官方最大单次600字符，本项目限制更小。 |
| 真实视频 | `wan2.2-t2v-plus`；`size:832*480`，16:9，固定5秒，n=1；无声 | POST `/api/v1/services/aigc/video-generation/video-synthesis`，`X-DashScope-Async:enable`；GET `/api/v1/tasks/{task_id}`；北京 Key、模型权限、持久化 task_id。Wan 需新增适配。 |

图片、视频官方现推荐业务空间域名 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com`，同时说明旧北京域名 `https://dashscope.aliyuncs.com` 仍可使用；TTS 官方示例仍使用后者。本轮优先遵循固定适配及对应服务文档，配置时记录每个能力的最终 endpoint。模型、endpoint、Key 必须同地域，北京与新加坡 Key 不可混用；不能自动切换国际/全球部署。见[图片地域说明](https://help.aliyun.com/zh/model-studio/qwen-image-api)、[视频地域说明](https://help.aliyun.com/zh/model-studio/legacy-wan-text-to-video-api-reference)、[TTS 示例与 Key 说明](https://help.aliyun.com/zh/model-studio/qwen-tts-api)、[获取 Key](https://help.aliyun.com/zh/model-studio/get-api-key)。

凭据仅放服务端受控配置，按项目/业务空间隔离，日志只留指纹和配置版本；不放浏览器、提示词、课程包或本文。新 Key 的创建、模型开通、余额及地域核价由配置阶段完成。本次不读取任何现有 key。

## 4. 备选服务及取舍

| 方案 | 固定上游支持 | 选择条件和费用状态 |
| --- | --- | --- |
| 百炼 Qwen + Wan（推荐） | 图像/TTS已有，Wan需新增 | 统一百炼北京 Key 与人民币计价；首轮视频成本容易按5秒预占。用已核实的2.0图片快照，不选官网接口说明已变化的旧 Plus 名称作为首选。 |
| 火山 Seedream + Seedance + 豆包TTS | 三类 provider 均已存在 | 候选 `doubao-seedream-4-0-250828`、`doubao-seedance-1-0-lite-t2v-250428`，5秒480P；TTS使用 `seed-tts-2.0` 系统音色。必须先核实旧模型账号仍可开通、当前规格及正式价格，才可替换推荐方案；不自动回退。 |

火山图/视频使用方舟北京 `https://ark.cn-beijing.volces.com/api/v3`，视频任务路径 `/contents/generations/tasks`；TTS 使用 `https://openspeech.bytedance.com/api/v3/tts/unidirectional`，产品权限与方舟不同。固定上游支持 `appId:accessKey` 旧控制台鉴权或 Agent Plan 单 Key，不可假设“同一服务商”就是“一把 Key”。官方：[视频任务接口](https://console.volcengine.com/ark/region%3Acn-beijing/docs/create-video-generation-task-api)、[模型价格](https://docs.volcengine.com/docs/ark/model-pricing?lang=zh)、[语音鉴权与接口](https://docs.volcengine.com/docs/DoubaoVoice/SoundReplicaAPI-V3?lang=zh)、[语音开通与控制台](https://docs.volcengine.com/docs/DoubaoVoice/console-guidance?lang=zh)。

本次未可靠核到上述火山旧型号在拟用账号的最终价格与完整 token 规格，标记**待配置核价**；不能引用扣子集成价、活动页过期价或第三方转售价格代替方舟直连价。Seedance新版本采用 token 计价，不能只拿秒数当金额硬约束；2.0系列官方还有账号余额/资源包开通条件，首轮不建议为5元试验盲目升级。见[Seedance2.0开通说明](https://docs.volcengine.com/docs/ark/seedance-2-0?lang=zh)。

## 5. 首轮规模与人民币预算（待人确认）

范围严格为**1课、3场景**。使用已保存并审核的课稿/媒体提示，不凭本媒体预算重新启动 DeepSeek 文本生成；如需再生成课程结构或改写提示，应另列新的文本调用、输入/输出 token 及金额授权，旧预算继续保持关闭。

| 项目 | 真实生成提交上限 | 输入、规格及时长限制 | 金额预占上限 |
| --- | --- | --- | --- |
| 图片 | ≤2次，每次1张，总≤2张 | 每次输入≤800 token；分辨率固定1280×720；无上传参考图、无编辑/放大；禁用额外提示改写模型调用 | 图片合计≤1.00元 |
| TTS | ≤3次，每场景最多1次 | 每次≤150字符且≤300输入 token，总≤450字符/900 token；目标每段≤30秒，总≤90秒；音色/语速固定，不复刻 | 配音合计≤1.00元 |
| 视频 | ≤1次，仅一个任务 | 输入≤600字符且≤800 token；832×480；n=1；固定5秒；无声，无参考音视频，无延长/超分 | 视频合计≤2.00元 |
| 下载/存储/其他 | 不新增生成调用 | 只下载上述产物；不买资源包/订阅、不搜索收费图库 | 辅助费用预留≤1.00元，未知收费项先停 |
| 总计 | **≤6次生成提交** | 3场景、2图、3段配音、1段5秒视频；总输入 token 应用上限3300 | **建议新媒体预算总上限5.00元人民币** |

每个生成任务最多提交一次，**不自动重试、不自动换模型/换服务商、不补图、不补配音**。异步视频查询不算第二次生成提交；限定最多40次、间隔15秒，总等待≤10分钟，超时进入待核账，不能再次 POST。生成型连接测试、页面自动播放触发的再次TTS、后台恢复、并发点击、修复/润色都不能绕过6次上限。

上述 token 数是应用准入限制，不是三类媒体的计费单位或可设置的统一 `max_tokens`。实际依对应模型 tokenizer 验证；没有可靠计数器时不以字符数假称精确 token 数，阻止提交或采用已验证保守上界。输出图片/视频不按输出文本 token 结算；TTS3按字符计费，无需套用旧 Qwen-TTS 的音频 token 单价。

**时长存在区别：** Wan5秒为供应商请求规格；TTS普通接口不能保证每段严格30秒。通过150字符稿、固定语速预估，并对回收音频测时；若超出90秒，按已产生的完整合成费用记账，可本地裁剪/退回人工编辑，但不得自动付费重做。不能用“裁成90秒”声称供应商只生成/收费90秒。

按2026-10-19官方北京公开价，图像2.0快照0.20元/张，Qwen3-TTS-Flash 0.80元/万输入字符，Wan2.2-T2V-Plus 480P 0.14元/秒；以满额产物计算：`2×0.20 + 450/10000×0.80 + 5×0.14 = 1.136元`。不将免费额度、活动或资源包抵扣计为预算保障。5元是待确认的保守授权总上限，不是价格承诺，也不是允许多做几轮的余额。依据：[百炼官方模型价格，北京对应三项](https://help.aliyun.com/zh/model-studio/model-pricing)。执行当日须再次核价并保存型号、地域、单位、时间及来源；若涨价、税费/最小计费规则不明或不能覆盖预占，暂停并重新确认，绝不编造价格。

## 6. 金额硬约束与未知费用

本节是待实现与验收的要求，不能据此宣称当前已拦截供应商扣费。

1. **代理统一出口**：OpenMAIC所有媒体与后台worker请求经过受控代理，关闭绕行与自动回退。以新批次ID绑定课/owner/供应商/地域/型号/参数/次数/金额，旧文本预算不可引用。限制并发为1。
2. **提交前原子预占**：数据库原子校验生成次数、字符/token、分辨率/时长及分项和总额；先预占最坏可计费金额，再发请求。重复operation_id/内容哈希只能返回同一操作状态，不能再次消耗次数或发 POST。TTS按完整输入字符预占，视频按完整5秒预占。
3. **已发不释放**：持久化请求ID、供应商任务ID、预占、实际usage及账单状态。超时、断网、取消、下载失败、提交响应丢失均可能已经计费；保持预占，进入 `unknown_billing`。只查询既有任务/账单，不能重新生成来探测成功。仅有明确供应商未计费证据时释放金额；失败次数不自动恢复。
4. **供应商侧约束**：专用项目凭据、最小模型权限、可配置的额度/余额告警、关闭自动充值；执行前核实是否有真正的硬限额。按量后付费账单和告警可能延迟，不能承诺充值5元或告警5元就绝不会超支。代理负责已知调用最坏费用硬拦截，供应商账单负责最终核对，两者共同闭环。
5. **不能估价就不发**：未知计价、无法界定最大输出、存在未受控重试、未能阻止直连或余额/模型配置不明时，保持禁用。核账完成后封存批次，即使5元未花完也不自行续跑。

## 7. 课堂形态、版权和外发数据

静态图配合配音、字幕、镜头推移可以构成可播放讲解课件，但它仍是静态图配音合成。真实生成视频要求供应商返回实际连续动态画面的视频文件；首轮须保留一段 Wan 真实5秒素材及对应任务证据，不能用幻灯片录屏或图片平移冒充。Wan2.2无声，课堂语音由独立TTS提供；该设计不承诺角色口型同步或原生音画生成。

首轮只发送：去标识化的媒体提示词、≤450字符教学旁白、模型/尺寸/音色等生成参数，以及供应商所需认证信息。禁止发送学生姓名、账号、行为轨迹、聊天全量、成绩、摄像头/麦克风录音、个人照片、未授权教材整书、项目源码及密钥。无需参考媒体上传；生成图/声/视频由后端下载进入本项目存储，导出包记录来源、模型、时间、任务ID和哈希，缓存/播放不重新调用生成。

百炼官方说明不将用户数据用于模型训练，同时会依法存储调用产生的数据；不能将“不训练”解释为“不留存”，本次未核得所有媒体服务统一的精确留存天数。[隐私说明](https://help.aliyun.com/zh/model-studio/privacy-notice)。服务协议对合法输入条件下、阿里及关联公司模型生成内容的知识产权作出约定，但仍受其他约定与法律约束；不能承诺每张图必然有著作权、没有第三方相似性或可无条件商业使用。[百炼服务协议](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html)。

本项目采用原创教学提示、系统音色和通用场景，保留生成来源与适用标识；不复刻真人声音，不使用真人肖像、影视角色或品牌素材。数学标注优先由应用排版，生成媒体仍需人工核对事实、中文文字和教学适切性。OpenMAIC 的 MIT 许可只覆盖软件，不能替代模型服务和素材授权。

## 8. 执行准入与后续验收

先由用户确认“百炼北京、上述三型号、1课3场景、≤6次生成、零自动重试、≤5元新媒体预算”；随后才配置专用媒体凭据、核价、补 Wan 适配及预算控制。若只能接受完全现成适配，应改选火山并重新提供实际报价后确认，不能在同一授权下悄悄替换。

真实验收应同时具备：2图以内、3段配音以内且测得总≤90秒、1段真实5秒动态视频、离线资源可播放、供应商任务与账单可核对、预算批次已封存；任何缺项均记录为未完成。**当前仅完成选型与预算方案；没有实现完成或课堂验收完成的结论。未获预算确认，不发真实调用。**


## 本轮决策（2026-10-19）

用户答复：**先完成代码，暂不批准真实媒体费用**。因此本轮仅实现适配器、持久任务、金额/次数/时长限制、私有资产与离线HTTP模拟验收；未执行收费图片、语音或视频生成。方案中的人民币5元是待批准的上限，不是已经批准或已经发生的费用。旧OpenMAIC文本预算继续关闭。后续需用户另行确认费用、本机配置北京密钥、核对账户实际价格/权限，再授权独立新批次；不得通过重试、换模型或重开旧预算绕过。
