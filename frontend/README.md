# 桂子问津 React 双端

学生端与管理端各自拥有 HTML、React 入口、路由树和构建输出，不提供角色切换按钮。页面沿用原型的纸面底色、墨绿、衬线标题与侧栏工作台。

## 工程结构

```text
apps/student/            学生端
apps/admin/              管理端
packages/ui/             视觉基础、登录与角色门、布局、弹窗和状态反馈
packages/api-client/     请求、错误、CSRF 与从 FastAPI 生成的类型
packages/math/           数学素养空态与学习路径展示
scripts/generate-api.mjs 类型生成与漂移检查
e2e/                     真实 HTTP + 临时数据库的浏览器验收
dist/student/            独立构建结果（忽略）
dist/admin/              独立构建结果（忽略）
```

当前真实能力：登录/退出、角色门、账号创建/分页/本页筛选/密码重置/禁用；项目和临时空间的创建、选择、改名、归档恢复。课程、陪学、复习、圆桌、我教 AI、证据和策略是可导航的待开放页面，不生成模拟分数或假结果。

## 安装、构建和运行

实际验证环境：Node 22.17.1、npm 10.9.2。依赖已精确锁在 package-lock.json；新机器使用 npm ci，不通过全局工具修改锁定版本。

```powershell
cd frontend
npm ci
npm run check:api
npm run build
```

check:api 需要仓库根目录的 Python `.venv`，准备方式为根目录 `uv sync --locked --extra dev`。FastAPI 请求/响应变更后执行 `npm run generate:api`，检查并提交生成差异。

回到仓库根目录，按 S02 方式启动 PostgreSQL、迁移和初始化管理员。运行：

```powershell
uv run --locked python -m app.main --env-file .local/database.env
```

浏览地址：`http://127.0.0.1:4003/student/` 和 `http://127.0.0.1:4003/admin/`。后端自动挂载存在的两个构建目录，深层地址可直接刷新。缺失 JS/CSS 不回退成 HTML。

Python wheel 与前端输出分开部署：wheel 不包含 node_modules 或 React 构建结果。可以把 dist 复制到部署目录并设置 RSI_FRONTEND_DIR 为该目录，其下应有 student 和 admin 两个子目录。

## 开发热更新

后端开发进程显式允许本机两个 Vite 来源：

```powershell
$env:RSI_ALLOWED_ORIGINS='http://127.0.0.1:4003,http://127.0.0.1:5173,http://127.0.0.1:5174'
uv run --locked python -m app.main --env-file .local/database.env
```

在 frontend 下分别执行 `npm run dev:student`、`npm run dev:admin`。访问 `/student/`（5173）或 `/admin/`（5174），请求由 Vite 代理到 8000。不要在生产配置中照搬开发来源范围；同源构建预览不需要这项开发配置。

## 认证与范围

- 会话 Cookie 为 HttpOnly；不在 localStorage、sessionStorage 保存凭据或密码。写请求从 CSRF Cookie 读取 token。
- 私有 QueryClient 与登录账号同生命周期，退出/换账号后卸载；查询键带账号，后续项目数据必须继续带 workspace_id。
- 当前空间由 URL `workspace` 与按账号区分的 sessionStorage ID 记住。ID 不提供访问权限，服务端仍验证归属。
- 同源多标签页通过 BroadcastChannel 刷新身份；窗口重新聚焦会再确认身份。学生与管理页使用同一个后端会话，切换角色会触发另一页的身份检查，两种页面布局与权限仍独立。
- 创建空间表单保留本次提交的幂等键，原内容重试复用；改变内容后换键。版本冲突刷新列表，下一次提交须由用户重新确认。
- 目前不保存本地学习草稿；对应学习接口接入后再按项目设计，不把原型全局 state 搬入正式应用。

## 浏览器验收

先构建，在 frontend 执行：

```powershell
$env:PW_CHANNEL='msedge'
npm run test:e2e
$env:PW_CHANNEL='chrome'
npm run test:e2e
```

没有系统 Chrome/Edge 的机器可先 `npx playwright install chromium`，清除 PW_CHANNEL 后使用 Playwright Chromium。验收后端只监听 127.0.0.1:8781，使用 `.local/e2e/run-*` 下新建的隔离 SQLite，所有固定凭据都是测试专用，不会写入 PostgreSQL 或创建正式管理员。

覆盖真实表单/刷新/空间切换/归档恢复/角色门/跨账号与标签页/故障重试/390px/键盘弹窗，测试截图和失败追踪在忽略的 test-results 下。当前手机验证是浏览器窄屏检查，不宣称已验证实体手机或 Safari。

后端完整回归仍通过根目录 `RSI_TEST_POSTGRES=1` + pytest 执行；前端验收不替代 PostgreSQL 并发与恢复验收。
