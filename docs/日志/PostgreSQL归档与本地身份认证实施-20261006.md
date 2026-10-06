# PostgreSQL 归档与本地身份认证实施

- 记录时间：2026-10-06 14:38:00 +08:00。
- 分支：`v1/20261005-phase1-foundation`。仅本地开发，没有提交或推送。
- 范围：独立归档端口、安全离线备份恢复、本地单用户 API 身份认证。

## 安装环境

Docker Desktop 4.29.0 / Docker Engine 26.0.0 已有环境。启动容器 `eduagent-postgres`，镜像 `postgres:16`，digest 为 `sha256:42df6755a4110ea9e324bfaefd02eb2f6afc0f0b9bc063c55c9f73dc29d25770`。只绑定 `127.0.0.1:5433` 到容器 5432，重启策略 `unless-stopped`，数据保存在忽略目录 `platform/data/postgres/pgdata`。

连接配置仅在忽略的 `platform/data/postgres/.env.local`，凭据另见忽略的 `docs/本地配置/数据库与登录-20261006.md`。版本依赖为 `psycopg 3.3.6`、`psycopg-binary 3.3.6`、`tzdata 2026.5`；`platform/pyproject.toml` 声明 psycopg[binary]>=3.2，uv.lock 已解析并与其他代理 OCR 依赖合并。

## 归档端口

`app/storage/postgres.py` 独立管理 `evidence-archive-v1` schema，归档证据、掌握/保持状态、迁移记录、幂等收据。SQLite 仍然是运行时主数据源，未宣称 PostgreSQL 运行时迁移完成。

相同内容重复导出返回原收据。每学习者使用 PostgreSQL 事务 advisory lock，避免不同进程同时首次导出的竞争；证据标识/序号、相同状态版本、迁移标识发生内容冲突时拒绝覆盖。导出前和事务提交前检查教学用途授权。四个写入阶段的故障会整笔回滚。

历史 `as_of` 导出明确拒绝，避免选了历史证据却归档当前状态；历史查询仍使用学习核心 replay。恢复只导入不可变证据事实和事件时钟，不恢复授权、会话、消费记录或当前状态。恢复后的数据是离线取证目标，不能直接视作已完整恢复的运行环境；业务状态需要明确授权后的重新构建。

`app/storage/archive_cli.py` 提供健康检查、SQLite 快照备份、归档导出、事实恢复。目标采用独占新文件创建，拒绝覆盖现有文件；SQLite backup API 完成后 integrity_check；失败关闭 Windows 文件句柄并清理本次新建目标。错误输出不回显 DSN 或凭据。

在 platform 目录使用命令：

```powershell
.venv\Scripts\python.exe -m app.storage.archive_cli health
.venv\Scripts\python.exe -m app.storage.archive_cli sqlite-backup data/local.sqlite3 data/backups/local-20261006.sqlite3
.venv\Scripts\python.exe -m app.storage.archive_cli export data/local.sqlite3 demo_student_001
.venv\Scripts\python.exe -m app.storage.archive_cli restore demo_student_001 data/backups/evidence-restore-20261006.sqlite3
```

实际 SQLite 路径以忽略的本地配置为准。未制作虚假示例学习者的生产归档，不向用户数据库插入测试学习者。真实 PG 测试各用随机 learner，并只清理对应测试记录。

## 本地身份认证

`app/gateway/security.py` 增加 opt-in 本地单用户身份。默认 Settings 关闭；本机忽略 `.env.local` 开启，密码只存 PBKDF2-SHA256 600000 次加盐散列。登录使用 32 字节随机 opaque Cookie，服务端只保留 token hash；HttpOnly、SameSite=strict、8 小时到期，注销撤销，重启后会话失效。HTTP localhost 不启用 Secure；HTTPS 部署必须配置 Secure。

所有 `/api/` 统一保护，只有 auth status/login 允许匿名访问。写请求拒绝跨站 Origin / Sec-Fetch-Site，登录按真实连接 peer 限每分钟 5 次，不信任转发头。会话有数量上限并清除到期项。匿名访问工作台重定向 `/login`；新增独立登录页，没有修改工作台主页面。

登录不是教学同意，不取代 learning consent。教师/家长用途口令与学习者 ID 所有权校验继续保留。配置增加独立 `RSI_LOCAL_PARENT_TOKEN`。这是本地单用户模式，不支持真实多用户、多租户、角色隔离、共享会话库或互联网生产部署。

## 测试

2026-10-06 14:37 +08:00 定向结果：PostgreSQL 12 项真实数据库测试通过；离线 CLI 文件安全 4 项通过；本地身份 6 项通过。覆盖导出恢复对照、幂等、并发、校验和篡改、授权撤回、历史快照拒绝、四阶段回滚、状态冲突、现有目标保护、损坏源清理、密码散列、过期注销重启、Cookie、限流及跨站保护。

FastAPI TestClient 有上游 Starlette/httpx 弃用警告，不影响当前测试结果。全量测试结果另追加最终记录；登录页浏览器验证由主代理集成执行。

2026-10-06 14:39:56 +08:00：共享代码全量测试 `395 passed`，耗时 16.20s，一条上述上游警告。`git diff --check` 没有空白错误，只有 Windows LF/CRLF 提示。去掉新增测试的未使用 import；没有删除既有业务代码、测试资产或用户文件。

2026-10-06 14:46:42 +08:00：审查发现 `.env` 和 `.env.local` 使用先后 setdefault 时，默认文件可压过本地登录开启值。已将 Settings.load 改为 `真实环境变量 > .env.local > .env`，文件读取不污染 os.environ，支持重新加载更新后的文件。新增配置优先级、显式空值、重复加载及课程覆盖层测试 3 项；与身份、恢复任务、项目/看板组合测试共 `17 passed`。本机家长用途口令只添加在忽略的 `.env.local`，凭据只同步到忽略的本地配置文档；`git check-ignore` 确认这两处被忽略。

2026-10-06 14:55 +08:00：补充独立连接并发回归 `tests/test_followup_concurrency.py` 共 3 项并通过。使用 rollback 后事件窗口确定性复现另一 worker 先完成，断言失败元数据不覆盖已完成任务、尝试次数为一次、再次执行仍只有一个计划；质性作品使用显式不同微秒时钟和提交屏障，避免 Windows 同时钟 tick 掩盖问题，验证相同请求两个 200 且收据相同、冲突内容一个 200 一个 409，数据库仅一份证据/收据/作品。修复代码由根代理完成，本代理仅添加测试，没有修改恢复/看板/质性模块。
