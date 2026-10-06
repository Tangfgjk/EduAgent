"""Local single-user learning workspace API with authorized evidence access."""
from datetime import timedelta
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.core.schema import QuestionItem, utcnow
from app.learning.assets import VersionedRef, load_catalog
from app.learning.diagnosis import DiagnosticObservation, next_task
from app.learning.metrics import observation_from_evidence, performance_gain
from app.learning.review_port import review_task
from app.learning.schema import LearningEvidence
from app.learning.service import ConsentDenied, EvidenceConflict, LearningService
from app.learning.verifier import verify_item

ROOT = Path(__file__).resolve().parents[2]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConsentIn(RequestModel):
    scopes: list[Literal["teaching", "research", "evolution"]]
    version: str = Field(min_length=1)
    source: str = Field(min_length=1)


class DiagnosisIn(RequestModel):
    target_kc_id: str = "MATH.G7.EQ.SOLVE"
    max_tasks: int = 8


class AssessmentIn(RequestModel):
    assessment_id: str
    assessment_version: str = "1.0.0"
    attempt_id: str = Field(min_length=1)
    answer: str = Field(max_length=1000)
    occurred_at: AwareDatetime
    hint_level: int = Field(default=0, ge=0, le=3)
    assistance_mode: Literal["none", "hint", "answer"] = "none"
    answer_exposed: bool = False
    self_report: Literal["answered", "skipped", "dont_know", "guessed"] = "answered"
    delivery_ref: str | None = Field(default=None, min_length=1, max_length=100)


class DeliveryIn(RequestModel):
    assessment_id: str
    assessment_version: str = "1.0.0"
    issuance_id: str = Field(min_length=1, max_length=200)
    occurred_at: AwareDatetime | None = None


class HintIn(RequestModel):
    delivery_ref: str
    assessment_id: str
    assessment_version: str = "1.0.0"
    level: int = Field(ge=1, le=2)
    occurred_at: AwareDatetime | None = None


class ReviewIn(AssessmentIn):
    task_id: str = Field(min_length=1)


class CorrectionIn(RequestModel):
    evidence_id: str
    correction_id: str = Field(min_length=1)
    status: Literal["passed", "failed", "partial", "unverifiable"]
    score: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)
    rubric: dict = Field(min_length=1)


def install_learning_routes(app: FastAPI, store, settings):
    service = LearningService(store)
    catalog = load_catalog(settings)
    from app.learning.assessment_delivery import AssessmentDelivery
    delivery = AssessmentDelivery(store)
    with store.lock:
        store.conn.execute("CREATE TABLE IF NOT EXISTS learning_api_receipts (learner_id TEXT,attempt_id TEXT,content_hash TEXT,payload TEXT,PRIMARY KEY(learner_id,attempt_id))")
        store.conn.commit()

    def local(learner_id):
        if learner_id != settings.learner_id:
            raise HTTPException(403, "Local single-user workspace only")

    def authorized(learner_id):
        local(learner_id)
        try:
            return service.evidences(learner_id)
        except ConsentDenied as exc:
            raise HTTPException(403, str(exc)) from exc

    def effective(evidences):
        replaced = {item.supersedes for item in evidences if item.supersedes}
        return [item for item in evidences if item.evidence_id not in replaced]

    def asset_for(asset_id, version):
        asset = next((item for item in catalog.assessments if item.ref.asset_id == asset_id and item.ref.version == version), None)
        if asset is None:
            raise HTTPException(404, "Unknown assessment asset version")
        return asset

    def public_asset(asset):
        return asset.model_dump(mode="json", exclude={"answer"})

    def kc_for(kc_id):
        refs = [item.ref for item in catalog.knowledge if item.ref.asset_id == kc_id]
        if len(refs) != 1:
            raise HTTPException(404, "Unknown or ambiguous KC version")
        return refs[0]

    def receipt(learner_id, key, fingerprint):
        row = store.conn.execute("SELECT content_hash,payload FROM learning_api_receipts WHERE learner_id=? AND attempt_id=?", (learner_id, key)).fetchone()
        if row:
            if row[0] != fingerprint:
                raise HTTPException(409, "attempt_id content conflict")
            return json.loads(row[1])
        return None

    def submit(learner_id, body, *, review=False):
        authorized(learner_id)
        if not settings.allow_simulated_time and body.occurred_at > utcnow() + timedelta(minutes=5):
            raise HTTPException(400, "Future learning events require explicit simulated-time mode")
        fingerprint = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        key = body.attempt_id
        with store.lock:
            old = receipt(learner_id, key, fingerprint)
            if old is not None:
                return old
            asset = asset_for(body.assessment_id, body.assessment_version)
            consent = service.consent(learner_id)
            try:
                delivery_details, hint_level, assistance_mode, answer_exposed = delivery.observation(
                    learner_id, body, asset, consent["version"], required=settings.assessment_require_ticket)
            except EvidenceConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            if asset.kind in {"recall", "explanation"}:
                raise HTTPException(400, "This assessment kind requires its configured verifier adapter")
            if review:
                try:
                    tasks = [review_task(state, body.occurred_at) for state in service.retentions(learner_id)]
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from exc
                task = next((task for task in tasks if task and task.task_id == body.task_id), None)
                if task is None:
                    raise HTTPException(409, "Review task is not due or was superseded")
                if asset.kind != "delayed" or [ref.asset_id for ref in asset.kc_refs] != [task.kc_id]:
                    raise HTTPException(400, "Review assessment must match the due task KC")
                if hint_level or assistance_mode != "none" or answer_exposed:
                    raise HTTPException(400, "Review requires an independent recall attempt")
            elif asset.kind == "delayed":
                raise HTTPException(400, "Delayed recall must use the due-review endpoint")
            item = QuestionItem(item_id=asset.ref.asset_id, kc_id=asset.kc_refs[0].asset_id,
                                difficulty=0.2, stem=asset.stem, answer=asset.answer)
            # Numeric demo tasks never send arbitrary identifiers to SymPy's expression parser.
            variable = re.escape(str(asset.answer.get("var", "x"))) if "expr" in asset.answer else ""
            if body.self_report != "answered" or not re.fullmatch(r"(?:[a-zA-Z]\s*=\s*)?[0-9." + variable + r"\s()+\-*/^]+", body.answer.strip()):
                from app.core.schema import Verdict
                verdict = Verdict(status="unverifiable", score=0, verifier_id="safe-numeric-v1")
            else:
                verdict = verify_item(body.answer, item)
            digest = hashlib.sha256(f"{learner_id}:{key}".encode()).hexdigest()
            evidence = LearningEvidence(
                evidence_id="assessment:" + digest, learner_id=learner_id, session_id="assessment-workspace",
                kc_refs=[ref.asset_id for ref in asset.kc_refs], attempt_id=key,
                artifact_ref="answer-sha256:" + hashlib.sha256(body.answer.encode()).hexdigest(),
                verdict_ref="verdict:" + digest, verdict_status=verdict.status,
                verifier_version=verdict.verifier_id, confidence=1 if verdict.status != "unverifiable" else 0,
                verifier_details=dict(answer=body.answer, rubric_ref=asset.rubric_ref.key,
                                      explanation=verdict.explainability, source="local-assessment-verifier",
                                      assessment_provenance=asset.provenance,
                                      calibration_status=asset.calibration_status,
                                      comparison_group=asset.comparison_group,
                                      diagnostic_response=body.self_report, **delivery_details),
                occurred_at=body.occurred_at, hint_level=hint_level, assistance_mode=assistance_mode,
                answer_exposed=answer_exposed, consent_scope=service.consent(learner_id)["scopes"],
                consent_version=service.consent(learner_id)["version"], authorization_source=service.consent(learner_id)["source"],
                assessment_id=asset.ref.asset_id, assessment_version=asset.ref.version,
                assessment_kind={"pretest": "pre", "posttest": "post", "delayed": "review"}.get(asset.kind, asset.kind),
                rubric_version=asset.rubric_ref.version, difficulty_band=asset.difficulty_band, score=verdict.score)
            def result_for(transition):
                return dict(evidence_id=evidence.evidence_id, verdict_status=verdict.status,
                            score=verdict.score, transition=transition, assessment=public_asset(asset))
            try:
                def delivery_hook(checkpoint):
                    if checkpoint == "receipt":
                        delivery.consume_in_transaction(learner_id, body, asset, consent["version"], evidence.evidence_id,
                            (delivery_details, hint_level, assistance_mode, answer_exposed))
                transition = service.consume(evidence, receipt=(key, fingerprint, result_for), fault=delivery_hook)
            except ConsentDenied as exc:
                raise HTTPException(403, str(exc)) from exc
            except EvidenceConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            return result_for(transition)

    @app.get("/learning")
    def learning_page():
        return FileResponse(ROOT / "web" / "learning.html")

    @app.get("/api/learning/config")
    def local_config():
        return dict(learner_id=settings.learner_id, mode="local_single_user", catalog_version=catalog.catalog_version,
                    local_auth_enabled=settings.local_auth_enabled, assessment_require_ticket=settings.assessment_require_ticket)

    @app.get("/api/learning/assets")
    def assets():
        return dict(catalog_version=catalog.catalog_version, knowledge=[item.model_dump(mode="json") for item in catalog.knowledge],
                    rubrics=[item.model_dump(mode="json") for item in catalog.rubrics], assessments=[
                        {k: v for k, v in public_asset(item).items() if k != "stem" or not settings.assessment_require_ticket}
                        for item in catalog.assessments])

    @app.get("/api/learning/consent/{learner_id}")
    def get_consent(learner_id: str):
        local(learner_id)
        return dict(mode="local_single_user", consent=service.consent(learner_id))

    @app.post("/api/learning/consent/{learner_id}")
    def set_consent(learner_id: str, body: ConsentIn):
        local(learner_id)
        with store.lock:
            existing = store.conn.execute("SELECT payload FROM consent_records WHERE learner_id=? AND version=?", (learner_id, body.version)).fetchone()
            if existing:
                previous = json.loads(existing[0])
                if previous["scopes"] == sorted(set(body.scopes)) and previous["source"] == body.source:
                    return previous
                raise HTTPException(409, "Consent version content conflict")
        try:
            return service.set_consent(learner_id, body.scopes, body.version, body.source, utcnow())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.delete("/api/learning/consent/{learner_id}")
    def revoke(learner_id: str):
        local(learner_id)
        return service.set_consent(learner_id, [], "withdrawal:" + uuid4().hex, "local-learner-withdrawal", utcnow())

    @app.get("/api/learning/state/{learner_id}")
    def state(learner_id: str):
        authorized(learner_id)
        return dict(mastery=[item.model_dump(mode="json") for item in service.masteries(learner_id)],
                    retention=[item.model_dump(mode="json") for item in service.retentions(learner_id)], transitions=service.transitions(learner_id))

    @app.get("/api/learning/plans/{learner_id}/workspace")
    def plan_workspace(learner_id: str):
        authorized(learner_id)
        from app.learning.plans import PlanConflict, PlanService
        plans = PlanService(store)
        with store.lock:
            contract = store.latest_contract(learner_id)
            if contract is None:
                return dict(contract=None, active=None, drafts=[], proposals=[])
            try:
                active = plans.active(learner_id, contract.goal_contract_id)
            except PlanConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            rows = store.conn.execute("SELECT payload FROM plan_versions WHERE goal_contract_id=? ORDER BY rowid DESC", (contract.goal_contract_id,)).fetchall()
            versions = [json.loads(row[0]) for row in rows]
            baseline = active.version_id if active else None
            drafts = [version for version in versions if version["status"] == "draft" and version.get("prior_version_id") == baseline]
            proposals = [version for version in versions if version["status"] == "proposed" and version.get("prior_version_id") == baseline]
            return dict(contract=contract.model_dump(mode="json"), active=active.model_dump(mode="json") if active else None,
                        drafts=drafts, proposals=proposals)

    @app.post("/api/learning/plans/{learner_id}/{version_id}/accept")
    def accept_saved_proposal(learner_id: str, version_id: str):
        authorized(learner_id)
        from app.learning.plans import PlanConflict, PlanService
        try:
            return PlanService(store).accept(learner_id, version_id, utcnow()).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except PlanConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/learning/evidence/{learner_id}")
    def evidence_list(learner_id: str):
        return [item.model_dump(mode="json") for item in authorized(learner_id)]

    @app.post("/api/learning/diagnosis/{learner_id}/next")
    def diagnose(learner_id: str, body: DiagnosisIn):
        if not 1 <= body.max_tasks <= 30:
            raise HTTPException(400, "Diagnosis task budget must be between 1 and 30")
        observations = []
        for evidence in effective(authorized(learner_id)):
            if evidence.assessment_kind == "diagnostic":
                observations.append(DiagnosticObservation(evidence_id=evidence.evidence_id, learner_id=learner_id,
                    event_seq=evidence.event_seq,
                    assessment_ref=VersionedRef(asset_id=evidence.assessment_id, version=evidence.assessment_version),
                    response=evidence.verifier_details["diagnostic_response"] if evidence.verifier_details.get("diagnostic_response") in {"skipped", "dont_know", "guessed"} else {"passed": "correct", "failed": "incorrect", "partial": "partial", "unverifiable": "unverifiable"}[evidence.verdict_status],
                    hint_level=evidence.hint_level, assistance_mode="none" if evidence.assistance_mode == "none" else "hint", answer_exposed=evidence.answer_exposed))
        try:
            result = next_task(catalog, (kc_for(body.target_kc_id),), tuple(observations), learner_id=learner_id, max_tasks=body.max_tasks)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        selected = public_asset(asset_for(result.assessment_ref.asset_id, result.assessment_ref.version)) if result.assessment_ref else None
        if selected and settings.assessment_require_ticket:
            selected.pop("stem", None)
        return dict(decision=result.model_dump(mode="json"), assessment=selected)

    def delivery_time(requested):
        if requested and not settings.allow_simulated_time:
            raise HTTPException(400, "Client-issued delivery time requires explicit simulation mode")
        return requested or utcnow()

    @app.post("/api/learning/assessment/{learner_id}/issue")
    def issue_assessment(learner_id: str, body: DeliveryIn):
        authorized(learner_id)
        asset = asset_for(body.assessment_id, body.assessment_version)
        try:
            with store.lock:
                result = delivery.issue(learner_id, asset, body.issuance_id, body.occurred_at,
                    delivery_time(body.occurred_at), service.consent(learner_id)["version"])
                return dict(**{k: v for k, v in result.items() if k != "assessment_sha256"}, assessment=public_asset(asset))
        except ConsentDenied as exc:
            raise HTTPException(403, str(exc)) from exc
        except EvidenceConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/learning/assessment/{learner_id}/hint")
    def assessment_hint(learner_id: str, body: HintIn):
        authorized(learner_id)
        try:
            return delivery.hint(learner_id, body.delivery_ref, asset_for(body.assessment_id, body.assessment_version),
                delivery_time(body.occurred_at), service.consent(learner_id)["version"], body.level)
        except ConsentDenied as exc:
            raise HTTPException(403, str(exc)) from exc
        except EvidenceConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/learning/assessment/{learner_id}/submit")
    def assessment_submit(learner_id: str, body: AssessmentIn):
        return submit(learner_id, body)

    @app.get("/api/learning/reviews/{learner_id}")
    def reviews(learner_id: str, as_of: AwareDatetime | None = None):
        authorized(learner_id)
        as_of = as_of or utcnow()
        try:
            tasks = [review_task(item, as_of) for item in service.retentions(learner_id)]
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return dict(as_of=as_of.isoformat(), tasks=[task.model_dump(mode="json") for task in tasks if task])

    @app.post("/api/learning/reviews/{learner_id}/submit")
    def review_submit(learner_id: str, body: ReviewIn):
        return submit(learner_id, body, review=True)

    @app.get("/api/learning/metrics/{learner_id}")
    def metrics(learner_id: str, kc_id: str = "MATH.G7.EQ.SOLVE", as_of: AwareDatetime | None = None, post_kind: str = "posttest"):
        if post_kind not in {"posttest", "transfer", "delayed"}:
            raise HTTPException(400, "Invalid comparison kind")
        ref = kc_for(kc_id)
        projected = [observation_from_evidence(item, catalog, ref) for item in effective(authorized(learner_id))]
        return performance_gain(tuple(item for item in projected if item), learner_id=learner_id,
                                kc_ref=ref, as_of=as_of or utcnow(), post_kind=post_kind).model_dump(mode="json")

    @app.post("/api/learning/teacher/{learner_id}/corrections")
    def correct(learner_id: str, body: CorrectionIn, x_teacher_token: str = Header(default="")):
        local(learner_id)
        if not settings.local_teacher_token or not hmac.compare_digest(x_teacher_token.encode(), settings.local_teacher_token.encode()):
            raise HTTPException(403, "Authorized local teacher token required")
        evidences = authorized(learner_id)
        original = next((item for item in evidences if item.evidence_id == body.evidence_id), None)
        if original is None:
            raise HTTPException(404, "Unknown learner evidence")
        fingerprint = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        key = "teacher:" + body.correction_id
        with store.lock:
            old = receipt(learner_id, key, fingerprint)
            if old is not None:
                return old
            if any(item.supersedes == original.evidence_id for item in evidences):
                raise HTTPException(409, "Correct the current evidence revision, not a superseded original")
            correction = original.model_copy(update={"evidence_id": f"correction:{learner_id}:{body.correction_id}",
                "supersedes": original.evidence_id, "verdict_ref": key, "verdict_status": body.status,
                "score": body.score, "confidence": body.confidence, "verifier_version": "teacher-rubric-v1",
                "verifier_details": dict(rubric=body.rubric, rubric_ref=f"teacher-review@{original.rubric_version}",
                                         reason=body.reason, source="authorized-local-teacher",
                                         confidence=body.confidence, original_verifier_details=original.verifier_details),
                "event_seq": None, "ingested_at": None})
            audit = dict(kind="teacher_correction", evidence_id=correction.evidence_id, supersedes=original.evidence_id,
                         rubric=body.rubric, rubric_version=original.rubric_version, reason=body.reason,
                         confidence=body.confidence, verifier_version="teacher-rubric-v1", at=utcnow().isoformat())
            def result_for(transition):
                return dict(evidence_id=correction.evidence_id, transition=transition, audit=audit)
            def audit_hook(checkpoint):
                if checkpoint == "receipt":
                    store.conn.execute("INSERT INTO learning_audit(learner_id,payload) VALUES (?,?)", (learner_id, json.dumps(audit)))
            try:
                transition = service.consume(correction, receipt=(key, fingerprint, result_for), fault=audit_hook)
            except EvidenceConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            return result_for(transition)
