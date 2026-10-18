"""两个有界 LangGraph：学生先表达，模板挑战，学生核验后保存报告。"""
from copy import deepcopy
from hashlib import sha256
from typing import TypedDict
from uuid import NAMESPACE_URL, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.core.actions import ActionEnvelope, ActionType, ActorKind
from app.core.rules import ActionGovernor, GovernorContext, registry_hash
from app.core.schema import MentalStateSnapshot
from app.infrastructure.runtime.bounded_graph import invoke
from app.modules.errors import DomainError
from app.modules.learning.activity_content import CONTENT_VERSION, UNIT_ID
from app.modules.learning.activity_catalog import get_activity, activity_for_version
from app.modules.learning.activity_contracts import ActivityView
from app.modules.learning.numeric_verifier import verify, VERIFIER_VERSION

VERSION = "activities.v1:" + sha256((CONTENT_VERSION + registry_hash() + VERIFIER_VERSION).encode()).hexdigest()[:40]


def version_for(unit_id=UNIT_ID):
    item = get_activity(unit_id)
    return VERSION if unit_id == UNIT_ID else "activities.v2:" + sha256((item.version + registry_hash() + VERIFIER_VERSION).encode()).hexdigest()[:40]


def check_action(view, action):
    if action == 'refine':
        if (view['kind'] != 'teach' or view['flow'] != 'teach_cycle_v1' or view['phase'] != 'challenge'
                or not view.get('model_requested') or view.get('model_rounds', 1) >= 3):
            raise DomainError(409, 'action_unavailable', '本轮不再开放追问，请保存修正并进入独立验证')
        return
    expected = {"opening": "contribute", "challenge": "conclude", "verification": "verify"}.get(view["phase"])
    if action != expected:
        raise DomainError(409, "action_unavailable", "当前步骤已变化，请刷新后继续")


class ActivityState(TypedDict):
    run_id: str
    kind: str
    learner_id: str
    turn: int
    gate_id: str
    view: dict


def govern_text(learner_id, text, feedback=False, unit_id=UNIT_ID):
    envelope = ActionEnvelope(type=ActionType.FEEDBACK if feedback else ActionType.EXPLAIN,
        actor_kind=ActorKind.derived_agent, actor_ref="development_activity",
        template_version=version_for(unit_id), params={"text": text, "mode": "nudge", "target": "practice"},
        policy_provenance={"generated_by": "development_template", "grounded_to_kg": ["MATH.G7.EQ.SOLVE" if unit_id == UNIT_ID else get_activity(unit_id).parent_unit_id]})
    outcome = ActionGovernor().decide(envelope,
        GovernorContext(MentalStateSnapshot(learner_id=learner_id), 0, 0, 4, 0, artifact_present=True))
    if outcome.decision == "deny":
        raise DomainError(409, "activity_governed", "互动模板未通过教学规则，请管理员核对内容")
    return outcome.envelope.params["text"]


def interaction(state: ActivityState):
    decision = interrupt({"kind": state["kind"] + "_input", "gate_id": state["gate_id"]})
    view = deepcopy(state["view"])
    item = activity_for_version(view["activity_version"])
    check_action(view, decision["action"])
    view["messages"].append({"speaker": "我", "text": decision["reasoning"], "answer": decision["answer"], "category": "student_contribution"})
    if decision["action"] == "contribute":
        view['model_requested'] = bool(decision.get('request_model'))
        view['model_rounds'] = int(view['model_requested'])
        for message in ([] if view['model_requested'] else deepcopy(item.templates[state["kind"]])):
            message["text"] = govern_text(state["learner_id"], message["text"], unit_id=item.unit_id)
            view["messages"].append(message)
        view["phase"] = "challenge"
    elif decision['action'] == 'refine':
        view['model_rounds'] = max(1, view.get('model_rounds', 1)) + 1
    elif decision['action'] == 'verify':
        task = item.verification_task
        status = verify(decision['answer'], task['answer'])
        view['verification_check'] = {'status': status, 'message': '仅核验新题数值；解释与迁移能力尚未评定。',
            'reasoning_reviewed': False, 'independent': True}
        view['phase'] = 'summary'
        view['report'] = ['已分别保留初次讲解、各轮修正和新题验证。',
            '本轮请求了模型追问，实际结果与状态单独保留；模型不评价讲解质量。' if view.get('model_requested') else '学伴追问为预设模板，未自动理解你的讲解。',
            '新题未提供辅助，仅核验数值；不据此判定教学效果、迁移能力或素养。']
        if state['kind'] == 'roundtable':
            view['report'] = ['已保留学生初始思路、自己的比较修正与另一题验证。',
                '三种视角由同一模型生成，不是独立专家或教师结论；实际状态单独保留。' if view.get('model_requested') else '使用固定角色模板，不自动理解学生思路。',
                '数值由程序另行核验，不评价解释质量、掌握、迁移或教学效果。']
    else:
        status = verify(decision["answer"], item.task["answer"])
        messages = {"passed": "数值结果符合此题条件；解释与纠错质量尚未评定。",
                    "failed": "数值结果不符合此题条件；已保留本次尝试，不据此推断误区成因。",
                    "unverifiable": "当前输入无法自动核验，已进入人工数值复核；不等于答错。"}
        view["check"] = {"status": status, "message": govern_text(state["learner_id"], messages[status], feedback=True, unit_id=item.unit_id), "reasoning_reviewed": False, "independent": False}
        view["phase"] = "summary"
        view["report"] = ["已保存两次学生贡献及其数值表达。", "角色内容为固定开发模板，不是教师结论，也不是对学生文字的智能评价。",
                          "本次数值核验发生在辅助互动之后，不作为独立检测或迁移测验。", "讲解质量、论证有效性和素养变化尚未评定；需要后续专业量规与人工审核。"]
        if view.get('flow') in {'teach_cycle_v1', 'roundtable_cycle_v1'}:
            task = item.verification_task
            view['phase'] = 'verification'
            view['verification_task'] = {'task_id': task['id'], 'stem': task['stem'], 'answer_format': task['format']}
            view['report'] = []
    turn = state["turn"] + 1
    return {"view": ActivityView(**view).model_dump(), "turn": turn,
            "gate_id": str(uuid5(NAMESPACE_URL, state["run_id"] + ":activity:" + str(turn)))}


def advance_activity(run_id, learner_id, payload, image):
    kind = payload["activity_kind"]
    item = get_activity(payload.get("unit_id", UNIT_ID))
    view = ActivityView(kind=kind, phase="opening", task={"task_id": item.task["id"], "stem": item.task["stem"], "answer_format": item.task["format"]},
        messages=[], check=None, report=[], activity_version=item.version, flow=item.flow).model_dump()
    initial = {"run_id": run_id, "kind": kind, "learner_id": learner_id, "turn": 0, "view": view,
               "gate_id": str(uuid5(NAMESPACE_URL, run_id + ":activity:0"))}
    graph = StateGraph(ActivityState)
    graph.add_node("interaction", interaction)
    graph.add_edge(START, "interaction")
    graph.add_conditional_edges("interaction", lambda state: "done" if state["view"]["phase"] == "summary" else "continue",
                                {"done": END, "continue": "interaction"})
    return invoke(graph, run_id, initial, payload, image)
