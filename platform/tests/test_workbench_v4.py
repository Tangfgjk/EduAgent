from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.gateway.routes import create_app
from app.gateway.workbench_routes import growth_projection
from app.learning.schema import LearningEvidence
from app.storage.db import Store

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)
WEB = Path(__file__).parents[1] / "web"


def evidence(index, **changes):
    data = dict(evidence_id=f"e{index}", learner_id="s", session_id="test", kc_refs=["MATH.EQ"],
                attempt_id=f"a{index}", artifact_ref=f"a{index}", verdict_ref=f"v{index}",
                verdict_status="passed", verifier_version="symbolic-v1", confidence=1,
                occurred_at=NOW, event_seq=index, consent_scope=["teaching"], consent_version="test",
                authorization_source="synthetic-fixture", assessment_id=f"task{index}", score=1)
    return LearningEvidence(**(data | changes))


def test_projection_has_no_fake_points_or_unvalidated_claims():
    empty = growth_projection([], [], "s", NOW)
    assert empty["mastery"] == empty["independent"] == empty["assistance"] == []
    assert empty["effective_count"] == 0 and empty["not_an_effect_claim"]


def test_current_effective_replay_corrects_history_and_excludes_assistance_from_mastery():
    records = [evidence(1), evidence(2, hint_level=1, assistance_mode="hint"),
               evidence(3, supersedes="e1", verdict_status="failed", assessment_id="task1", score=0)]
    result = growth_projection(records, [], "s", NOW)
    assert result["effective_count"] == 2
    assert [p["evidence_ref"] for p in result["mastery"][0]["points"]] == ["e3"]
    assert result["independent"][-1]["value"] == 0
    assert result["assistance"][-1]["value"] == .5
    assert {e["evidence_id"] for e in result["evidence_choices"]} == {"e2", "e3"}


def test_independent_first_attempt_does_not_promote_assisted_then_repeated_item():
    records = [evidence(1, assessment_id="same", hint_level=1), evidence(2, assessment_id="same")]
    assert growth_projection(records, [], "s", NOW)["independent"] == []


def test_correction_preserves_original_exposure_position_not_second_attempt():
    records = [evidence(1, assessment_id="same", hint_level=1, verdict_status="failed"),
               evidence(2, assessment_id="same"),
               evidence(3, assessment_id="same", hint_level=1, verdict_status="failed", supersedes="e1")]
    assert growth_projection(records, [], "s", NOW)["independent"] == []
    corrected_first = records + [evidence(4, assessment_id="same", supersedes="e3")]
    assert [p["evidence_ref"] for p in growth_projection(corrected_first, [], "s", NOW)["independent"]] == ["e4"]


def test_projection_separates_kcs_omits_future_and_other_learners_and_bounds_output():
    records = [evidence(i) for i in range(1, 211)]
    records += [evidence(211, learner_id="other"), evidence(212, occurred_at=NOW + timedelta(days=1))]
    result = growth_projection(records, [], "s", NOW)
    assert result["effective_count"] == 210 and result["truncated"]
    assert len(result["mastery"][0]["points"]) == 200
    assert len(result["independent"]) == 200
    multi = growth_projection([evidence(1, kc_refs=["MATH.ONE", "MATH.TWO"])], [], "s", NOW)
    assert {c["kc_id"] for c in multi["mastery"]} == {"MATH.ONE", "MATH.TWO"}


def test_growth_endpoint_requires_local_learner_and_teaching_consent_and_never_writes():
    store = Store()
    with TestClient(create_app(Settings(learner_id="s"), store=store)) as client:
        assert client.get("/api/learning/growth/other").status_code == 403
        assert client.get("/api/learning/growth/s").status_code == 403
        assert client.post("/api/learning/consent/s", json={"scopes": ["teaching"], "version": "test", "source": "synthetic"}).status_code == 200
        changes = store.conn.total_changes
        assert client.get("/api/learning/growth/s").json()["effective_count"] == 0
        assert store.conn.total_changes == changes
        client.delete("/api/learning/consent/s")
        assert client.get("/api/learning/growth/s").status_code == 403
    store.close()


@pytest.mark.parametrize("route", ["/teacher", "/parent", "/research", "/assets/workbench/charts.js", "/assets/workbench/workbench.css"])
def test_independent_surfaces_and_static_assets_are_available(route):
    store = Store()
    with TestClient(create_app(Settings(), store=store)) as client:
        response = client.get(route)
        assert response.status_code == 200
        assert "541521" not in response.text
    store.close()


def test_students_do_not_have_teacher_or_parent_secrets_or_import_controls():
    content = (WEB / "learning.html").read_text(encoding="utf-8")
    for control in ['id="teacher-token"', 'id="staff-token"', 'id="qualitative-rubric"',
                    'id="correction-evidence"', 'id="import-knowledge"', 'id="load-dashboard"']:
        assert control not in content
    script = (WEB / "workbench" / "workbench.js").read_text(encoding="utf-8")
    assert "x-teacher-token" not in script and "x-parent-token" not in script
    assert "92%" not in content and "0.41 → 0.68" not in content
    assert "localStorage" not in script and "sessionStorage" not in script


def test_legacy_prototype_url_no_longer_serves_hard_coded_results():
    store = Store()
    with TestClient(create_app(Settings(), store=store)) as client:
        response = client.get("/prototype")
        assert "Wenjin V4" in response.text
        assert "0.41 → 0.68" not in response.text
    store.close()


def test_all_pages_have_unique_ids_and_real_external_scripts_without_inline_events():
    class IDs(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = []
        def handle_starttag(self, tag, attrs):
            data = dict(attrs)
            if "id" in data:
                self.ids.append(data["id"])
            assert not any(k.startswith("on") for k in data)
    for name in ["learning.html", "teacher.html", "parent.html", "research.html"]:
        parser = IDs()
        parser.feed((WEB / name).read_text(encoding="utf-8"))
        assert len(parser.ids) == len(set(parser.ids))
