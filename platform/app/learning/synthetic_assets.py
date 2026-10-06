"""Reproducible synthetic curriculum assets; never create learner Evidence.

JSON/CSV are interchangeable generation manifests. Curated replacement assets
use the existing AssetCatalog contract, so storage and learner-state consumers
do not depend on this generator or a particular course data source.
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.learning.assets import AssessmentAsset, AssetCatalog, KnowledgeAsset, RubricAsset, VersionedRef

GENERATOR_VERSION = "synthetic-equation-v1"
ASSET_VERSION = "1.0.0"
AssessmentKind = Literal["diagnostic", "practice", "pretest", "posttest", "transfer", "delayed"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CourseRow(StrictModel):
    kc_id: str = Field(pattern=r"^[A-Z]+(\.[A-Z0-9_]+)+$")
    title: str = Field(min_length=1)
    family: Literal["balance", "solve", "setup", "apply"]
    prerequisites: tuple[str, ...] = ()


class GenerationSpec(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    data_classification: Literal["synthetic_ai_generated"] = "synthetic_ai_generated"
    not_real_evidence: Literal[True] = True
    seed: int = Field(default=20261006, ge=0, le=2**32 - 1)
    generated_at: AwareDatetime = datetime.fromisoformat("2026-10-06T00:00:00+08:00")
    questions_per_cell: int = Field(default=2, ge=1, le=25)
    difficulties: tuple[Literal["easy", "medium", "hard"], ...] = ("easy", "medium", "hard")
    kinds: tuple[AssessmentKind, ...] = ("diagnostic", "practice", "transfer")
    courses: tuple[CourseRow, ...] = Field(min_length=1, max_length=20)
    provenance: str = "AI-authored-local-generator-and-templates-20261006"
    license_note: str = "Local original generated content; no external question-bank text copied"

    @model_validator(mode="after")
    def check_spec(self):
        ids = [course.kc_id for course in self.courses]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate KC row")
        for course in self.courses:
            if len(set(course.prerequisites)) != len(course.prerequisites):
                raise ValueError("Duplicate prerequisite KC")
            if not set(course.prerequisites) <= set(ids):
                raise ValueError("Unknown prerequisite KC")
        for values in (self.difficulties, self.kinds):
            if not values or len(set(values)) != len(values):
                raise ValueError("Nonempty unique difficulty/kind lists required")
        return self


class HintAsset(StrictModel):
    level: int = Field(ge=0, le=3)
    form: Literal["nudge", "directive", "worked_partial", "worked_full"]
    text: str = Field(min_length=1)
    answer_exposed: bool


class SyntheticBundle(StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    data_classification: Literal["synthetic_ai_generated"] = "synthetic_ai_generated"
    not_real_evidence: Literal[True] = True
    generator_version: Literal["synthetic-equation-v1"] = GENERATOR_VERSION
    seed: int
    generated_at: AwareDatetime
    manifest_sha256: str
    provenance: str
    license_note: str
    catalog: AssetCatalog
    hint_ladders: dict[str, tuple[HintAsset, ...]]
    generation_details: dict[str, dict[str, int]]

    @model_validator(mode="after")
    def check_bundle(self):
        keys = {asset.ref.key for asset in self.catalog.assessments}
        if set(self.hint_ladders) != keys or set(self.generation_details) != keys:
            raise ValueError("Hint/derivation references must exactly match assessment versions")
        for asset in self.catalog.assessments:
            if asset.calibration_status != "demo" or "synthetic_ai_generated" not in asset.provenance:
                raise ValueError("Generated assets must remain explicitly synthetic and uncalibrated")
            hints = self.hint_ladders[asset.ref.key]
            if [hint.level for hint in hints] != [0, 1, 2, 3]:
                raise ValueError("Each assessment requires exactly L0-L3 in order")
            if [hint.form for hint in hints] != ["nudge", "directive", "worked_partial", "worked_full"]:
                raise ValueError("Hint form must match its ladder level")
            if [hint.answer_exposed for hint in hints] != [False, False, False, True]:
                raise ValueError("Only L3 may expose the full solution")
        return self


class QualityReport(StrictModel):
    valid: bool
    kc_count: int
    assessment_count: int
    difficulty_counts: dict[str, int]
    answer_checks_passed: int
    errors: tuple[str, ...]
    limitations: tuple[str, ...] = ("not_calibrated", "not_learning_evidence", "no_educational_effect_claim",
                                    "hint_semantics_require_human_review")


def load_generation_spec(path: str | Path, *, seed: int = 20261006) -> GenerationSpec:
    path = Path(path)
    if path.suffix.lower() == ".json":
        return GenerationSpec.model_validate_json(path.read_text(encoding="utf-8-sig"))
    if path.suffix.lower() != ".csv":
        raise ValueError("Generation manifest must be JSON or CSV")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["kc_id", "title", "family", "prerequisites"]:
            raise ValueError("CSV columns must be kc_id,title,family,prerequisites")
        courses = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("CSV row width mismatch")
            courses.append(CourseRow(kc_id=row["kc_id"].strip(), title=row["title"].strip(),
                          family=row["family"].strip(), prerequisites=tuple(part.strip()
                          for part in row["prerequisites"].split(";") if part.strip())))
    return GenerationSpec(seed=seed, courses=tuple(courses))


def _equation_text(coefficient, offset, rhs):
    variable = "x" if coefficient == 1 else f"{coefficient}x"
    return f"{variable} {'+' if offset > 0 else '-'} {abs(offset)} = {rhs}"


def _setup_stem(coefficient, offset, rhs, choice):
    options = [_equation_text(coefficient, -offset, rhs), _equation_text(coefficient, offset, rhs + 1)]
    options.insert(choice - 1, _equation_text(coefficient, offset, rhs))
    return (f"合成列式练习：某数量 x 的 {coefficient} 倍再{'增加' if offset > 0 else '减少'} {abs(offset)} 得到 {rhs}。"
            + "请选择与数量关系一致的方程编号：" + "；".join(f"{i}. {text}" for i, text in enumerate(options, 1)) + "。")


def generate_assets(spec: GenerationSpec) -> SyntheticBundle:
    canonical = json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    version = f"{ASSET_VERSION}-synthetic-{fingerprint[:12]}"
    provenance = f"synthetic_ai_generated;not_real_evidence;{GENERATOR_VERSION};seed={spec.seed};manifest={fingerprint}"
    rng = random.Random(spec.seed)
    knowledge = tuple(KnowledgeAsset(ref=VersionedRef(asset_id=course.kc_id, version=version),
        title=course.title, domain="mathematics", kc_type="concept" if course.family == "balance" else "procedure",
        curriculum_ref="synthetic-local-demo-not-official-curriculum", grade_band="G7",
        prerequisite_refs=tuple(VersionedRef(asset_id=kc, version=version) for kc in course.prerequisites),
        provenance=provenance) for course in spec.courses)
    rubric = RubricAsset(ref=VersionedRef(asset_id="synthetic-exact-equation", version=version),
        dimensions=({"dimension_id": "correctness", "description": "整数代入满足所生成等式", "weight": 1},),
        passing_score=1, assessor_policy="deterministic", provenance=provenance)
    assessments, ladders, details = [], {}, {}
    used = set()
    for course in spec.courses:
        for difficulty in spec.difficulties:
            for kind in spec.kinds:
                for number in range(1, spec.questions_per_cell + 1):
                    for _ in range(1000):
                        coefficient = 1 if course.family == "balance" or difficulty == "easy" else rng.randint(2, 5 if difficulty == "medium" else 9)
                        low, high = (1, 9) if difficulty == "easy" else (-15, 15) if difficulty == "medium" else (-30, 30)
                        solution, offset = rng.randint(low, high), rng.randint(low, high)
                        if offset and (coefficient, offset, solution) not in used:
                            break
                    else:
                        raise ValueError("Requested sample diversity exceeds this generator's question pool")
                    used.add((coefficient, offset, solution))
                    rhs = coefficient * solution + offset
                    variable_term = "x" if coefficient == 1 else f"{coefficient}x"
                    expression = f"{variable_term} {'+' if offset > 0 else '-'} {abs(offset)}"
                    choice = rng.randint(1, 3) if course.family == "setup" else None
                    stem = (_setup_stem(coefficient, offset, rhs, choice) if choice is not None else
                            f"合成迁移练习：某数量 x 的 {coefficient} 倍再{'增加' if offset > 0 else '减少'} {abs(offset)} 得到 {rhs}，求 x。"
                            if kind == "transfer" or course.family == "apply" else f"合成练习：解等式 {expression} = {rhs}。")
                    item = AssessmentAsset(ref=VersionedRef(asset_id=f"SYN-{course.kc_id}-{difficulty}-{kind}-{number}", version=version),
                        kc_refs=(VersionedRef(asset_id=course.kc_id, version=version),), rubric_ref=rubric.ref,
                        kind=kind, difficulty_band=difficulty, comparison_group=f"synthetic-{course.kc_id}-{difficulty}-{version}",
                        bloom_level="understand" if course.family == "balance" else "apply", stem=stem,
                        answer={"var": "x", "value": str(choice if choice is not None else solution)}, calibration_status="demo", provenance=provenance)
                    assessments.append(item)
                    ladders[item.ref.key] = (
                        HintAsset(level=0, form="nudge", text="先说说等式两边表示什么，下一步希望保留哪一项？", answer_exposed=False),
                        HintAsset(level=1, form="directive", text="观察未知数旁边的常数，想想应对两边做哪一种相同运算。", answer_exposed=False),
                        HintAsset(level=2, form="worked_partial", text=f"两边先同时{'减去' if offset > 0 else '加上'} {abs(offset)}；剩余步骤由你写，并准备代回检验。", answer_exposed=False),
                        HintAsset(level=3, form="worked_full", text=f"两边去除常数后得到 {variable_term} = {rhs - offset}；再除以 {coefficient} 得 x = {solution}。代回原等式验证 {coefficient} × ({solution}) + ({offset}) = {rhs}。", answer_exposed=True),
                    )
                    details[item.ref.key] = dict(coefficient=coefficient, offset=offset, rhs=rhs)
                    if choice is not None:
                        details[item.ref.key].update(choice=choice, solution=solution)
                        ladders[item.ref.key] = (
                            HintAsset(level=0, form="nudge", text="先明确 x 表示什么，再逐句读数量关系。", answer_exposed=False),
                            HintAsset(level=1, form="directive", text="检查倍数对应的系数、增减对应的符号，以及最后得到的数量。", answer_exposed=False),
                            HintAsset(level=2, form="worked_partial", text="把情境拆成乘法、增减和相等三部分；逐个排除不一致的选项，先不求 x。", answer_exposed=False),
                            HintAsset(level=3, form="worked_full", text=f"列式为 {_equation_text(coefficient, offset, rhs)}，对应选项 {choice}。这是列式识别题，不作为完整开放式建模能力证明。", answer_exposed=True),
                        )
    catalog = AssetCatalog(catalog_version=f"synthetic-math-g7-{fingerprint[:12]}", knowledge=knowledge,
                           rubrics=(rubric,), assessments=tuple(assessments))
    bundle = SyntheticBundle(seed=spec.seed, generated_at=spec.generated_at, manifest_sha256=fingerprint,
        provenance=spec.provenance, license_note=spec.license_note, catalog=catalog,
        hint_ladders=ladders, generation_details=details)
    report = quality_report(bundle)
    if not report.valid:
        raise ValueError(f"Synthetic quality check failed: {report.errors}")
    return bundle


def quality_report(bundle: SyntheticBundle) -> QualityReport:
    errors, passed = [], 0
    seen = set()
    for asset in bundle.catalog.assessments:
        if asset.stem in seen:
            errors.append(f"duplicate_stem:{asset.ref.key}")
        seen.add(asset.stem)
        detail = bundle.generation_details.get(asset.ref.key, {})
        try:
            valid = (asset.answer.get("var") == "x" and detail["coefficient"] != 0
                     and (int(asset.answer["value"]) == detail["choice"]
                          and detail["coefficient"] * detail["solution"] + detail["offset"] == detail["rhs"]
                          if "choice" in detail else
                          detail["coefficient"] * int(asset.answer["value"]) + detail["offset"] == detail["rhs"]))
        except (KeyError, ValueError, TypeError):
            valid = False
        equation = re.fullmatch(r"合成练习：解等式 (\d*)x ([+-]) (\d+) = (-?\d+)。", asset.stem)
        transfer = re.fullmatch(r"合成迁移练习：某数量 x 的 (\d+) 倍再(增加|减少) (\d+) 得到 (-?\d+)，求 x。", asset.stem)
        match = equation or transfer
        if match:
            coefficient = int(match[1] or "1")
            offset = int(match[3]) * (-1 if match[2] in {"-", "减少"} else 1)
            parsed = dict(coefficient=coefficient, offset=offset, rhs=int(match[4]))
            valid = valid and all(parsed[key] == detail.get(key) for key in parsed)
        elif "choice" in detail:
            valid = valid and asset.stem == _setup_stem(detail["coefficient"], detail["offset"], detail["rhs"], detail["choice"])
        else:
            valid = False
        if valid:
            passed += 1
        else:
            errors.append(f"answer_equivalence_failed:{asset.ref.key}")
    return QualityReport(valid=not errors, kc_count=len(bundle.catalog.knowledge),
        assessment_count=len(bundle.catalog.assessments), difficulty_counts=dict(sorted(Counter(
        asset.difficulty_band for asset in bundle.catalog.assessments).items())),
        answer_checks_passed=passed, errors=tuple(errors))


def catalog_overlay(bundle: SyntheticBundle, base: AssetCatalog) -> dict:
    """Export additive catalog JSON; validate cross-base refs after caller merges.

    Existing KC identifiers keep the base version. New KC/assets retain the
    synthetic manifest version, never replacing an existing ID/version pair.
    """
    mapping = {}
    for asset in bundle.catalog.knowledge:
        existing = [item for item in base.knowledge if item.ref.asset_id == asset.ref.asset_id]
        if len(existing) > 1:
            raise ValueError("Base KC has ambiguous versions")
        if existing and (existing[0].domain != asset.domain or existing[0].kc_type != asset.kc_type):
            raise ValueError("Base KC semantics differ; use an explicit migration")
        mapping[asset.ref.key] = existing[0].ref if existing else asset.ref
    knowledge = [asset.model_copy(update={"prerequisite_refs": tuple(mapping[ref.key] for ref in asset.prerequisite_refs)})
                 for asset in bundle.catalog.knowledge if mapping[asset.ref.key] == asset.ref]
    assessments = [asset.model_copy(update={"kc_refs": tuple(mapping[ref.key] for ref in asset.kc_refs)})
                   for asset in bundle.catalog.assessments]
    for collection, old in ((knowledge, base.knowledge), (assessments, base.assessments), (bundle.catalog.rubrics, base.rubrics)):
        if {asset.ref.key for asset in collection}.intersection(asset.ref.key for asset in old):
            raise ValueError("Overlay cannot overwrite an existing asset version")
    return dict(catalog_version=f"synthetic-overlay-{bundle.manifest_sha256[:12]}",
                knowledge=[asset.model_dump(mode="json") for asset in knowledge],
                rubrics=[asset.model_dump(mode="json") for asset in bundle.catalog.rubrics],
                assessments=[asset.model_dump(mode="json") for asset in assessments])


class LoadedCourseAssets(StrictModel):
    source_kind: Literal["synthetic_ai_generated", "curated_course_assets"]
    not_real_evidence: Literal[True] = True
    catalog: AssetCatalog
    synthetic_bundle: SyntheticBundle | None = None


class CurriculumAssetProvider:
    """Replace a manifest/bundle with curated AssetCatalog, without learner writes."""
    def __init__(self, path: str | Path, *, source_kind: Literal["synthetic_ai_generated", "curated_course_assets"], seed: int = 20261006):
        if source_kind not in {"synthetic_ai_generated", "curated_course_assets"}:
            raise ValueError("Explicit curriculum source kind required")
        self.path, self.source_kind, self.seed = Path(path), source_kind, seed

    def load(self) -> LoadedCourseAssets:
        if self.source_kind == "curated_course_assets":
            catalog = AssetCatalog.load(self.path)
            if any("synthetic_ai_generated" in item.provenance for item in catalog.assessments):
                raise ValueError("Synthetic catalog cannot be relabeled curated")
            return LoadedCourseAssets(source_kind=self.source_kind, catalog=catalog)
        if self.path.suffix.lower() == ".csv":
            bundle = generate_assets(load_generation_spec(self.path, seed=self.seed))
        else:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            bundle = SyntheticBundle.model_validate(data) if "catalog" in data else generate_assets(GenerationSpec.model_validate(data))
        report = quality_report(bundle)
        if not report.valid:
            raise ValueError(f"Synthetic quality check failed: {report.errors}")
        return LoadedCourseAssets(source_kind=self.source_kind, catalog=bundle.catalog, synthetic_bundle=bundle)


def main() -> None:
    """Inspect quality or print a bundle; no database/learner writes or network."""
    import argparse
    parser = argparse.ArgumentParser(description="Validate/generate explicitly synthetic course assets")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--seed", type=int, default=20261006, help="CSV seed; JSON carries its own seed")
    parser.add_argument("--bundle", action="store_true", help="Print the generated bundle instead of quality report")
    args = parser.parse_args()
    loaded = CurriculumAssetProvider(args.manifest, source_kind="synthetic_ai_generated", seed=args.seed).load()
    output = loaded.synthetic_bundle if args.bundle else quality_report(loaded.synthetic_bundle)
    print(output.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
