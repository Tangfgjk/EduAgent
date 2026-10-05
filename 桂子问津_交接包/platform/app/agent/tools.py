"""学习工具集（ZCode 的工具箱对应物）：11 个工具，每个映射动作类型并过 ActionGovernor 门禁。

工具是无状态函数集合，状态（快照/阶梯/预算/验证器）由宿主 LearningAgent 注入。
被拒（denied）的工具调用由循环层转为教学化替代并落审计——与 v1 会话层同一纪律。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.agent.workspace import LearningWorkspace, default_plan_md
from app.core.actions import ActionEnvelope, ActionType
from app.core.rules import ActionGovernor, GovernorContext
from app.core.schema import (
    GoalContract, MentalStateSnapshot, QuestionItem, Verdict, new_id,
)
from app.learning.tracer import BKTTracer
from app.learning.verifier import verify_item
from app.llm.client import BaseLLM, LLMError
from app.storage.db import Store


@dataclass
class ToolResult:
    status: str = "ok"           # ok | denied | fail
    detail: str = ""
    payload: dict = field(default_factory=dict)


class LearningTools:
    """工具门面： LearningAgent 持有一个实例并注入共享状态。"""

    def __init__(self, store: Store, llm: BaseLLM, governor: ActionGovernor,
                 snapshot: MentalStateSnapshot, tracer: BKTTracer,
                 workspace: LearningWorkspace, bank: list[QuestionItem]):
        self.store = store
        self.llm = llm
        self.governor = governor
        self.snapshot = snapshot          # 与 agent 共享同一引用
        self.tracer = tracer
        self.workspace = workspace
        self.bank = bank                  # 运行时题库（quiz.generate 可追加）
        self.hints_used = 0
        self.hint_budget = 4
        self.frustration_streak = 0
        self.wrong_streak = 0
        self.session_id = new_id()
        self.current_difficulty = 0.4
        self.checkpoint_mode = False
        self.ladder_pos = 0

    # ---------- 门禁上下文 ----------

    def gctx(self) -> GovernorContext:
        return GovernorContext(
            snapshot=self.snapshot, ladder_pos=self.ladder_pos,
            hints_used=self.hints_used, hint_budget=self.hint_budget,
            frustration_streak=self.frustration_streak,
            artifact_present=False, checkpoint_mode=self.checkpoint_mode,
            current_difficulty=self.current_difficulty,
        )

    def _gated(self, env: ActionEnvelope):
        """构建信封 → 过门禁 → (ToolResult, envelope)。denied 时审计落库。"""
        outcome = self.governor.decide(env, self.gctx())
        if outcome.decision == "deny":
            self.store.append_event(_audit_event(self.session_id, self.snapshot,
                                                 outcome.rule_id or "R", outcome.reason,
                                                 env.action_id))
            return ToolResult("denied", outcome.reason, {"rule": outcome.rule_id}), env
        if outcome.decision == "rewrite":
            env = outcome.envelope
            self.store.append_event(_audit_event(self.session_id, self.snapshot,
                                                 outcome.rule_id or "R-04",
                                                 outcome.reason, env.action_id))
        return ToolResult("ok", "通过门禁", {"action_id": env.action_id}), env

    # ---------- 1. learner_model.read ----------

    def learner_model_read(self) -> ToolResult:
        masteries = {m.kc_id: m.p_mastery for m in self.snapshot.knowledge_state.kc_masteries}
        if masteries:
            detail = "；".join(f"{k}={v:.2f}" for k, v in masteries.items())
        else:
            detail = "新学习者，暂无掌握记录"
        return ToolResult(detail=detail, payload={"mastery": masteries,
                                                  "autonomy": self.snapshot.autonomy_index.composite})

    # ---------- 2. bank.search ----------

    def bank_search(self, max_difficulty: float = 0.45, kc_prefix: str = "MATH",
                    count: int = 3, exclude: list[str] | None = None) -> ToolResult:
        exclude = set(exclude or [])
        picked = [q for q in sorted(self.bank, key=lambda q: q.difficulty)
                  if q.difficulty <= max_difficulty and q.kc_id.startswith(kc_prefix)
                  and q.item_id not in exclude][:count]
        return ToolResult(detail=f"取得 {len(picked)} 道",
                          payload={"items": [q.model_dump() for q in picked]})

    # ---------- 3. quiz.generate（GLM 生成，接地 KC，失败回落题库） ----------

    def quiz_generate(self, kc_id: str, difficulty: float = 0.5, topic_note: str = "") -> ToolResult:
        try:
            raw = self.llm.complete([{
                "role": "system",
                "content": "出题器。输出 JSON：{\"stem\":\"题干\",\"value\":\"答案数值\"}，"
                           "只出一道一元一次方程/应用题，答案为整数。"},
                {"role": "user", "content": f"知识点：{kc_id}；难度 {difficulty}；要求：{topic_note}"},
            ], temperature=0.4)
            from app.llm.client import extract_json

            data = json.loads(extract_json(raw))
            item = QuestionItem(
                item_id=f"GEN-{new_id()[:6]}", kc_id=kc_id, difficulty=difficulty,
                stem=str(data["stem"]), answer={"var": "x", "value": str(data["value"])},
                hints=[], misconception_links=[],
            )
            self.bank.append(item)     # 接地生成：必须挂 KC（R-06 语义）
            return ToolResult(detail=f"生成：{item.stem}", payload={"item": item.model_dump()})
        except (LLMError, KeyError, json.JSONDecodeError):
            fallback = self.bank_search(max_difficulty=difficulty, kc_prefix=kc_id.split(".")[0],
                                        count=1).payload.get("items")
            if fallback:
                return ToolResult(detail=f"生成失败，回落题库：{fallback[0]['stem']}",
                                  payload={"item": fallback[0]})
            return ToolResult("fail", "生成失败且题库无可用题")

    # ---------- 4. task.issue（TASK：R-03/R-06 门禁） ----------

    def task_issue(self, item: QuestionItem) -> ToolResult:
        env = ActionEnvelope.task(self.session_id, item.kc_id, item.stem,
                                  item_id=item.item_id, difficulty=item.difficulty,
                                  kind="inquiry" if item.pattern in ("inquiry", "pbl") else "practice")
        result, _ = self._gated(env)
        if result.status == "ok":
            self.current_difficulty = item.difficulty
        return result

    # ---------- 5. hint.ladder（HINT：R-07 主动预算） ----------

    def hint_ladder(self, item: QuestionItem, proactive: bool = True,
                    text: str | None = None) -> ToolResult:
        level = self.ladder_pos
        text = text or item.hint_text(level) or "先把你的想法说出来。"
        form = ("nudge" if level == 0 else "directive" if level == 1 else
                "worked_partial" if level == 2 else "worked_full")
        env = ActionEnvelope.hint(self.session_id, item.kc_id, level, form, text,
                                  proactive=proactive)
        result, env = self._gated(env)
        if result.status == "ok":
            if proactive:
                self.hints_used += 1
            result.detail = text
            result.payload = {"level": level, "form": form, "text": text}
        return result

    # ---------- 6. explain.gated（EXPLAIN：R-01 门控；被拒回落阶梯提示） ----------

    def explain_gated(self, item: QuestionItem, mode: str = "worked_full",
                      text: str = "", target: str = "practice") -> ToolResult:
        env = ActionEnvelope.explain(self.session_id, item.kc_id, mode, text=text, target=target)
        result, _ = self._gated(env)
        if result.status == "denied" and result.payload.get("rule") == "R-01":
            fallback = self.hint_ladder(item, proactive=False)
            fallback.detail = f"R-01 拦截后转阶梯提示：{fallback.detail}"
            return fallback
        if result.status == "ok":
            result.detail = text or mode
        return result

    # ---------- 7. verify.answer（验证 + BKT + 误区 + 阶梯/挫败联动） ----------

    def verify_answer(self, item: QuestionItem, answer: str) -> ToolResult:
        verdict = verify_item(answer, item, llm=self.llm)
        if not verdict.artifact_id:
            verdict.artifact_id = new_id()
        self.store.save_verdict(verdict, self.session_id, item.item_id)
        if verdict.status == "unverifiable":
            return ToolResult("fail", "无法自动判分，说说你的思路",
                              payload={"verdict": verdict.model_dump()})
        correct = verdict.status == "passed"
        self.tracer.update(self.snapshot, item.kc_id, correct)
        if correct:
            self.wrong_streak = 0
            self.frustration_streak = 0
        else:
            self.wrong_streak += 1
            self.frustration_streak += 1
            self.ladder_pos = min(3, self.ladder_pos + 1)   # 仍卡壳 → 阶梯升级
        return ToolResult(
            "ok" if correct else "fail",
            verdict.explainability,
            payload={"verdict": verdict.model_dump(), "correct": correct},
        )

    # ---------- 8/9. workspace.read / write ----------

    def workspace_read(self, name: str) -> ToolResult:
        try:
            content = self.workspace.read(name)
        except Exception as err:
            return ToolResult("fail", str(err))
        return ToolResult(detail=f"{len(content)} 字符", payload={"content": content})

    def workspace_write(self, name: str, content: str) -> ToolResult:
        try:
            path = self.workspace.write(name, content)
        except Exception as err:
            return ToolResult("fail", str(err))
        return ToolResult(detail=str(path), payload={"path": path})

    # ---------- 10. plan.update（写计划.md；关口确认由循环层负责） ----------

    def plan_update(self, goal: str, daily_count: int = 2, deadline_note: str = "",
                    content_md: str | None = None) -> ToolResult:
        content = content_md or default_plan_md(goal, daily_count, deadline_note)
        path = self.workspace.write_plan(content)
        version = new_id()[:8]
        self.store.save_plan_update(version, content)
        return ToolResult(detail=f"计划.md 已写入（版本 {version}）",
                          payload={"path": path, "version": version, "content": content})

    # ---------- 11. escalate.teacher（R-05/红线 → 暂停转人工） ----------

    def escalate_teacher(self, reason: str) -> ToolResult:
        self.store.append_event(_audit_event(self.session_id, self.snapshot,
                                             "R-05", f"转人工：{reason}", None))
        return ToolResult(detail=f"已转人工：{reason}",
                          payload={"escalated": True, "reason": reason})


def _audit_event(session_id: str, snapshot: MentalStateSnapshot, rule_id: str,
                 reason: str, action_id: str | None):
    from app.core.schema import ActorRef, InteractionEvent, Observation

    return InteractionEvent(
        learner_pseudo_id=snapshot.learner_id, session_id=session_id,
        actor=ActorRef(kind="companion"), action_ref=action_id,
        observation=Observation(kind="utterance", text=reason,
                                payload={"audit": True, "denial": {"rule": rule_id,
                                                                   "reason": reason}}),
    )


# ---------- 红线检测（R-05 的 agent 侧入口：目标文本命中即升级） ----------

def goal_hits_red_line(goal_text: str) -> str | None:
    from app.core.rules import RED_LINE_PATTERNS

    for pattern in RED_LINE_PATTERNS:
        if re.search(pattern, goal_text or ""):
            return pattern
    return None
