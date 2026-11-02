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
from app.modules.learning.answer_interpretation import interpret,check_explicit_equalities
from app.modules.learning.tutor_contracts import TutorView
from app.modules.planning.service import PROMPTS
from app.modules.learning import teaching_loop

LEGACY_VERSION = "tutor.v1:" + CONTENT_VERSION[:12] + ":" + registry_hash() + ":" + VERIFIER_VERSION
VERSION = "tutor.v2:" + CONTENT_VERSION[:12] + ":" + registry_hash() + ":" + VERIFIER_VERSION


def version_for(unit_id=UNIT_ID, item=None):
    item = item or get_pack(unit_id)
    if any((task.get('pedagogy') or {}).get('profile')=='rational-agent.v2' for task in item.tasks):
        return 'tutor.v4:'+item.version[:12]+':'+registry_hash()+':'+VERIFIER_VERSION
    return VERSION if unit_id == UNIT_ID else "tutor.v3:" + item.version[:12] + ":" + registry_hash() + ":" + VERIFIER_VERSION


def content_for(data):
    if data.get("content_snapshot"):
        from app.modules.learning.content_catalog import ContentPack
        from app.modules.planning.runtime import apply_task_order
        return apply_task_order(ContentPack(**data["content_snapshot"]),data.get("skill_runtime"))
    from app.modules.planning.runtime import apply_task_order
    return apply_task_order(get_pack(data.get("unit_id", UNIT_ID)),data.get("skill_runtime"))


def public_view(data):
    item = content_for(data)
    phase, index = data["phase"], data["index"]
    practice = phase == "practice"
    signals = teaching_loop.signals(data['records'], data.get('feedback'), adaptive=data.get('adaptive_support', False),
        runtime=data.get('skill_runtime'), hints=len(data['hints']))
    unassisted = signals['can_fade']
    support = {"adaptive_enabled": bool(data.get("adaptive_support")), "mode": "socratic_first" if unassisted else "step_support", "basis": "最近练习首次数值通过且未使用提示，未发现已核验的算术矛盾" if unassisted else "当前记录或学习策略不满足减少支持的条件", "restorable": True, "ability_judgment": False}
    if data.get('last_teaching_decision'):
        decision=data['last_teaching_decision']
        support['agenda_decision']={k:v for k,v in decision.items() if k!='variant'}
        if practice and (decision.get('execution') or {}).get('next_task_id') == item.tasks[index]['id']:
            if decision.get('support')=='restore':support.update(mode='restored_step_support',basis=decision.get('support_reason') or decision.get('reason','学生需要恢复支持'))
            elif decision.get('support')=='fade' and unassisted:support.update(mode='socratic_first',basis=decision.get('reason','依据独立练习记录渐撤'))
    if practice and signals['needs_support']:
        support.update(mode='restored_step_support',basis='本题数值未通过、表达未确定或理由存在算术矛盾，恢复具体支持。')
    pedagogy=item.tasks[index].get('pedagogy') if index<len(item.tasks) else None
    if practice and pedagogy and pedagogy.get('profile')=='rational-agent.v2':
        status=(data.get('feedback') or {}).get('status')
        focus='representation' if status in {'failed','unverifiable'} else 'basis' if len(data['hints'])>=2 else 'verification' if unassisted else 'basis'
        last = data.get('last_teaching_decision') or {}
        if (last.get('execution') or {}).get('next_task_id') == item.tasks[index]['id'] and not data.get('feedback'):
            focus = last.get('question_focus', focus)
        if signals['reasoning_status'] == 'contradiction_found':
            focus = 'verification'
        support.update(profile='rational-agent.v2',concept=pedagogy['concept_label'],concept_id=pedagogy['concept'],action=focus,
            question=pedagogy['questions'][focus],plan_rationale=pedagogy['rationale'],
            basis='本题数值未通过或不可验证，先换表示核对；未判定具体语义误区。' if status in {'failed','unverifiable'} else '依据本轮实际提示和数值记录选择已审核的概念支持，允许继续求助。')
    reflection_prompt = PROMPTS[data.get('reflection_focus', 'basis')]
    if teaching_loop.reasoning_status(data.get('feedback')) == 'contradiction_found':
        reflection_prompt = '最终数值与理由分别核验。请重新检查理由中的算术等式，说明如何修正；尚不确定也可以如实记录。'
    return TutorView(support_decision=support, teaching_history=data.get('teaching_history', []), strategy_version_id=data.get("strategy_version_id"), reflection_prompt=reflection_prompt, unit_id=item.unit_id, title=item.title, content_version=item.version, phase=phase,
        task=None if phase == "summary" else item.task_public(index), task_index=min(index + 1, len(item.tasks)),
        task_count=len(item.tasks), attempts=data["attempts"], hints=data["hints"], feedback=data["feedback"],
        can_answer=phase in {"diagnostic", "practice", "checkpoint"} and not data["answered"],
        can_hint=practice and len(data["hints"]) < 4,
        can_continue=phase in {"diagnostic", "checkpoint"} and (data["answered"] or (data.get("feedback") or {}).get("status") == "unverifiable"),
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
        "attempts": data["attempts"], "hints_used": len(data["hints"]), "reflection": "",
        "reasoning_status": teaching_loop.reasoning_status(data.get('feedback'))})
    teaching_loop.observe(data, data['records'][-1])


def next_task(data,decision=None):
    from app.modules.learning.adaptive import apply
    if data['phase']=='reflection':
        decision = teaching_loop.decide(data, decision)
        apply(data,decision)
    tasks = content_for(data).tasks
    if data['phase']=='reflection':
        completed={r['task_id'] for r in data['records'] if r['stage']=='practice'}
        remaining=[i for i,t in enumerate(tasks) if t['stage']=='practice' and t['id'] not in completed]
        requested=(decision or {}).get('next_task_id')
        chosen=next((i for i in remaining if tasks[i]['id']==requested),None)
        data['index']=chosen if chosen is not None else remaining[0] if remaining else next((i for i,t in enumerate(tasks) if t['stage']=='checkpoint'),len(tasks))
        if decision and decision.get('execution'):
            decision['execution']['next_task_id']=tasks[data['index']]['id'] if data['index']<len(tasks) else None
            if decision.get('action')=='practice':decision['execution']['status']='practice_selected' if remaining else 'no_practice_remaining'
        if chosen is not None:data['last_teaching_decision']=decision
        teaching_loop.track(data, decision, tasks[data['index']] if data['index'] < len(tasks) else None)
    else:data['index'] += 1
    data["phase"] = tasks[data["index"]]["stage"] if data["index"] < len(tasks) else "summary"
    data.update(attempts=0, hints=[], feedback=None, answered=False)


def interaction(state: TutorState):
    decision = interrupt({"kind": "tutor_input", "gate_id": state["gate_id"]})
    check_action(state["view"], decision["action"], state["learner_id"])
    data = deepcopy(state["data"])
    task = content_for(data).tasks[data["index"]]
    action, review_task = decision["action"], None
    if action == "answer":
        interpretation = interpret(decision["answer"])
        status = interpretation.check(task["answer"])
        reasoning_check=check_explicit_equalities(decision.get('reasoning') or decision['answer'])
        messages = {"passed": "数值结果验证通过。步骤和依据尚未审核，请再代入检查。",
                    "failed": "数值结果与本题条件不一致。请检查等式两边的运算和代入结果。",
                    "unverifiable": "还不能可靠确定你的最终答案，这不代表答错。请明确一个数值或分数；可以说‘我的答案是3’。含单位、多个候选或证明需另行评阅。"}
        if content_for(data).unit_id != UNIT_ID:
            messages["failed"] = "数值结果与本题参考结果不一致。请核对条件、运算顺序和数值。步骤尚未审核。"
        if reasoning_check['status']=='contradiction_found':
            messages[status]+=' 你写出的算术等式存在不相等，请单独检查理由；最终数值与理由分别记录。'
        envelope = ActionEnvelope(type=ActionType.FEEDBACK, params={"text": messages[status]})
        feedback = ActionGovernor().decide(envelope, GovernorContext(MentalStateSnapshot(learner_id=state["learner_id"]), 0, 0, 4, 0))
        data["feedback"] = {"status": status, "message": feedback.envelope.params["text"], "reasoning_reviewed": False,
            "interpretation": {"version": interpretation.version, "candidate": interpretation.candidate, "reason": interpretation.reason,'reasoning_check':reasoning_check}}
        data["attempts"] += 1
        if status == "unverifiable":
            data["unknowns"] += 1
            review_task = task["id"]
        if status != "unverifiable" and (data["phase"] != "practice" or status == "passed"):
            record(data, status)
            data["answered"] = True
            if data["phase"] == "practice":
                data["phase"] = "reflection"
    elif action == "hint":
        support=public_view(data)['support_decision']
        if not data['hints'] and support.get('profile')=='rational-agent.v2':
            data['hints'].append(support['question'])
        elif not data['hints'] and public_view(data)['support_decision']['mode'] == 'socratic_first':
            data['hints'].append('先由你提出一个可以检查自己思路的问题：需要核对哪项条件或依据？若仍困难，可再次请求具体支持。')
        else:
            hint=task['hints'][len(data['hints'])]
            if len(data['hints'])==1 and hint==data['hints'][0] and task.get('pedagogy'):
                hint=task['hints'][0]
            data['hints'].append(hint)
    elif action == "skip":
        record(data, "skipped")
        data["answered"], data["phase"] = True, "reflection"
    elif action == "reflect":
        data["records"][-1]["reflection"] = decision["reflection"]
        teaching_loop.observe(data, data['records'][-1])
        next_task(data,decision.get('teaching_decision'))
    elif action == "next":
        if not data["answered"] and (data.get("feedback") or {}).get("status") == "unverifiable":
            record(data, "unverifiable")
        next_task(data)
    turn = state["turn"] + 1
    return {"data": data, "view": public_view(data), "turn": turn, "review_task": review_task,
            "gate_id": str(uuid5(NAMESPACE_URL, state["run_id"] + ":tutor:" + str(turn)))}


def advance_tutor(run_id, learner_id, payload, image, *, item=None):
    data = {"unit_id": payload.get("unit_id", UNIT_ID), "strategy_version_id": payload.get("strategy_version_id"), "reflection_focus": payload.get("reflection_focus", "basis"), "index": 0, "phase": "diagnostic", "attempts": 0, "hints": [], "feedback": None,
            "answered": False, "unknowns": 0, "records": []}
    data["skill_runtime"]=payload.get("skill_runtime")
    if item is not None:
        from dataclasses import asdict
        data["unit_id"] = item.unit_id
        data["adaptive_support"] = True
        data["content_snapshot"] = asdict(item)
    initial = {"run_id": run_id, "learner_id": learner_id, "turn": 0, "data": data,
        "view": public_view(data), "gate_id": str(uuid5(NAMESPACE_URL, run_id + ":tutor:0")), "review_task": None}
    graph = StateGraph(TutorState)
    graph.add_node("interaction", interaction)
    graph.add_edge(START, "interaction")
    graph.add_conditional_edges("interaction", lambda state: "done" if state["data"]["phase"] == "summary" else "continue",
                                {"done": END, "continue": "interaction"})
    return invoke(graph, run_id, initial, payload, image)
