"""Frozen adapter checks for providers, source grounding and derived agents."""
import pytest
from pypdf import PdfWriter

from app.agents.derived import DerivedRunner, RoleBudget
from app.core.actions import ActionType
from app.core.rules import ActionGovernor, GovernorContext
from app.core.schema import MentalStateSnapshot
from app.learning.retrieval import LocalRetrieval, ParseError, RetrievalContext
from app.llm.client import FakeLLM, LLMError
from app.llm.registry import ProviderConfig, ProviderRegistry


def context(**changes):
    values = dict(snapshot=MentalStateSnapshot(learner_id="learner"), ladder_pos=0,
                  hints_used=0, hint_budget=3, frustration_streak=0)
    values.update(changes)
    return GovernorContext(**values)


def test_fake_provider_generation_has_provenance():
    registry = ProviderRegistry()
    registry.register(ProviderConfig(provider_id="offline", kind="fake", model="fixture-v1"),
                      client=FakeLLM(["hello"]))
    result = registry.generate("offline", [{"role": "user", "content": "hi"}],
                               governor=ActionGovernor(), context=context(), request_id="req-1")
    assert result.text == "hello"
    assert result.provenance["request_id"] == "req-1"
    assert result.provenance["model"] == "fixture-v1"
    assert result.provenance["provider_id"] == "offline"


def test_provider_config_rejects_secret_and_invalid_timeout():
    with pytest.raises(ValueError):
        ProviderConfig(provider_id="p", model="m", api_key="secret")
    with pytest.raises(ValueError):
        ProviderConfig(provider_id="p", model="m", timeout_seconds=0)


def test_provider_uses_environment_secret_without_recording_it(monkeypatch):
    monkeypatch.setenv("EDU_TEST_KEY", "secret-test")
    registry = ProviderRegistry()
    config = ProviderConfig(provider_id="configured", kind="openai_compatible", model="fixture",
                            base_url="https://example.com/v1", api_key_env="EDU_TEST_KEY",
                            timeout_seconds=2)
    registry.register(config)
    client = registry.client("configured")
    assert client._client.timeout.read == 2
    assert "secret-test" not in str(registry.provenance("configured", "r"))
    client._client.close()


def test_provider_missing_secret_explicit_failure(monkeypatch):
    monkeypatch.delenv("EDU_MISSING_KEY", raising=False)
    registry = ProviderRegistry()
    registry.register(ProviderConfig(provider_id="p", kind="openai_compatible", model="m",
                                    base_url="https://example.com/v1", api_key_env="EDU_MISSING_KEY"))
    with pytest.raises(LLMError, match="EDU_MISSING_KEY"):
        registry.client("p")


def test_provider_fallback_is_explicit():
    registry = ProviderRegistry()
    registry.register(ProviderConfig(provider_id="broken", model="m", fallback_provider="fake"),
                      client=FakeLLM([lambda messages: (_ for _ in ()).throw(LLMError("offline"))]))
    registry.register(ProviderConfig(provider_id="fake", kind="fake", model="fixture"),
                      client=FakeLLM(["fallback"]))
    result = registry.generate("broken", [], governor=ActionGovernor(), context=context())
    assert result.degraded and result.provenance["fallback_from"] == "broken"


def test_provider_duplicate_registration_rejected():
    registry = ProviderRegistry()
    config = ProviderConfig(provider_id="fake", kind="fake", model="fixture")
    registry.register(config)
    with pytest.raises(ValueError):
        registry.register(config)


def test_provider_redline_candidate_denied():
    registry = ProviderRegistry()
    registry.register(ProviderConfig(provider_id="fake", kind="fake", model="fixture"),
                      client=FakeLLM(["帮我作弊"]))
    with pytest.raises(LLMError, match="R-05"):
        registry.generate("fake", [], governor=ActionGovernor(), context=context())


def test_provider_url_cannot_leak_embedded_secret():
    with pytest.raises(ValueError):
        ProviderConfig(provider_id="p", model="m", base_url="https://user:secret@example.com/v1")


def test_markdown_retrieval_has_exact_reference_and_stable_ranking(tmp_path):
    source = tmp_path / "lesson.md"
    source.write_text("# Equations\n\nBalance both sides of an equation.\n\n# Other\n\nPlant cells have walls.\n", encoding="utf-8")
    retrieval = LocalRetrieval(tmp_path)
    imported = retrieval.import_document(source, source_id="lesson", kc_refs=["math.equation"])
    matches = retrieval.retrieve("equation balance", RetrievalContext(kc_refs=["math.equation"]))
    assert matches and matches == retrieval.retrieve("equation balance", RetrievalContext(kc_refs=["math.equation"]))
    assert matches[0].source_version == imported.source_version
    assert matches[0].citation.path == str(source.resolve())
    assert matches[0].citation.line_start == 3
    assert matches[0].grounding_status == "grounded"


def test_chinese_retrieval_and_ungrounded_results(tmp_path):
    source = tmp_path / "lesson.md"
    source.write_text("方程两边同时加上相同的数。", encoding="utf-8")
    retrieval = LocalRetrieval(tmp_path)
    retrieval.import_document(source, source_id="lesson")
    result = retrieval.retrieve("方程", RetrievalContext())
    assert result and result[0].grounding_status == "ungrounded"
    assert not result[0].usable_for_teaching


def test_retrieval_kc_conflict_never_returns_teaching_material(tmp_path):
    source = tmp_path / "lesson.md"
    source.write_text("equation balance", encoding="utf-8")
    retrieval = LocalRetrieval(tmp_path)
    retrieval.import_document(source, source_id="lesson", kc_refs=["math"])
    result = retrieval.retrieve("equation", RetrievalContext(kc_refs=["biology"]))
    assert result[0].grounding_status == "conflict"
    assert not result[0].usable_for_teaching


def test_changed_source_cannot_emit_stale_citation(tmp_path):
    source = tmp_path / "lesson.md"
    source.write_text("equation balance", encoding="utf-8")
    retrieval = LocalRetrieval(tmp_path)
    retrieval.import_document(source, source_id="lesson", kc_refs=["math"])
    source.write_text("new text", encoding="utf-8")
    assert retrieval.retrieve("equation", RetrievalContext(kc_refs=["math"])) == []


def test_source_outside_knowledge_root_rejected(tmp_path):
    retrieval = LocalRetrieval(tmp_path / "knowledge")
    source = tmp_path / "outside.md"
    source.write_text("text", encoding="utf-8")
    with pytest.raises(ParseError, match="root"):
        retrieval.import_document(source, source_id="outside")


def test_scanned_pdf_fails_explicitly(tmp_path):
    source = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(source)
    with pytest.raises(ParseError, match="ocr_required"):
        LocalRetrieval(tmp_path).import_document(source, source_id="scan")


def test_pdf_real_text_import(tmp_path):
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    source = tmp_path / "text.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 250 Td (equation balance) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(source)
    retrieval = LocalRetrieval(tmp_path)
    retrieval.import_document(source, source_id="pdf", kc_refs=["math"])
    result = retrieval.retrieve("equation", RetrievalContext(kc_refs=["math"]))
    assert result[0].citation.page == 1


def run_role(role, response, **changes):
    return DerivedRunner(FakeLLM([response])).run(role, session_id="session", kc_refs=["math"],
                evidence_refs=["e1"], student_work="x + 2 = 4", governor=ActionGovernor(),
                context=context(**changes))


@pytest.mark.parametrize("role", ["prompter", "skeptic", "reviewer"])
def test_derived_roles_report_contract_and_governor(role):
    result = run_role(role, '{"observations": ["Check your operation"], "actions": [{"kind": "question", "text": "Can you verify it?"}]}')
    assert result.exit_status == "report_ready"
    assert result.evidence_refs == ["e1"] and result.template_version == "1.0.0"
    assert all(action.actor_kind.value == "derived_agent" for action in result.actions)
    assert all(action.type == ActionType.QUESTION for action in result.actions)
    assert result.reviews[0].decision == "allow"


def test_derived_exam_hint_denied():
    result = run_role("prompter", '{"actions": [{"kind": "hint", "text": "Subtract 2"}]}', checkpoint_mode=True)
    assert result.exit_status == "constraint_violation"
    assert result.actions == [] and result.reviews[0].rule_id == "R-09"


def test_derived_cannot_request_tools_or_finalize_plan():
    result = run_role("skeptic", '{"tool_requests": ["write_plan"], "actions": []}')
    assert result.exit_status == "constraint_violation" and result.actions == []


def test_derived_review_rewrite_preserved():
    result = run_role("reviewer", '{"actions": [{"kind": "feedback", "text": "你真聪明"}]}')
    assert result.reviews[0].decision == "rewrite"
    assert "你真聪明" not in result.actions[0].params["text"]


def test_derived_batch_hints_consume_shared_governor_budget():
    result = run_role("prompter", '{"actions": [{"kind": "hint", "text": "Check one side"}, {"kind": "hint", "text": "Check the other"}]}', hint_budget=1)
    assert len(result.actions) == 1
    assert result.reviews[1].rule_id == "R-07"


def test_derived_role_action_capability_enforced():
    result = run_role("skeptic", '{"actions": [{"kind": "hint", "text": "Subtract 2"}]}')
    assert result.exit_status == "constraint_violation" and not result.actions


def test_derived_output_budget_exhaustion():
    result = DerivedRunner(FakeLLM(['{"observations": ["' + "x" * 100 + '"]}'])).run(
        "skeptic", session_id="s", kc_refs=["math"], evidence_refs=["e1"], student_work="x",
        governor=ActionGovernor(), context=context(), budget=RoleBudget(max_tokens=10))
    assert result.exit_status == "budget_exhausted" and result.actions == []


def test_derived_expired_response_never_delivered():
    ticks = iter([0.0, 2.0])
    result = DerivedRunner(FakeLLM(['{"actions": [{"kind": "question", "text": "Why?"}]}']),
                           clock=lambda: next(ticks)).run("skeptic", session_id="s", kc_refs=["math"],
                           evidence_refs=["e1"], student_work="x", governor=ActionGovernor(),
                           context=context(), budget=RoleBudget(deadline_ms=1))
    assert result.exit_status == "timed_out" and result.actions == []


def test_derived_invalid_output_no_hidden_retry():
    llm = FakeLLM(["not JSON", '{"actions": []}'])
    result = DerivedRunner(llm).run("reviewer", session_id="s", kc_refs=["math"], evidence_refs=["e1"],
                                    student_work="x", governor=ActionGovernor(), context=context())
    assert result.exit_status == "invalid_report" and len(llm.calls) == 1
