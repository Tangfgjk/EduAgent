"""V4 presentation projections; never own or mutate the learning state."""
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.schema import utcnow
from app.learning.mastery_port import observation_skip_reason, update_mastery
from app.learning.schema import MasteryState
from app.learning.service import ConsentDenied, LearningService

WEB = Path(__file__).resolve().parents[2] / "web"


def growth_projection(evidence, current, learner_id, as_of):
    """Linear current-effective-set projection, not stored historic mastery.

    Corrections restate the curve after removing superseded facts. First-item
    success and assistance are descriptive, not validated effect measures.
    Processing event order is retained; future-dated facts are excluded.
    """
    selected = [e for e in evidence if e.learner_id == learner_id and e.occurred_at <= as_of]
    replaced = {e.supersedes for e in selected if e.supersedes}
    by_id = {e.evidence_id: e for e in selected}
    first_exposure = {}
    for item in selected:
        if item.supersedes:
            continue
        key = (item.assessment_id, item.assessment_version)
        first_exposure[key] = min(first_exposure.get(key, item.event_seq), item.event_seq)

    def exposure_sequence(item):
        seen = set()
        while item.supersedes and item.supersedes in by_id:
            if item.evidence_id in seen:
                raise ValueError("Invalid cyclic correction lineage")
            seen.add(item.evidence_id)
            item = by_id[item.supersedes]
        return item.event_seq
    selected = sorted((e for e in selected if e.evidence_id not in replaced), key=lambda e: e.event_seq)
    states, curves, independent, assistance = {}, {}, [], []
    independent_total = independent_passed = assisted_total = 0
    for index, item in enumerate(selected, 1):
        point = dict(index=index, at=item.occurred_at.isoformat(), evidence_ref=item.evidence_id)
        assisted_total += bool(item.hint_level or item.assistance_mode != "none" or item.answer_exposed)
        assistance.append(dict(**point, value=assisted_total / index))
        eligibility = observation_skip_reason(MasteryState(learner_id=learner_id, kc_id=item.kc_refs[0]), item)
        key = (item.assessment_id, item.assessment_version)
        # First effective exposure counts even if assisted; later independent
        # repetitions must not be mislabelled independent first attempts.
        if exposure_sequence(item) == first_exposure.get(key) and eligibility is None:
            independent_total += 1
            independent_passed += item.verdict_status == "passed"
            independent.append(dict(**point, value=independent_passed / independent_total))
        for kc in item.kc_refs:
            prior = states.setdefault(kc, MasteryState(learner_id=learner_id, kc_id=kc))
            states[kc], reason = update_mastery(prior, item)
            if reason is None:
                curves.setdefault(kc, []).append(dict(**point, value=states[kc].p_mastery))
    return dict(projection_version="effective-evidence-growth-v1", effective_count=len(selected),
                truncated=any(len(c) > 200 for c in [independent, assistance, *curves.values()]),
                mastery=[dict(kc_id=kc, points=points[-200:]) for kc, points in curves.items()],
                current_mastery=[m.model_dump(mode="json") for m in current],
                independent=independent[-200:], assistance=assistance[-200:],
                evidence_choices=[dict(evidence_id=e.evidence_id, assessment_id=e.assessment_id,
                                       verdict_status=e.verdict_status, occurred_at=e.occurred_at.isoformat())
                                  for e in selected[-200:]], not_an_effect_claim=True)


def install_workbench_routes(app, store, settings):
    app.mount("/assets/workbench", StaticFiles(directory=WEB / "workbench"), name="workbench-assets")
    service = LearningService(store)

    @app.get("/teacher", include_in_schema=False)
    @app.get("/parent", include_in_schema=False)
    @app.get("/research", include_in_schema=False)
    def staff_page(request: Request):
        return FileResponse(WEB / (request.url.path[1:] + ".html"), headers={"Cache-Control": "no-store"})

    @app.get("/api/learning/growth/{learner_id}")
    def growth(learner_id: str):
        if learner_id != settings.learner_id:
            raise HTTPException(403, "Local single-user workspace only")
        try:
            with store.lock:
                evidence = service.evidences(learner_id)
                current = service.masteries(learner_id)
                return growth_projection(evidence, current, learner_id, utcnow())
        except ConsentDenied as exc:
            raise HTTPException(403, str(exc)) from exc
