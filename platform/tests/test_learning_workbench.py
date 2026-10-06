"""Structural UI contracts for governed data flows, without duplicating CSS."""
from html.parser import HTMLParser
from pathlib import Path


PAGE = Path(__file__).parents[1] / "web" / "learning.html"


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.by_id = {}
        self.ids = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if "id" in attrs:
            self.ids.append(attrs["id"])
            self.by_id[attrs["id"]] = (tag, attrs)


def test_workbench_tabs_have_unique_ids_linked_panels_and_roving_tabindex():
    document = Elements()
    document.feed(PAGE.read_text(encoding="utf-8"))
    assert len(document.ids) == len(set(document.ids))
    tabs = [(key, attrs) for key, (_, attrs) in document.by_id.items() if attrs.get("role") == "tab"]
    assert len(tabs) == 6
    assert sum(attrs.get("tabindex") == "0" for _, attrs in tabs) == 1
    for key, attrs in tabs:
        panel = document.by_id[attrs["aria-controls"]][1]
        assert panel["role"] == "tabpanel"
        assert panel["aria-labelledby"] == key


def test_workbench_governed_features_have_controls_and_real_endpoint_routes():
    document = Elements()
    content = PAGE.read_text(encoding="utf-8")
    document.feed(content)
    for control in ("saved-plan", "restore-plans", "development-split", "refresh-development", "rebuild-memory",
                    "appeal-evidence", "appeal-reason", "submit-appeal", "recovery-result", "retry-recovery"):
        assert control in document.by_id
    for route in ("/api/learning/plans/", "/workspace", "/api/learning/metrics/development/",
                  "/api/learning/memory/", "/api/learning/appeals/", "/api/learning/recovery/"):
        assert route in content
    assert document.by_id["teacher-token"][1]["type"] == "password"
    assert "541521" not in content


def test_recovery_and_development_views_are_source_linked_and_not_auto_signed():
    content = PAGE.read_text(encoding="utf-8")
    recovery = content.split("async function runRecovery()", 1)[1].split("$('retry-recovery')", 1)[0]
    assert "/run" in recovery and "restorePlanWorkspace" in recovery
    assert "/sign" not in recovery and "/accept" not in recovery
    assert "sourceDetails(result.source_refs)" in content
    assert "privacyEpoch++" in content and "request.abort()" in content
    assert "recoveryJob=null;appealSubmission=null" in content


def test_pending_correction_jobs_are_restored_not_recreated_or_auto_run():
    content = PAGE.read_text(encoding="utf-8")
    correction = content.split("$('teacher-correct').onclick", 1)[1].split("$('appeal-evidence')", 1)[0]
    assert "loadRecoveryQueue(result.evidence_id)" in correction
    assert "await runRecovery()" not in correction
    assert "{correction_id:" not in correction
    assert "'/reconcile'" in content
    assert "await loadRecoveryQueue()" in content


def test_collaboration_views_mark_candidates_and_use_governed_endpoints():
    document = Elements()
    content = PAGE.read_text(encoding="utf-8")
    document.feed(content)
    for control in ("knowledge-query", "search-knowledge", "import-knowledge", "load-strategies",
                    "collaboration-evidence", "role-review", "roundtable-review", "teach-student"):
        assert control in document.by_id
    for route in ("/api/knowledge/search", "/api/knowledge/import", "/api/strategies?kc_id=",
                  "/api/roles/", "/api/roundtable/review", "/api/teach-student/lesson"):
        assert route in content
    assert "appendCandidateReport" in content and "未直接写入学习状态或签署计划" in content
    assert "external_model_smoke" not in content


def test_projects_sharing_qualitative_and_local_auth_have_real_controls():
    document = Elements()
    content = PAGE.read_text(encoding="utf-8")
    document.feed(content)
    for control in ("project-title", "add-project-task", "project-evidence-refs", "new-project", "save-project",
                    "share-audience", "save-sharing", "dashboard-audience", "dashboard-token", "load-dashboard",
                    "qualitative-rubric", "rubric-dimensions", "read-artifact", "qualitative-review",
                    "session-recovery-id", "restore-session", "logout"):
        assert control in document.by_id
    for route in ("/api/learning/projects/", "/api/learning/sharing/", "/api/learning/dashboard/",
                  "/api/learning/qualitative/", "/api/auth/logout"):
        assert route in content
    assert document.by_id["dashboard-token"][1]["type"] == "password"
    assert "expected_revision:" in content
    assert "response.status===401" in content and "location.assign('/login')" in content
    assert "localStorage" not in content and "sessionStorage" not in content


def test_revoke_clears_new_private_extension_data_and_inputs():
    content = PAGE.read_text(encoding="utf-8")
    clear = content.split("function clearLearningView()", 1)[1].split("$('withdraw').onclick", 1)[0]
    for control in ("project-evidence-refs", "dashboard-token", "artifact-result", "collaboration-work",
                    "collaboration-evidence", "teach-results", "session-recovery-id"):
        assert control in clear
    assert "projectEdit=null;projectTasks=[]" in clear
    assert "teacherCorrectionSubmission=null;qualitativeSubmission=null" in clear


def test_candidate_actions_use_parameter_text_not_json_body_fallback():
    content = PAGE.read_text(encoding="utf-8")
    assert "action.params?.text||action.params?.stem||action.params?.question" in content
    assert "JSON.stringify(action)" not in content
    report = content.split("function appendCandidateReport", 1)[1].split("$('role-review')", 1)[0]
    assert "candidateActionText(action)" in report
    assert "actions:report.actions" in report
