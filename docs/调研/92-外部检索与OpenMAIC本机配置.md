# 外部检索与 OpenMAIC 本机配置

> 当前更新：用户已确认下述两项范围；Tavily密钥已在本机配置并开启搜索，一次实测1 credit。OpenMAIC已由本机Node迁为独立Docker服务，一门三场景文本课堂实测8次调用、42450输入/6181输出tokens，首轮预算已停止。详见 [95号验收](../进度/95-G02G05外部真实接入验收.md)。下方此前“待确认”和Node临时部署说明保留当时记录。

核对日期：2026-10-18。实际接入进度见 91 号验收，数据与权限边界见 90 号架构。这里的建议不是已获得全部费用授权。

## 1. 确认记录与仍待确认项

用户已同意普通课程/通用互动使用 DeepSeek：主题、已授权来源、当前项目脱敏输入；课程单次4096、每课程最多六次含一次手动修复；互动512、每活动最多三次；共用原日额度、失败不自动重试。

用户希望同时外部搜索、真实部署 OpenMAIC，并要求确认服务商、来源和总预算。已提出下表，尚待进一步答复；因此本轮没有实际搜索或 OpenMAIC 模型生成。

| 项目 | 建议范围 | 调用与总预算 | 当前状态 |
|---|---|---|---|
| 外部检索 | Tavily Basic；仅标准数学关键词；允许教育部 `moe.gov.cn`、人教社 `pep.com.cn`、国家智慧教育 `zxx.edu.cn` 及子域 HTTPS 发现结果 | 每主题最多2次、全站每天20次；最多5条；不启用 deep、自动扩检或重试 | 适配器/Mock 通过，密钥未配置，搜索开关关闭；待确认 |
| OpenMAIC | 固定 v1.2.0、本机4004、独立 PostgreSQL；DeepSeek 文本；一门、恰好三场景；所有媒体与内部搜索关闭 | 单请求最多4096输出；整次最多20请求、累计128000输入与32768输出 tokens；首轮仅一门；内部重试也计数 | 本机构建、健康与门禁通过；预算未初始化；真实生成待确认 |

### 费用估算如何看

- Tavily 的 Basic 检索为1 credit/次；按官方 pay-as-you-go `$0.008/credit`，二十次约 `$0.16/日`，免费额度是否可用以账号为准。平台限制的是预约次数，异常/未知也不自动返还，不把零结果算成免费承诺。[官方 credits 说明](https://docs.tavily.com/documentation/api-credits)
- DeepSeek 官方当日 Flash 高峰缓存未命中输入2元/百万、输出8元/百万。以本次资源 token 上限估算，`128000×2/百万 + 32768×8/百万 ≈ 0.52元/一门`。这是按当前单价推算的模型费用，不是人民币扣费硬锁；不含托管、带宽或其他服务，模型计价变化须重新核对。程序硬锁 token/请求预算，服务商账单为准。[官方模型价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)
- 实际配置与调用记录使用 `deepseek-flash`，不把历史界面的“v3 候选版”当作服务商当前真实模型版本。

## 2. Tavily 本机密钥与启用

确认上表范围后，用户取得自己的服务密钥，在项目根目录隐藏输入：

```powershell
.venv/Scripts/python.exe -m app.manage set-search-key
```

保存到被 Git 忽略的 `.local/tavily.key`；也可由部署环境配置 `WENJIN_TAVILY_KEY` / `WENJIN_TAVILY_KEY_FILE`。不要在聊天、网页、截图或文档填入密钥。

管理员随后到“课程生成与资源”启用搜索。模型 worker 同时处理检索任务；不需前端接触服务商密钥。

请求固定发往 `https://api.tavily.com/search`，设置 Basic、五条结果、白名单、关闭回答/图片/原始网页内容/自动参数。响应再过滤域名。不下载网页，不因搜索结果具有网址就认定资料合法授权。[官方搜索接口](https://docs.tavily.com/documentation/api-reference/endpoint/search)

## 3. OpenMAIC 固定版本与许可

- 仓库：`https://github.com/THU-MAIC/OpenMAIC`。
- tag：`v1.2.0`；固定提交：`a9da60b3b4adb429369c0da13d5637a904ead2b0`；MIT 许可保留在 checkout。
- 源码位于忽略目录 `.local/services/OpenMAIC-v1.2.0`，不把整套上游或本机密钥提交进 EduAgent。
- `scripts/patch_openmaic.py` 校验提交后添加本平台服务门禁。它是集成补丁，不声称原版上游具有本平台预算与访问约束。
- 官方部署要求常驻服务与持久数据库，不能仅当作无状态静态资源调用。[官方部署说明](https://open.maic.chat/docs/deployment)

### 本机实际部署

本轮 Docker Hub 连续出现 EOF/网络超时，无法取得 Node 基础镜像；没有反复盲建或改用未核验来源。已用本机 Node24.19.0、pnpm10.28.0 完成依赖安装、packages 构建和 Next 生产构建，独立 PostgreSQL 仍运行于 Docker。

| 服务 | 监听/路径 | 隔离 |
|---|---|---|
| EduAgent API | `127.0.0.1:4003` | 现有项目库，`g05_002` |
| OpenMAIC | `127.0.0.1:4004` | 独立 Node 进程，固定源码与门禁补丁 |
| OpenMAIC PostgreSQL | `127.0.0.1:55433` | 容器 `wenjin-openmaic-db`，卷 `wenjin-openmaic-pg`，独立用户/库 |
| 现有 EduAgent PostgreSQL | `127.0.0.1:55432` | 不与资源服务共享数据表或账号 |

`.local/openmaic-postgres.env`、`.local/openmaic-proxy.key`、上游 `.env.local` 均是本机忽略文件。随机代理密钥只用于后端到资源服务；上游没有 DeepSeek 原密钥。

### 换机器重建

在仓库根目录先准备 Node24 和 pnpm10.28，再执行下列流程。每条有依赖的命令成功后再执行下一条：

```powershell
git clone --branch v1.2.0 --depth 1 https://github.com/THU-MAIC/OpenMAIC .local/services/OpenMAIC-v1.2.0
git -C .local/services/OpenMAIC-v1.2.0 rev-parse HEAD
# 人工核对输出等于上文固定 SHA
.venv/Scripts/python.exe scripts/prepare_openmaic.py
.venv/Scripts/python.exe scripts/patch_openmaic.py
docker run -d --name wenjin-openmaic-db --env-file .local/openmaic-postgres.env -p 127.0.0.1:55433:5432 -v wenjin-openmaic-pg:/var/lib/postgresql/data postgres:17
cd .local/services/OpenMAIC-v1.2.0
pnpm install --frozen-lockfile --ignore-scripts
pnpm run build:packages
pnpm build
cd ../../..
.venv/Scripts/python.exe scripts/start_openmaic.py --node C:/你的Node24目录/node.exe
```

容器已经存在时使用 `docker start wenjin-openmaic-db`；不删除旧容器/卷重建来解决连通问题。启动脚本不开放预算。当前机器的 Node24 位于 Codex 捆绑运行时，维护时也可设置 `WENJIN_OPENMAIC_NODE` 指定另一个已安装 Node24。

Next 构建成功，但上游有43条 Node/Edge 兼容性警告；通过健康检查不证明所有上游功能可靠。首轮只启用受控文本生成路径。

## 4. 预算确认后的唯一初始化步骤

**仅在用户明确批准首轮 OpenMAIC 数据范围/整次预算后执行：**

```powershell
.venv/Scripts/python.exe scripts/prepare_openmaic.py --activate-budget
```

此命令遇到已有预算不会清零、重新启用或扩额。管理员对已审核课程点击“预约 OpenMAIC 文本课堂”，平台保存独立 owner 和远端 job 映射。首次整次预算只能关联一门，重复点同课程返回既有任务；不同课程不自动占用第二门。

上游模型请求经 `http://127.0.0.1:4003/internal/openmaic/v1` 转发到已发布 DeepSeek。每次先预约累计额度再出网。用量未知保留预约；已知用量可结算未用部分。超额度、未知超时、取消后的迟到结果均不能绕过限制。

如远端提交结果未知且本地没有 jobId，不再次创建；先按对应 owner/服务记录受控核对。当前未实现完整自动对账恢复界面，不能靠重置预算绕过。

## 5. 当前已经验证 / 尚未验证

已验证：固定 SHA、packages/Next 构建、独立数据库、健康200、能力全部关闭、无凭据数据 API403、非 API 写操作403；Mock 验证202不代表完成、同 owner 轮询、来源撤回拦截、取消/未知预算、ZIP安全转换与审核。

尚未验证：Tavily 真实账号连通、真实 OpenMAIC 一门课堂的生成/消耗/内容质量与完成恢复。普通 DeepSeek 课程与三条互动的五次真实调用不冒充这两项验收。


## 6. 当前Docker部署与重启入口

官方镜像下载已恢复。本机实际使用 `wenjin-openmaic-service` 容器与 `wenjin-resources` 独立网络，PG容器不变。更新构建入口如下，构建前固定上游SHA并执行门禁补丁；配置和凭据只在忽略目录运行时挂载：

```powershell
.venv/Scripts/python.exe scripts/patch_openmaic.py
docker build -f deploy/openmaic/Dockerfile -t wenjin-openmaic:1.2.0-text .local/services/OpenMAIC-v1.2.0
.venv/Scripts/python.exe scripts/deploy_openmaic_docker.py
```

已有容器时启动脚本只启动既有容器，不删除数据，不重新创建课堂，也不开放/重置预算。首轮额度已停止，不能靠重复运行 `prepare_openmaic.py --activate-budget` 清零或扩额。新预算需要后续明确安排。
