> 当前状态以 [1—5 改造及验收](WENJIN_1_5_ACCEPTANCE_2026-11-12.md) 为准。下文保留历史阶段记录，不能据旧阶段判断当前数据库仍未切换。

# PostgreSQL 本地部署

- Compose：`compose.wenjin-postgres.yaml`
- 容器：`wenjin-learning-postgres-1`
- 地址：`127.0.0.1:55434`；数据库 `wenjin`；应用用户 `wenjin_app`（非超级用户）
- PostgreSQL 17.11；命名卷 `wenjin-learning_postgres_data`
- 启动：`docker compose -f compose.wenjin-postgres.yaml up -d --wait`
- 停止：`docker compose -f compose.wenjin-postgres.yaml stop`
- 查看：`docker compose -f compose.wenjin-postgres.yaml ps`
- 连接配置：`data/system/postgres/connection.json`；已由 data/ 忽略规则排除 Git，勿提交或公开。
- 安装驱动：`uv pip install --python .venv/Scripts/python.exe -r requirements/postgres.txt`
- 备份：`.venv/Scripts/python.exe scripts/backup_wenjin_postgres.py`；输出 `data/backups/postgres/*.dump`。

命名卷保留容器重建后的数据。不要使用 down -v 或删除命名卷。备份需另行复制到独立存储；数据库逻辑备份不包含上传文件、配置及账号角色密码，需分别保存。

验收：应用账号 TCP 连接成功、非超级用户、事务写入成功、容器重启后记录保留。临时验收表已删除。

状态：仅完成数据库部署；应用业务仍使用 SQLite/JSON。下一步适配存储及迁移工具，核验数量/关联/用户隔离后再切换，不能仅靠配置连接地址完成迁移。
