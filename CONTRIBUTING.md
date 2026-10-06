# Contributing

## 分支与范围

`main`、`V2`、`V3` 是历史基线；`V4` 用于最终收口规划。提交前说明影响的产品边界、Evidence contract 和验收证据。不要把 synthetic 结果标为 real，不要绕过 ActionGovernor、课程来源校验或 consent。

## 本地检查

```bash
uv sync --locked --extra dev
uv run pytest
python -m compileall app
```

涉及 API 的变更应补契约测试；涉及页面的变更应补桌面/移动 smoke；涉及文档的变更应更新 `docs/文档导航.md` 和对应时间记录。

## 提交纪律

不提交 `.env`、密码、API key、DSN、本地数据库、`platform/data/`、缓存、截图或个人路径。提交信息使用清晰的 `feat:`、`fix:`、`docs:` 或 `test:` 前缀。
