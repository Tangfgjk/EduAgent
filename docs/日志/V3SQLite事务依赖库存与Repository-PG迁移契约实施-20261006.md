# V3 SQLite 事务依赖库存与 Repository/PG 迁移契约实施

记录时间：2026-10-06 18:18（Asia/Shanghai，UTC+08:00）。本次仅完成架构盘点和验收契约，没有修改 Runtime/Runner、网关、SQLite schema 或 PostgreSQL 归档实现。

## 已完成

- 阅读并盘点 `storage/db.py`、`learning/service.py`、`orchestration/runtime.py`、`learning/plans.py`、`workspace.py`、`recovery.py`、`memory.py/memory_review.py`、`governance/service.py`、网关直接 SQL 和 `storage/postgres.py`。
- 新建[SQLite事务依赖库存与Repository/PG迁移契约](../架构/V3SQLite事务依赖库存与Repository-PG迁移契约-20261006.md)，记录直接连接调用点、表分类、事务单元、CAS、append-only、幂等、故障要求、未来 Repository 端口和 PostgreSQL 分阶段迁移。
- 将新 `memory_annotations` 纳入 learner purge 的迁移验收要求；当前治理实现已有 SQLite inventory，但未来 PG repository 必须显式实现同一数据域。

## 核实的当前事实

- SQLite `Store` 以共享 `RLock` 与 `BEGIN IMMEDIATE` 保证本地提交；LearningService 的 evidence 消费把状态、转移、快照、重建、纠正 outbox 和 receipt 放在同一事务。
- Runtime 复制 SQLite 快照，在模型工作期间不持生产写事务；提交时检查全局 token、schema、allowlist、append-only 行和可变表 CAS。
- Plan/workspace/recovery/governance 各自有事务和 CAS/状态条件，不能在迁移中简单替换为无条件 upsert。
- `storage/postgres.py` 有真实归档/恢复测试和 hash/版本防覆盖，但职责是 archive/restore，不能称作 runtime PostgreSQL。

## 当前未完成与外部条件

Repository 抽象、PG schema/migrations、连接池/锁超时、双后端 fixture、真实 PG 主存储故障注入、迁移/回滚、备份恢复和生产监控均未完成。需要主任务后续安排实现与真实 PostgreSQL 16 环境验收；不能以本地 SQLite 回归替代。

本轮未安装包、未启动/修改数据库、未提交或推送。建议下一步先实现 `SQLiteRepository` 兼容端口和双后端测试 contract，再实现 PG repository；保持现有 Runtime/Runner 行为不变。
