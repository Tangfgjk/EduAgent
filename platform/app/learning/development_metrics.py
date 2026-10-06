"""Source-backed cognitive and autonomy behavior measures without causal or ranking claims."""
from collections import Counter
from datetime import datetime

LEVELS = ("remember","understand","apply","analyze","evaluate","create")


def cognitive_change(evidence, *, learner_id, as_of, split_at, min_samples=2):
    if min_samples < 2 or any(t.tzinfo is None for t in (as_of,split_at)) or split_at >= as_of:
        raise ValueError("Valid aware ordered windows and two independent samples required")
    samples = {}
    for e in sorted(evidence,key=lambda x:(x.occurred_at,x.evidence_id)):
        if e.learner_id != learner_id or e.occurred_at > as_of or e.hint_level or e.assistance_mode != "none" or e.answer_exposed or e.verdict_status != "passed":
            continue
        level = e.verifier_details.get("taxonomy_level")
        rubric = e.verifier_details.get("bloom_rubric_version")
        group = e.verifier_details.get("comparison_group")
        # A task's Bloom label is not an assessment of the student's demonstrated level.
        if level not in LEVELS or not rubric or not group or e.difficulty_band=="unknown" or e.confidence is None or e.confidence < .7:
            continue
        provenance=e.verifier_details.get("assessment_provenance", "unknown")
        if provenance=="unknown": continue
        source_class="synthetic" if "synthetic" in provenance or "demo" in provenance else "curated"
        comparison=(tuple(sorted(e.kc_refs)),e.difficulty_band,group,rubric,source_class)
        phase = "before" if e.occurred_at < split_at else "after"
        samples.setdefault((phase,e.assessment_id), (e,level,comparison))
    before = [v for (p,_),v in samples.items() if p=="before"]
    after = [v for (p,_),v in samples.items() if p=="after"]
    result = dict(metric_version="bloom-window-v1",status="insufficient_data",as_of=as_of.isoformat(),split_at=split_at.isoformat(),
        before_count=len(before),after_count=len(after),evidence_refs=[v[0].evidence_id for v in before+after],
        uncertainty_method="descriptive_only", interpretation="Window estimates; no permanent ability or causal claim")
    if len(before)<min_samples or len(after)<min_samples:
        return result
    comparisons={v[2] for v in before+after}
    if len(comparisons)!=1 or {v[0].assessment_id for v in before}&{v[0].assessment_id for v in after}:
        result["status"]="incomparable"
        return result
    comparison=comparisons.pop()
    result.update(status="comparable",rubric_version=comparison[3],kc_refs=list(comparison[0]),
        difficulty_band=comparison[1],comparison_group=comparison[2],data_source_class=comparison[4],
        not_an_effect_claim=True,
        before_distribution=dict(Counter(v[1] for v in before)),after_distribution=dict(Counter(v[1] for v in after)))
    return result


def autonomy_behaviors(evidence, events, *, learner_id, as_of):
    eligible = [e for e in evidence if e.learner_id==learner_id and e.occurred_at<=as_of]
    observed = [e for e in events if e["learner_pseudo_id"]==learner_id and datetime.fromisoformat(e["ts"])<=as_of]
    independent = [e for e in eligible if e.assistance_mode=="none" and not e.hint_level and not e.answer_exposed and e.verdict_status in {"passed","failed"}]
    counts=Counter(e["observation"]["kind"] for e in observed if e.get("actor",{}).get("kind")=="student")
    return dict(metric_version="autonomy-behavior-v1",status="observed" if eligible or observed else "insufficient_data",
        as_of=as_of.isoformat(),attempt_count=len(eligible),independent_attempt_count=len(independent),
        independent_success_count=sum(e.verdict_status=="passed" for e in independent),
        assisted_attempt_count=sum(e.assistance_mode!="none" or e.hint_level>0 or e.answer_exposed for e in eligible),
        help_request_count=counts["help_seeking"],reflection_count=counts["reflection"],revision_count=counts["revision"],
        self_check_count=sum(e["observation"].get("payload",{}).get("self_check") is True for e in observed),
        evidence_refs=[e.evidence_id for e in eligible],event_refs=[e["event_id"] for e in observed],
        composite=None,interpretation="Behavior decomposition only; fewer help requests do not imply greater autonomy; no ranking")
