# OpenMAIC 教学资源生成接入核对

核对日期：2026-10-18。性质：接入设计调研；未安装 OpenMAIC、未调用付费模型、未上传项目或学生数据。本文件不代表已完成集成。

## 1. 核对对象与依据

官方对象为 [THU-MAIC/OpenMAIC](https://github.com/THU-MAIC/OpenMAIC)。以已发布的 **v1.2.0** 为本方案候选基线，对应 [提交 a9da60b3b4adb429369c0da13d5637a904ead2b0](https://github.com/THU-MAIC/OpenMAIC/commit/a9da60b3b4adb429369c0da13d5637a904ead2b0)，[发布页](https://github.com/THU-MAIC/OpenMAIC/releases/tag/v1.2.0)标注发布日期为 2026-10-16。后续实现应固定此 ref 或经重新核对的升级 ref，避免直接依赖滚动 main。

用户提供的[微信文章](https://mp.weixin.qq.com/s/DQqj7ERp2R6YYskNnWsxgQ?scene=1&click_id=982268676)无法通过本次检索读取正文，因此没有将其观点、演示效果或接入方式写成已核实事实。也未以第三方同名网站代替官方资料。

## 2. 已核实事实

| 核对项 | 官方资料支持的事实 | 对本项目的含义 |
| --- | --- | --- |
| 资源能力 | 官方列出幻灯片、测验、交互 HTML 和 PBL 场景生成。 | 可作为教学资源生产组件；本项目仍需自己的学习任务契约和核验。 |
| 提交边界 | `POST /api/generate-classroom` 接收 `requirement`、可选 `materialIds`；先经 owner、输入和模型配置检查，再建立生成任务并返回 202。 | 适配器应提交异步任务，不能将 202 当作课程可用。 |
| 资料边界 | 上传资料归属于请求 owner；上传、生成、状态查询必须维持同一身份。最多 5 个资料 ID；提交接口不接受直接文档 URL。 | 搜索下载与来源审查先由本项目完成，再上传允许的资料字节；不可透传任意用户 URL。 |
| 模型配置 | Provider 和各用途 slot 在服务端配置；支持锁定配置、禁止用户添加密钥。请求体不能任意覆盖模型或媒体开关。 | 管理端治理需同步约束独立服务，不能只修改 EduAgent 的 Provider 配置。 |

来源分别为固定版本 [README](https://github.com/THU-MAIC/OpenMAIC/blob/v1.2.0/README.md)、[生成入口源码](https://github.com/THU-MAIC/OpenMAIC/blob/v1.2.0/app/api/generate-classroom/route.ts)、[生成接口说明](https://github.com/THU-MAIC/OpenMAIC/blob/v1.2.0/skills/openmaic/references/generate-flow.md#request-contract)、[模型配置说明](https://github.com/THU-MAIC/OpenMAIC/blob/v1.2.0/packages/docs/content/docs/configuration.mdx#models-and-providers)。

部署要求已变化：v1.2.0 需要常驻 Node.js 服务和 PostgreSQL；数据库保存生成运行，服务内 worker 使用租约接管，关页面不停止生成，重启后可从已完成步骤继续。部署文档要求 Node.js 至少 22.19.0、pnpm 10.28.0；上传资料、实例密钥等仍涉及持久 `data/`，不能仅备份数据库。多实例需要共享相关持久目录和实例密钥。来源：[部署说明](https://github.com/THU-MAIC/OpenMAIC/blob/v1.2.0/packages/docs/content/docs/deployment.mdx)。

## 3. 导出与运行内容

| 产物 | 能力与限制 |
| --- | --- |
| PPTX | 保留可编辑幻灯片；交互、测验和 PBL 可用占位页链接到在线场景，不能据此承诺全部交互都在 PowerPoint 内运行。 |
| Resource Pack | PPTX 与交互网页资源。 |
| Offline HTML | 离线课堂播放器；包含脚本与媒体，大课程可导出为 HTML 加媒体目录的 ZIP。PBL 仅保留任务说明，继续项目仍需在线课堂和网络。 |
| Classroom ZIP | 课程结构和媒体的迁移、备份载体；导入本系统仍需开发明确的格式转换与验证。 |

固定版本[导出说明](https://github.com/THU-MAIC/OpenMAIC/blob/v1.2.0/packages/docs/content/docs/getting-started.mdx#export-a-classroom)及 [README 导出段](https://github.com/THU-MAIC/OpenMAIC/blob/v1.2.0/README.md#export)支持上述区分；部分外部资源下载失败仍可能保留 URL，因此本项目验收必须实际检查离线播放的网络请求。

官方[发布提交中的数学加固说明](https://github.com/THU-MAIC/OpenMAIC/commit/a9da60b3b4adb429369c0da13d5637a904ead2b0)区分了应用数学渲染和生成交互页：后者在 sandbox frame 中运行自己的脚本。**本次未读到该固定版本具体 frame 实现文件，未核实每一项 sandbox 标志及网络策略**；不能据此声称现成导出页可以安全放入 EduAgent 同源页面。上线前必须补做对应源码和实际行为审计。

## 4. 推荐接入方式（本项目设计，尚未实现）

1. **独立服务适配**：将 OpenMAIC 放在内网独立容器和独立数据库/存储域，通过 EduAgent 后端适配器发起任务。学生浏览器不直接持有服务密钥；使用本项目账号/项目到远端 owner 的明确映射，禁止共用单用户 owner 给所有学生。
2. **先内容后媒体**：先接入可检查的文本、结构化题目、幻灯片和简单交互。对生成要求固定初中数学范围、知识点 ID、学习目标、来源片段、年龄与难度、题目与隐藏答案的分离格式。复杂仿真、图片、语音和视频后续逐项启用。
3. **双运行账本**：保存本地任务 ID、远端 run/classroom ID、配置 ref、输入/产物哈希、状态和费用。提交响应丢失时先核对既有运行，禁止盲目再次创建。取消或超时后也要确认远端是否仍在计费。
4. **预算单独验收**：官方运行接管可能重做未完成步骤，模型配置还可存在重试与回退。现有“一次最多 512 tokens、失败不自动重试”的陪学授权不能直接覆盖课程生成。应先核实所有模型、搜索、媒体调用的次数和上限及终止机制，明确总预算，再开放真实生成。
5. **产物待验收**：导入候选资源后，执行结构、数学、年龄适配、来源和安全验证；题目答案由独立数学工具检查，证明与教学适切性进入专业审核。失败隔离并限定修复次数，不能用另一段模型赞同文本代替核验通过。
6. **HTML 隔离**：优先采用经过校验的结构化 DSL 和本项目渲染器。确需 HTML 时，用独立 origin、无账号 Cookie 的 sandbox frame、最小脚本权限、严格 CSP 和网络白名单；禁用顶层导航、弹窗、下载和任意网络访问，消息通道校验来源、资源 ID、版本、消息类型与大小。静态扫描加浏览器测试后才可展示；不在同源页面直接执行生成脚本，也不把网页上报“答对”当作可信数学事实。

以上不要求复制其整套课堂运行时：EduAgent 保留陪学、圆桌、我教 AI、证据、策略审批、数学核验与项目权限的主状态；OpenMAIC 输出转换成版本化资源。圆桌和我教 AI 的教学任务仍要由本项目生成 Agent 产生并校验，不能靠导出一个课堂链接替代功能完成。

## 5. 许可证与待确认项

固定版本 [LICENSE](https://github.com/THU-MAIC/OpenMAIC/blob/v1.2.0/LICENSE)为 **MIT**，版权归 THU-MAIC，分发源码或实质部分需保留版权及许可声明，且不提供质量担保。根许可不能自动覆盖所有依赖、字体、教材、外链图片、搜索结果和第三方模型服务条款；正式引入时建立依赖及素材许可清单。

进入实现前还需验收：具体 frame 与 postMessage 源码；导出结构版本和转换兼容性；远端 owner 防串项目；失败/取消/重启后预算行为；资料删除与备份恢复；资源断网播放；数学错误和答案泄露拦截。设计文档确定接入方向，不代表上述项目已经通过。
