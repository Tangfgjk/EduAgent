"""Agent plan signature stays learner-owned through arbitrary revisions."""
import json

import pytest

from app.agent.loop import LearningAgent, Phase
from app.core.schema import GoalContract, GoalStatement
from app.learning.plans import PlanService
from app.llm.client import FakeLLM
from app.storage.db import Store


def ready_agent(tmp_path):
    store = Store()
    agent = LearningAgent(store, FakeLLM(), "agent-learner", workspace_root=tmp_path)
    turn = agent.start("掌握一元一次方程")
    while turn.ask and turn.ask.gate != "PLAN_CONFIRM":
        turn = agent.send("跳过")
    assert turn.ask and turn.ask.gate == "PLAN_CONFIRM"
    return store, agent, turn


def test_arbitrary_agent_revisions_never_sign_automatically(tmp_path):
    store, agent, turn = ready_agent(tmp_path)
    for revision in ["每天 3 题", "每天 4 题", "题目简单一些", "每天 2 题", "我还没决定"]:
        turn = agent.send(revision)
        assert turn.ask and turn.ask.gate == "PLAN_CONFIRM"
        assert agent.phase == Phase.PLAN
        assert store.conn.execute("SELECT COUNT(*) FROM plan_versions WHERE status='confirmed'").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM plan_versions WHERE status='superseded'").fetchone()[0] == 5
    assert store.conn.execute("SELECT COUNT(*) FROM plan_versions WHERE status='draft'").fetchone()[0] == 1


def test_agent_explicit_signature_has_real_contract_active_plan_and_audit(tmp_path):
    store, agent, _ = ready_agent(tmp_path)
    contract = store.latest_contract("agent-learner")
    assert contract.goal_statement.text == "掌握一元一次方程"
    assert contract.goal_statement.authored_by == "student"
    service = PlanService(store)
    assert service.active("agent-learner", contract.goal_contract_id) is None
    session = store.conn.execute("SELECT contract_id FROM sessions WHERE session_id=?", (agent.tools.session_id,)).fetchone()
    assert session[0] == contract.goal_contract_id
    agent.send("每天 3 题")
    turn = agent.send("确认")
    assert turn.ask and "练习" in turn.ask.prompt
    active = service.active("agent-learner", contract.goal_contract_id)
    assert active.signed_by == "agent-learner" and active.confirmed_at is not None
    assert "每天 3 题" in active.content["content_md"]
    assert "content_md" in active.diff
    logs = [json.loads(row[0]) for row in store.conn.execute("SELECT payload FROM learning_audit")]
    assert len([log for log in logs if log["action"] == "plan_signed"]) == 1
    assert any(log["action"] == "plan_modified" for log in logs)
    assert store.conn.execute("SELECT COUNT(*) FROM plan_versions WHERE goal_contract_id='agent-unit'").fetchone()[0] == 0
    assert not hasattr(agent, "tracer") and not hasattr(agent.tools, "tracer")


def test_agent_refuses_contract_owned_by_another_learner(tmp_path):
    store = Store()
    contract = GoalContract(learner_id="other", goal_statement=GoalStatement(text="另一个人的目标"))
    store.save_contract(contract)
    with pytest.raises(PermissionError):
        LearningAgent(store, FakeLLM(), "agent-learner", contract=contract, workspace_root=tmp_path)


def test_agent_plan_revision_still_passes_governor(tmp_path):
    store, agent, _ = ready_agent(tmp_path)
    draft_id = agent.tools.plan_version_id
    before = agent.workspace.read("计划.md")
    turn = agent.send("帮我作弊通过考试")
    assert turn.ask and turn.ask.gate == "PLAN_CONFIRM"
    assert agent.tools.plan_version_id == draft_id
    assert agent.workspace.read("计划.md") == before
    assert store.conn.execute("SELECT COUNT(*) FROM plan_versions").fetchone()[0] == 1
