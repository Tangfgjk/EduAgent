# 教育智能体 RSI 平台 · 设计文档

> **项目定位**：以"递归自进化（Recursive Self-Improvement）"为核心能力的教育伴生智能平台。
> 目标不是再造一个答疑机器人，而是构建覆盖**目标设置 → 学习执行 → 反思归纳 → 迭代调整**
> 完整自我调节学习（SRL）闭环的自主式、探究式学习系统，并在闭环之上叠加
> 学生、教师、智能体三方的循环同步进化。
>
> **北极星指标**：无智能体辅助条件下的表现增量 + 认知层级跃迁 + 学习者自主性增益。
> （不是完课率、不是互动率、不是满意度。）

---

## 文档地图（建议阅读顺序）

| # | 文档 | 内容 | 读者 |
|---|------|------|------|
| 01 | [总体架构设计](../architecture/01-overall-architecture-20260916.md) | 愿景与设计原则、六层架构、神经-符号分工、心智世界模型、多智能体派生机制、RSI 三环进化、治理、里程碑 | 全体 |
| 02 | [核心规格：心智状态 Schema](../architecture/02-mental-state-schema-20260916.md) | 学习者心智状态、学习契约和设计规则 | 后端 / 算法 |
| 03 | [核心规格：动作语法与教学法状态机](../architecture/03-action-syntax-pedagogy-state-machine-20260916.md) | 教学动作、动作信封、硬约束、提示阶梯、5E 和派生状态机 | 后端 / 算法 |
| 04 | [核心规格：进化环接口约定](../architecture/04-evolution-loop-contract-20260916.md) | 交互事件、验证器、技能库、学生孪生、策略评估和晋升门 | 后端 / 算法 / 运维 |
| 05 | [UML 图集](../architecture/05-uml-collection-20260916.md) | 用例、类、时序、状态、活动和组件图 | 全体 / 开发 |
| 06 | [ER 图与数据字典](../architecture/06-er-data-dictionary-20260916.md) | 持久化实体关系图和核心表字典 | 后端 / DBA |
| 07 | [UI 设计稿](../architecture/07-ui-design-draft-20260916.md) | 学生、教师、家长和平台端 UI 规格 | 前端 / 产品 |
| 08 | [融合方案与优势说明](../architecture/08-integration-plan-and-advantages-20260918.md) | 教师方案与学生方案的冲突裁决和融合架构 | 全体 / 评审 |
| 09 | [三方案对标与吸收](../architecture/09-three-project-comparison-20260920.md) | OpenMAIC、Hyperknow、DeepTutor 的吸收决策 | 全体 / 评审 |

## 设计文档 → 未来代码的映射约定

```
platform/
  core/            # ← 02 状态Schema（Pydantic 模型）+ 03 动作语法（类型系统与规则引擎）
  orchestration/   # ← 03 状态机执行器 + 05 时序图（会话编排、派生调度）
  agents/          # ← 05 类图（AgentTemplate / DerivedAgent）
  learning/        # ← 02 认知诊断 + 04 OutcomeLabeler / Verifier
  evolution/       # ← 04 进化环全部接口（夜间流水线、晋升门、技能库）
  gateway/         # ← 07 UI 对应的 API 面
  governance/      # ← 01 §7 治理（硬约束注册表、审计、同意管理）
```

## 版本

- v0.1（2026-09-16）：初版设计定稿，覆盖架构、三份核心规格、UML/ER/UI。
- v0.2（2026-09-18）：融合学生方案《未来学习平台》——产品壳（三栏工作区/八模块/14 屏高保真）、PLAN_VERSION、TriggerEngine、内容审核并入；新增 R-09；详见 08 号文档。
- v0.3（2026-09-20）：吸收 OpenMAIC / Hyperknow / DeepTutor——教学模式库与圆桌研讨、晨间简报与截止日期反向排程、三层记忆与记忆图谱、技能市场门禁、活书、可插拔检索、IM 触达；详见 09 号文档。
- **v1 代码（2026-09-20）**：里程碑 M0+L0 已实现；运行方式见 [平台开发指南](platform-development-guide-20260920.md)。
- **v2 代码（2026-09-20）**：**ZCode 式学习智能体运行时**——`app/agent/`（诊断→计划→执行→反思的单元级自主循环、11 个学习工具×门禁、三关口可重入暂停）+ `app/cli.py` 终端 REPL + 学习工作区（计划/错题本/笔记/作品真实文件）；已接内网 GLM-5.2 真机跑通完整学习单元，50 个测试全绿。
- **M1 第一刀（2026-09-27）**：产品定名**桂子问津（Wenjin）**；官方 Web 工作台、R-10、scaffold_type、路径推荐 v1、成长证据 v1 和网关端点已落地。**交接见 [项目交接文档](../overview/project-handover-20260928.md)**。
