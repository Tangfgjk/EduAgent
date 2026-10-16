# 02 · 核心规格：学习者心智状态 Schema

| | |
|---|---|
| 版本 | v0.1（2026-09-16） |
| 状态 | 设计定稿 |
| 上游 | 01 §3 神经-符号分工、01 §4 心智世界模型 |
| 下游消费方 | 03 动作语法（前置条件判定）、04 进化环（孪生实例化、增益标注）、07 UI（开放学习者模型渲染） |

---

## 1. 设计规则（Schema 级强制）

1. **不确定性显式**：所有估计值必须携带概率与置信区间（`p` + `ci95`），并标注估计来源模型与版本。
2. **能力是状态，不是特质**：不存在"固定能力等级"字段。`p_mastery` 等状态可升可降，Schema 中禁止任何被解释为"天生能力"的字段；`aptitude` 类字段一律不允许出现。
3. **可解释可追溯**：每个非空字段必须能通过 `evidence_refs` 回溯到交互事件（04 §1）。
4. **符号骨架，神经填充**：字段、取值域、约束由本 Schema（符号）定义；取值由神经感知器估计，写入即固化、带 `provenance`。
5. **默认对学生本人可见**（开放学习者模型）：除 `raw_signal_refs` 外所有字段可向学生渲染；对教师/家长的可见性走 L6 角色视图，另行裁剪。
6. **版本化与迁移**：`schema_version` 强制；破坏性变更必须提供迁移器，旧快照只读归档。

---

## 2. 心智状态快照 `MentalStateSnapshot`

> 学习者心智状态的完整快照。每次会话结束、反思关卡完成、外部测评录入时生成新版本；
> 历史版本只追加不覆盖（时序化存储，供孪生实例化与进化分析）。

```jsonc
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "rsi/mental-state-snapshot/1.0",
  "type": "object",
  "required": ["snapshot_id", "learner_id", "schema_version", "created_at",
               "knowledge_state", "competency_state", "affect_motivation",
               "autonomy_index", "provenance"],
  "properties": {
    "snapshot_id":     { "type": "string", "format": "uuid" },
    "learner_id":      { "type": "string" },
    "schema_version":  { "const": "1.0" },
    "created_at":      { "type": "string", "format": "date-time" },
    "ttl_seconds":     { "type": "integer", "minimum": 0, "default": 3600,
                         "description": "过期后必须重估，禁止拿陈旧状态做高 stakes 决策" },

    "knowledge_state": {
      "type": "object",
      "properties": {
        "kc_masteries": {
          "type": "array",
          "items": {
            "type": "object",
            "required": ["kc_id", "p_mastery", "ci95", "model"],
            "properties": {
              "kc_id":       { "type": "string", "pattern": "^[A-Z]+(\\.[A-Z0-9]+)+$",
                               "examples": ["MATH.G7.EQ.LINEAR", "ENG.READ.INFER"] },
              "p_mastery":   { "type": "number", "minimum": 0, "maximum": 1 },
              "ci95":        { "type": "array", "items": { "type": "number" }, "minItems": 2, "maxItems": 2 },
              "last_practiced_at": { "type": ["string", "null"], "format": "date-time" },
              "forgetting_curve_params": { "type": "object", "description": "间隔复习调度用" },
              "model":       { "type": "string", "examples": ["BKT", "DINA", "DKT-v3"] },
              "evidence_refs": { "type": "array", "items": { "type": "string", "format": "uuid" } }
            }
          }
        }
      }
    },

    "competency_state": {
      "type": "array",
      "description": "核心素养追踪（4C / 自主发展 / 学科素养），独立于知识点的另一条轴",
      "items": {
        "type": "object",
        "required": ["competency_id", "level", "assessor"],
        "properties": {
          "competency_id": { "type": "string", "examples": ["4C.CRITICAL_THINKING", "SELF.SELF_REGULATION", "CH.LITERATURE_AESTHETIC"] },
          "level":         { "type": "integer", "minimum": 1, "maximum": 5 },
          "level_rubric_ref": { "type": "string" },
          "assessor":      { "enum": ["agent_rubric", "teacher", "student_self", "peer"] },
          "evidence_refs": { "type": "array", "items": { "type": "string", "format": "uuid" } }
        }
      }
    },

    "misconception_hypotheses": {
      "type": "array",
      "description": "ToM 层：误区=竞争性假设，诊断=假设检验。永远保存假设分布而非单一结论",
      "items": {
        "type": "object",
        "required": ["space_id", "posterior"],
        "properties": {
          "space_id":  { "type": "string", "description": "误区本体空间引用, 如 MC.ALG.SIGN" },
          "posterior": {
            "type": "array",
            "items": { "type": "object",
                       "required": ["hypothesis_id", "p"],
                       "properties": {
                         "hypothesis_id": { "type": "string" },
                         "p":  { "type": "number", "minimum": 0, "maximum": 1 },
                         "description": { "type": "string", "description": "该误区的自然语言描述, 开放学习者模型直接展示" }
                       } }
          },
          "discriminating_actions_planned": { "type": "array", "items": { "type": "string" },
            "description": "计划用哪些诊断动作区分假设(动作类型引用, 见03)" }
        }
      }
    },

    "cognitive_load": {
      "type": "object",
      "required": ["estimate", "band"],
      "properties": {
        "estimate": { "type": "number", "minimum": 0, "maximum": 1 },
        "band":     { "enum": ["under", "optimal", "over"] },
        "signal_refs": { "type": "array", "items": { "type": "string" } }
      }
    },

    "affect_motivation": {
      "type": "object",
      "required": ["engagement", "frustration", "self_efficacy"],
      "properties": {
        "engagement":    { "type": "number", "minimum": 0, "maximum": 1 },
        "frustration":   { "type": "number", "minimum": 0, "maximum": 1 },
        "self_efficacy": { "type": "number", "minimum": 0, "maximum": 1,
                           "description": "对自己能否学会的信念, 影响 Hint 接受度与坚持性" },
        "flow_band":     { "enum": ["bored", "flow", "anxious"],
                           "description": "挑战-技能平衡, 联动 85% 规则调度" },
        "attribution_style": { "enum": ["effort_positive", "ability_fixed_negative", "mixed", "unknown"] },
        "trust_in_agent":    { "type": "number", "minimum": 0, "maximum": 1,
                               "description": "对智能体的信任度, 决定提示/纠正的措辞强度" },
        "signal_refs":   { "type": "array", "items": { "type": "string" } }
      }
    },

    "in_session_state": {
      "type": "object",
      "description": "快变字段(分钟级演化), 对齐 MWM 框架(arXiv:2607.27201)的心智分类; 与慢变量(归因风格/自我效能)分速存储",
      "properties": {
        "attention_focus": { "type": ["string", "null"],
                             "description": "当前注意力焦点(哪个KC/哪道题/哪个子目标), null=漂移" },
        "current_intention": { "type": ["string", "null"],
                               "description": "当下意图(如'想直接要答案'/'想再试一次'), 影响动作选择" },
        "last_action_interpretation": {
          "type": "object",
          "description": "学生对上一动作的解读(被解读的语义, 而非动作意图)——心智转移的输入",
          "properties": {
            "action_ref": { "type": "string" },
            "perceived": { "enum": ["encourage", "pressure", "condescending", "neutral", "unclear"] },
            "confidence": { "type": "number", "minimum": 0, "maximum": 1 }
          }
        }
      }
    },

    "goal_context": {
      "type": "object",
      "properties": {
        "active_goal_contract_id": { "type": ["string", "null"] },
        "ownership": { "enum": ["student", "negotiated", "externally_imposed"],
                       "description": "externally_imposed 仅允许来自课标/教师硬性要求, 智能体不允许制造此态(R-02)" },
        "progress_ratio": { "type": "number", "minimum": 0, "maximum": 1 }
      }
    },

    "strategy_profile": {
      "type": "object",
      "description": "『什么策略对我有效』的个体档案, 是迭代调整环节的输入",
      "properties": {
        "tried_strategies": {
          "type": "array",
          "items": { "type": "object",
                     "required": ["strategy_id", "outcome"],
                     "properties": {
                       "strategy_id": { "type": "string" },
                       "outcome": { "enum": ["effective", "neutral", "ineffective", "harmful"] },
                       "context_tag": { "type": "string", "description": "适用情境标签" },
                       "evidence_refs": { "type": "array", "items": { "type": "string" } }
                     } }
        },
        "preferred_hint_style": { "enum": ["socratic", "minimal", "worked_example", "visual", "unknown"] },
        "help_seeking_pattern": { "enum": ["too_early", "healthy", "reluctant", "avoidant"] }
      }
    },

    "metacognition": {
      "type": "object",
      "properties": {
        "calibration": {
          "type": "object",
          "description": "元认知校准: 预测自己做对 vs 实际做对",
          "properties": {
            "predicted_mean": { "type": "number" },
            "actual_mean":    { "type": "number" },
            "delta_trend":    { "enum": ["improving", "stable", "worsening"] }
          }
        },
        "reflection_completion_rate": { "type": "number", "minimum": 0, "maximum": 1 }
      }
    },

    "autonomy_index": {
      "type": "object",
      "required": ["composite"],
      "description": "原则P4的量化核心: 自主性指数。全平台北极星之一。可持续上升也可下降",
      "properties": {
        "composite": { "type": "number", "minimum": 0, "maximum": 1 },
        "subscores": {
          "type": "object",
          "properties": {
            "goal_self_set_ratio":     { "type": "number", "description": "目标自主设定占比" },
            "attempt_before_help_ratio": { "type": "number", "description": "求助前自主尝试占比" },
            "self_check_ratio":        { "type": "number", "description": "主动自查/自评占比" },
            "strategy_transfer_ratio": { "type": "number", "description": "跨情境迁移已学策略占比" }
          }
        },
        "measurement_note": { "type": "string", "description": "如引用 MSLQ 等量表版本" }
      }
    },

    "provenance": {
      "type": "object",
      "required": ["models", "data_window"],
      "properties": {
        "models": { "type": "array", "items": { "type": "string" },
                    "description": "参与估计的模型及版本, 如感知器/BKT/误区分类器" },
        "data_window": { "type": "string", "description": "估计所依据的交互事件窗口" },
        "audit_id": { "type": "string" }
      }
    }
  }
}
```

---

## 3. 关键子对象

### 3.1 学习契约 `GoalContract`（目标设置环节的固化物）

```jsonc
{
  "goal_contract_id": "uuid",
  "learner_id": "...",
  "goal_statement": {            // 学生自己的话
    "text": "两周内能独立解一元一次方程应用题",
    "authored_by": "student"     // R-02: 最终决定权归学生
  },
  "success_criteria": [          // 与学生协商的可验收标准
    { "criterion_id": "...", "kind": "post_test", "threshold": 0.8,
      "kc_refs": ["MATH.G7.EQ.LINEAR"] }
  ],
  "difficulty_band": { "min": 0.4, "max": 0.7 },   // 85%规则带, 软参数可微调
  "time_box": { "start": "...", "end": "..." },
  "resources": { "kc_refs": [], "prerequisite_check": "passed" },
  "external_deadline_refs": [        // 反向排程输入(Hyperknow式): 从截止日期倒排计划
    { "source": "lms | syllabus | exam_calendar", "title": "期中考试",
      "due_at": "2026-11-03", "kc_refs": ["MATH.G7.EQ.LINEAR"] }
  ],
  "negotiation_log_refs": ["uuid"],   // 协商过程留痕
  "plan_version_refs": ["uuid"],  // 展开为计划版本(06 PLAN_VERSION): 草案→学生确认→生效;
                                  // 调整产生新版本并留 change_reason, AI 不得静默改写(R-02 邻域)
  "status": { "enum": ["drafting", "active", "achieved", "revised", "abandoned"],
              "description": "revised/abandoned 必须带反思记录——双环学习的入口" }
}
```

### 3.2 策略档案条目与技能库的关系

`strategy_profile.tried_strategies[i].strategy_id` 引用的是**技能库中的技能实例**
（04 §4 `SkillEntry`）。学生档案是"这个学生对某技能的历史效果"，
技能库是"该技能的通用版本与回归测试"。二者通过 `strategy_id` + `skill_version` 关联。

### 3.3 存储与可见性

| 事项 | 约定 |
|---|---|
| 存储 | 快照只追加（append-only），时序版本；当前态 = 最新快照投影 |
| 学生可见 | 全部字段（除 `raw_signal_refs` 明细）——07 UI"我的镜子" |
| 教师可见 | 聚合视图 + 干预建议；默认不见情感/信任原始值，除非学生授权（P6） |
| 家长可见 | 进度与素养概览；心智明细不可见 |
| 进化层可见 | 脱敏后的派生视图（`learner_id` → 伪 ID），孪生实例化用 |
| 保留期限 | 心智明细默认 12 个月滚动；素养档案与契约长期保留 |

### 3.4 三层记忆与记忆图谱（v0.3，融合 DeepTutor）

学习者记忆按三层组织，层间引用可钻取；开放学习者模型（UI"我的镜子"）以
**记忆图谱**形态呈现该引用链——每条画像判断都可下钻到原始证据（TutorBench
实测可追溯记忆使个性化 +10.8%）：

| 层 | 内容 | 存储形态 |
|---|---|---|
| L1 原始事件 | InteractionEvent 流水账 | 事件流（append-only，04 §1） |
| L2 场景小结 | 按学习场景聚合的关键事实与结论 | **派生视图**：从 L1 聚合可再生，标注 `derived=true` 与生成时间；不可作为独立证据来源 |
| L3 画像层 | 心智快照 + 策略档案 + 素养档案 | 02 快照（append-only） |

规则：L3 的每个非空字段 `evidence_refs` 必须指向 L2 条目或 L1 事件；
**禁止 L3 引用 L3**（结论必须可最终下钻到原始事件）——"画像由证据形成，
不以单一总分代替具体表现"（学生方案）由此获得可执行的存储定义。

---

## 4. 校验与合规检查清单（实现时作为单测）

- [ ] 任何估计字段缺失 `ci95` / `provenance` 时 Schema 校验失败（规则1、4）
- [ ] 全库扫描不存在 `aptitude` / `iq` / `ability_level` 等特质字段（规则2，CI 正则检查）
- [ ] 每个非空 `*_refs` 能解析到真实交互事件（规则3）
- [ ] `goal_context.ownership == "externally_imposed"` 时必须存在课标/教师来源记录，否则判定违反 R-02
- [ ] Schema 变更 PR 必须包含迁移器与兼容性测试（规则6）
