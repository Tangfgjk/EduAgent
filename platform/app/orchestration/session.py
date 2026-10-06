"""docs/03 §5：会话编排环 —— step0 触发 → 1 感知 → 2 固化快照 → 3 选动作 → 3b 裁决 → 4 生成 → 5 记录。

原则落位：状态无智能体（持久态全部进 Store，本对象只持会话内瞬态）；
deny/rewrite 全部落审计事件（进化负样本）；快照 append-only；
题目推进：peek 不消费，仅当 TASK/QUESTION 真正执行时消费（检查点队列同理）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.actions import ActionEnvelope, ActionType, ActorKind
from app.core.machines import HINT_LABELS, HintLadder, Phase5E, Session5E, hint_budget
from app.core.rules import ActionGovernor, GovernorContext, registry_hash  # noqa: F401
from app.core.schema import (
    ActorRef, Calibration, GoalContext, InteractionEvent, MentalStateSnapshot,
    Observation, QuestionItem, TriedStrategy, Verdict, new_id,
    GoalContract,
)
from app.learning.perception import (
    apply_candidate, apply_misconception_hits, perceive, seed_misconception_space,
)
from app.learning.bridge import record_attempt, refresh_projection
from app.learning.verifier import verify_item
from app.orchestration.policy import BuiltInPolicyV1, TurnContext
from app.orchestration.trigger import TriggerEngine

BANK_PATH = Path(__file__).resolve().parent.parent.parent / "seeds" / "question_bank.json"

# 误区假设空间（题库 misconception_links 引用；均匀先验，感知器命中后验上浮）
MISCONCEPTION_HYPOTHESES: dict[str, dict[str, str]] = {
    "MC.EQ.SIGN": {"H_SIGN_MOVE": "移项时没有变号", "H_SIGN_DIST": "去括号时符号处理错", "H_OTHER": "其他原因"},
    "MC.EQ.DISTRIBUTE": {"H_DIST_ALL": "漏乘括号内某一项", "H_DIST_SIGN": "乘负数未变号", "H_OTHER": "其他原因"},
    "MC.EQ.REVERSE_OPS": {"H_INV_OPS": "加减乘除互逆关系用反", "H_OTHER": "其他原因"},
    "MC.APP.EQUAL_RELATION": {"H_REL_WRONG": "等量关系找错", "H_SETUP_WRONG": "未知数设错", "H_OTHER": "其他原因"},
    "MC.APP.DISCOUNT_ORDER": {"H_ORDER_SAME": "误以为先后顺序不影响结果", "H_CALC_WRONG": "百分数计算错", "H_OTHER": "其他原因"},
}

GENERIC_HINT_FALLBACK = "先把你的想法说出来，说错了也没关系。"

HELP_TEXT_RE = re.compile(r"怎么(做|解|办)|提示|思路|卡住|不会|帮(帮)?我")


def load_bank(path: Path | None = None) -> list[QuestionItem]:
    data = json.loads((path or BANK_PATH).read_text(encoding="utf-8"))
    return [QuestionItem.model_validate(raw) for raw in data["items"]]


@dataclass
class TurnResult:
    reply: str
    ui: dict
    actions: list[dict]
    events: list[InteractionEvent]
    denial: dict | None = None


@dataclass
class TutorSession:
    store: object
    llm: object
    learner_id: str
    contract: object | None = None
    session_type: str = "explore"          # explore | checkpoint
    bank: list[QuestionItem] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.bank = self.bank or load_bank()
        self.session_id = new_id()
        self.policy = BuiltInPolicyV1()
        self._asset_catalog = None
        self.governor = ActionGovernor()
        base = self.store.latest_snapshot(self.learner_id)
        self.snapshot = base.model_copy(deep=True) if base else MentalStateSnapshot(learner_id=self.learner_id)
        if self.contract is not None:
            self.snapshot.goal_context = GoalContext(
                active_goal_contract_id=self.contract.goal_contract_id, ownership="student")
        aut = self.snapshot.autonomy_index.composite
        self.ladder = HintLadder(aut)
        self.hint_budget = hint_budget(aut)
        self.hints_used = 0
        self.assistance_levels: dict[str, int] = {}
        self.exposed_answers: set[str] = set()
        self.assessment_kinds: dict[str, str] = {}
        self.trigger = TriggerEngine()
        self.phase = Session5E()
        self.current_item: QuestionItem | None = None
        self.next_index = 0
        self.issued_item_ids: set[str] = set()
        self.checkpoint_queue: list[QuestionItem] = []
        if self.session_type == "checkpoint":
            practices = sorted((q for q in self.bank if q.pattern == "practice"),
                               key=lambda q: q.difficulty)
            step = max(1, len(practices) // 3)
            self.checkpoint_queue = [practices[0], practices[step], practices[2 * step]]
        self.wrong_streak = 0
        self.correct_streak = 0
        self.frustration_streak = 0
        self.turn_count = 0
        self.verdicts: list[Verdict] = []
        self.pending_feedback: Verdict | None = None
        self.last_action_id: str | None = None
        self.store.ensure_learner(self.learner_id)
        self.store.save_session(self.session_id, self.learner_id,
                                self.contract.goal_contract_id if self.contract else None,
                                self.session_type)
        self._persist_snapshot()

    def _persist_snapshot(self) -> None:
        """快照 append-only：每次固化分配新版本 ID（docs/02 §3.3 存储约定）。"""
        refresh_projection(self.store, self.snapshot)
        self.snapshot.snapshot_id = new_id()
        self.store.append_snapshot(self.snapshot)

    def export_state(self) -> dict:
        """Versioned continuation state; restoring it never re-runs initialization."""
        return {
            "schema_version": "tutor-runtime-v1", "session_id": self.session_id,
            "learner_id": self.learner_id, "session_type": self.session_type,
            "contract": self.contract.model_dump(mode="json") if self.contract else None,
            "bank": [q.model_dump(mode="json") for q in self.bank],
            "snapshot": self.snapshot.model_dump(mode="json"),
            "current_item": self.current_item.item_id if self.current_item else None,
            "next_index": self.next_index, "issued_item_ids": sorted(self.issued_item_ids),
            "checkpoint_queue": [q.item_id for q in self.checkpoint_queue],
            "ladder_pos": self.ladder.pos, "hint_budget": self.hint_budget, "hints_used": self.hints_used,
            "assistance_levels": self.assistance_levels, "exposed_answers": sorted(self.exposed_answers),
            "assessment_kinds": self.assessment_kinds, "wrong_streak": self.wrong_streak,
            "correct_streak": self.correct_streak, "frustration_streak": self.frustration_streak,
            "turn_count": self.turn_count, "phase": self.phase.phase.value,
            "verdicts": [v.model_dump(mode="json") for v in self.verdicts],
            "pending_feedback": self.pending_feedback.model_dump(mode="json") if self.pending_feedback else None,
            "last_action_id": self.last_action_id,
            "trigger_last_fired": {k: v.isoformat() for k, v in self.trigger._last_fired.items()},
        }

    @classmethod
    def restore(cls, store, llm, state: dict):
        from datetime import datetime
        if state["schema_version"] != "tutor-runtime-v1":
            raise ValueError("Unsupported tutor runtime version")
        session = cls.__new__(cls)
        session.store, session.llm = store, llm
        for name in ("session_id", "learner_id", "session_type", "next_index", "hint_budget", "hints_used",
                     "wrong_streak", "correct_streak", "frustration_streak", "turn_count", "last_action_id"):
            setattr(session, name, state[name])
        session.contract = GoalContract.model_validate(state["contract"]) if state["contract"] else None
        session.bank = [QuestionItem.model_validate(q) for q in state["bank"]]
        items = {q.item_id: q for q in session.bank}
        session.current_item = items.get(state["current_item"])
        session.checkpoint_queue = [items[key] for key in state["checkpoint_queue"]]
        session.snapshot = MentalStateSnapshot.model_validate(state["snapshot"])
        session.ladder = HintLadder(session.snapshot.autonomy_index.composite)
        session.ladder.pos = state["ladder_pos"]
        session.issued_item_ids, session.exposed_answers = set(state["issued_item_ids"]), set(state["exposed_answers"])
        session.assistance_levels, session.assessment_kinds = dict(state["assistance_levels"]), dict(state["assessment_kinds"])
        session.phase = Session5E(phase=state["phase"])
        session.verdicts = [Verdict.model_validate(v) for v in state["verdicts"]]
        session.pending_feedback = Verdict.model_validate(state["pending_feedback"]) if state["pending_feedback"] else None
        session.trigger = TriggerEngine()
        session.trigger._last_fired = {k: datetime.fromisoformat(v) for k, v in state["trigger_last_fired"].items()}
        session.policy, session.governor, session._asset_catalog = BuiltInPolicyV1(), ActionGovernor(), None
        return session

    # ---------- 题目推进（peek 不消费，执行才消费） ----------

    def _ordered(self) -> list[QuestionItem]:
        return sorted(self.bank, key=lambda q: q.difficulty)

    def _signed_path_context(self) -> dict:
        """Read only an effective learner-signed plan; proposed/draft is ignored."""
        contract_id = self.contract.goal_contract_id if self.contract else self.snapshot.goal_context.active_goal_contract_id
        if not contract_id:
            return {}
        from app.learning.plans import PlanService
        from app.learning.assets import load_catalog
        from app.learning.decisions import recommend
        from app.learning.service import ConsentDenied, LearningService
        plan = PlanService(self.store).active(self.learner_id, contract_id)
        if plan is None or plan.signed_by != self.learner_id:
            return {}
        content = plan.content
        path = content.get("path") or {}
        nodes = content.get("nodes") or path.get("nodes") or []
        refs = content.get("kc_refs") or content.get("bank_scope") or path.get("kc_refs") or []
        scope = list(dict.fromkeys([*refs, *(n["kc_id"] for n in nodes if isinstance(n, dict) and n.get("kc_id"))]))
        if not scope:
            return {}
        if self._asset_catalog is None:
            self._asset_catalog = load_catalog()
        catalog = self._asset_catalog
        prerequisites = {asset.ref.asset_id: [r.asset_id for r in asset.prerequisite_refs] for asset in catalog.knowledge}
        unknown_kcs = set(scope) - set(prerequisites)
        try:
            recommendation = recommend(LearningService(self.store), self.learner_id, self._now(), prerequisites=prerequisites)
        except ConsentDenied:
            return {"signed": True, "plan_version_id": plan.version_id, "allowed_kcs": scope,
                    "nodes": [], "blocked_kcs": set(scope), "unknown_kcs": unknown_kcs,
                    "catalog_version": catalog.catalog_version}
        chosen = [n for n in recommendation["nodes"] if n["kc_id"] in scope and n["kc_id"] not in unknown_kcs]
        chosen.extend({"kc_id": kc, "decision": "UNKNOWN", "status": "blocked", "reason": "missing_catalog_asset",
                       "missing_prerequisites": [], "gate": {"status": "insufficient_evidence", "reason": "missing_catalog_asset"},
                       "evidence_refs": [], "state_version": None, "retention_version": None}
                      for kc in sorted(unknown_kcs))
        return {"signed": True, "plan_version_id": plan.version_id, "allowed_kcs": scope,
                "nodes": chosen, "blocked_kcs": unknown_kcs | {n["kc_id"] for n in chosen if n["missing_prerequisites"]},
                "unknown_kcs": unknown_kcs, "catalog_version": catalog.catalog_version,
                "path_version": recommendation["contract_version"], "as_of": recommendation["as_of"]}

    def _peek_next(self) -> QuestionItem | None:
        path = self._signed_path_context()
        if path.get("signed"):
            nodes = path.get("nodes", [])
            ranks = {node["kc_id"]: i for i, node in enumerate(nodes)}
            choices = [q for q in self.bank if q.kc_id in path["allowed_kcs"]
                       and q.kc_id not in path["blocked_kcs"] and q.item_id not in self.issued_item_ids]
            choices.sort(key=lambda q: (ranks.get(q.kc_id, len(ranks)), q.difficulty, q.item_id))
            return choices[0] if choices else None
        ordered = self._ordered()
        return ordered[self.next_index] if self.next_index < len(ordered) else None

    def _consume(self, item: QuestionItem) -> None:
        self.issued_item_ids.add(item.item_id)
        ordered = self._ordered()
        if self.next_index < len(ordered) and ordered[self.next_index].item_id == item.item_id:
            self.next_index += 1

    def _checkpoint_next(self) -> QuestionItem | None:
        if self.current_item is None and self.checkpoint_queue:
            item = self.checkpoint_queue.pop(0)
            self.current_item = item
            self._seed_spaces_for(item)
        return self.current_item

    def _seed_spaces_for(self, item: QuestionItem | None) -> None:
        for space_id in (item.misconception_links if item else []):
            hyps = MISCONCEPTION_HYPOTHESES.get(space_id)
            if hyps:
                seed_misconception_space(self.snapshot, space_id, hyps)

    # ---------- 事件 ----------

    def _event(self, kind: str, text: str | None = None, payload: dict | None = None,
               actor: ActorKind = ActorKind.companion, action_ref: str | None = None,
               verdict_ref: str | None = None) -> InteractionEvent:
        return InteractionEvent(
            learner_pseudo_id=self.learner_id, session_id=self.session_id,
            actor=ActorRef(kind=actor.value), action_ref=action_ref,
            observation=Observation(kind=kind, text=text, payload=payload or {}),
            verdict_ref=verdict_ref,
            context_snapshot={"mental_state_snapshot": self.snapshot.snapshot_id,
                              "policy_version": self.policy.policy_version,
                              "state_machine_pos": {"hint_ladder": self.ladder.label,
                                                    "session_5e": self.phase.phase.value}},
        )

    def _governor_ctx(self) -> GovernorContext:
        # Optional strict provenance/safety ports are installed by the gateway;
        # the legacy local session remains compatible when they are absent.
        return GovernorContext(
            snapshot=self.snapshot, ladder_pos=self.ladder.pos,
            hints_used=self.hints_used, hint_budget=self.hint_budget,
            frustration_streak=self.frustration_streak,
            artifact_present=False,   # v1 保守：不启用"有作品即可看答案"捷径
            checkpoint_mode=self.session_type == "checkpoint",
            current_difficulty=self.current_item.difficulty if self.current_item else 0.5,
            grounding_validator=(self._runtime_sources.validate if getattr(self, "_runtime_sources", None)
                                 else getattr(self.store, "governance_validator", None)),
            safety_review_sink=getattr(self.store, "governance_review_sink", None),
        )

    def _turn_ctx(self, cand, proposals: list, text: str) -> TurnContext:
        help_asked = cand.current_intention == "ask_help" or bool(HELP_TEXT_RE.search(text or ""))
        next_item = None if help_asked else self._peek_next()
        if next_item:
            self._seed_spaces_for(next_item)
        if self.session_type == "checkpoint":
            self._checkpoint_next()
        signals = self._learning_signals()
        review_tasks = signals.get("review_tasks", [])
        return TurnContext(
            snapshot=self.snapshot, ladder_pos=self.ladder.pos, session_type=self.session_type,
            item=self.current_item, next_item=next_item, verdict_pending=self.pending_feedback,
            perception=cand, proposals=proposals, student_text=text,
            path_context=None if help_asked else self._signed_path_context(),
            review_context={"due": bool(next_item and any(task["kc_id"] == next_item.kc_id for task in review_tasks)),
                            "tasks": review_tasks},
            learning_signals=signals,
        )

    # ---------- 会话开始 ----------

    def start(self) -> TurnResult:
        if self.session_type == "checkpoint":
            item = self._checkpoint_next()
            env = ActionEnvelope.question(
                session_id=self.session_id, kc_id=item.kc_id, stem=item.stem,
                item_id=item.item_id, target="exam_item", intent="diagnostic")
        else:
            item = self._peek_next()
            if item is None:
                if self._signed_path_context().get("signed"):
                    env = ActionEnvelope.feedback(self.session_id, "MATH.G7.EQ.SOLVE", "process",
                        "当前已签署范围没有可执行任务，请先复核先修诊断或调整计划草案；未映射知识点需要补齐评测资产。")
                    self._bind_execution_source(env, system_template=True)
                    outcome = self.governor.decide(env, self._governor_ctx())
                    if outcome.decision == "rewrite":
                        env = outcome.envelope
                    path_context = self._signed_path_context()
                    event = self._event("utterance", text=self._render(env), action_ref=env.action_id,
                        payload={"path_context": {"plan_version_id": path_context["plan_version_id"],
                            "unknown_kcs": sorted(path_context.get("unknown_kcs", [])),
                            "blocked_kcs": sorted(path_context["blocked_kcs"]),
                            "catalog_version": path_context["catalog_version"]}})
                    self.store.append_events([event])
                    return TurnResult(reply=self._render(env), ui=self._ui_state(None), actions=[env.model_dump()], events=[event])
                raise RuntimeError("题库为空")
            self.current_item = item
            self._seed_spaces_for(item)
            env = ActionEnvelope.task(
                session_id=self.session_id, kc_id=item.kc_id, stem=item.stem,
                item_id=item.item_id, difficulty=item.difficulty,
                kind="inquiry" if item.pattern in ("inquiry", "pbl") else "practice")
            self._attach_learning_provenance(env)
        self._bind_execution_source(env)
        outcome = self.governor.decide(env, self._governor_ctx())
        denial_info = None
        if outcome.decision == "deny":
            denial_info = {"rule": outcome.rule_id, "reason": outcome.reason}
            self.current_item = None
            env = ActionEnvelope.feedback(self.session_id, item.kc_id, "process",
                "当前任务需要先补充支持或调整计划，我们先放缓一步。")
            self._bind_execution_source(env, system_template=True)
            outcome = self.governor.decide(env, self._governor_ctx())
        if outcome.decision == "rewrite":
            env = outcome.envelope
            self._bind_execution_source(env, system_template=True)
        if outcome.decision == "deny":
            env = self._safe_wait()
        self._execute(env)
        reply = self._render(env)
        event = self._event("utterance", text=reply,
                            payload={"action_id": env.action_id, "type": env.type.value,
                                     "governor": outcome.decision, "denial": denial_info,
                                     "learning_provenance": env.policy_provenance},
                            action_ref=env.action_id)
        self.last_action_id = env.action_id
        self.store.append_events([event])
        self._persist_snapshot()
        return TurnResult(reply=reply, ui=self._ui_state(None),
                          actions=[env.model_dump()], events=[event], denial=denial_info)

    # ---------- 回合主流程（docs/03 §5） ----------

    def handle_turn(self, text: str = "", answer: str | None = None, attempt_id: str | None = None) -> TurnResult:
        events: list[InteractionEvent] = []
        denial_info: dict | None = None
        if text or answer:
            kind = "answer" if answer else ("help_seeking" if HELP_TEXT_RE.search(text or "") else "utterance")
            events.append(self._event(kind, text=text or answer, actor=ActorKind.student))

        # step1 感知
        cand = perceive(self.llm, self.snapshot,
                        self.current_item.model_dump() if self.current_item else None,
                        text, answer,
                        [s.model_dump() for s in self.snapshot.misconception_hypotheses])
        verdict: Verdict | None = None

        # 作答 → 验证 + BKT + 阶梯 + 误区后验（step2 固化）
        if answer and self.current_item:
            verdict = verify_item(answer, self.current_item,
                                  llm=None if getattr(self, "_runtime_sources", None) else self.llm)
            if not verdict.artifact_id:
                verdict.artifact_id = new_id()
            self.verdicts.append(verdict)
            self.store.save_verdict(verdict, self.session_id, self.current_item.item_id)
            correct = verdict.status == "passed"
            record_attempt(self.store, self.learner_id, self.session_id, self.current_item, verdict,
                           attempt_id or new_id(), hint_level=self.assistance_levels.get(self.current_item.item_id, 0),
                           answer_exposed=self.current_item.item_id in self.exposed_answers,
                           assessment_kind="post" if self.session_type == "checkpoint"
                           else self.assessment_kinds.get(self.current_item.item_id, "practice"),
                           action_ref=self.last_action_id, occurred_at=self._now())
            refresh_projection(self.store, self.snapshot)
            if correct:
                self.wrong_streak = 0
                self.correct_streak += 1
                self.frustration_streak = 0
                if cand.confidence == 0:
                    cand = cand.model_copy(update={"frustration_delta": -0.1})
                if self.phase.phase == Phase5E.EXPLORE:
                    self.phase.transition(Phase5E.EXPLAIN)
                if self.phase.phase == Phase5E.EXPLAIN and self.correct_streak >= 2:
                    self.phase.transition(Phase5E.ELABORATE)
            else:
                self.correct_streak = 0
                self.wrong_streak += 1
                self.frustration_streak += 1
                if cand.confidence == 0:
                    cand = cand.model_copy(update={"frustration_delta": 0.12})
                self.ladder.advance()   # 仍卡壳 → 阶梯升级（docs/03 §4.1）
                for space_id in self.current_item.misconception_links:
                    apply_misconception_hits(self.snapshot, space_id, cand.misconception_hits)
            self.pending_feedback = verdict if verdict.status != "unverifiable" else None
            if self.session_type == "checkpoint":
                self.current_item = None  # 交卷即离场，下一回合弹出队列中的下一题

        apply_candidate(self.snapshot, cand, action_ref=self.last_action_id)

        # step0 触发（反馈回合不需要触发器，且不消耗冷却窗口）
        learning_signals = self._learning_signals()
        proposals = [] if self.pending_feedback is not None \
            else self.trigger.evaluate(self.snapshot, self.wrong_streak,
                                       now=self._now(),
                                       learning_signals=learning_signals)

        # step3 选动作 → step3b 裁决（deny 教学化替代 + 审计；rewrite 审计）
        tctx = self._turn_ctx(cand, proposals, text)
        expected_template = BuiltInPolicyV1().choose(tctx)
        env = self.policy.choose(tctx)
        self._attach_learning_provenance(env)
        self._bind_execution_source(env, context=tctx, expected_action=expected_template)
        outcome = self.governor.decide(env, self._governor_ctx())
        if outcome.decision == "deny":
            denial_info = {"rule": outcome.rule_id, "reason": outcome.reason}
            events.append(self._event("utterance", text=outcome.reason,
                                      payload={"audit": True, "denial": denial_info,
                                               "rejected_action": env.action_id}))
            env = self._fallback_after_denial(env, outcome, tctx)
            self._attach_learning_provenance(env)
            self._bind_execution_source(env, context=tctx, system_template=True)
            outcome = self.governor.decide(env, self._governor_ctx())
            if outcome.decision == "rewrite":
                env = outcome.envelope
                self._bind_execution_source(env, system_template=True)
        elif outcome.decision == "rewrite":
            events.append(self._event("utterance", text=f"[R-04 改写] {outcome.reason}",
                                      payload={"audit": True, "rewrite": True,
                                               "action": env.action_id}))
            env = outcome.envelope
            self._bind_execution_source(env, system_template=True)

        if outcome.decision == "deny":
            env = self._safe_wait()

        # step4 执行副作用 + 生成
        self._execute(env)
        reply = self._render(env)

        # step5 记录
        events.append(self._event(
            "utterance", text=reply,
            payload={"action_id": env.action_id, "type": env.type.value,
                     "governor": outcome.decision,
                     "learning_provenance": env.policy_provenance,
                     "params": {k: v for k, v in env.params.items() if k not in ("stem", "text")}},
            action_ref=env.action_id,
            verdict_ref=(verdict.artifact_id if verdict and verdict.artifact_id else None),
        ))
        self.last_action_id = env.action_id
        self.pending_feedback = None
        self.store.append_events(events)
        self._persist_snapshot()
        self.turn_count += 1
        return TurnResult(reply=reply, ui=self._ui_state(verdict),
                          actions=[env.model_dump()], events=events, denial=denial_info)

    def _learning_signals(self) -> dict:
        """Best-effort read of explicit learning state; absent consent/data is empty."""
        try:
            from app.learning.service import ConsentDenied, LearningService
            from app.learning.review_port import review_task
            from app.learning.gates import evaluate_gate
            from app.learning.schema import AssessmentProfile
            service = LearningService(self.store)
            retentions = service.retentions(self.learner_id)
            as_of = self._now()
            tasks = [task for state in retentions if (task := review_task(state, as_of)) is not None]
            refs = list(dict.fromkeys(ref for task in tasks for ref in task.evidence_refs))
            version = max((task.state_version for task in tasks), default=None)
            state = next((m for m in service.masteries(self.learner_id)
                          if self.current_item and m.kc_id == self.current_item.kc_id), None)
            gate = evaluate_gate(state, service.evidences(self.learner_id), AssessmentProfile()) if state else None
            if gate and gate.status in {"uncertain", "insufficient_evidence"}:
                refs = list(dict.fromkeys([*refs, *gate.evidence_refs]))
                version = state.state_version if version is None else max(version, state.state_version)
            return {"review_due": bool(tasks), "evidence_refs": refs,
                    "unknown_evidence": bool(gate and gate.status in {"uncertain", "insufficient_evidence"}),
                    "state_version": version, "version": "learning-signals-v1",
                    "as_of": as_of.isoformat(), "review_tasks": [t.model_dump(mode="json") for t in tasks]}
        except ConsentDenied:
            return {}

    def _now(self):
        from app.core.clock import SystemClock, aware_utc
        if getattr(self, '_clock', None):
            return aware_utc(self._clock())
        return getattr(self, 'clock_port', SystemClock()).now()

    def _attach_learning_provenance(self, env: ActionEnvelope) -> None:
        if env.type != ActionType.TASK:
            return
        path = self._signed_path_context()
        kc = next(iter(env.policy_provenance.get("grounded_to_kg", [])), None)
        node = next((n for n in path.get("nodes", []) if n["kc_id"] == kc), None)
        if node:
            env.policy_provenance.update(plan_version_id=path["plan_version_id"],
                path_version=path["path_version"], learning_decision=node["decision"],
                mastery_version=node["state_version"], retention_version=node["retention_version"],
                evidence_refs=node["evidence_refs"], as_of=path["as_of"])

    def _fallback_after_denial(self, env: ActionEnvelope, outcome,
                               tctx: TurnContext) -> ActionEnvelope:
        """被拦动作的教学化替代：R-01→阶梯提示；R-09→规则说明；R-03→支持反馈；其余→鼓励反馈。"""
        sid = env.session_id
        kc = tctx.item.kc_id if tctx.item else "MATH.G7.EQ.SOLVE"
        if outcome.rule_id == "R-01":
            level = self.ladder.pos
            text = (tctx.item.hint_text(level) if tctx.item else None) or GENERIC_HINT_FALLBACK
            form = ("nudge" if level == 0 else "directive" if level == 1
                    else "worked_partial" if level == 2 else "worked_full")
            return ActionEnvelope.hint(sid, kc, level, form, text, proactive=False)
        if outcome.rule_id == "R-09":
            return ActionEnvelope.explain(sid, kc, "rule_explanation",
                                          text="阶段测试中我只讲规则不提供答案；提交后统一查看解析。",
                                          target="exam_item", rule_explanation=True)
        if outcome.rule_id == "R-03":
            return ActionEnvelope.feedback(sid, kc, "process",
                                           text="先缓一缓，我们不赶难度。把上一题的过程再走一遍，我陪你。")
        return ActionEnvelope.feedback(sid, kc, "process",
                                       text="这个请求我先按下不表——先把当前这步自己完成，会更有收获。")

    def _execute(self, env: ActionEnvelope) -> None:
        sources = getattr(self, "_runtime_sources", None)
        if sources and sources.validate(env):
            raise PermissionError("Refusing execution of an unbound or changed runtime action")
        if self.current_item and env.params.get("assistance_hint_level"):
            self.assistance_levels[self.current_item.item_id] = max(
                int(env.params["assistance_hint_level"]), self.assistance_levels.get(self.current_item.item_id, 0))
        if self.current_item and env.type == ActionType.HINT:
            level = max(1, env.params.get("ladder_level", 0))
            self.assistance_levels[self.current_item.item_id] = max(level, self.assistance_levels.get(self.current_item.item_id, 0))
        if self.current_item and ((env.type == ActionType.EXPLAIN and env.params.get("mode") == "worked_full")
                                 or (env.type == ActionType.HINT and env.params.get("form") == "worked_full")):
            self.exposed_answers.add(self.current_item.item_id)
        if env.type == ActionType.HINT and env.params.get("proactive"):
            self.hints_used += 1   # R-07 只约束主动提示；学生求助不计入预算
        if env.type in (ActionType.TASK, ActionType.QUESTION) and env.params.get("item_id"):
            item = next((q for q in self.bank if q.item_id == env.params["item_id"]), None)
            if item and env.type == ActionType.TASK:
                if env.policy_provenance.get("learning_decision") == "REVIEW":
                    self.assessment_kinds[item.item_id] = "review"
                elif env.params.get("assessment_kind") == "diagnostic":
                    self.assessment_kinds[item.item_id] = "diagnostic"
                self.current_item = item
                self._seed_spaces_for(item)
                self._consume(item)
        if self.phase.phase == Phase5E.ENGAGE and env.type in (ActionType.TASK, ActionType.QUESTION):
            self.phase.transition(Phase5E.EXPLORE)

    # ---------- 最终回复只渲染已治理模板：自由模型润色不能保留语义安全 ----------

    def _bind_execution_source(self, env, *, context=None, expected_action=None, system_template=False):
        sources = getattr(self, "_runtime_sources", None)
        if sources:
            sources.propose(env, context=context, expected_action=expected_action, system_template=system_template)

    def _safe_wait(self):
        env = ActionEnvelope(session_id=self.session_id, type=ActionType.WAIT)
        self._bind_execution_source(env, system_template=True)
        return env

    def _render(self, env: ActionEnvelope) -> str:
        sources = getattr(self, "_runtime_sources", None)
        if sources and sources.validate(env):
            return "内容来源校验未通过，请先调整任务或重新审核课程。"
        return self._deterministic_text(env)

    def _deterministic_text(self, env: ActionEnvelope) -> str:
        t = env.type
        if t == ActionType.HINT:
            return f"【{HINT_LABELS.get(env.params.get('ladder_level', 0), '提示')}】{env.params.get('text', '')}"
        if t in (ActionType.EXPLAIN, ActionType.FEEDBACK):
            return str(env.params.get("text", ""))
        if t in (ActionType.TASK, ActionType.QUESTION):
            return str(env.params.get("stem", ""))
        if t == ActionType.REFLECTION:
            return "用几分钟回顾一下：这次的策略哪里有效？"
        if t == ActionType.ESCALATE:
            return "我把这个情况转给老师看一下。"
        if t == ActionType.WAIT:
            return "当前动作未通过安全与来源校验，我们先暂停，请调整任务或重新审核课程。"
        return "……"

    # ---------- 反思关卡（必经） ----------

    def submit_reflection(self, payload: dict) -> dict:
        attribution = payload.get("attribution", "effort_positive")
        predicted = float(payload.get("predicted_score", 0.5))
        actual_scores = [1.0 if v.status == "passed" else 0.5 if v.status == "partial" else 0.0
                         for v in self.verdicts if v.status != "unverifiable"]
        actual = sum(actual_scores) / len(actual_scores) if actual_scores else 0.0
        self.snapshot.metacognition.calibration = Calibration(
            predicted_mean=round(min(1.0, max(0.0, predicted)), 3),
            actual_mean=round(actual, 3), delta_trend="stable")
        self.snapshot.metacognition.reflection_completion_rate = 1.0
        if payload.get("strategy_keep") and self.current_item:
            self.snapshot.strategy_profile.tried_strategies.append(TriedStrategy(
                strategy_id=f"strategy.{self.current_item.kc_id.lower().replace('.', '_')}.v1",
                outcome="effective" if actual >= 0.5 else "neutral",
                context_tag=self.current_item.pattern))
        aut = self.snapshot.autonomy_index
        bump = 0.02 if attribution == "effort_positive" else 0.0
        aut.composite = round(min(1.0, aut.composite + bump + 0.01), 4)
        aut.subscores["self_check_ratio"] = round(
            min(1.0, aut.subscores.get("self_check_ratio", 0.0) + 0.05), 4)
        self.snapshot.provenance.audit_id = new_id()
        self.store.append_events([self._event(
            "reflection", text=json.dumps(payload, ensure_ascii=False), actor=ActorKind.student)])
        self._persist_snapshot()
        try:
            self.phase.transition(Phase5E.EVALUATE)
        except ValueError:
            pass
        return {"status": "reflection_saved",
                "calibration": self.snapshot.metacognition.calibration.model_dump(),
                "autonomy": self.snapshot.autonomy_index.composite}

    # ---------- 我的镜子（open learner model，docs/07 §3） ----------

    def mirror(self) -> dict:
        s = self.snapshot
        evidence = {m.kc_id: m.evidence_refs[-3:] for m in s.knowledge_state.kc_masteries}
        return {
            "learner_id": s.learner_id,
            "snapshot_id": s.snapshot_id,
            "mastery": [m.model_dump() for m in s.knowledge_state.kc_masteries],
            "misconceptions": [sp.model_dump() for sp in s.misconception_hypotheses],
            "affect": s.affect_motivation.model_dump(),
            "autonomy": s.autonomy_index.model_dump(),
            "metacognition": s.metacognition.model_dump(),
            "strategies": [t.model_dump() for t in s.strategy_profile.tried_strategies],
            "evidence_refs": evidence,
            "hard_constraint_hash": registry_hash(),
            "session": {"session_id": self.session_id, "type": self.session_type,
                        "verdicts": [v.status for v in self.verdicts],
                        "ladder": self.ladder.label,
                        "hint_budget": f"{self.hints_used}/{self.hint_budget}"},
        }

    def _ui_state(self, verdict: Verdict | None) -> dict:
        return {
            "phase": self.phase.phase.value,
            "ladder": {"pos": self.ladder.pos, "label": self.ladder.label, "max": 3},
            "hint_budget": {"used": self.hints_used, "total": self.hint_budget},
            "autonomy": self.snapshot.autonomy_index.composite,
            "frustration": self.snapshot.affect_motivation.frustration,
            "current_item": self.current_item.stem if self.current_item else None,
            "current_item_id": self.current_item.item_id if self.current_item else None,
            "current_kc_id": self.current_item.kc_id if self.current_item else None,
            "assistance": {
                "hint_level": self.assistance_levels.get(self.current_item.item_id, 0) if self.current_item else 0,
                "answer_exposed": bool(self.current_item and self.current_item.item_id in self.exposed_answers),
            },
            "verdict": ({"status": verdict.status, "explain": verdict.explainability}
                        if verdict else None),
        }
