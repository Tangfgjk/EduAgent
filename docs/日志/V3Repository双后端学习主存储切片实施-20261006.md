# V3 Repository双后端学习主存储切片实施

记录时间：2026-10-06 20:08（Asia/Shanghai，UTC+08:00）。分支：本地 `V3`。用户要求继续实施剩余任务，本次把此前架构契约的学习领域部分落成真实可运行代码；不提交推送。

## 已实现

新增 `platform/app/storage/learning_repository.py`：后端协议、Unit of Work、SQLite与PostgreSQL实现。新增 `platform/app/learning/repository_service.py`：复用现有BKT、保持与回放算法的学习领域服务；既有 `LearningService` 增加显式工厂 `for_repository(repository)`，没有改变原 `LearningService(Store)` 行为。

- SQLiteRepository使用现有Store及领域表，事务必须拥有完整提交边界，拒绝在既有事务里隐式嵌套commit。
- PGRepository自建独立schema和真实JSONB表：consent/evidence/state/transition/rebuild/snapshot/outbox/receipt/audit。数据库sequence生成event_seq，每个learner事务使用持久行锁；各连接竞争由数据库处理，不靠Python单进程锁。
- 证据hash、ID与attempt身份、collection/current授权和privacy生命周期检查、状态版本CAS、纠正引用/分叉拒绝、迟到和纠正回放在同一个unit of work。
- evidence、mastery/retention、transition、snapshot、rebuild、correction outbox、API receipt与成功审计全成或全回滚。outbox内容格式与既有SQLite RecoveryJobs匹配。
- evidence/receipt相同key同内容返回原收据，冲突不覆盖；每次状态版本恰好加1。PG事件sequence允许事务回滚留下缺号，但不能重复或倒退。
- PG限定schema标识符、受限领域表、参数化SQL、10秒锁等待和30秒语句超时；bootstrap记录版本，并拒绝未知schema版本直接继续。没有把SQLite快照或archive表伪装成PG主存储。
- 同一算法在两后端输出相同mastery、retention和离线rebuild，测试与原SQLite学习服务一致。

## 使用入口与安全边界

内部受信调用示意：

```python
from app.storage.learning_repository import SQLiteLearningRepository, PostgresLearningRepository
from app.learning.service import LearningService

# 原应用SQLite领域兼容入口：
learning = LearningService.for_repository(SQLiteLearningRepository(existing_store))

# PG学习领域独立实例；DSN由受控配置注入，禁止把DSN写进日志：
repository = PostgresLearningRepository(controlled_dsn, schema="eduagent_learning_v3")
repository.bootstrap()
learning = LearningService.for_repository(repository)
```

示例只展示服务端口，不新建用户配置或默认切换网关。对外请求不能自选learner/schema/DSN或自己提供授权可信性。本轮没有把原8000个人应用切换PG，也没有采集真人数据。

真实PG测试每个测试使用新生成 `eduagent_repo_test_<uuid>` schema，不读取用户业务表。测试完成仅删除该次生成的schema；凭据只从既有受控/忽略配置加载，输出不含密码/DSN。

## 验证

2026-10-06 20:06本地命令（在platform目录）：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_learning_repository.py tests/test_learning_storage.py tests/test_postgres_archive.py -q
.venv/Scripts/python.exe -m pip check
```

结果：`69 passed in 18.92s`，无跳过；其中42项新增双后端契约测试（SQLite21、真实PostgreSQL16 21），其余27项原SQLite存储与PG归档回归。`pip check`：`No broken requirements found.`。复用Python3.11.5和现有psycopg依赖，未安装新包。

测试覆盖：真实PG表/JSONB/版本bootstrap、无archive依赖的直接consume、与旧服务算法一致、七个提交checkpoint回滚、outbox回滚、receipt/evidence幂等与冲突、同attempt身份、状态CAS、同key八次并发只有一份事实、两个竞争state版本一个成功一个冲突、sequence故障重试、源权限伪造、跨学习者纠正、研究collection过滤与撤回、privacy隔离独立于fresh consent。

## 明确未完成的完整应用迁移

本次交付是**学习领域主存储切片**，不是整个应用已使用PG；不能把本地通过的PG切片宣称生产级基础设施完成。剩余清单：

1. SessionRuntime：SQLite backup/token/PRAGMA/table changeset需替换为真实PG snapshot/CAS事务协议；TutorSession的Store写入要迁移，保持模型工作不持事务。
2. PlanService：合同、签署、激活状态与audit同事务Repository；Workspace项目/共享revision CAS同样迁移。
3. RecoveryJobs：真实PG worker claim/lease/CAS、Plan proposal和job状态原子提交；目前PG只完成outbox，不执行PG恢复worker。
4. Memory/人工注释、Governance隐私/删除/安全审批、检索索引、定性artifact、daily budget和gateway receipt等Repository端口及双后端验收。
5. 全表正式schema迁移/回滚工具、pool/超时观测、备份恢复与删除库存、独立研究实例/身份隔离、生产监控演练。
6. 完整gateway配置选择与既有浏览器流程/压力验证后才能默认切换PG。当前config由主任务统一集成，不允许仅填DSN就视为完整PG模式。

上述范围不掩盖或降级原任务；它们是本次可靠切片之外必须完成的迁移工作。后续依照[事务依赖库存与迁移契约](../架构/V3SQLite事务依赖库存与Repository-PG迁移契约-20261006.md)逐域推进。
