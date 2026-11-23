"""Existing results are scoped, idempotent references, never local mastery."""

from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
import pytest

from deeptutor.learning.external_diagnoses import (
    ExternalImportConflict,
    commit_import,
    external_references,
    list_imports,
    preview_import,
    revoke_import,
)
from deeptutor.learning.external_diagnosis_models import (
    ExternalCommitRequest,
    ExternalDiagnosisDocument,
    ExternalPreviewRequest,
)
from deeptutor.learning.models import KnowledgePoint, LearningModule, LearningProgress
from deeptutor.learning.policy import is_assessed_mastered, next_objective
from deeptutor.learning.storage import LearningStore
from deeptutor.learning.wenjin import teaching_decision


def document(**updates):
    value = dict(
        schema_version=1,
        source_system="school-test",
        result_id="exam-01",
        result_version="1",
        student_ref="000123",
        subject="math",
        scope="linear equations",
        observed_on=datetime.now(timezone.utc).date().isoformat(),
        model_version="KT-test",
        taxonomy_version="math-1",
        mapping_version="map-1",
        zero_encoding="zero_is_untested",
        total_knowledge_count=434,
        estimates=[
            dict(
                knowledge_id="external-k",
                knowledge_name="Equation",
                probability=0.8,
                measurement_status="estimated",
            )
        ],
    )
    return {**value, **updates}


@pytest.fixture
def store(tmp_path):
    store = LearningStore(tmp_path)
    store.save(
        LearningProgress(
            book_id="p",
            wenjin_enabled=True,
            modules=[
                LearningModule(
                    id="m",
                    name="Chapter",
                    order=0,
                    knowledge_points=[
                        KnowledgePoint(id="k", name="Equation", module_id="m", type="concept")
                    ],
                )
            ],
        )
    )
    return store


def request(**updates):
    return ExternalPreviewRequest(
        document=document(**updates),
        links=[dict(knowledge_id="external-k", objective_id="k", relation="partial")],
    )


def confirmed(store, req=None):
    req = req or request()
    preview = preview_import(store, "p", req)
    return ExternalCommitRequest(
        **req.model_dump(mode="json"),
        outline_digest=preview["outline_digest"],
        preview_digest=preview["preview_digest"],
        belongs_to_me=True,
        mapping_confirmed=True,
    )


def test_preview_is_read_only_and_does_not_automap_same_names(store):
    before = store.load("p").model_dump()
    preview = preview_import(store, "p", ExternalPreviewRequest(document=document()))
    assert preview["rows"][0]["suggestions"] == ["k"]
    assert preview["coverage"]["mapped"] == 0 and not preview["can_import"]
    assert store.load("p").model_dump() == before


def test_commit_reference_is_durable_and_duplicate_does_not_bump_revision(store):
    body = confirmed(store)
    first = commit_import(store, "p", body)
    revision = store.load("p").version
    second = commit_import(store, "p", body)
    assert not first["duplicate"] and second["duplicate"]
    assert store.load("p").version == revision
    p = store.load("p")
    assert len(p.external_diagnoses) == 1 and not p.quiz_attempts and not p.mastery_levels
    assert external_references(p)[0]["provenance"] == "self_uploaded_unverified"
    assert (
        teaching_decision(p, p.modules[0].knowledge_points[0])["action"]
        == "verify_external_reference"
    )
    assert next_objective(p).action == "probe"
    assert not is_assessed_mastered(p, p.modules[0].knowledge_points[0])


def test_zero_conventions_and_unmeasured_are_distinct(store):
    entries = [
        dict(
            knowledge_id="external-k",
            knowledge_name="Equation",
            probability=0,
            measurement_status="estimated",
        )
    ]
    legacy = preview_import(store, "p", request(estimates=entries))
    assert legacy["rows"][0]["probability"] is None
    valid = preview_import(
        store, "p", request(estimates=entries, zero_encoding="valid_probability")
    )
    assert valid["rows"][0]["probability"] == 0
    assert valid["rows"][0]["measurement_status"] == "estimated"


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf"), True, ".8"])
def test_invalid_probability_rejected(value):
    entries = [dict(knowledge_id="x", knowledge_name="X", probability=value)]
    with pytest.raises(ValidationError):
        ExternalDiagnosisDocument(**document(estimates=entries))


def test_missing_versions_numeric_student_and_duplicate_entries_rejected():
    for field in ("model_version", "mapping_version", "taxonomy_version"):
        value = document()
        value.pop(field)
        with pytest.raises(ValidationError):
            ExternalDiagnosisDocument(**value)
    with pytest.raises(ValidationError):
        ExternalDiagnosisDocument(**document(student_ref=123))
    value = document()
    value["estimates"] *= 2
    with pytest.raises(ValidationError):
        ExternalDiagnosisDocument(**value)
    with pytest.raises(ValidationError):
        ExternalDiagnosisDocument(
            **document(
                estimates=[
                    dict(
                        knowledge_id="x",
                        knowledge_name="X",
                        measurement_status="unmeasured",
                        probability=0,
                    )
                ]
            )
        )


def test_explicit_identity_and_mapping_confirmation_required(store):
    body = confirmed(store)
    for key in ("belongs_to_me", "mapping_confirmed"):
        with pytest.raises(ValueError):
            commit_import(store, "p", body.model_copy(update={key: False}))
    assert not store.load("p").external_diagnoses


def test_mapping_is_checked_against_current_project(store):
    req = request()
    req.links[0].objective_id = "foreign"
    with pytest.raises(ValueError):
        preview_import(store, "p", req)


def test_outline_changes_between_preview_and_commit_rejected(store):
    body = confirmed(store)

    def edit(tx):
        tx.progress.modules[0].knowledge_points[0].name = "Changed"
        tx.touch()

    store.mutate("p", edit)
    with pytest.raises(ExternalImportConflict):
        commit_import(store, "p", body)
    assert not store.load("p").external_diagnoses


def test_payload_changed_after_preview_rejected(store):
    body = confirmed(store)
    body.document.estimates[0].probability = 0.1
    with pytest.raises(ExternalImportConflict):
        commit_import(store, "p", body)


def test_same_result_version_with_different_values_does_not_overwrite(store):
    commit_import(store, "p", confirmed(store))
    req = request()
    req.document.estimates[0].probability = 0.2
    with pytest.raises(ExternalImportConflict):
        preview_import(store, "p", req)
    assert store.load("p").external_diagnoses[0].document.estimates[0].probability == 0.8


def test_binding_rejects_other_student_reference(store):
    commit_import(store, "p", confirmed(store))
    body = confirmed(store, request(student_ref="other", result_id="another"))
    with pytest.raises(ValueError, match="student reference"):
        commit_import(store, "p", body)


def test_stale_results_only_history_and_outline_change_disables_reference(store):
    past = (datetime.now(timezone.utc).date() - timedelta(days=100)).isoformat()
    commit_import(store, "p", confirmed(store, request(observed_on=past)))
    assert external_references(store.load("p"))[0]["stale"]
    assert (
        teaching_decision(store.load("p"), store.load("p").modules[0].knowledge_points[0])["action"]
        == "probe"
    )
    commit_import(store, "p", confirmed(store, request(result_version="2")))

    def edit(tx):
        tx.progress.modules[0].knowledge_points[0].type = "procedure"
        tx.touch()

    # Use a validated enum rather than a raw string in persisted current outline.
    from deeptutor.learning.models import KnowledgeType

    def change(tx):
        tx.progress.modules[0].knowledge_points[0].type = KnowledgeType.PROCEDURE
        tx.touch()

    store.mutate("p", change)
    assert not any(r["active_reference"] for r in external_references(store.load("p")))


def test_revoke_is_idempotent_and_never_reactivates_on_reimport(store):
    body = confirmed(store)
    saved = commit_import(store, "p", body)
    revoke_import(store, "p", saved["import_id"])
    revision = store.load("p").version
    revoke_import(store, "p", saved["import_id"])
    assert revision == store.load("p").version
    assert not external_references(store.load("p"))
    assert len(list_imports(store, "p")["imports"]) == 1
    with pytest.raises(ExternalImportConflict):
        commit_import(store, "p", body)


def test_external_reference_does_not_enter_local_observations(store):
    from deeptutor.learning.diagnosis import project_facts

    commit_import(store, "p", confirmed(store))
    facts = project_facts(store.load("p"))
    assert not facts.observations and len(facts.external_references) == 1
    assert facts.objectives[0]["measurement_status"] == "unmeasured"


def test_revoke_allows_correcting_self_declared_student_binding(store):
    old = commit_import(store, "p", confirmed(store))
    revoke_import(store, "p", old["import_id"])
    corrected = commit_import(store, "p", confirmed(store, request(student_ref="corrected-id")))
    assert corrected["import_id"] != old["import_id"]
    assert len(store.load("p").external_diagnoses) == 2


def test_new_local_success_is_not_overridden_by_external_low_probability(store):
    from deeptutor.learning import goal_contract as goals
    from deeptutor.learning.diagnosis import assessment_context
    from deeptutor.learning.models import LearningEvidence, PendingQuestion, QuizAttempt

    goals.confirm(
        store,
        "p",
        revision=store.load("p").version,
        level="apply",
        summary="Apply equations independently",
        request_id="local-goal-confirmation",
    )
    p = store.load("p")

    for i, ability in enumerate(("explanation", "application")):
        q = PendingQuestion(
            question_id=str(i),
            knowledge_point_id="k",
            prompt="new task " + str(i),
            expected_answer="2",
        )
        goals.bind_task(p, q)
        p.quiz_attempts.append(
            QuizAttempt(
                question_id=str(i),
                knowledge_point_id="k",
                is_correct=True,
                diagnostic_evidence_id=str(i),
                evidence_kind=ability,
            )
        )
        p.learning_evidence.append(
            LearningEvidence(
                evidence_id=str(i),
                question_id=str(i),
                knowledge_point_id="k",
                result="correct",
                evidence_kind=ability,
                diagnostic_context=assessment_context(p, q, "2", "short"),
            )
        )
    store.save(p)
    doc = request()
    doc.document.estimates[0].probability = 0.1
    commit_import(store, "p", confirmed(store, doc))
    p = store.load("p")
    assert is_assessed_mastered(p, p.modules[0].knowledge_points[0])
    assert teaching_decision(p, p.modules[0].knowledge_points[0])["action"] == "reflect"
