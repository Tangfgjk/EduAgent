from typing import TypedDict
from uuid import NAMESPACE_URL, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.core.rules import registry_hash
from app.infrastructure.runtime.bounded_graph import invoke
from app.modules.planning.contracts import SoftPolicy

VERSION = "strategy-template.v1:" + registry_hash()
CHECKS = ["软策略严格白名单与取值范围通过", "教学硬规则注册表未修改", "资料仅作为数据引用，不作为指令执行",
          "本项目 L1 事实、自述和人工判断分开引用；不采用 L3 作为新事实", "未生成素养分数或真实学习效果结论"]


class PlanningState(TypedDict):
    run_id: str
    gate_id: str
    view: dict


def admin_review(state: PlanningState):
    decision = interrupt({"kind": "strategy_admin_review", "gate_id": state["gate_id"]})
    if decision["action"] not in {"approve", "reject"}: raise ValueError("admin gate")
    return {"view": {**state["view"], "phase": "student_confirm" if decision["action"] == "approve" else "rejected"},
            "gate_id": str(uuid5(NAMESPACE_URL, state["run_id"] + ":student"))}


def student_confirm(state: PlanningState):
    decision = interrupt({"kind": "strategy_student_confirm", "gate_id": state["gate_id"]})
    if decision["action"] not in {"confirm", "decline"}: raise ValueError("student gate")
    return {"view": {**state["view"], "phase": "approved" if decision["action"] == "confirm" else "declined"}}


def advance_strategy(run_id, payload, image):
    policy = SoftPolicy.model_validate(payload["candidate"]).model_dump() if not image else None
    initial = {"run_id": run_id, "gate_id": str(uuid5(NAMESPACE_URL, run_id + ":admin")),
               "view": {"phase": "admin_review", "policy": policy, "checks": CHECKS, "source": "development_template"}}
    graph = StateGraph(PlanningState)
    graph.add_node("admin_review", admin_review)
    graph.add_node("student_confirm", student_confirm)
    graph.add_edge(START, "admin_review")
    graph.add_conditional_edges("admin_review", lambda state: "done" if state["view"]["phase"] == "rejected" else "student",
                                {"done": END, "student": "student_confirm"})
    graph.add_edge("student_confirm", END)
    return invoke(graph, run_id, initial, payload, image)
