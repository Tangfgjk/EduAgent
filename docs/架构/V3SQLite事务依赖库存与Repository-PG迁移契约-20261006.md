# V3 SQLite 事务依赖库存与 Repository/PG 迁移契约

建立时间：2026-10-06 18:16（Asia/Shanghai，UTC+08:00）。适用分支：本地 `V3`。20:07更新：已落地学习领域双后端 Repository 切片，详见下节；完整应用PG主存储迁移、学校多租户和生产运维切换仍未完成。

## 20:07实现增量：真实学习领域主存储切片

新增 `platform/app/storage/learning_repository.py`：`LearningRepository` 事务协议、`LearningTransaction` 领域操作、`SQLiteLearningRepository` 和 `PostgresLearningRepository`。新增 `platform/app/learning/repository_service.py`，通过 `LearningService.for_repository(repository)` 显式选择。既有 `LearningService(Store)` 和 SessionRuntime 保持不变。

SQLite实现复用现有Store表和事务边界，无新复制库；PostgreSQL实现用独立 schema、真实 JSONB domain表、数据库sequence、learner行锁、唯一约束、条件更新CAS和上下文事务。直接在PG写入/查询 learning evidence、状态、转移、快照、重建、outbox、收据和审计，不依赖 SQLite backup 或 PostgreSQL归档导出。

可用切片：consent、current/collection authorization、privacy quarantine检查、consume、evidences、masteries/retentions、transitions、receipt、离线rebuild。BKT/Retention/replay仍调用现有算法；SQLite与真实PG的同一测试契约通过。PG sequence 回滚允许缺号但保持唯一递增，不强求SQLite的序列回收行为。

当前**不得把全系统配置直接改成PG**：SessionRuntime的SQLite snapshot/changeset、PlanService、WorkspaceService、RecoveryJobs worker、Memory/Governance/检索索引仍是直接SQLite调用。PG切片能原子排入正确格式的correction outbox，但其worker和plan proposal还需Repository迁移后才能作为完整PG后台链路执行。整个网关没有切换到该后端，不宣称生产PG上线。

测试记录：[真实双后端学习主存储切片](../日志/V3Repository双后端学习主存储切片实施-20261006.md)。以下旧“未开始”阶段表是18:16初始基线；其学习领域部分已实现，其他领域仍按后文剩余验收契约推进。

## 当前边界

运行时当前以单进程/本地 SQLite `Store` 为主：`platform/app/storage/db.py` 创建 schema、共享 `RLock`、`BEGIN IMMEDIATE` 和 append-only 事件/快照；各服务直接使用 `store.conn` 的参数化 SQL。`platform/app/storage/postgres.py` 目前是**归档/恢复端口**，不是运行时 Repository，也不能作为 PG 主库已验收的证据。

18:16架构准备未修改Runtime/Runner/网关；20:07已新增学习领域Repository双后端实现，仍不修改Runtime/Runner/网关。迁移的唯一允许方向是：先固定行为契约和双后端测试，再逐个替换调用点；不得以兼容层掩盖事务边界、授权、幂等或CAS差异。

## SQLite 依赖库存

| 调用模块 | 当前直接依赖 | 事务/一致性责任 | 迁移时必须保持 |
|---|---|---|---|
| `storage/db.py::Store` | `sqlite3.Connection`、`RLock`、`executescript`、`event_clock` | schema 初始化；参数化读写；事件序列全局递增 | Repository 不暴露连接；事件序列由后端生成且同一事务可见 |
| `learning/service.py` | `BEGIN IMMEDIATE`、证据 hash、evidence/state/transition/receipt 多表 SQL | `consume` 将授权、证据、mastery、retention、transition、snapshot、rebuild、outbox、receipt 原子提交；异常回滚 | 一个 `submit_evidence` 单元必须全成或全回滚；同 evidence ID 内容冲突；消费 receipt 幂等 |
| `orchestration/runtime.py` | SQLite `backup` 快照、`PRAGMA table_info`、allowlist changeset | 模型工作不持生产写锁；提交前 token/CAS；append-only 表不得改写/删除，`learning_states/runtime_sessions` 可更新 | Repository 提供 snapshot token + allowlisted changeset commit；检测并发写和 schema 变更 |
| `learning/plans.py` | `BEGIN IMMEDIATE`、版本读取、状态更新、审计写入 | plan 签名时检查 learner 及版本，audit 同事务；旧版本保留 | `sign/modify/reject` 的版本 CAS、签名权限和审计原子性 |
| `learning/workspace.py` | `workspace_records(record_id,revision)`、`BEGIN IMMEDIATE` | 每次修改插入新 revision；期望 revision 不符拒绝 | revision CAS、历史不可覆盖、同一 record 的并发竞争只有一个成功 |
| `learning/recovery.py` | correction job/outbox SQL、`json_extract`、`BEGIN IMMEDIATE` | 排队在证据事务内；worker claim/完成/失败可重试；完成结果不可被失败 worker 覆盖 | outbox 与源事务原子；job claim CAS；重复运行不产生重复 plan/attempt |
| `learning/memory.py` / `memory_review.py` | `memory_views`、`memory_annotations`、`learning_audit`、当前授权查询 | view fingerprint 来源绑定；人工注释追加/撤回，不改 evidence/mastery；audit 故障整笔回滚 | source snapshot、annotation 版本链、撤回/重建失效与 learner 删除清单一致 |
| `governance/service.py` | SQLite 表发现、`BEGIN IMMEDIATE`、动态删除清单 | 权限、隐私状态、删除 inventory 和 isolated deletion 统一事务 | Repository 暴露显式数据域 inventory；未知表/未归属数据阻挡删除；不得把 `sqlite_master` 当跨库契约 |
| `gateway/*_routes.py` | 少量建表/receipt/budget SQL、`store.conn` | API 幂等 receipt、每日预算、审计与业务写入边界 | 网关改为调用 service/repository；外部请求不得获得任意 SQL 或绕过授权 |
| `retrieval.py` | 可选同一 SQLite 连接存 `knowledge_sources/chunks` | 来源导入和 chunk 替换事务；当前文件 hash 复核 | 检索索引可重建，不能与 learning evidence 共用提交假设；PG 迁移需单独索引事务 |

### 表分类与写入纪律

- **append-only事实**：`events`、`learning_evidence`、`learning_transitions`、`learning_rebuilds`、`learning_audit`、`snapshots`、`verdicts`、`consent_records`（同主体/版本幂等）。PG 不得使用普通 UPDATE 覆盖历史。
- **CAS/当前投影**：`learning_states`、`runtime_sessions`、`workspace_records` 的新 revision、计划激活状态、预算计数、job 状态。更新必须带旧 version/revision/状态条件。
- **幂等收据**：`learning_api_receipts`、`gateway_receipts`、`evidence_consumption`、`memory_annotations`。相同 key+hash 返回原结果；相同 key+不同 hash 是冲突，不得静默覆盖。
- **可重建/索引**：`memory_views`、`knowledge_sources/chunks`、`snapshots` 部分读投影。迁移故障时从受保护事实重建，不把投影当唯一事实。
- **待删除/治理域**：`memory_annotations` 已加入当前 SQLite 删除 inventory；迁移前必须让 PG repository 的 learner purge 覆盖它及未来新增主体数据表。

## 目标 Repository 契约（待实现）

目标接口应以领域对象/事务句柄为边界，避免暴露 SQLite/psycopg 连接。建议最小端口：

```text
Repository.begin() -> Transaction
Transaction.authorize(learner, purpose)
Transaction.submit_evidence(evidence, expected_state_versions, idempotency_key)
Transaction.append_event(event)
Transaction.append_snapshot(snapshot)
Transaction.append_audit(audit)
Transaction.upsert_current_state(state, expected_version)  # CAS
Transaction.append_workspace_revision(record, expected_revision)
Transaction.enqueue_correction(job)  # same transaction as evidence
Transaction.put_receipt(scope, idempotency_key, content_hash, payload)
Transaction.commit() / rollback()
Repository.read_snapshot(learner, token) -> SourceSnapshot
Repository.commit_changeset(snapshot_token, allowlisted_changeset) -> CommitReceipt
Repository.learner_inventory(learner) -> Inventory
Repository.purge_isolated(learner, approved_policy) -> DeletionReceipt
```

`Transaction` 不能接受任意 SQL；表名、字段、append/update 类型由实现的 allowlist 控制。事务上下文中禁止网络/LLM/OCR；昂贵模型工作必须在 snapshot 外完成，提交阶段只做授权、CAS 和受限写入。

## SQLite -> PostgreSQL 迁移阶段

1. **契约阶段（当前）**：保留 SQLite 行为作为 reference；列出每个调用点的事务单元和错误类型，补齐双后端 fixture。PG 归档继续单独运行。
2. **Repository 阶段（未开始）**：实现 SQLiteRepository 包装现有 `Store`，先让 service 不再读取 `conn`；禁止先把 PG 方言散落到业务层。
3. **PG 主存储阶段（未开始）**：实现 PostgreSQLRepository，使用真实 schema/migrations、`SERIALIZABLE` 或明确行锁策略、参数化 JSONB、唯一约束、`SELECT ... FOR UPDATE`/版本条件。迁移工具必须校验 hash、event_seq、receipt 和审计数量。
4. **双后端阶段（未开始）**：同一 contract fixture 在 SQLite/PG 执行，比较领域结果和 append-only 内容 hash；故障注入在各事务阶段验证无半提交。
5. **切换阶段（未开始）**：先独立研究实例灰度和只读比对；具备备份恢复、监控、连接池、锁超时、迁移回滚和数据删除演练后才能考虑默认 PG。当前不能宣称生产迁移或多租户完成。

## 双后端验收矩阵

| 契约 | SQLite reference | PostgreSQL 目标 | 必测故障/断言 |
|---|---|---|---|
| 证据提交原子性 | `test_learning_storage` fault: evidence/mastery/transition/snapshot | 同一 fault hooks | 任意阶段失败后事实、状态、receipt、outbox均无新增 |
| evidence 幂等/冲突 | 相同 ID+hash返回同 receipt；不同 hash `EvidenceConflict` | 唯一 `(learner,evidence_id)` + hash check | 并发 8 worker 至多一份事实，冲突不能覆盖 |
| 状态 CAS | `expected_versions` 和 state version 条件 | `UPDATE ... WHERE version=?` 检查 rowcount | 两连接竞争只有一个成功，另一个可安全重试 |
| runtime changeset | snapshot token、allowlist、append-only保护 | `REPEATABLE READ`/token + 条件更新 | LLM 工作期间外部写入导致 `RuntimeConflict`，无部分提交 |
| workspace revision | `expected_revision`，历史新行 | 唯一 `(record_id,revision)` + CAS | 并发修改一个成功；历史行不变 |
| plan 签名 | learner/version/audit 同事务 | 行锁/版本条件 | audit 故障回滚；草案不能冒充签名 |
| correction outbox | 同 evidence transaction；worker claim CAS | `FOR UPDATE SKIP LOCKED` 或等价 lease | 重试一次不重复 plan；失败 worker不覆盖已完成结果 |
| memory annotation | fingerprint、reviewer、supersedes、audit | hash/check constraints + FK/主体隔离 | 源 view 变化旧注释不可读；audit 故障整笔回滚 |
| learner purge | inventory unknown table blocker，隔离 DB 才执行 | 显式数据域清单/外部备份状态 | `memory_annotations` 必须删除；未知表阻挡；不声称备份已擦除 |
| 归档/恢复 | 当前 `postgres.py` 归档验证 | 主 Repository 迁移另测 | archive 成功不能替代 runtime 双后端通过 |

## 禁止提前宣称完成

- PostgreSQL 16 已运行或归档通过，不等于 PG 主存储/Repository 已完成。
- SQLite 直接 SQL 的测试通过，不等于跨后端事务隔离和锁语义等价。
- `memory_views`/检索 chunk 可以重建，不得因此跳过 source hash、授权和删除治理。
- 没有真实研究授权、教师审核、生产身份/租户与恢复演练时，不得把迁移准备包当作 Pilot 或生产基础设施。
