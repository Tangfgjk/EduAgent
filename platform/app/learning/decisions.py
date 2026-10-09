"""Explainable path v2 using only mastery, retention and prerequisites."""
from datetime import datetime

from app.learning.gates import evaluate_gate
from app.learning.review_port import retrievability, require_aware
from app.learning.schema import AssessmentProfile, MasteryState


def recommend(service, learner_id: str, as_of: datetime, prerequisites=None, profiles=None, allowed_kcs=None):
    require_aware(as_of)
    if prerequisites is None:
        prerequisites = {"MATH.G7.EQ.SOLVE": [], "MATH.G7.EQ.SETUP": ["MATH.G7.EQ.SOLVE"], "MATH.G7.EQ.APPLY": ["MATH.G7.EQ.SOLVE", "MATH.G7.EQ.SETUP"]}
    profiles = profiles or {}
    evidences = service.evidences(learner_id)
    selected = [e for e in evidences if e.occurred_at <= as_of]
    active = {e.evidence_id: e for e in selected}
    for e in selected:
        if e.supersedes:
            active.pop(e.supersedes, None)
    states = {s.kc_id:s for s in service.masteries(learner_id)}
    retention = {s.kc_id:s for s in service.retentions(learner_id)}
    if len(selected) != len(evidences):
        from app.learning.replay import replay
        result = replay(selected,learner_id,as_of)
        states,retention = result.mastery,result.retention
    kcs = sorted(set(prerequisites) | set(states) | {k for refs in prerequisites.values() for k in refs})
    if allowed_kcs is not None:
        kcs = [kc for kc in kcs if kc in allowed_kcs]
    gates = {kc:evaluate_gate(states.get(kc,MasteryState(learner_id=learner_id,kc_id=kc)),list(active.values()),profiles.get(kc,AssessmentProfile())) for kc in kcs}
    nodes = []
    for kc in kcs:
        gate = gates[kc]
        state = states.get(kc,MasteryState(learner_id=learner_id,kc_id=kc))
        missing = [p for p in prerequisites.get(kc,[]) if gates[p].status != "mastered"]
        r = retention.get(kc)
        risk = 1-retrievability(r,as_of) if r and r.last_review_at else None
        if missing:
            decision,reason = "DIAGNOSE", "先修尚未通过门控，先诊断或补练"
        elif gate.status in {"uncertain", "insufficient_evidence"}:
            decision,reason = "DIAGNOSE", "证据或可靠度不足，需要独立诊断"
        elif gate.status != "mastered":
            decision,reason = "REMEDIATE", "证据充分但尚未达到掌握要求"
        elif risk is None or risk >= .2:
            decision,reason = "REVIEW", "需要独立回忆以建立或恢复保持状态"
        else:
            decision,reason = "ADVANCE", "掌握门控通过、先修满足、遗忘风险低"
        nodes.append(dict(kc_id=kc,decision=decision,status="recommended",p_mastery=state.p_mastery,ci95=[max(0,state.p_mastery-(1-state.confidence)/2),min(1,state.p_mastery+(1-state.confidence)/2)],depends_on=prerequisites.get(kc,[]),missing_prerequisites=missing,gate=gate.model_dump(mode="json"),forgetting_risk=risk,state_version=state.state_version,retention_version=r.state_version if r else None,evidence_refs=state.evidence_refs,reason=reason))
    priority = {"REVIEW":0,"DIAGNOSE":1,"REMEDIATE":2,"ADVANCE":3}
    nodes.sort(key=lambda n:(bool(n["missing_prerequisites"]),priority[n["decision"]],n["p_mastery"],n["kc_id"]))
    for i,n in enumerate(nodes):
        n["seq"] = i+1
        n["status"] = "current" if i==0 else "recommended"
    return dict(contract_version="PathRecommendation@2",path_id=f"path:{learner_id}",learner_id=learner_id,status="proposed",as_of=as_of.isoformat(),policy_version="path-v2",nodes=nodes,rationale="只依据掌握门控、遗忘风险与先修关系；采纳生成草案，签署后生效",source_signals=[dict(kind="state",ref=str(n["state_version"]),note=n["reason"]) for n in nodes])
