# 05 · UML 图集

| | |
|---|---|
| 版本 | v0.1（2026-09-16） |
| 状态 | 设计定稿 |
| 说明 | 全部使用 Mermaid（GitHub/IDE 可直接渲染）；类图承载行为、ER 图（06）承载数据，二者互补不重复 |

---

## 1. 用例图（Mermaid 以流程图近似）

```mermaid
flowchart LR
    S((学生))
    T((教师))
    P((家长/治理委员会))
    O((平台运营))

    UC1([协商学习契约])
    UC2([进行探究式会话])
    UC3([查看我的镜子<br/>开放学习者模型])
    UC4([反思与归纳])
    UC5([迭代调整策略])
    UC6([设计学习体验<br/>教案/题组/项目])
    UC7([干预与辅导<br/>干预队列])
    UC8([提交金标纠正])
    UC9([审定价值观与硬约束])
    UC10([管理数据同意与可见性])
    UC11([监控进化流水线<br/>晋升/回滚])
    UC12([回放审计谱系])

    S --- UC1
    S --- UC2
    S --- UC3
    S --- UC4
    S --- UC5
    T --- UC6
    T --- UC7
    T --- UC8
    P --- UC9
    P --- UC10
    O --- UC11
    O --- UC12
    UC2 -. include .-> UC4
    UC4 -. include .-> UC5
    UC8 -. feed .-> UC11
```

---

## 2. 类图（核心领域模型）

```mermaid
classDiagram
    class Learner {
        +str learner_id
        +ConsentRecord consent
    }
    class MentalStateSnapshot {
        +uuid snapshot_id
        +KCmastery[] kc_masteries
        +MisconceptionHypothesis[] misconceptions
        +Affect affect
        +AutonomyIndex autonomy_index
        +Provenance provenance
    }
    class GoalContract {
        +str goal_contract_id
        +GoalStatement goal_statement
        +Criterion[] success_criteria
        +ContractStatus status
    }
    class Session {
        +uuid session_id
        +SessionState state_5e
        +HintLadderPos hint_ladder
    }
    class InteractionEvent {
        +uuid event_id
        +Observation observation
        +ConsentScope consent_scope
    }
    class PedagogicalAction {
        <<abstract>>
        +ActionEnvelope envelope
        +validate()
    }
    class HintAction
    class QuestionAction
    class TaskAction
    class ExplainAction
    class FeedbackAction
    class ReflectionAction
    class ActionGovernor {
        +Decision decide(envelope)
        -RuleSet hard_rules
    }
    class CompanionAgent {
        +LongTermMemory memory
    }
    class DerivedAgent {
        +AgentState lifecycle_state
        +report() Report
    }
    class AgentTemplate {
        +str template_id
        +str version
        +Budget budget
        +ReportContract contract
    }
    class SkillEntry {
        +str skill_id
        +str version
        +str regression_suite_id
    }
    class PolicyVersion {
        +str policy_id
        +SoftParams params
    }
    class Verifier {
        <<interface>>
        +Verdict verify(artifact, spec)
    }
    class StudentSimulator {
        <<interface>>
        +TwinHandle create_twin(snapshot)
        +SimTrajectory rollout(twin, policy)
    }
    class EvolutionJob {
        +run() PipelineResult
    }
    class EvalReport {
        +int hard_constraint_violations
        +float autonomy_effect
    }
    class PromotionGate {
        +GateDecision admit(report)
    }
    class TeacherGoldChannel {
        +GoldRecord submit(correction)
    }

    Learner "1" --> "*" MentalStateSnapshot : 时序快照
    Learner "1" --> "*" GoalContract : 拥有
    GoalContract "1" --> "*" Session : 治理
    Session "1" --> "*" InteractionEvent : 记录
    Session "1" --> "*" PedagogicalAction : 执行
    PedagogicalAction <|-- HintAction
    PedagogicalAction <|-- QuestionAction
    PedagogicalAction <|-- TaskAction
    PedagogicalAction <|-- ExplainAction
    PedagogicalAction <|-- FeedbackAction
    PedagogicalAction <|-- ReflectionAction
    ActionGovernor ..> PedagogicalAction : 三值裁决
    ActionGovernor ..> MentalStateSnapshot : 读前置条件
    CompanionAgent "1" --> "*" DerivedAgent : 派生
    DerivedAgent ..> AgentTemplate : 模板实例化
    PolicyVersion o-- SkillEntry : 装配
    EvolutionJob ..> StudentSimulator : 孪生推演
    EvolutionJob ..> Verifier : 过滤标注
    EvolutionJob --> EvalReport : 产出
    PromotionGate ..> EvalReport : 判定
    PromotionGate --> PolicyVersion : 晋升/回滚
    TeacherGoldChannel ..> EvolutionJob : 金标入集
```

---

## 3. 时序图

### 3.1 探究式学习会话（含派生与门控）

```mermaid
sequenceDiagram
    autonumber
    actor Stu as 学生
    participant CA as 学伴Agent(编排器)
    participant AG as ActionGovernor规则引擎
    participant LSE as 学习科学引擎
    participant ENV as 环境层(沙箱/仿真)
    participant SK as 怀疑者(派生体)

    Stu->>CA: 打开学习契约, 进入会话
    CA->>LSE: 加载 MentalStateSnapshot
    CA->>AG: 提案 TASK(inquiry, 难度带内)
    AG-->>CA: 允许
    CA->>Stu: Engage 情境问题
    Stu->>ENV: 动手探究(ENV_OP)
    ENV-->>CA: artifact + 过程数据
    CA->>LSE: 神经感知→候选状态更新
    LSE-->>CA: 误区假设分布变化
    CA->>SK: derive(怀疑者模板, 预算6轮)
    SK-->>CA: SkepticReport(两个漏洞)
    CA->>Stu: QUESTION(socratic, 针对漏洞)
    Stu->>CA: 求完整解法
    CA->>AG: 提案 EXPLAIN(worked_full)
    AG-->>CA: 拒绝(R-01: hint_level<3 且无对照artifact)
    CA->>Stu: HINT(level 1 nudge)
    Stu->>ENV: 修改探究
    ENV-->>CA: artifact 通过验证器
    CA->>Stu: REFLECTION(归因+校准)
    Stu-->>CA: 反思提交
    CA->>LSE: 固化新快照 + InteractionEvent 落事件流
```

### 3.2 目标契约协商（目标归学生所有，R-02）

```mermaid
sequenceDiagram
    autonumber
    actor Stu as 学生
    participant CA as 学伴Agent
    participant LSE as 学习科学引擎
    actor T as 教师(可选)

    Stu->>CA: 想学好"一元一次方程应用题"
    CA->>LSE: 诊断先修与当前掌握
    LSE-->>CA: 诊断报告(带置信区间)
    CA->>Stu: 提案: 成功标准/时间盒/难度带
    Stu->>CA: 修改成功标准(更低频更具体)
    CA->>AG: 校验 R-02(终稿 authored_by=student?)
    AG-->>CA: 通过
    opt 教师硬性要求叠加
        T->>CA: 课标必测项
        CA->>Stu: 说明并征得同意并入契约
    end
    CA-->>Stu: GoalContract(active)
```

### 3.3 夜间进化环（中环）

```mermaid
sequenceDiagram
    autonumber
    participant CRON as EvolutionJob
    participant REG as HardConstraintRegistry
    participant EV as Verifier+Labeler
    participant TW as 孪生模拟器
    participant PE as PolicyEvaluator
    participant PG as PromotionGate
    participant PUB as 技能库/灰度发布

    CRON->>REG: 断言 constraint_set_hash 一致(P3)
    REG-->>CRON: OK
    CRON->>EV: Stage1-2 增量事件过滤+增益标注
    EV-->>CRON: 干净数据集+GainLabel
    CRON->>TW: Stage4 候选策略孪生推演
    TW-->>CRON: SimTrajectory 集
    CRON->>PE: Stage5 评估(EvalReport)
    PE-->>PG: EvalReport
    PG->>PG: 门禁1-7(约束/回归/公平/自主性/置信)
    alt 全部通过
        PG->>PUB: 晋升+灰度1%→50%+回滚预案
    else 任一不过
        PG-->>CRON: 拒绝原因入候选池(负样本)
    end
```

### 3.4 教师金标注入（同步进化枢纽）

```mermaid
sequenceDiagram
    autonumber
    actor T as 教师
    participant TA as 师伴Agent
    participant GC as TeacherGoldChannel
    participant LSE as 学习科学引擎
    participant EVJ as EvolutionJob(下夜)

    T->>TA: "这个判定错了: 学生不是粗心, 是符号法则误区"
    TA->>GC: submit_correction(target=state_estimate)
    GC->>LSE: 快路径: 线上误区假设立即重排(安全类即时生效)
    GC-->>EVJ: 金标记录入集(进化路径)
    Note over EVJ: 次夜 Stage2/3 消化，委员会批准的纠正提升优化权重
    EVJ-->>TA: 周报: 该纠正已结晶进策略 vX.Y
```

---

## 4. SRL 闭环活动图（微环）

```mermaid
flowchart TB
    A([会话开始]) --> B{有活动契约?}
    B -- 无 --> C[GOAL_NEGOTIATE<br/>学生拥有目标]
    B -- 有 --> D[载入快照]
    C --> D
    D --> E[执行：探究/练习<br/>提示阶梯+门控]
    E --> F{验证通过?}
    F -- 否 --> G[FEEDBACK 归因+HINT 升级]
    G --> E
    F -- 是 --> H[迁移任务]
    H --> I[REFLECTION 反思关卡<br/>归因/校准/伦理]
    I --> J[固化快照+事件落流]
    J --> K[策略档案更新<br/>什么对我有效]
    K --> L{契约达成?}
    L -- 未 --> D
    L -- 已 --> M([双环：质疑并重设目标])
    M --> A
```

---

## 5. 组件图（部署视角）

```mermaid
flowchart LR
    subgraph Client["端"]
        UI1[学生端 App/Web]
        UI2[教师驾驶舱]
        UI3[家长治理端]
        UI4[进化监控台]
    end
    subgraph Core["核心服务"]
        GW[API 网关]
        ORCH[会话编排器]
        GOV[规则引擎 ActionGovernor]
        WMM[心智世界模型服务]
        AGF[派生工厂]
        EXT[外部Agent网关<br/>Claude Code/Codex白名单]
        VER[验证器集群<br/>沙箱/SymPy/Lean]
    end
    subgraph Data["数据层"]
        EVS[(事件流<br/>append-only)]
        SNAP[(快照库<br/>时序版本)]
        KG[(知识图谱/素养图谱/误区本体)]
        RET[(可插拔检索引擎<br/>GraphRAG/LightRAG/PageIndex)]
        SKB[(技能库+策略版本)]
        AUD[(审计与谱系)]
    end
    subgraph EVO["进化集群(离线)"]
        PIPE[EvolutionJob 流水线]
        SIM[孪生学生群]
        TRN[优化器 DSPy/参数搜索]
    end

    UI1 & UI2 & UI3 & UI4 --> GW
    GW --> ORCH
    ORCH <--> GOV
    ORCH <--> WMM
    ORCH --> AGF
    AGF --> VER
    ORCH --> EVS
    WMM --> SNAP
    GOV --> KG
    PIPE --> EVS
    PIPE --> SIM
    PIPE --> TRN
    PIPE --> SKB
    PIPE --> AUD
    EVS -.只读.-> AUD
```

---

## 6. 状态图补充

03 号文档已含提示阶梯 / 5E 会话 / 派生体生命周期三套状态机，
本文件不重复。开发时以 03 §4 为状态机唯一权威来源。
