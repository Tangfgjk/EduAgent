# S03 React 双端与真实接口验收

日期：2026-10-16。当前分支：`main`。依据 12–16 号文档、两个 HTML 原型及用户最新确认实施；既有 docs 基准和原型均保留。

## 1 本期完成

| 范围 | 当前交付 |
|---|---|
| 双端工程 | React + TypeScript + Vite；student/admin 独立 HTML、入口、路由与输出目录 |
| 整体风格 | 沿用纸面、墨绿、衬线标题、工作台侧栏和轻量卡片；统一表单、弹窗、空态、错误和焦点样式 |
| 学生端真实功能 | 登录/退出；创建、选择、刷新保留、改名、归档/恢复正式项目与临时空间 |
| 管理端真实功能 | 登录/退出；学生账号分页、本页筛选、建号、密码重置、禁用及操作确认 |
| 角色限制 | 学生端无管理导航；直接打开另一端也必须通过身份门，后端角色授权继续生效 |
| 范围状态 | URL 与按账号区分的 sessionStorage 仅记住空间 ID；业务查询按账号隔离，退出/切换时换用独立缓存实例 |
| 多标签页 | 同源页面通过 BroadcastChannel 刷新身份；重新聚焦重新确认；跨标签换学生后不继续展示旧学生项目 |
| 协议 | FastAPI 增加实际响应模型；OpenAPI 生成 TypeScript；提供生成与漂移校验脚本 |
| 部署入口 | 构建后由正式网关提供 `/student/` 和 `/admin/`；深层路由刷新回到各自入口，丢失资源保持 404 |
| 设备范围 | 用户确认桌面 Chrome/Edge 优先，兼顾手机；本次验证 1440px 与 390px 浏览器视口 |

## 2 页面保留与实现边界

学生端保留：今日学习、项目与会话、数学单元、计划、陪学、复习、圆桌讨论、我教 AI、成长证据、规划与策略、偏好与账号。

管理端保留：试点概览、学生账号、知识点与题库、内容与解答复核、运行记录、策略评审、配置与权限。

本期**没有把后续学习功能包装为已完成**：

- 陪学、复习、圆桌、我教 AI 的入口和页面结构已迁入 React；尚未接真实编排，输入与执行按钮明确为待开放状态。
- 六维素养提供观察框架和空态，不绘制未经证据支持的分数；全局宏观与当前空间局部可以切换，范围说明随项目变化。
- 规划页面保留学生体验、成长证据、教材/课标、考纲/期限等输入方向，以及检查、管理评审、学生确认、激活与回滚流程；尚不生成策略，不写真实 Skill/Harness。
- 管理端不填入虚构运行量、学生成绩或待审数量；后续队列在相应后端完成后接入。
- 课程列表仅表示课标知识点组织方向，不代表已审核并开放全部七至九年级内容。

项目切换已经是真实操作，不只是修改前端标题。现阶段没有本地学习草稿，因此不存在把原型的全局临时状态作为正式学习记录保存的问题。S04 以后新增查询和草稿继续使用 workspace_id 隔离。

## 3 验收结果

| 检查 | 实际结果 |
|---|---|
| TypeScript 严格检查 | 通过 |
| OpenAPI 类型漂移 | `npm run check:api` 通过 |
| 两端生产构建 | 通过；分别位于 frontend/dist/student、frontend/dist/admin |
| Edge 浏览器 | 8 条流程全部通过，13.8 秒 |
| Chrome 浏览器 | 同一组 8 条流程全部通过，13.6 秒 |
| Python 完整回归 | **97 passed，2 warnings，17.29 秒**，包含真实 PostgreSQL 项，无跳过 |
| 启动与分发包 | 正式入口启动、sdist → wheel、隔离包运行检查通过 |
| 页面观感 | 查看桌面成长证据、管理账号、390px 手机截图；修正手机关闭导航的可见性，正文无横向溢出 |

浏览器流程覆盖：学生项目创建/刷新/改名/归档恢复/临时空间；圆桌与我教 AI 可见性；全局/局部证据空态；管理员建号/重置/禁用与学生会话失效；跨学生与跨标签页隔离；错误密码/角色门/深层路由；请求失败与重试；手机导航/键盘关闭弹窗/焦点和核心页面宽度。

浏览器验收使用新建的隔离 SQLite 与固定 synthetic 测试账号，不写入真实 PostgreSQL，也不替负责人设置管理员密码。PostgreSQL 的隔离、并发和恢复由同轮 Python 完整回归单独验证。手机结果是窄屏浏览器检查，不代表实体手机或 Safari 已全面验收。

本轮最初 Docker Linux 引擎未就绪，曾先完成 81 passed、16 skipped 的本机回归；用户启动 Docker 后项目数据库恢复健康，随后完整 97 项全部通过。前一跳过结果未被当成完整验收。

两项 Python warnings 为既有 Starlette/httpx 与 anyio 弃用提示，非新增功能失败。浏览器运行时错误检查通过；测试终端的颜色环境提示不影响结果。

## 4 实现落点

```text
frontend/
  apps/student/src/        学生路由与实际项目管理
  apps/admin/src/          管理路由与实际账号管理
  packages/ui/src/         风格、身份门、布局、弹窗、状态
  packages/api-client/src/ 请求/响应类型、CSRF、统一错误
  packages/math/src/       六维空态和学习路线展示
  e2e/                    浏览器验收
app/gateway/frontend.py   双静态入口与 SPA 深层路由
app/gateway/v1.py         请求/响应模型与现有真实接口
scripts/export_openapi.py 纯 Schema 导出，无真实数据库/模型访问
scripts/serve_frontend_test.py 仅测试的临时服务
tests/test_frontend.py    独立入口、资源和权限路由回归
```

后端不修改 S02 业务表结构，仍使用 s02_003。补充 PostgreSQL 连接超时 5 秒，避免数据库离线时无限等待。Python wheel 与前端输出是两个部署产物，通过 RSI_FRONTEND_DIR 可指定外置的双端 dist 目录。

工具链实际锁定：React 19.3.1、React Router 7.18.4、TanStack Query 5.104.3、Vite 8.3.4、TypeScript 5.9.3、openapi-typescript 7.13.0、Playwright 1.64.1。TypeScript 按生成器支持范围固定在 5.x，未强行使用不兼容的较新主版本。

## 5 如何运行

首次在仓库根目录准备 Python 与 PostgreSQL：

```powershell
uv sync --locked --extra dev
uv run --locked python scripts/dev_postgres.py
uv run --locked python -m app.manage --env-file .local/database.env migrate
```

进入 frontend 安装和构建，再回根目录：

```powershell
cd frontend
npm ci
npm run check:api
npm run build
cd ..
```

管理员仅在未初始化时执行一次，密码本机隐藏输入：

```powershell
uv run --locked python -m app.manage --env-file .local/database.env bootstrap-admin --username admin
uv run --locked python -m app.main --env-file .local/database.env
```

浏览入口：`http://127.0.0.1:8000/student/`、`http://127.0.0.1:8000/admin/`。先在管理端创建学生，再使用单独浏览器会话或退出后登录学生端。两种页面独立，但共享同一个后端会话 Cookie；切换账号会让其他同源标签页重新确认身份。

本轮未创建正式管理员，也未将验收账号复制到正式库。已有管理员的环境跳过 bootstrap。完整热更新、测试与部署说明见 [frontend/README.md](../../frontend/README.md)。

## 6 本轮修复的前端问题

第一轮真实浏览器检查发现：身份确认后的清理 effect 删除了刚挂载的工作空间查询，刷新后停在「正在读取」。修复为身份缓存与私有业务缓存分离，业务缓存实例随账号卸载/重建；原失败的刷新、跨账号、故障恢复流程均通过，并补测跨标签页切换。

同时修正学生账号字段的可访问名称，以及手机导航关闭后的可见性。历史问题记录另新增在 [S03 前端问题记录](../knowledge-base/20261016-S03前端问题记录.md)，不改动此前知识库文档。

## 7 下一阶段 S04

接入 LangGraph 运行骨架与 worker，先做最小「提交目标 → 计划确认关口 → 暂停/恢复」流程，再逐步切换现有教学行为。需要落实持久检查点、运行版本、正确身份恢复、重复提交防重、错误/取消和 SSE 续传。

首轮可以继续用 Fake Provider 验证流程。连接真实外部模型前，仍需用户确认供应商、端点、模型与预算。数学闭环、内容审核、圆桌与我教 AI 按后续里程碑接入；不把本阶段页面完成当作教学功能完成。

## 8 参考依据

工程方式参考 [Vite 官方入门](https://vite.dev/guide/)与 [React 官方建应用说明](https://react.dev/learn/creating-a-react-app)；类型生成参考 [openapi-typescript 文档](https://openapi-ts.dev/introduction)；隔离后端随验收启动的方式参考 [Playwright Web server 文档](https://playwright.dev/docs/test-webserver)。
