"""Synthetic curriculum assets are reproducible fixtures, never learner evidence."""
import json
from pathlib import Path

import pytest

from app.learning.synthetic_assets import (
    CurriculumAssetProvider, GenerationSpec, SyntheticBundle, catalog_overlay, generate_assets,
    load_generation_spec, quality_report,
)


SEEDS = Path(__file__).parents[1] / "seeds" / "synthetic"


def test_json_and_csv_specs_generate_identical_catalogs():
    json_spec = load_generation_spec(SEEDS / "方程合成课程配置-20261006.json")
    csv_spec = load_generation_spec(SEEDS / "方程合成课程配置-20261006.csv", seed=20261006)
    assert json_spec == csv_spec
    first = generate_assets(json_spec)
    assert first == generate_assets(csv_spec)
    assert first == generate_assets(json_spec)
    assert len(first.catalog.knowledge) == 2
    assert len(first.catalog.assessments) == 36
    assert first.data_classification == "synthetic_ai_generated"
    assert first.not_real_evidence is True


def test_seed_changes_questions_without_ambiguous_asset_versions():
    spec = load_generation_spec(SEEDS / "方程合成课程配置-20261006.json")
    original = generate_assets(spec)
    updated = generate_assets(spec.model_copy(update={"seed": 7}))
    assert original.catalog.assessments != updated.catalog.assessments
    assert original.catalog.catalog_version != updated.catalog.catalog_version
    assert original.catalog.assessments[0].ref.key != updated.catalog.assessments[0].ref.key


@pytest.mark.parametrize("seed", [0, 13, 24, 27, 32, 35, 36, 42, 46, 48])
def test_seeds_with_previous_cross_kc_collisions_stay_unique(seed):
    spec = load_generation_spec(SEEDS / "方程合成课程配置-20261006.json")
    bundle = generate_assets(spec.model_copy(update={"seed": seed}))
    assert quality_report(bundle).valid
    assert len({item.stem for item in bundle.catalog.assessments}) == 36


def test_generated_answer_equivalence_and_four_hint_levels():
    bundle = generate_assets(load_generation_spec(SEEDS / "方程合成课程配置-20261006.json"))
    for item in bundle.catalog.assessments:
        derivation = bundle.generation_details[item.ref.key]
        answer = int(item.answer["value"])
        assert derivation["coefficient"] * answer + derivation["offset"] == derivation["rhs"]
        hints = bundle.hint_ladders[item.ref.key]
        assert [hint.level for hint in hints] == [0, 1, 2, 3]
        assert not any(hint.answer_exposed for hint in hints[:3])
        assert hints[3].answer_exposed and hints[3].form == "worked_full"
        assert item.calibration_status == "demo"


def test_quality_report_counts_and_limits_are_explicit():
    bundle = generate_assets(load_generation_spec(SEEDS / "方程合成课程配置-20261006.json"))
    report = quality_report(bundle)
    assert report.valid and report.assessment_count == 36
    assert report.kc_count == 2 and report.difficulty_counts == {"easy": 12, "medium": 12, "hard": 12}
    assert report.answer_checks_passed == 36
    assert "not_calibrated" in report.limitations and "not_learning_evidence" in report.limitations


@pytest.mark.parametrize("change", [{"questions_per_cell": 0}, {"seed": -1}, {"data_classification": "real"}, {"learner_id": "student"}])
def test_spec_rejects_invalid_or_learner_fields(change):
    fields = json.loads((SEEDS / "方程合成课程配置-20261006.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError):
        GenerationSpec.model_validate(fields | change)


def test_bundle_cannot_be_labeled_real_or_promoted_to_calibrated():
    bundle = generate_assets(load_generation_spec(SEEDS / "方程合成课程配置-20261006.json"))
    data = bundle.model_dump(mode="json")
    with pytest.raises(ValueError):
        SyntheticBundle.model_validate(data | {"not_real_evidence": False})
    data["catalog"]["assessments"][0]["calibration_status"] = "calibrated"
    with pytest.raises(ValueError):
        SyntheticBundle.model_validate(data)


def test_quality_detects_wrong_answer_and_missing_hints():
    bundle = generate_assets(load_generation_spec(SEEDS / "方程合成课程配置-20261006.json"))
    key = bundle.catalog.assessments[0].ref.key
    broken = bundle.model_copy(update={"generation_details": {**bundle.generation_details, key: {"coefficient": 1, "offset": 0, "rhs": -999}}})
    assert not quality_report(broken).valid
    incomplete = bundle.model_dump(mode="json")
    del incomplete["hint_ladders"][key]
    with pytest.raises(ValueError):
        SyntheticBundle.model_validate(incomplete)


def test_provider_switches_to_existing_real_asset_contract_without_state_writes(tmp_path):
    bundle = generate_assets(load_generation_spec(SEEDS / "方程合成课程配置-20261006.json"))
    synthetic = tmp_path / "bundle.json"
    synthetic.write_text(bundle.model_dump_json(), encoding="utf-8")
    provider = CurriculumAssetProvider(synthetic, source_kind="synthetic_ai_generated")
    assert provider.load().catalog == bundle.catalog
    real = Path(__file__).parents[1] / "seeds" / "learning_assets_v1.json"
    loaded = CurriculumAssetProvider(real, source_kind="curated_course_assets").load()
    assert loaded.source_kind == "curated_course_assets"
    assert loaded.not_real_evidence and loaded.catalog.catalog_version == "demo-math-g7-v1"
    assert "learning_evidence" not in json.loads(bundle.model_dump_json())


def test_csv_unknown_or_duplicate_rows_rejected(tmp_path):
    source = tmp_path / "broken.csv"
    source.write_text("kc_id,title,family,prerequisites,extra\nMATH.X,Title,balance,,oops\n", encoding="utf-8")
    with pytest.raises(ValueError, match="columns"):
        load_generation_spec(source)


def test_quality_rejects_stem_answer_mismatch():
    bundle = generate_assets(load_generation_spec(SEEDS / "方程合成课程配置-20261006.json"))
    first = bundle.catalog.assessments[0].model_copy(update={"stem": "合成练习：解等式 x + 1 = 999。"})
    catalog = bundle.catalog.model_copy(update={"assessments": (first, *bundle.catalog.assessments[1:])})
    assert not quality_report(bundle.model_copy(update={"catalog": catalog})).valid


def test_synthetic_catalog_cannot_be_relabeled_curated(tmp_path):
    bundle = generate_assets(load_generation_spec(SEEDS / "方程合成课程配置-20261006.json"))
    source = tmp_path / "catalog.json"
    source.write_text(bundle.catalog.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="relabeled"):
        CurriculumAssetProvider(source, source_kind="curated_course_assets").load()


def test_four_kc_overlay_is_additive_and_references_base_versions():
    from app.learning.assets import AssetCatalog
    bundle = generate_assets(load_generation_spec(SEEDS / "四知识点合成课程配置-20261006.json"))
    assert quality_report(bundle).valid and len(bundle.catalog.assessments) == 144
    base = AssetCatalog.load(SEEDS.parent / "learning_assets_v1.json")
    overlay = catalog_overlay(bundle, base)
    assert {item["ref"]["asset_id"] for item in overlay["knowledge"]} == {"MATH.G7.EQ.SETUP", "MATH.G7.EQ.APPLY"}
    payload = base.model_dump(mode="json")
    for key in ("knowledge", "rubrics", "assessments"):
        payload[key].extend(overlay[key])
    merged = AssetCatalog.model_validate(payload)
    assert len(merged.assessments) == 156
    assert merged.topological_order()[:2] == ("MATH.G7.EQ.BALANCE@1.0.0", "MATH.G7.EQ.SOLVE@1.0.0")
    setup = [item for item in bundle.catalog.assessments if item.kc_refs[0].asset_id.endswith("SETUP")]
    assert {int(item.answer["value"]) for item in setup} == {1, 2, 3}
