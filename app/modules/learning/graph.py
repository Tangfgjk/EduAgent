"""有界、无外部调用的目标协商图。不是 S09 的策略规划图。"""
from typing import TypedDict
from uuid import NAMESPACE_URL, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.core.actions import ActionEnvelope, ActionType
from app.core.rules import ActionGovernor, GovernorContext, registry_hash
from app.core.schema import MentalStateSnapshot
from app.modules.errors import DomainError
from app.modules.learning.contracts import PlanDraft
from app.infrastructure.runtime.bounded_graph import invoke

VERSION = "plan-setup.v1:template.v1:" + registry_hash()


def govern(goal: str, learner_id: str, finalize=False):
    envelope = ActionEnvelope(type=ActionType.GOAL_NEGOTIATE,
        params={"text": goal, "finalize": finalize, "authored_by": "student" if finalize else "companion"})
    context = GovernorContext(MentalStateSnapshot(learner_id=learner_id), 0, 0, 0, 0)
    result = ActionGovernor().decide(envelope, context)
    if result.decision != "allow":
        raise DomainError(422, "goal_not_supported", "这个目标暂不能进入自动流程，请调整为自己的数学学习目标")


class PlanState(TypedDict):
    run_id: str
    learner_id: str
    goal: str
    daily_minutes: int
    generation: int
    draft: dict
    confirmed: bool
    gate_id: str


def propose(state: PlanState):
    govern(state["goal"], state["learner_id"])
    generation = state["generation"] + 1
    draft = PlanDraft(goal=state["goal"], daily_minutes=state["daily_minutes"], generation=generation,
        steps=["说说已有的理解，选出一个最想弄懂的问题", "独立尝试一道相关题，记录思路与卡点", "核对依据，用自己的话解释并留下待复习的问题"])
    return {"draft": draft.model_dump(), "generation": generation,
            "gate_id": str(uuid5(NAMESPACE_URL, state["run_id"] + ":" + str(generation)))}


def confirm(state: PlanState):
    # interrupt 节点恢复会从头执行，之前不做写入、计费等副作用。
    decision = interrupt({"kind": "plan_confirm", "gate_id": state["gate_id"]})
    if decision["action"] == "confirm":
        govern(state["goal"], state["learner_id"], finalize=True)
        return {"confirmed": True}
    return {"daily_minutes": decision["daily_minutes"]}


def advance(run_id: str, learner_id: str, payload: dict, image: str | None) -> tuple[dict, str, bool]:
    """返回检查点镜像，调用者与事件、任务结果一起原子提交。

    仅供短时无 IO 的开发图使用。真实模型/工具接入前须换成独立持久 saver、
    执行租约与结果协调；禁止在此事务边界中增加网络模型调用。
    """
    graph = StateGraph(PlanState)
    graph.add_node("propose", propose)
    graph.add_node("confirm", confirm)
    graph.add_edge(START, "propose")
    graph.add_edge("propose", "confirm")
    graph.add_conditional_edges("confirm", lambda state: "done" if state["confirmed"] else "revise",
                                {"done": END, "revise": "propose"})
    initial = {"run_id": run_id, "learner_id": learner_id, "goal": payload.get("goal", ""),
               "daily_minutes": payload["daily_minutes"], "generation": 0, "confirmed": False}
    return invoke(graph, run_id, initial, payload, image)
