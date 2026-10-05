# 项目总览与开发路线

更新时间：2026-10-05

## 1. 项目定位

桂子问津（Wenjin）是一个面向长期学习过程的教育伴生智能平台。它把目标设置、学习执行、反思归纳和迭代调整组织成自我调节学习闭环。系统的北极星不是对话次数或完课率，而是无辅助表现增量、认知层级跃迁和学习者自主性增益。

核心原则是：LLM 可以提出教学动作，但不能绕过教育规则；事实和推断分离；学习状态跨会话积累；高层判断必须有证据链；v1 先建立数据与验证基础，v2 才考虑自动进化。

## 2. 当前系统形态

```text
学生输入
  -> Perception / Verifier
  -> MentalState + Learning Evidence
  -> BKT / Path Recommendation
  -> Policy + ActionGovernor
  -> Agent / LLM
  -> append-only Event / Snapshot
```

当前只有一个桂子问津自己的 Agent Runtime。外部项目只提供可抽取的能力参考，不作为第二个控制器：DeepTutor 主要提供 Learning、Memory、RAG 和 Provider 的工程参考，OpenMAIC 提供教学场景，Hyperknow 提供主动学习节奏。

## 3. 已完成基线

设计层已经形成总体架构、心智状态 Schema、动作语法与教学法状态机、R-01 至 R-10 硬约束、进化环接口、UML、ER 数据字典、UI 设计、融合方案和项目交接材料。

实现层已经包含：

- `platform/app/core/`：Pydantic 状态模型、动作信封、提示阶梯、5E 状态机和硬约束裁决。
- `platform/app/orchestration/`：TriggerEngine、Policy 和会话编排。
- `platform/app/learning/`：Perception、BKT tracer、Verifier、路径推荐和成长证据聚合。
- `platform/app/agent/`：目标到反思的学习循环、工具调用、三个必须停下的人类关口、工作区和转录。
- `platform/app/llm/`：OpenAI-compatible 客户端和 FakeLLM 回退。
- `platform/app/storage/`：SQLite 事件和快照的 append-only 存储。
- `platform/evolution/`：L0 内容沉淀演示。
- `platform/web/`：计划、陪学、圆桌、成长证据和设置五个工作台视图。

项目交接材料记录的历史基线是 M0 全量完成、M1 第一刀完成、61 个测试通过。本次整理后已在隔离环境重新执行全量测试，结果为 `61 passed`；同时修复了同时间戳快照读取顺序不稳定的问题。

## 4. 尚未完成

第一阶段尚未完成的核心工作是 DeepTutor Learning 能力的裁剪式接入，具体包括 `LearningEvidence` 适配、统一掌握门控和 retention/review 适配器，以及对应的先行测试。

随后还需要完成三层 Memory 巩固器、PostgreSQL 迁移、冻结回归场景、派生 Agent ①④⑤⑥、教师金标通道、最小 RAG 和 Provider Registry。RSI 自动进化当前只保留接口、数据、审计和人工晋升准备，不点火。

## 5. 分阶段路线

### 阶段一：Learning 闭环

先写 Adapter Test，再移植 DeepTutor 的必要能力。闭环为 `Verifier -> LearningEvidence -> BKT / Gate -> Retention -> PathRecommendation -> Policy`。不可验证结果只进入审计，不更新长期学习状态。

### 阶段二：长期 Memory

保留 L1 原始事实、L2 场景摘要和 L3 学习者画像。L1 append-only，L2 可重建，L3 必须能追溯到 L1/L2，禁止以 L3 作为自身证据。

### 阶段三：最小 RAG 与 Provider

只建设 PDF/Markdown 的解析、切块、混合检索和 KC/KG grounding；模型厂商通过统一 Provider Registry 接入，上层教育内核不感知厂商。

### 阶段四：可验证进化

当真实学习者数据、冻结回归集、教师金标和基线面板达到交接文档定义的条件后，才评估 RSI 灰度晋升。硬约束必须哈希锁定并保持治理审批。

## 6. 开发纪律

1. 一个 Controller：只保留桂子问津 Agent Runtime。
2. 教育规则必须落在代码和测试中，不能只写在 Prompt。
3. 先测试 Adapter，再引入外部源码。
4. 事实与推断分开，所有高层结论带 provenance。
5. LLM 只能提案，ActionGovernor 决定是否允许执行。
6. 机制不可测就不进入 v1。
7. 目标是学生越来越能独立完成，而不是 AI 越来越替学生完成。
