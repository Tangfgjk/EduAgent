"""LearningAgent：单元级自主循环（ZCode 式）。

诊断 → 计划 → 执行 → 反思，一次目标自主跑完；三关口（计划签署/反思/红线升级）
以 Ask 暂停等人，学生输入后从断点继续（生成器 send 协议，天然可重入）。
混合驱动：阶段推进是硬状态机（防跑飞），阶段内工具选择由 GLM 驱动（FakeLLM 给
默认值即可全流程测试）；每个工具调用过 ActionGovernor 门禁。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from app.agent.gates import (
    GATE_ESCALATION, GATE_PLAN_CONFIRM, GATE_REFLECTION,
    is_confirm, is_skip, parse_plan_revision, parse_reflection,
)
from app.agent.tools import LearningTools, goal_hits_red_line
from app.agent.transcript import Ask, EventKind, TranscriptEvent
from app.agent.workspace import LearningWorkspace, default_plan_md
from app.core.machines import hint_budget, start_level
from app.core.rules import ActionGovernor
from app.core.schema import (
    Calibration, GoalContext, MentalStateSnapshot, QuestionItem,
    StrategyProfile, TriedStrategy, new_id,
)
from app.learning.perception import seed_misconception_space
from app.learning.tracer import BKTTracer
from app.llm.client import BaseLLM, LLMError
from app.orchestration.session import (
    MISCONCEPTION_HYPOTHESES, load_bank,
)
from app.storage.db import Store

MAX_STEPS = 24   # 单元内工具调用步数预算（软参数）


class Phase(str, Enum):
    GOAL = "GOAL"
    DIAGNOSE = "DIAGNOSE"
    PLAN = "PLAN"
    EXECUTE = "EXECUTE"
    REFLECT = "REFLECT"
    DONE = "DONE"


# ---------- LLM 结构化输出模型（GLM 驱动工具选择的接口） ----------

class UnitIntent(BaseModel):
    """开场意图规划：诊断题量、每日练习量。"""
    diag_count: int = 2
    daily_count: int = 2
    note: str = ""


class ToolChoice(BaseModel):
    """答错后的下一步工具选择。"""
    tool: str = "hint.ladder"        # hint.ladder | explain.gated
    note: str = ""


class PlanDraft(BaseModel):
    """计划正文润色（结构不变，仅文字）。"""
    content_md: str = ""


@dataclass
class AgentTurn:
    events: list[TranscriptEvent] = field(default_factory=list)
    ask: Ask | None = None
    done: bool = False
    summary: str = ""


class LearningAgent:
    def __init__(self, store: Store, llm: BaseLLM, learner_id: str,
                 contract=None, bank=None, workspace_root: str | Path = "workspace"):
        self.store = store
        self.llm = llm
        self.learner_id = learner_id
        self.contract = contract
        base = store.latest_snapshot(learner_id)
        self.snapshot = (base.model_copy(deep=True) if base
                         else MentalStateSnapshot(learner_id=learner_id))
        if contract is not None:
            self.snapshot.goal_context = GoalContext(
                active_goal_contract_id=contract.goal_contract_id, ownership="student")
        self.workspace = LearningWorkspace(workspace_root, learner_id)
        self.tracer = BKTTracer()
        self.governor = ActionGovernor()
        self.tools = LearningTools(store, llm, self.governor, self.snapshot,
                                   self.tracer, self.workspace,
                                   bank=bank if bank is not None else load_bank())
        aut = self.snapshot.autonomy_index.composite
        self.tools.hint_budget = hint_budget(aut)
        self.tools.ladder_pos = start_level(aut)
        self.mastery_start = {m.kc_id: m.p_mastery
                              for m in self.snapshot.knowledge_state.kc_masteries}
        self.verdicts: list[dict] = []       # {status, item_id}
        self.mistakes = 0
        self.steps = 0
        self.phase = Phase.GOAL
        self.used_item_ids: list[str] = []
        self.polish = type(llm).__name__ == "OpenAICompatClient"
        self._gen = None
        store.ensure_learner(learner_id)
        store.save_session(self.tools.session_id, learner_id,
                           contract.goal_contract_id if contract else None, "agent-unit")

    # ---------- 驱动协议 ----------

    def start(self, goal: str) -> AgentTurn:
        self._gen = self._run(goal)
        return self._pull(None)

    def send(self, text: str) -> AgentTurn:
        if self._gen is None:
            raise RuntimeError("先调用 start(goal)")
        return self._pull(text)

    def _pull(self, value: str | None) -> AgentTurn:
        events: list[TranscriptEvent] = []
        while True:
            try:
                ev = self._gen.send(value)
            except StopIteration:
                return AgentTurn(events=events, done=True)
            value = None
            events.append(ev)
            if isinstance(ev, Ask):
                return AgentTurn(events=events, ask=ev)

    # ---------- 转录辅助 ----------

    def _msg(self, text: str) -> TranscriptEvent:
        return TranscriptEvent(EventKind.message, self._polish_plain(text))

    def _tool_events(self, name: str, result, params_desc: str = ""):
        """一进一出：tool_call + tool_result；计入步数预算。"""
        yield TranscriptEvent(EventKind.tool_call, name + (f" {params_desc}" if params_desc else ""))
        self.steps += 1
        yield TranscriptEvent(EventKind.tool_result, name,
                              detail=result.detail, status=result.status,
                              payload=result.payload)

    def _polish_plain(self, text: str) -> str:
        """message 级话术润色：仅真机 LLM；任何失败或跑偏回落原文。"""
        if not self.polish:
            return text
        try:
            from app.llm.prompts import BASE_PEDAGOGY

            out = self.llm.complete([
                {"role": "system", "content": BASE_PEDAGOGY +
                 "\n输出纪律：你是润色过滤器——只直接输出改写后的那句话本身；"
                 "禁止解释、禁止反问、禁止提及润色这件事。"},
                {"role": "user", "content": f"润色：{text}"},
            ], temperature=0.3)
            out = out.strip()
            # 跑偏防护：润色不得引入原句没有的疑问，也不得提及"润色"
            if (not out or len(out) < 4 or "润色" in out
                    or ("？" in out and "？" not in text)):
                return text
            return out
        except LLMError:
            return text

    # ---------- 主循环 ----------

    def _run(self, goal: str):
        # 红线升级（关口③）
        if hit := goal_hits_red_line(goal):
            self.tools.escalate_teacher(f"目标命中红线 {hit}")
            self.phase = Phase.DONE
            yield Ask(prompt=f"这个目标我不接：命中红线（{hit}），已记录并转人工复审。"
                             f"请换一个学习目标重新开始。", gate=GATE_ESCALATION)
            return

        yield self._msg(f"收到目标：{goal}。先诊断，再定计划，然后我们开练。")
        intent = self._plan_intent(goal)
        yield from self._phase_diagnose(intent)
        yield from self._phase_plan(goal, intent)
        yield from self._phase_execute(intent)
        yield from self._phase_reflect()
        self.phase = Phase.DONE
        yield TranscriptEvent(EventKind.summary, self._summary_text(), payload={
            "verdicts": self.verdicts, "mistakes": self.mistakes, "steps": self.steps})

    def _plan_intent(self, goal: str) -> UnitIntent:
        try:
            intent = self.llm.complete_json([{
                "role": "system",
                "content": "学习单元规划器。输出 JSON：{\"diag_count\":1到3的诊断题数,"
                           "\"daily_count\":1到5的每日练习量,\"note\":\"一句话安排\"}。"
                           "若目标含考试期限则宁紧勿松。"},
                {"role": "user", "content": goal},
            ], UnitIntent, temperature=0.2)
        except LLMError:
            intent = UnitIntent()
        intent.diag_count = max(1, min(3, intent.diag_count))
        intent.daily_count = max(1, min(5, intent.daily_count))
        return intent

    # ---------- 阶段①：诊断 ----------

    def _phase_diagnose(self, intent: UnitIntent):
        self.phase = Phase.DIAGNOSE
        result = self.tools.learner_model_read()
        yield from self._tool_events("learner_model.read", result)

        search = self.tools.bank_search(max_difficulty=0.45, count=intent.diag_count,
                                        exclude=self.used_item_ids)
        yield from self._tool_events("bank.search", search,
                                     f"难度≤0.45 × {intent.diag_count}")
        items = [QuestionItem.model_validate(p) for p in search.payload.get("items", [])]

        for i, item in enumerate(items, 1):
            self._seed_spaces(item)
            issued = self.tools.task_issue(item)
            yield from self._tool_events("task.issue", issued, item.item_id)
            if issued.status == "denied":
                continue
            self.used_item_ids.append(item.item_id)
            answer = yield Ask(prompt=f"（诊断 {i}/{len(items)}）请作答：{item.stem}")
            if is_skip(answer):
                yield self._msg("跳过，下一题。")
                continue
            yield from self._answer_flow(item, answer, diagnose=True)

    # ---------- 阶段②：计划（关口①） ----------

    def _phase_plan(self, goal: str, intent: UnitIntent):
        self.phase = Phase.PLAN
        deadline_note = self._deadline_note(goal)
        content = default_plan_md(goal, intent.daily_count, deadline_note)
        try:
            draft = self.llm.complete_json([{
                "role": "system",
                "content": "计划润色器。保持以下条目结构不变，把计划写得更具体温暖，输出 JSON:"
                           "{\"content_md\":\"完整markdown计划\"}。"},
                {"role": "user", "content": content},
            ], PlanDraft, temperature=0.4)
            if len(draft.content_md.strip()) > 50:
                content = draft.content_md
        except LLMError:
            pass
        saved = self.tools.plan_update(goal, content_md=content)
        yield from self._tool_events("plan.update", saved)
        self.store.save_plan_update(saved.payload.get("version", "v"), content, status="draft")

        revision = 0
        while True:
            summary = self.workspace.plan_summary(content)
            reply = yield Ask(
                gate=GATE_PLAN_CONFIRM,
                prompt=f"关口①（计划签署）：计划已写入 计划.md\n  摘要：{summary}\n"
                       f"回复「确认」开始执行；或直接说要改什么（例：每天 3 题）")
            if is_confirm(reply) or revision >= 2:
                self.store.save_plan_update(saved.payload.get("version", "v"),
                                            content, status="confirmed")
                yield self._msg("计划确认 ✓ 开始执行。" if is_confirm(reply)
                                else "先按当前计划执行，随时可以叫我改。")
                break
            rev = parse_plan_revision(reply)
            if rev:
                intent.daily_count = rev.get("daily_count", intent.daily_count)
                content = default_plan_md(goal, intent.daily_count,
                                          rev.get("deadline", deadline_note))
            else:
                content += f"\n- 学生修订：{reply.strip()}"
            saved = self.tools.plan_update(goal, content_md=content)
            yield from self._tool_events("plan.update", saved)
            revision += 1

    # ---------- 阶段③：执行（自主循环） ----------

    def _phase_execute(self, intent: UnitIntent):
        self.phase = Phase.EXECUTE
        for i in range(intent.daily_count):
            if self.steps >= MAX_STEPS:
                yield self._msg("本单元步数预算用尽，先进入反思。")
                break
            search = self.tools.bank_search(max_difficulty=0.75, count=1,
                                            exclude=self.used_item_ids)
            payload = search.payload.get("items") or []
            if not payload:
                generated = self.tools.quiz_generate(kc_id="MATH.G7.EQ.SOLVE",
                                                     difficulty=0.55,
                                                     topic_note="巩固移项与去括号")
                yield from self._tool_events("quiz.generate", generated)
                payload = generated.payload.get("items") or \
                          ([generated.payload["item"]] if "item" in generated.payload else [])
                if not payload:
                    break
            item = QuestionItem.model_validate(payload[0])
            self._seed_spaces(item)
            issued = self.tools.task_issue(item)
            yield from self._tool_events("task.issue", issued, item.item_id)
            if issued.status == "denied":
                continue
            self.used_item_ids.append(item.item_id)
            answer = yield Ask(prompt=f"（练习 {i + 1}/{intent.daily_count}）请作答：{item.stem}")
            if is_skip(answer):
                yield self._msg("跳过，下一题。")
                continue
            yield from self._answer_flow(item, answer, diagnose=False)

    # ---------- 阶段④：反思（关口②） ----------

    def _phase_reflect(self):
        self.phase = Phase.REFLECT
        reply = yield Ask(
            gate=GATE_REFLECTION,
            prompt="关口②（反思）：1) 这次主要用了什么策略？2) 预计下次正确率？3) 存入策略档案吗？\n"
                   "随便说，例如：策略=先移项再验算；预计=80；存=是")
        parsed = parse_reflection(reply)
        # 校准 + 策略 + 自主性（与 v1 反思关卡同语义）
        scores = [1.0 if v["status"] == "passed" else 0.5 if v["status"] == "partial" else 0.0
                  for v in self.verdicts]
        actual = sum(scores) / len(scores) if scores else 0.0
        self.snapshot.metacognition.calibration = Calibration(
            predicted_mean=parsed["predicted_score"], actual_mean=round(actual, 3),
            delta_trend="stable")
        self.snapshot.metacognition.reflection_completion_rate = 1.0
        if parsed["strategy_keep"] and parsed["strategy_note"]:
            self.snapshot.strategy_profile.tried_strategies.append(TriedStrategy(
                strategy_id=f"strategy.unit.{new_id()[:6]}",
                outcome="effective" if actual >= 0.5 else "neutral",
                context_tag=self.phase.value, evidence_refs=[]))
        bump = 0.02 if parsed["attribution"] == "effort_positive" else 0.0
        self.snapshot.autonomy_index.composite = round(
            min(1.0, self.snapshot.autonomy_index.composite + bump + 0.01), 4)
        self.snapshot.provenance.audit_id = new_id()
        self._persist_snapshot()

    # ---------- 作答处理（诊断/执行共用） ----------

    def _answer_flow(self, item: QuestionItem, answer: str, diagnose: bool):
        self.store.append_events([self._student_event("answer", answer)])
        result = self.tools.verify_answer(item, answer)
        self.verdicts.append({"status": ("passed" if result.status == "ok" else
                                         "failed" if result.status == "fail" else "partial"),
                              "item_id": item.item_id, "answer": answer})
        yield from self._tool_events("verify.answer", result, f"{item.item_id}")

        if result.status == "fail" and "无法自动判分" in result.detail:
            yield self._msg("说说你的思路，我们一起看。")
            return

        if result.status == "ok":
            yield self._msg("做对了！这个流程（先化简再验算）记住，难的题也一样用。")
            return

        # 答错：错题归档 → LLM 选择补救工具（默认阶梯提示）
        self.mistakes += 1
        mc = "；".join(item.misconception_links) or "待诊断"
        archived = self.workspace.append_mistake(
            item.item_id, item.stem, answer,
            str(item.answer.get("value", item.answer.get("expr", "?"))), mc)
        yield TranscriptEvent(EventKind.tool_result, "workspace.write 错题本.md",
                              detail=archived, status="ok")
        choice = ToolChoice()
        if self.polish or isinstance(self.llm, BaseLLM):
            try:
                choice = self.llm.complete_json([{
                    "role": "system",
                    "content": "学生答错后的补救工具选择。输出 JSON：{\"tool\":\"hint.ladder|explain.gated\",\"note\":\"理由\"}。"
                               "默认 hint.ladder；仅当学生已尝试两步以上才可选 explain.gated。"},
                    {"role": "user", "content": f"题目：{item.stem}；学生答案：{answer}"},
                ], ToolChoice, temperature=0.2)
            except LLMError:
                pass
        if choice.tool == "explain.gated":
            outcome = self.tools.explain_gated(item, mode="worked_full",
                                               text=str(item.answer.get("value", "")))
        else:
            outcome = self.tools.hint_ladder(item, proactive=True)
        yield from self._tool_events(choice.tool, outcome)
        if outcome.status == "ok":
            yield self._msg(f"提示：{outcome.payload.get('text', outcome.detail)}")
        else:
            yield self._msg(f"（{choice.tool} 被门禁拦下：{outcome.detail}）先自己再试一步。")

    # ---------- 杂项 ----------

    def _seed_spaces(self, item: QuestionItem):
        from app.orchestration.session import MISCONCEPTION_HYPOTHESES

        for space_id in item.misconception_links:
            hyps = MISCONCEPTION_HYPOTHESES.get(space_id)
            if hyps:
                seed_misconception_space(self.snapshot, space_id, hyps)

    def _student_event(self, kind: str, text: str):
        from app.core.schema import ActorRef, InteractionEvent, Observation

        return InteractionEvent(
            learner_pseudo_id=self.learner_id, session_id=self.tools.session_id,
            actor=ActorRef(kind="student"),
            observation=Observation(kind=kind, text=text))

    def _deadline_note(self, goal: str) -> str:
        if self.contract is not None and self.contract.external_deadline_refs:
            d = self.contract.external_deadline_refs[0]
            return f"{d.title} {str(d.due_at)[:10]}（倒排）"
        import re

        if m := re.search(r"(期中|期末|月考|考试).{0,10}?(\d{4}-\d{2}-\d{2})", goal):
            return f"{m.group(1)} {m.group(2)}（倒排）"
        return ""

    def _summary_text(self) -> str:
        now = {m.kc_id: m.p_mastery for m in self.snapshot.knowledge_state.kc_masteries}
        deltas = [f"{kc} {self.mastery_start.get(kc, 0.1):.2f}→{p:.2f}"
                  for kc, p in now.items()]
        passed = sum(1 for v in self.verdicts if v["status"] == "passed")
        return (f"单元总结：作答 {len(self.verdicts)} 次（对 {passed}），错题 {self.mistakes} 道"
                f"已归档并排期重练；掌握 {'；'.join(deltas) or '（首单元建立基线）'}；"
                f"自主性 {self.snapshot.autonomy_index.composite}。")

    def _persist_snapshot(self) -> None:
        self.snapshot.snapshot_id = new_id()
        self.store.append_snapshot(self.snapshot)

    def mirror(self) -> dict:
        s = self.snapshot
        return {
            "learner_id": s.learner_id,
            "mastery": [m.model_dump() for m in s.knowledge_state.kc_masteries],
            "misconceptions": [sp.model_dump() for sp in s.misconception_hypotheses],
            "autonomy": s.autonomy_index.model_dump(),
            "metacognition": s.metacognition.model_dump(),
            "affect": s.affect_motivation.model_dump(),
            "strategies": [t.model_dump() for t in s.strategy_profile.tried_strategies],
            "phase": self.phase.value, "steps": self.steps, "mistakes": self.mistakes,
        }
