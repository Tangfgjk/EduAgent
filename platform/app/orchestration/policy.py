"""内置教学策略 policy_v1（docs/03 §5 step3）：在硬约束下选候选动作。

策略只负责"选什么动作"；"何时值得介入"归 TriggerEngine（step0），
"动作是否被允许"归 ActionGovernor（step3b）——三层各司其职（docs/08 裁决②）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.actions import ActionEnvelope
from app.core.schema import QuestionItem, Verdict
from app.learning.perception import PerceptionCandidate
from app.orchestration.trigger import TriggerProposal

ANSWER_SEEK = re.compile(r"直接(告诉|给|说)|答案是什么|到底(等于|是)几|我不想(想|做)了|算了.{0,6}告诉我|^(?:我想要|我要|给我)?答案[！!。?？]?$|^给我答案$")
HELP_SEEK = re.compile(r"怎么(做|解|办)|提示|思路|卡住|不会|帮(帮)?我|从哪(里|儿)开始")


@dataclass
class TurnContext:
    """一回合内策略可见的全部上下文（由会话层装配）。"""

    snapshot: object
    ladder_pos: int
    session_type: str                      # explore | checkpoint
    item: QuestionItem | None              # 当前题
    next_item: QuestionItem | None         # 若答对将进入的题
    verdict_pending: Verdict | None        # 本回合刚产生、尚未反馈的验证结果
    perception: PerceptionCandidate
    proposals: list[TriggerProposal]
    student_text: str = ""
    path_context: dict | None = None
    review_context: dict | None = None
    learning_signals: dict | None = None


def _generic_hint(level: int) -> str:
    return ["先把你的想法说出来，说错了也没关系。",
            "回忆一下：先移项（变号），再合并同类项。",
            "我写第一步：两边同时做一次运算，让 x 的一边只剩一项——下一步你来。",
            "我们一起走一遍：移项变号 → 合并同类项 → 系数化为 1 → 代回验算。"][min(level, 3)]


# ---- 支架类型选择矩阵（docs/11 §3.6/§4；调研报告§四：触发状态 → 支架类型） ----
# 功能轴 scaffold_type 与强度轴 ladder_level 正交；此处按可观察状态做 v1 确定性映射。
SCAFFOLD_RULES: list[tuple[str, str]] = [
    (r"为什么|什么意思|概念|原理|本质", "conceptual"),
    (r"步骤|顺序|流程|怎么开始|下一步怎么", "procedural"),
    (r"验算|检查|漏了|忘了|确认", "metacognitive"),
    (r"依据|证据|凭什么|怎么证明", "epistemic"),
    (r"观点|谁对|两种说法|他们", "collaborative"),
    (r"不想做|没意思|走神|无聊", "participation"),
]


def choose_scaffold_type(student_text: str = "", proposals=(),
                         default: str = "strategic") -> str:
    """触发状态 → 支架类型（v1 规则版；v2 由 POMDP 策略模型接管，接口不变）。"""
    for pattern, scaffold_type in SCAFFOLD_RULES:
        if re.search(pattern, student_text or ""):
            return scaffold_type
    if any(p.rule_id == "TR_FRUSTRATION" for p in proposals):
        return "metacognitive"
    return default


class BuiltInPolicyV1:
    policy_version = "policy_v1"

    def choose(self, ctx: TurnContext) -> ActionEnvelope:
        session_id = ctx.snapshot.goal_context.active_goal_contract_id or ""
        help_asked = ctx.perception.current_intention == "ask_help" or \
            bool(HELP_SEEK.search(ctx.student_text or ""))
        want_answer = ctx.perception.current_intention == "want_answer" or \
            bool(ANSWER_SEEK.search(ctx.student_text or ""))

        # 0) 检查点会话：考试流程优先级最高（R-09 隔离由规则引擎强制）
        if ctx.session_type == "checkpoint":
            if ctx.verdict_pending is not None:
                return self._feedback_for(ctx)
            if want_answer:
                return ActionEnvelope.explain(
                    session_id=session_id,
                    kc_id=ctx.item.kc_id if ctx.item else "MATH.G7.EQ.SOLVE",
                    mode="worked_full", text="（完整解题过程）", target="exam_item",
                )
            if help_asked:
                return ActionEnvelope.explain(
                    session_id=session_id,
                    kc_id=ctx.item.kc_id if ctx.item else "MATH.G7.EQ.SOLVE",
                    mode="rule_explanation",
                    text="阶段测试中我只讲规则不提供答案；独立完成，提交后统一查看解析。",
                    target="exam_item", rule_explanation=True,
                )
            if ctx.item:
                return ActionEnvelope.question(
                    session_id=session_id, kc_id=ctx.item.kc_id, stem=ctx.item.stem,
                    item_id=ctx.item.item_id, target="exam_item",
                )
            return ActionEnvelope.feedback(
                session_id=session_id, kc_id="MATH.G7.EQ.SOLVE", kind="process",
                text="本阶段测试完成。我们回探究模式看看错题背后的策略。",
            )

        # 1) 刚产生验证结果 → 先反馈（归因措辞在 FEEDBACK 模板内保证，R-04 兜底改写）
        if ctx.verdict_pending is not None:
            return self._feedback_for(ctx)

        # Learning review/diagnostic signals can suggest an action, but do not
        # sign or mutate a learning plan. The Governor still decides whether
        # the proposed task is allowed.
        if (not help_asked and not want_answer and ctx.review_context
                and ctx.review_context.get("due") and ctx.next_item):
            item = ctx.next_item
            return ActionEnvelope.task(
                session_id=session_id, kc_id=item.kc_id, stem=item.stem,
                item_id=item.item_id, difficulty=item.difficulty, kind="practice",
            )

        # 2) 学生想要答案 → 给 EXPLAIN(worked_full) 候选（预期被 R-01 拦截，会话层转提示）
        if want_answer:
            return ActionEnvelope.explain(
                session_id=session_id,
                kc_id=ctx.item.kc_id if ctx.item else "MATH.G7.EQ.SOLVE",
                mode="worked_full", text="（完整解题过程）", target="practice",
            )

        # 3) 挫败触发且学生未求助 → 支持性反馈（R-03 的前置安抚）
        frustrated = any(p.rule_id == "TR_FRUSTRATION" for p in ctx.proposals) or \
            ctx.snapshot.affect_motivation.frustration > 0.75
        if frustrated and ctx.item and not help_asked:
            return ActionEnvelope.feedback(
                session_id=session_id, kc_id=ctx.item.kc_id, kind="process",
                text="这道题确实有点绕，卡住很正常。我们换个角度：把题目里的数量关系用自己的话讲一遍，我听着。",
            )

        # 4) 求助或连续卡壳 → 阶梯提示（学生求助 = 非主动提示，不占 R-07 主动预算）
        stuck = any(p.rule_id == "TR_STUCK" for p in ctx.proposals)
        if (help_asked or stuck) and ctx.item:
            level = ctx.ladder_pos
            text = ctx.item.hint_text(level) or _generic_hint(level)
            return ActionEnvelope.hint(
                session_id=session_id, kc_id=ctx.item.kc_id, level=level,
                form=("nudge" if level == 0 else "directive" if level == 1 else
                      "worked_partial" if level == 2 else "worked_full"),
                text=text, proactive=not help_asked,
                scaffold_type=choose_scaffold_type(ctx.student_text, ctx.proposals),
            )

        # An active item remains the student's current task until a verified
        # answer completes it. Free-form chat must not silently issue another.
        if ctx.item:
            return ActionEnvelope.feedback(
                session_id=session_id, kc_id=ctx.item.kc_id, kind="process",
                text=f"我们还在这道题：{ctx.item.stem}。先尝试作答，或者请求提示。",
            )

        if any(p.rule_id == "TR_UNKNOWN_EVIDENCE" for p in ctx.proposals) and ctx.next_item:
            item = ctx.next_item
            candidate = ActionEnvelope.task(session_id=session_id, kc_id=item.kc_id, stem=item.stem,
                item_id=item.item_id, difficulty=item.difficulty, kind="practice")
            candidate.params["assessment_kind"] = "diagnostic"
            return candidate

        # 5) 默认 → 布置下一题（TASK 需接地 R-06）
        if ctx.next_item:
            item = ctx.next_item
            kind = "inquiry" if item.pattern in ("inquiry", "pbl") else "practice"
            return ActionEnvelope.task(
                session_id=session_id, kc_id=item.kc_id, stem=item.stem,
                item_id=item.item_id, difficulty=item.difficulty, kind=kind,
            )

        # 6) 无题可出 → 邀请反思
        return ActionEnvelope.feedback(
            session_id=session_id, kc_id=ctx.item.kc_id if ctx.item else "MATH.G7.EQ.SOLVE",
            kind="process", text="今天的题先到这里——说说你今天用了哪个策略、哪里最卡？",
        )

    def _feedback_for(self, ctx: TurnContext) -> ActionEnvelope:
        session_id = ctx.snapshot.goal_context.active_goal_contract_id or ""
        v = ctx.verdict_pending
        item = ctx.item or ctx.next_item
        kc = item.kc_id if item else "MATH.G7.EQ.SOLVE"
        if v is not None and v.status == "passed":
            text = "做对了！这一路你是靠自己先移项、再验算走过来的——这个流程记住，后面复杂的题也一样用。"
        elif v is not None and v.status == "partial":
            text = "接近了！结果差一点，把最后一步再验算一遍，看看是计算还是抄写出了偏差。"
        elif v is not None and v.status == "failed":
            base = "这一步没走通，不着急。对照你的过程看：题目要求的等量关系找到了吗？"
            nudge = (item.hint_text(0) if item else None) or _generic_hint(0)
            text = f"{base}给个方向：{nudge}"
        else:
            text = "这道题暂时没法自动判分，说说你的思路，我们一起看。"
        kind = "verification" if v is not None and v.status in ("passed", "failed") else "process"
        action = ActionEnvelope.feedback(session_id=session_id, kc_id=kc, kind=kind, text=text)
        if v is not None and v.status == "failed":
            action.params["assistance_hint_level"] = 1
        return action
