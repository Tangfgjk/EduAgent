# 04 · 核心规格：进化环接口约定

| | |
|---|---|
| 版本 | v0.1（2026-09-16） |
| 状态 | 设计定稿 |
| 上游 | 01 §6 RSI 三环进化、01 §3.3 进化边界 |
| 下游 | `platform/evolution/` 实现包、05 UML 时序图「夜间进化环」 |

本文是进化层的 **API 契约**。接口以 Python Protocol 风格表述 + JSON 数据契约，
所有结构体字段与 02/03 号文档交叉引用。总原则：
**进化只触软参数（P3），真实世界是唯一锚点（P7），一切可回放可审计。**

---

## 1. 交互事件流 `InteractionEvent`（append-only，进化的唯一原料）

```jsonc
{
  "event_id": "uuid",
  "learner_pseudo_id": "pd_...",        // 进化层只见伪ID(脱敏视图)
  "session_id": "uuid",
  "ts": "2026-09-16T10:02:33+08:00",
  "actor": { "kind": "student | companion | derived_agent", "ref": "..." },
  "action_ref": "act_...",              // 03 §1 信封ID(学生事件为null)
  "observation": {
    "kind": "utterance | answer | artifact | latency | revision | help_seeking | env_result",
    "payload_ref": "存储指针, 明文留在主库, 事件流存指针+摘要"
  },
  "verdict_ref": "uuid | null",         // §2 验证裁决
  "context_snapshot": {
    "mental_state_snapshot": "uuid",    // 02
    "policy_version": "policy_v42",
    "state_machine_pos": { "hint_ladder": "L1", "session_5e": "Explore" }
  },
  "consent_scope": "teaching | evolution | research",   // 同意范围, 决定可用性
  "audit_id": "uuid"
}
```

约束：事件流**不可修改不可删除**（纠错用补偿事件）；`consent_scope` 不含
`evolution` 的事件对进化环不可见；保留与脱敏策略见 02 §3.3 与 01 §7。

---

## 2. 验证器 `Verifier`（一切进化的信任基础）

```python
class Verifier(Protocol):
    def verify(self, artifact: Artifact, spec: TaskSpec) -> Verdict: ...

@dataclass
class Verdict:
    artifact_id: str
    status: Literal["passed", "failed", "partial", "unverifiable"]
    score: float                                   # 0..1
    rubric: dict[str, float]                       # rubric 维度分
    taxonomy_level: Literal["remember","understand","apply",
                            "analyze","evaluate","create"]   # Bloom 打标
    misconception_hits: list[str]                  # 命中的误区假设(联动02)
    explainability: str                            # 面向师生的裁决理由
    verifier_id: str                               # 如 verifier.sympy.1.3 / verifier.pyexec.2.0
```

实现要求：`sympy` 等价验算、代码沙箱执行+测试、Lean 证明检查、
rubric-LLM 评阅（作文/作品，必须附 `explainability` 且抽样人审）。
**`unverifiable` 的样本永远不得进入进化训练集**（宁可少，不可脏）。

---

## 3. 结果标注器 `OutcomeLabeler`（把交互翻译成增益标签）

```python
class OutcomeLabeler(Protocol):
    def label(self, window: EventWindow) -> GainLabel: ...

@dataclass
class GainLabel:
    window: EventWindow
    post_test_ref: str            # 真实后测引用(外部锚点, P7) — 必填
    mastery_delta: dict[str, float]      # kc_id -> Δp_mastery(后测校准)
    taxonomy_lift: int            # 认知层级跃迁(可负)
    autonomy_delta: float         # 02 §2 autonomy_index 复合值变化
    transfer_evidence: list[str]  # 迁移任务证据引用
    labeler_version: str
```

禁止项（古德哈特防线）：`GainLabel` 中**不允许出现**完课率、消息数、停留时长、
点赞/星标等互动指标；它们只可作诊断遥测，永不进奖励与晋升判据。

---

## 4. 技能库 `SkillLibrary`（结晶化的策略，可版本可回归）

```python
class SkillLibrary(Protocol):
    def crystallize(self, candidate: StrategyCandidate,
                    provenance: EvalReport) -> SkillEntry: ...
    def retrieve(self, ctx: SessionContext) -> list[SkillEntry]: ...
    def promote(self, skill_id: str, to_version: str) -> None: ...   # 须过晋升门
    def revoke(self, skill_id: str, version: str, reason: str) -> None: ...

@dataclass
class SkillEntry:
    skill_id: str                       # 学生 strategy_profile 引用的就是它(02 §3.2)
    version: str                        # semver
    body: dict                          # 提示词模板/参数化策略/工作流定义
    applicable_context: dict            # 学段/KC前缀/自主性区间/误区空间
    regression_suite_id: str            # 该技能的专属回归测试集
    lineage: dict                       # 从哪些交互与评测中结晶而来
    status: Literal["candidate","active","deprecated","revoked"]
```

**技能市场兼容与门禁（v0.3，融合 DeepTutor EduHub）**：`SkillEntry.body` 采用开放
Agent-Skills 标准格式，支持导入导出与社区分发；本平台也可通过白名单外部编程 Agent
（Claude Code/Codex 等）驱动自身开放接口（机器可读输出）参与生态。铁律：
**市场开放，门禁不开放**——外部/社区技能导入后一律 `candidate`，
必须过验证器回归 + 晋升门 + 硬约束检查才可转 `active`；
社区来源在 `lineage.origin` 中永久标注，出问题可整批回收。

---

## 5. 数字孪生模拟器 `StudentSimulator`

```python
class StudentSimulator(Protocol):
    def create_twin(self, snapshot: MentalStateSnapshot,
                    cohort_spec: CohortSpec | None,
                    observation_filter: ObservationFilter) -> TwinHandle: ...
    # ObservationFilter(MWM第一人称局部观测): 定义孪生的感知权限与认知视角;
    # 孪生不得全知——看不到标准答案、教师意图等全局信息, 只能接收
    # 该学生可感知/可推断的内容, 否则模拟分布系统性失真
    def rollout(self, twin: TwinHandle, policy_ref: str,
                episode_spec: EpisodeSpec) -> SimTrajectory: ...
    def calibration_report(self, twin: TwinHandle) -> CalibrationReport: ...

@dataclass
class CalibrationReport:
    response_distribution_distance: float   # 与真实交互分布的距离(越小越好)
    misconception_discrimination: float     # 孪生能否复现已知误区行为
    module_error_decomposition: dict        # 分模块误差: 状态解析/观测生成/转移动态/价值评估
                                            # (MWM Oracle实验: 转移模拟是最大瓶颈, 优先投入)
    known_gaps: list[str]                   # 已知盲区清单(显式声明)
    calibrated_at: str
    trust_weight: float                     # 晋升门据此加权(§6)
```

校准制度：真实交互周度回放对齐；`trust_weight` 随校准结果自动升降；
校准误差按模块分解定位（状态解析/观测生成/转移动态/价值评估，转移动态通常是瓶颈，
见 01 §4.6 MWM Oracle 结论）；**孪生产物永远不得作为学生真实状态的证据写入 02 快照**
（只能用于演练/推演）。

---

## 6. 策略评估与晋升门 `PolicyEvaluator` / `PromotionGate`

```python
class PolicyEvaluator(Protocol):
    def evaluate(self, policy_ref: str, eval_spec: EvalSpec) -> EvalReport: ...

@dataclass
class EvalReport:
    candidate_policy: str
    hard_constraint_violations: int        # 必须 == 0
    constraint_set_hash: str               # 必须等于当前注册表哈希(P3)
    sim_gain: dict                         # 孪生群上的预估增益
    regression_delta: dict                 # 对照基线策略的回归结果
    taxonomy_lift: float
    fairness_audit: dict                   # 分组增益差、任务难度分配分布
    autonomy_effect: float                 # 策略对自主性指数的影响(可为负→可一票否决)
    sim2real_confidence: float             # 由 CalibrationReport.trust_weight 折算

class PromotionGate(Protocol):
    def admit(self, report: EvalReport) -> GateDecision: ...

# GateDecision 判定顺序(全部通过才晋升):
#   1. hard_constraint_violations == 0 且 constraint_set_hash 匹配   ← P3
#   2. regression_delta >= -epsilon(全指标), taxonomy_lift >= 0
#   3. fairness_audit 通过(分组增益差 < 阈值)
#   4. autonomy_effect >= 0(策略不得以牺牲自主性换短期增益)          ← P4
#   5. sim2real_confidence 低 → 强制提高真实A/B样本量要求            ← P7
#   6. L1 级变更: 人工(教师委员会)批准位 == True
#   7. 生成灰度计划(1%→10%→50%)与自动回滚预案
```

**回滚**：任何晋升保留上一版本热备；灰度期监控增益/约束/投诉三类信号，触线自动回退。

---

## 7. 夜间进化流水线 `EvolutionJob`（中环的批处理编排）

```text
EvolutionJob(nightly):
  Stage 1  COLLECT   拉取 consent_scope⊇evolution 的增量 InteractionEvent
  Stage 2  FILTER    Verifier 过滤(丢弃 unverifiable) + GainLabel 标注
  Stage 3  DISTILL   失败模式聚类 → StrategyCandidate(含对软参数的调整提案)
  Stage 4  OPTIMIZE  在孪生学生群上离线推演/优化(DSPy提示优化|软参数搜索)
  Stage 5  EVALUATE  PolicyEvaluator 产出 EvalReport(含回归测试集全跑)
  Stage 6  GATE      PromotionGate.admit → 晋升/入候选池/拒绝
  Stage 7  PUBLISH   灰度发布 + 技能库版本更新 + 全程审计写入
  断言: 开跑前 hard_constraint_set_hash == 注册表哈希, 不等则中止并告警(P3)
```

各 Stage 为独立进程、幂等、可重放；Stage 间以带版本的数据集交接（数据集即产物）。

---

## 8. 教师金标通道 `TeacherGoldChannel`（同步进化的枢纽）

```python
class TeacherGoldChannel(Protocol):
    def submit_correction(self, correction: TeacherCorrection) -> GoldRecord: ...

@dataclass
class TeacherCorrection:
    teacher_id: str
    target: Literal["verdict", "hint", "task", "state_estimate", "strategy"]
    target_ref: str                  # 被纠正对象的引用
    correction: dict                 # 结构化纠正(如: 误区标注/评分手工覆盖/策略否决)
    rationale: str                   # 教师理由(自然语言, 进化时作为弱监督文本)
```

两条生效路径：① **快路径**——纠正直接覆盖线上呈现（安全类立即生效）；
② **进化路径**——进入金标数据集，参与 Stage 2/3，教师委员会批准的纠正
可提升该教师所辖学科策略的优化权重。**金标是同步进化的机制核心**：
教师隐性智慧 → 结构化 → 注入智能体；智能体微观洞察 → 师伴简报 → 提升教师（01 §6.1）。

---

## 9. 硬约束注册表 `HardConstraintRegistry`

```python
class HardConstraintRegistry(Protocol):
    def current(self) -> ConstraintSet: ...          # R-01..R-08(03 §3)
    def hash(self) -> str: ...                       # SHA256(约束集规范化序列化)
    def propose_change(self, diff: ConstraintDiff) -> GovernanceTicket: ...
    # 变更永不自动生效; 仅治理委员会双签后由运维在维护窗口手工应用
```

进化环、编排器在**每次运行开始**时拉取哈希并断言一致；
注册表变更历史本身也是审计对象。

---

## 10. 接口一览与实现优先级

| 接口 | 里程碑（01 §8） | 优先级 |
|---|---|---|
| InteractionEvent / Verifier | M0 | P0 |
| OutcomeLabeler / SkillLibrary(基础) | M1 | P0 |
| StudentSimulator / CalibrationReport | M2 | P1 |
| PolicyEvaluator / PromotionGate / EvolutionJob | M3 | P1 |
| TeacherGoldChannel | M4 | P1 |
| HardConstraintRegistry | M0（最小集 R-01/03/04） | P0 |

**跨接口总验收**：给定一段真实交互回放，从事件流出发可复现
「过滤→标注→结晶→评估→晋升」全链路，且每个中间产物带版本与谱系。
