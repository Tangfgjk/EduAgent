from pathlib import Path

import pytest

from app.learning.assets import AssetCatalog, VersionedRef, load_catalog, merge_catalog_overlays
from app.config import Settings
from app.learning.diagnosis import DiagnosticObservation, next_task


SEED = Path(__file__).parents[1] / "seeds" / "learning_assets_v1.json"


def catalog():
    return AssetCatalog.load(SEED)


def ref(asset_id):
    return VersionedRef(asset_id=asset_id, version="1.0.0")


def observation(item, response="correct", **kwargs):
    return DiagnosticObservation(evidence_id=item, learner_id="s", assessment_ref=ref(item), response=response, **kwargs)


def test_seed_references_and_dag_are_valid():
    assets = catalog()
    assert assets.topological_order((ref("MATH.G7.EQ.SOLVE"),)) == (
        "MATH.G7.EQ.BALANCE@1.0.0", "MATH.G7.EQ.SOLVE@1.0.0")
    assert all(item.calibration_status == "demo" for item in assets.assessments)


@pytest.mark.parametrize("corruption", ["cycle", "unknown_kc", "unknown_rubric", "duplicate", "weights"])
def test_catalog_rejects_invalid_or_unversioned_relationships(corruption):
    payload = catalog().model_dump(mode="json")
    if corruption == "cycle":
        payload["knowledge"][0]["prerequisite_refs"] = [payload["knowledge"][1]["ref"]]
    elif corruption == "unknown_kc":
        payload["assessments"][0]["kc_refs"][0]["version"] = "missing"
    elif corruption == "unknown_rubric":
        payload["assessments"][0]["rubric_ref"]["version"] = "missing"
    elif corruption == "duplicate":
        payload["knowledge"].append(payload["knowledge"][0])
    else:
        payload["rubrics"][0]["dimensions"][0]["weight"] = 0.4
    with pytest.raises(ValueError):
        AssetCatalog.model_validate(payload)


def test_diagnosis_probes_prerequisite_before_goal_and_finishes_deterministically():
    assets = catalog()
    target = (ref("MATH.G7.EQ.SOLVE"),)
    history = []
    expected = ("D-BALANCE-1", "D-BALANCE-2", "D-SOLVE-1", "D-SOLVE-2")
    for item in expected:
        decision = next_task(assets, target, tuple(history), learner_id="s")
        assert decision.assessment_ref.asset_id == item
        history.append(observation(item))
    result = next_task(assets, target, tuple(history), learner_id="s")
    assert result.status == "complete"
    assert result.reason == "initial_readiness_not_mastery"
    assert result == next_task(assets, target, tuple(reversed(history)), learner_id="s")


@pytest.mark.parametrize("response", ["incorrect", "dont_know"])
def test_diagnostic_gap_stops_before_advancing(response):
    result = next_task(catalog(), (ref("MATH.G7.EQ.SOLVE"),),
                       (observation("D-BALANCE-1", response),), learner_id="s")
    assert result.status == "needs_learning"
    assert result.kc_ref == ref("MATH.G7.EQ.BALANCE")


@pytest.mark.parametrize("kwargs", [{"response": "guessed"}, {"response": "skipped"},
                                     {"hint_level": 1}, {"answer_exposed": True}])
def test_unreliable_diagnostic_success_does_not_establish_readiness(kwargs):
    history = (observation("D-BALANCE-1", **kwargs), observation("D-BALANCE-2"))
    result = next_task(catalog(), (ref("MATH.G7.EQ.SOLVE"),), history, learner_id="s")
    assert result.status == "missing_assets"
    assert result.kc_ref == ref("MATH.G7.EQ.BALANCE")


def test_diagnosis_budget_idempotency_and_learner_isolation():
    first = observation("D-BALANCE-1")
    result = next_task(catalog(), (ref("MATH.G7.EQ.SOLVE"),), (first, first), learner_id="s", max_tasks=1)
    assert result.status == "budget_exhausted"
    other = first.model_copy(update={"learner_id": "other"})
    assert next_task(catalog(), (ref("MATH.G7.EQ.SOLVE"),), (other,), learner_id="s").evidence_refs == ()
    conflicting = first.model_copy(update={"response": "incorrect"})
    with pytest.raises(ValueError, match="conflicting"):
        next_task(catalog(), (ref("MATH.G7.EQ.SOLVE"),), (first, conflicting), learner_id="s")


def test_irrelevant_measurement_does_not_consume_initial_diagnosis_budget():
    measurement = observation("PRE-SOLVE-1")
    result = next_task(catalog(), (ref("MATH.G7.EQ.SOLVE"),), (measurement,), learner_id="s", max_tasks=1)
    assert result.status == "task"
    assert result.evidence_refs == ()


def test_versioned_reference_cannot_create_ambiguous_combined_keys():
    with pytest.raises(ValueError):
        VersionedRef(asset_id="asset@v1", version="v2")
    with pytest.raises(ValueError):
        VersionedRef(asset_id="asset", version="v1@v2")


def test_independent_retry_after_remediation_can_refresh_diagnosis_without_erasing_history():
    first = observation("D-BALANCE-1", "incorrect").model_copy(update={"event_seq": 1})
    retried = first.model_copy(update={"event_seq": 2, "evidence_id": "retry", "response": "correct"})
    second = observation("D-BALANCE-2").model_copy(update={"event_seq": 3})
    result = next_task(catalog(), (ref("MATH.G7.EQ.SOLVE"),), (second, first, retried), learner_id="s")
    assert result.assessment_ref == ref("D-SOLVE-1")
    assert first.evidence_id in result.evidence_refs


def test_default_overlay_maps_all_original_bank_kcs_without_changing_base_catalog():
    merged = load_catalog(Settings())
    assert len(catalog().assessments) == 12
    assert {item.ref.asset_id for item in merged.knowledge} >= {
        "MATH.G7.EQ.BALANCE", "MATH.G7.EQ.SOLVE", "MATH.G7.EQ.SETUP", "MATH.G7.EQ.APPLY"}
    mapped = [item for item in merged.assessments if item.ref.asset_id.startswith("EQ-")]
    assert len(mapped) == 12
    assert all("original_local_question_bank" in item.provenance and "synthetic_ai_generated" not in item.provenance for item in mapped)
    assert merged.topological_order()


def test_explicit_catalog_path_is_not_silently_augmented_by_bundled_overlay():
    result = load_catalog(Settings(learning_catalog_path=str(SEED)))
    assert result == catalog()


def test_overlay_rejects_duplicate_versions_instead_of_silently_overwriting():
    with pytest.raises(ValueError, match="duplicate"):
        merge_catalog_overlays(catalog(), (SEED,))


@pytest.mark.parametrize("suffix", ["json", "csv"])
def test_replacement_source_accepts_synthetic_manifests_without_bundled_legacy(suffix):
    manifest = SEED.parent / "synthetic" / ("方程合成课程配置-20261006." + suffix)
    result = load_catalog(Settings(learning_catalog_path=str(manifest)))
    assert result.assessments
    assert all("synthetic_ai_generated" in item.provenance for item in result.assessments)
    assert not any(item.ref.asset_id.startswith("EQ-") for item in result.assessments)
    assert result.topological_order()


def test_settings_overlay_environment_requires_explicit_json_path_array(tmp_path, monkeypatch):
    monkeypatch.setenv("RSI_LEARNING_CATALOG_PATH", "seeds/learning_assets_v1.json")
    monkeypatch.setenv("RSI_LEARNING_CATALOG_OVERLAYS", '["seeds/synthetic/learning_assets_overlay_v1.json"]')
    settings = Settings.load(tmp_path / ".env")
    assert settings.learning_catalog_overlay_paths == ("seeds/synthetic/learning_assets_overlay_v1.json",)
    assert load_catalog(settings).topological_order()
    monkeypatch.setenv("RSI_LEARNING_CATALOG_OVERLAYS", '"not-an-array"')
    with pytest.raises(ValueError, match="JSON array"):
        Settings.load(tmp_path / ".env")
