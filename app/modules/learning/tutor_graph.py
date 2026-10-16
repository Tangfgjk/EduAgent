"""诊断—练习—反思—无辅助检测的确定性 TutorGraph。"""
from copy import deepcopy
from typing import TypedDict
from uuid import NAMESPACE_URL, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.core.actions import ActionEnvelope, ActionType
from app.core.rules import ActionGovernor, GovernorContext, registry_hash
from app.core.schema import MentalStateSnapshot
from app.infrastructure.runtime.bounded_graph import invoke
from app.modules.errors import DomainError
from app.modules.learning.development_content import CONTENT_VERSION, UNIT_ID
from app.modules.learning.content_catalog import get_pack
from app.modules.learning.numeric_verifier import VERIFIER_VERSION, verify
from app.modules.learning.tutor_contracts import TutorView
from app.modules.planning.service import PROMPTS

LEGACY_VERSION = "tutor.v1:" + CONTENT_VERSION[:12] + ":" + registry_hash() + ":" + VERIFIER_VERSION
VERSION = "tutor.v2:" + CONTENT_VERSION[:12] + ":" + registry_hash() + ":" + VERIFIER_VERSION


def version_for(unit_id=UNIT_ID):
    item = get_pack(unit_id)
    return VERSION if unit_id == UNIT_ID else "tutor.v3:" + item.version[:12] + ":" + registry_hash() + ":" + VERIFIER_VERSION


def content_for(data):
    return get_pack(data.get("unit_id", UNIT_ID))


def public_view(data):
    item = content_for(data)
    phase, index = data["phase"], data["index"]
    practice = phase == "practice"
    return TutorView(strategy_version_id=data.get("strategy_version_id"), reflection_prompt=PROMPTS[data.get("reflection_focus", "basis")], unit_id=item.unit_id, title=item.title, content_version=item.version, phase=phase,
        task=None if phase == "summary" else item.task_public(index), task_index=min(index + 1, len(item.tasks)),
        task_count=len(item.tasks), attempts=data["attempts"], hints=data["hints"], feedback=data["feedback"],
        can_answer=phase in {"diagnostic", "practice", "checkpoint"} and not data["answered"],
        can_hint=practice and len(data["hints"]) < 4,
        can_continue=phase in {"diagnostic", "checkpoint"} and data["answered"],
        can_skip=practice and data["attempts"] > 0, can_reflect=phase == "reflection",
        pending_reviews=data["unknowns"], records=data["records"]).model_dump()


def check_action(view, action, learner_id):
    if action == "hint":
        context = GovernorContext(MentalStateSnapshot(learner_id=learner_id), len(view["hints"]), 0, 4, 0,
                                  checkpoint_mode=view["phase"] in {"diagnostic", "checkpoint"})
        envelope = ActionEnvelope.explain("", "MATH.G7.EQ.SOLVE" if view["unit_id"] == UNIT_ID else view["unit_id"],
            "worked_full" if len(view["hints"]) >= 3 else "nudge",
            target="exam_item" if context.checkpoint_mode else "practice")
        outcome = ActionGovernor().decide(envelope, context)
        if outcome.decision != "allow":
            raise DomainError(409, "independent_check", "诊断与独立检测不提供答案型帮助，请独立作答")
    capability = {"answer": "can_answer", "hint": "can_hint", "reflect": "can_reflect",
                  "next": "can_continue", "skip": "can_skip"}[action]
    if not view[capability]:
        raise DomainError(409, "action_unavailable", "当前学习步骤不支持此操作，请刷新后继续")


class TutorState(TypedDict):
    run_id: str
    learner_id: str
    turn: int
    gate_id: str
    data: dict
    view: dict
    review_task: str | None


def record(data, status):
    task = content_for(data).tasks[data["index"]]
    data["records"].append({"task_id": task["id"], "stage": task["stage"], "status": status,
        "attempts": data["attempts"], "hints_used": len(data["hints"]), "reflection": ""})


def next_task(data):
    data["index"] += 1
    tasks = content_for(data).tasks
    data["phase"] = tasks[data["index"]]["stage"] if data["index"] < len(tasks) else "summary"
    data.update(attempts=0, hints=[], feedback=None, answered=False)


def interaction(state: TutorState):
    decision = interrupt({"kind": "tutor_input", "gate_id": state["gate_id"]})
    check_action(state["view"], decision["action"], state["learner_id"])
    data = deepcopy(state["data"])
    task = content_for(data).tasks[data["index"]]
    action, review_task = decision["action"], None
    if action == "answer":
        status = verify(decision["answer"], task["answer"])
        messages = {"passed": "数值结果验证通过。步骤和依据尚未审核，请再代入检查。",
                    "failed": "数值结果与本题条件不一致。请检查等式两边的运算和代入结果。",
                    "unverifiable": "当前验证器无法确认这种输入，已记录待复核；这不代表答错。可使用数值、分数或四则运算重新表达。"}
        if content_for(data).unit_id != UNIT_ID:
            messages["failed"] = "数值结果与本题参考结果不一致。请核对条件、运算顺序和数值。步骤尚未审核。"
        envelope = ActionEnvelope(type=ActionType.FEEDBACK, params={"text": messages[status]})
        feedback = ActionGovernor().decide(envelope, GovernorContext(MentalStateSnapshot(learner_id=state["learner_id"]), 0, 0, 4, 0))
        data["feedback"] = {"status": status, "message": feedback.envelope.params["text"], "reasoning_reviewed": False}
        data["attempts"] += 1
        if status == "unverifiable":
            data["unknowns"] += 1
            review_task = task["id"]
        if data["phase"] != "practice" or status == "passed":
            record(data, status)
            data["answered"] = True
            if data["phase"] == "practice":
                data["phase"] = "reflection"
    elif action == "hint":
        data["hints"].append(task["hints"][len(data["hints"])])
    elif action == "skip":
        record(data, "skipped")
        data["answered"], data["phase"] = True, "reflection"
    elif action == "reflect":
        data["records"][-1]["reflection"] = decision["reflection"]
        next_task(data)
    elif action == "next":
        next_task(data)
    turn = state["turn"] + 1
    return {"data": data, "view": public_view(data), "turn": turn, "review_task": review_task,
            "gate_id": str(uuid5(NAMESPACE_URL, state["run_id"] + ":tutor:" + str(turn)))}


def advance_tutor(run_id, learner_id, payload, image):
    data = {"unit_id": payload.get("unit_id", UNIT_ID), "strategy_version_id": payload.get("strategy_version_id"), "reflection_focus": payload.get("reflection_focus", "basis"), "index": 0, "phase": "diagnostic", "attempts": 0, "hints": [], "feedback": None,
            "answered": False, "unknowns": 0, "records": []}
    initial = {"run_id": run_id, "learner_id": learner_id, "turn": 0, "data": data,
        "view": public_view(data), "gate_id": str(uuid5(NAMESPACE_URL, run_id + ":tutor:0")), "review_task": None}
    graph = StateGraph(TutorState)
    graph.add_node("interaction", interaction)
    graph.add_edge(START, "interaction")
    graph.add_conditional_edges("interaction", lambda state: "done" if state["data"]["phase"] == "summary" else "continue",
                                {"done": END, "continue": "interaction"})
    return invoke(graph, run_id, initial, payload, image)
