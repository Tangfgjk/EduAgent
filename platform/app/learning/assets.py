"""Versioned curriculum and assessment assets, independent of learner state."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VersionedRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    asset_id: str = Field(min_length=1, pattern=r"^[^@\s]+$")
    version: str = Field(min_length=1, pattern=r"^[^@\s]+$")

    @property
    def key(self) -> str:
        return f"{self.asset_id}@{self.version}"


class KnowledgeAsset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ref: VersionedRef
    title: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    kc_type: str = Field(min_length=1)
    curriculum_ref: str
    grade_band: str
    prerequisite_refs: tuple[VersionedRef, ...] = ()
    provenance: str = Field(min_length=1)


class RubricDimension(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    dimension_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    weight: float = Field(gt=0, le=1)


class RubricAsset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ref: VersionedRef
    dimensions: tuple[RubricDimension, ...] = Field(min_length=1)
    passing_score: float = Field(ge=0, le=1)
    assessor_policy: Literal["deterministic", "teacher", "reviewed_llm"]
    provenance: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_dimensions(self):
        ids = [item.dimension_id for item in self.dimensions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rubric dimension")
        if abs(sum(item.weight for item in self.dimensions) - 1) > 1e-6:
            raise ValueError("rubric weights must sum to one")
        return self


class AssessmentAsset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ref: VersionedRef
    kc_refs: tuple[VersionedRef, ...] = Field(min_length=1)
    rubric_ref: VersionedRef
    kind: Literal["diagnostic", "practice", "pretest", "posttest", "transfer", "delayed", "recall", "explanation"]
    difficulty_band: str = Field(min_length=1)
    comparison_group: str = Field(min_length=1)
    bloom_level: Literal["remember", "understand", "apply", "analyze", "evaluate", "create"]
    stem: str = Field(min_length=1)
    answer: dict = Field(default_factory=dict)
    calibration_status: Literal["demo", "reviewed", "calibrated"] = "demo"
    provenance: str = Field(min_length=1)


class AssetCatalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    catalog_version: str = Field(min_length=1)
    knowledge: tuple[KnowledgeAsset, ...]
    rubrics: tuple[RubricAsset, ...]
    assessments: tuple[AssessmentAsset, ...]

    @model_validator(mode="after")
    def check_catalog(self):
        for collection in (self.knowledge, self.rubrics, self.assessments):
            keys = [asset.ref.key for asset in collection]
            if len(keys) != len(set(keys)):
                raise ValueError("duplicate asset ID/version")
        knowledge = {asset.ref.key: asset for asset in self.knowledge}
        rubrics = {asset.ref.key for asset in self.rubrics}
        for asset in self.knowledge:
            refs = [ref.key for ref in asset.prerequisite_refs]
            if len(refs) != len(set(refs)):
                raise ValueError("duplicate prerequisite reference")
            if any(ref not in knowledge for ref in refs):
                raise ValueError("unknown prerequisite version")
        for asset in self.assessments:
            if asset.rubric_ref.key not in rubrics:
                raise ValueError("unknown rubric version")
            if any(ref.key not in knowledge for ref in asset.kc_refs):
                raise ValueError("unknown assessment KC version")
            if len({ref.key for ref in asset.kc_refs}) != len(asset.kc_refs):
                raise ValueError("duplicate assessment KC reference")
        self.topological_order()
        return self

    def topological_order(self, targets: tuple[VersionedRef, ...] | None = None) -> tuple[str, ...]:
        knowledge = {asset.ref.key: asset for asset in self.knowledge}
        visited: set[str] = set()
        visiting: set[str] = set()
        ordered: list[str] = []

        def visit(key: str):
            if key not in knowledge:
                raise ValueError(f"unknown KC reference: {key}")
            if key in visiting:
                raise ValueError("prerequisite graph contains a cycle")
            if key in visited:
                return
            visiting.add(key)
            for ref in sorted(knowledge[key].prerequisite_refs, key=lambda item: item.key):
                visit(ref.key)
            visiting.remove(key)
            visited.add(key)
            ordered.append(key)

        keys = sorted(knowledge) if targets is None else sorted(ref.key for ref in targets)
        for key in keys:
            visit(key)
        return tuple(ordered)

    @classmethod
    def load(cls, path: Path | str) -> "AssetCatalog":
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def merge_catalog_overlays(base: AssetCatalog, paths: tuple[Path | str, ...]) -> AssetCatalog:
    """Validate overlays after merging exact references, never silently overwrite versions."""
    payload = base.model_dump(mode="json")
    versions = [base.catalog_version]
    for path in paths:
        overlay = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(overlay, dict) or set(overlay) != {"catalog_version", "knowledge", "rubrics", "assessments"}:
            raise ValueError("Catalog overlay must have the same four AssetCatalog fields")
        versions.append(overlay["catalog_version"])
        for key in ("knowledge", "rubrics", "assessments"):
            payload[key].extend(overlay[key])
    payload["catalog_version"] = "+".join(versions)
    return AssetCatalog.model_validate(payload)


def load_catalog(settings=None) -> AssetCatalog:
    """Load replaceable curated JSON or synthetic JSON/CSV, plus additive overlays.

    Relative paths resolve under platform/, independent of server working directory.
    The default bundled base stays unchanged; only the default source receives its
    bundled synthetic supplement. Custom paths must request overlays explicitly.
    """
    if settings is None:
        from app.config import Settings
        settings = Settings.load()
    platform_root = Path(__file__).resolve().parents[2]
    custom_path = settings.learning_catalog_path

    def resolve(path):
        value = Path(path)
        return value if value.is_absolute() else platform_root / value

    source = resolve(custom_path) if custom_path else platform_root / "seeds" / "learning_assets_v1.json"
    if source.suffix.lower() == ".csv":
        from app.learning.synthetic_assets import CurriculumAssetProvider
        base = CurriculumAssetProvider(source, source_kind="synthetic_ai_generated").load().catalog
    else:
        raw = json.loads(source.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict) and ("courses" in raw or "catalog" in raw):
            from app.learning.synthetic_assets import CurriculumAssetProvider
            base = CurriculumAssetProvider(source, source_kind="synthetic_ai_generated").load().catalog
        else:
            base = AssetCatalog.model_validate(raw)
    overlays = tuple(resolve(path) for path in settings.learning_catalog_overlay_paths)
    if not custom_path and not overlays:
        bundled = (platform_root / "seeds" / "synthetic" / "learning_assets_overlay_v1.json",
                   platform_root / "seeds" / "qualitative_assets_v1.json")
        overlays = tuple(path for path in bundled if path.exists())
    catalog = merge_catalog_overlays(base, overlays) if overlays else base
    if not custom_path:
        catalog = append_legacy_bank_assets(catalog, platform_root / "seeds" / "question_bank.json")
    return catalog


def append_legacy_bank_assets(catalog: AssetCatalog, path: Path | str) -> AssetCatalog:
    """Map original locally authored demo questions without relabeling them AI-generated."""
    items = json.loads(Path(path).read_text(encoding="utf-8"))["items"]
    refs_by_id: dict[str, list[VersionedRef]] = {}
    for asset in catalog.knowledge:
        refs_by_id.setdefault(asset.ref.asset_id, []).append(asset.ref)
    rubric = next((item for item in catalog.rubrics if item.ref.key == "math-exact@1.0.0"), None)
    if rubric is None:
        return catalog
    additions = []
    existing = {asset.ref.key for asset in catalog.assessments}
    for item in items:
        refs = refs_by_id.get(item["kc_id"], [])
        if len(refs) != 1:
            continue
        ref = VersionedRef(asset_id=item["item_id"], version="1.0.0")
        if ref.key in existing:
            continue
        difficulty = item["difficulty"]
        band = "easy" if difficulty <= 0.35 else "medium" if difficulty <= 0.55 else "hard"
        additions.append(AssessmentAsset(ref=ref, kc_refs=(refs[0],), rubric_ref=rubric.ref,
            kind="practice", difficulty_band=band, comparison_group=f"legacy-bank-{item['kc_id']}-{band}-v1",
            bloom_level=item["taxonomy_level"], stem=item["stem"], answer=item["answer"],
            calibration_status="demo", provenance="original_local_question_bank;legacy_version_mapping;not_calibrated;not_real_evidence"))
    payload = catalog.model_dump(mode="json")
    payload["assessments"].extend(asset.model_dump(mode="json") for asset in additions)
    payload["catalog_version"] += "+legacy-bank-v1"
    return AssetCatalog.model_validate(payload)
