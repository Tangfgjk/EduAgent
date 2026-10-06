# Platform

`platform/` 是桂子问津 Wenjin 的本地研究型运行时。`app/` 提供学习、证据、治理、学校隔离和 simulation 模块；`tests/` 保存回归与契约测试；`web/` 保存当前深色工作台；`scripts/`、`seeds/` 和 `data/` 用于本地辅助，运行数据不得提交。

常用命令：

```bash
uv sync --locked --extra dev
uv run python -m app.main
uv run python -m app.simulation.lab --port 8001
uv run pytest
```

当前主运行时仍以 SQLite 为主；学习域 Repository 提供 SQLite/PostgreSQL 双后端切片。完整 PostgreSQL、migration、生产 worker、SSO 和多租户部署属于 V4 P2 规划，不是当前平台 README 的完成声明。
