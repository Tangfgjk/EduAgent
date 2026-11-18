"""Visible impact is derived from real references and later local observations."""

import copy
from datetime import datetime, timedelta, timezone
import time

import pytest

from deeptutor.learning.diagnosis_models import DiagnosticContext
from deeptutor.learning.diagnostic_impact import import_impact
from deeptutor.learning.external_diagnoses import commit_import, revoke_import
from deeptutor.learning.models import LearningEvidence, QuizAttempt
from tests.capabilities.test_external_diagnoses import confirmed, request

pytest_plugins = ["tests.capabilities.test_external_diagnoses"]


def test_import_returns_coverage_and_concrete_next_action_without_mastery(store):
    result = commit_import(store, "p", confirmed(store))
    impact = result["impact"]
    assert impact["coverage"]["declared"] == 434
    assert impact["coverage"]["usable"] == 1
    assert impact["recommendation"]["objective_id"] == "k"
    assert impact["recommendation"]["probability"] == 0.8
    assert not impact["local_mastery_credit_from_import"]
    assert impact["followups"] == []


def test_read_is_pure_and_no_import_means_no_card(store):
    p = store.load("p")
    before = copy.deepcopy(p)
    assert import_impact(p) is None and p == before


@pytest.mark.parametrize("assistance", ["independent", "assisted", "unknown"])
def test_only_real_post_import_observations_update_card(store, assistance):
    commit_import(store, "p", confirmed(store))
    p = store.load("p")
    p.quiz_attempts.append(
        QuizAttempt(
            question_id="q",
            knowledge_point_id="k",
            is_correct=True,
            diagnostic_evidence_id="e",
            evidence_kind="explanation",
            timestamp=time.time(),
        )
    )
    p.learning_evidence.append(
        LearningEvidence(
            evidence_id="e",
            question_id="q",
            knowledge_point_id="k",
            result="correct",
            evidence_kind="explanation",
            timestamp=time.time(),
            diagnostic_context=DiagnosticContext(assistance=assistance),
        )
    )
    impact = import_impact(p)
    row = impact["followups"][0]
    assert row["assistance"] == assistance
    assert ("explanation" in row["achieved"]) == (assistance == "independent")
    assert row["interpretation"] == "local_observation_after_import_not_causal_effect"


def test_old_and_voided_answers_do_not_count_as_new_checks(store):
    commit_import(store, "p", confirmed(store))
    p = store.load("p")
    for i in range(2):
        p.quiz_attempts.append(
            QuizAttempt(
                question_id=str(i),
                knowledge_point_id="k",
                is_correct=True,
                diagnostic_evidence_id=str(i),
                voided=bool(i),
                timestamp=1 if not i else time.time(),
            )
        )
        p.learning_evidence.append(
            LearningEvidence(
                evidence_id=str(i),
                question_id=str(i),
                knowledge_point_id="k",
                result="correct",
                timestamp=1 if not i else time.time(),
            )
        )
    assert import_impact(p)["followups"] == []


def test_revoked_stale_and_scope_changed_imports_have_no_start_action(store):
    saved = commit_import(store, "p", confirmed(store))
    revoke_import(store, "p", saved["import_id"])
    assert import_impact(store.load("p"))["status"] == "revoked"
    past = (datetime.now(timezone.utc).date() - timedelta(days=100)).isoformat()
    commit_import(store, "p", confirmed(store, request(result_version="2", observed_on=past)))
    assert import_impact(store.load("p"))["status"] == "historical"
    p = store.load("p")
    p.modules[0].knowledge_points[0].name = "Changed"
    impact = import_impact(p)
    assert impact["status"] == "scope_changed" and impact["recommendation"] is None


def test_all_abilities_already_proven_do_not_create_unnecessary_probe(store):
    commit_import(store, "p", confirmed(store))
    p = store.load("p")
    for i, ability in enumerate(("explanation", "application")):
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
                diagnostic_context=DiagnosticContext(assistance="independent"),
            )
        )
    assert import_impact(p)["recommendation"] is None
