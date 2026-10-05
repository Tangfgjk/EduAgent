"""docs/03 §3：义务逻辑硬约束 R-01…R-10 与规则引擎 ActionGovernor。

设计要点：
- 每条规则 = 纯函数 check(env, ctx) → None(通过) | RuleOutcome(rewrite/deny)；
- 裁决三值：allow / rewrite / deny，deny 与 rewrite 前后由会话层落审计事件（进化负样本）；
- 硬约束注册表哈希锁定（docs/04 §9）：进化环每次运行断言哈希一致，本模块是唯一权威。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from app.core.actions import ActionEnvelope, ActionType
from app.core.schema import MentalStateSnapshot

# ---------- 硬约束注册表（docs/03 §3；v0.2 增 R-09；v0.4 增 R-10） ----------

HARD_RULES: dict[str, str] = {
    "R-01": "不许提前给答案：EXPLAIN(worked_full) 需阶梯 L3 耗尽或已有学生 artifact 对照",
    "R-02": "目标归学生所有：GOAL_NEGOTIATE 终稿 authored_by 必须为 student",
    "R-03": "挫败保护：frustration 连续超阈 k 轮时禁止上调任务难度",
    "R-04": "归因纪律：FEEDBACK 禁止能力归因措辞，自动改写为策略归因",
    "R-05": "价值观红线：命中红线词表即拒绝并转人工复审",
    "R-06": "接地约束：EXPLAIN/TASK 必须绑定知识图谱节点",
    "R-07": "脚手架预算递减：会话内主动提示次数 ≤ f(autonomy_index)",
    "R-08": "数据最小化：采集按会话目的最小化（设计层，v1 于存储层执行）",
    "R-09": "考试模式隔离：TestAttempt 进行中禁止答案型帮助，仅允许规则说明",
    "R-10": "过程所有权：探究/工程任务不代做完整方案，须学生已有部分设计产物（docs/10 §5.2）",
}

THETA_HIGH = 0.75          # R-03 挫败阈值（软参数，v1 固定）
K_FRUSTRATION = 3          # R-03 连续轮数

# R-10 "完整方案"判定（docs/10 §5.2）：params.full_design=True 即声明产出完整设计
DESIGN_KINDS = {"experiment", "engineering", "inquiry_design", "research_plan"}

# R-05 红线词表 v1 极简版（正式版应进治理层 L6 并由委员会审定）
RED_LINE_PATTERNS = [r"代(写|考)", r"替我(写|做)作业", r"帮我作弊", r"绕过监考", r"攻击(学校|老师)"]

# R-04 能力归因词表 → 改写模板
ABILITY_PATTERNS = re.compile(r"你(真|很|太)?(聪明|笨|蠢|差|不行|没天赋|有天赋)|你脑子|天生就会")
STRATEGY_REWRITE = "这个策略用得好；如果换一种顺序再验一遍就更稳了"


def registry_hash() -> str:
    canonical = json.dumps(HARD_RULES, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass
class GovernorContext:
    snapshot: MentalStateSnapshot
    ladder_pos: int
    hints_used: int
    hint_budget: int
    frustration_streak: int
    artifact_present: bool = False
    checkpoint_mode: bool = False
    current_difficulty: float = 0.5


@dataclass
class RuleOutcome:
    rule_id: str | None
    decision: Literal["allow", "rewrite", "deny"]
    reason: str
    envelope: ActionEnvelope


class ActionGovernor:
    """裁决顺序：R-09（最高利害）→ R-01 → R-10 → R-03 → R-07 → R-02 → R-05 → R-06 → R-04。"""

    def decide(self, env: ActionEnvelope, ctx: GovernorContext) -> RuleOutcome:
        for check in (
            self._r09_exam_isolation, self._r01_no_early_answer,
            self._r10_process_ownership,
            self._r03_frustration_guard, self._r07_scaffold_budget,
            self._r02_goal_ownership, self._r05_red_line,
            self._r06_grounding, self._r04_attribution,
        ):
            outcome = check(env, ctx)
            if outcome is not None:
                return outcome
        return RuleOutcome(None, "allow", "通过全部硬约束", env)

    # ---- R-09 考试模式隔离 ----
    def _r09_exam_isolation(self, env, ctx) -> RuleOutcome | None:
        if not ctx.checkpoint_mode:
            return None
        # QUESTION 下发考题本身不算帮助；只有 HINT/EXPLAIN 构成答案型帮助
        answer_help = env.type in (ActionType.HINT, ActionType.EXPLAIN) \
            and env.params.get("target") == "exam_item"
        rule_only = env.type == ActionType.EXPLAIN and env.params.get("mode") == "rule_explanation"
        if answer_help and not rule_only:
            return RuleOutcome("R-09", "deny",
                               "考试模式：提交前不提供答案型帮助，提交后统一解析", env)
        return None

    # ---- R-01 不许提前给答案 ----
    def _r01_no_early_answer(self, env, ctx) -> RuleOutcome | None:
        if env.type != ActionType.EXPLAIN:
            return None
        is_full = env.params.get("mode") == "worked_full" or env.params.get("form") == "worked_full"
        if not is_full:
            return None
        allowed = ctx.ladder_pos >= 3 or ctx.artifact_present
        if not allowed:
            return RuleOutcome(
                "R-01", "deny",
                f"提示阶梯未到 L3（当前 {ctx.ladder_pos}）且无学生作品可对照——先给出你的想法，我们再对照",
                env,
            )
        return None

    # ---- R-10 过程所有权（docs/10 §5.2；调研报告案例F：探究保护） ----
    def _r10_process_ownership(self, env, ctx) -> RuleOutcome | None:
        if not env.params.get("full_design"):
            return None
        if env.type not in (ActionType.EXPLAIN, ActionType.ENV_OP):
            return None
        if env.params.get("design_kind") not in DESIGN_KINDS:
            return None
        if ctx.checkpoint_mode:
            return RuleOutcome("R-10", "deny",
                               "考试模式：不提供任何方案性内容（R-09 叠加 R-10）", env)
        if not ctx.artifact_present:
            return RuleOutcome(
                "R-10", "deny",
                "完整实验/工程方案先不给你（R-10 过程所有权）：把你的部分写出来，我们对照补全",
                env,
            )
        return None

    # ---- R-03 挫败保护 ----
    def _r03_frustration_guard(self, env, ctx) -> RuleOutcome | None:
        if env.type != ActionType.TASK:
            return None
        new_difficulty = float(env.params.get("difficulty", ctx.current_difficulty))
        if (ctx.snapshot.affect_motivation.frustration > THETA_HIGH
                and ctx.frustration_streak >= K_FRUSTRATION
                and new_difficulty > ctx.current_difficulty):
            return RuleOutcome("R-03", "deny",
                               "连续挫败中：禁止上调任务难度，先给支持性反馈", env)
        return None

    # ---- R-07 脚手架预算递减 ----
    def _r07_scaffold_budget(self, env, ctx) -> RuleOutcome | None:
        if env.type != ActionType.HINT or not env.params.get("proactive"):
            return None
        if ctx.hints_used >= ctx.hint_budget:
            return RuleOutcome("R-07", "deny",
                               f"本会话主动提示预算已用尽（{ctx.hints_used}/{ctx.hint_budget}）——先自己再试一步", env)
        return None

    # ---- R-02 目标归学生所有 ----
    def _r02_goal_ownership(self, env, ctx) -> RuleOutcome | None:
        if env.type != ActionType.GOAL_NEGOTIATE:
            return None
        if env.params.get("finalize") and env.params.get("authored_by") != "student":
            return RuleOutcome("R-02", "deny",
                               "学习契约的最终决定权在学生：智能体只能提议，不能代为签署", env)
        return None

    # ---- R-05 价值观红线 ----
    def _r05_red_line(self, env, ctx) -> RuleOutcome | None:
        texts = [str(env.params.get("text", "")), str(env.params.get("stem", ""))]
        for text in texts:
            for pattern in RED_LINE_PATTERNS:
                if re.search(pattern, text):
                    return RuleOutcome("R-05", "deny",
                                       "命中价值观红线词表，转人工复审队列（R-05）", env)
        return None

    # ---- R-06 接地约束 ----
    def _r06_grounding(self, env, ctx) -> RuleOutcome | None:
        if env.type not in (ActionType.EXPLAIN, ActionType.TASK):
            return None
        grounded = env.policy_provenance.get("grounded_to_kg") or []
        if not grounded:
            return RuleOutcome("R-06", "deny",
                               "讲解/任务内容未绑定知识图谱节点（接地缺失）", env)
        return None

    # ---- R-04 归因纪律（可改写） ----
    def _r04_attribution(self, env, ctx) -> RuleOutcome | None:
        if env.type != ActionType.FEEDBACK:
            return None
        text = str(env.params.get("text", ""))
        if ABILITY_PATTERNS.search(text):
            env.params["text"] = STRATEGY_REWRITE
            return RuleOutcome("R-04", "rewrite",
                               "检测到能力归因措辞，已改写为策略归因", env)
        return None
