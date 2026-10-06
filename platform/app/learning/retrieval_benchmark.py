"""Frozen synthetic lexical retrieval evaluation, not a semantic or human benchmark."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from app.learning.course_governance import Contract
from app.learning.retrieval import LEXICAL_FEATURE_MODEL, RETRIEVAL_VERSIONS, LocalRetrieval, RetrievalContext


class BenchmarkDocument(Contract):
    source_id: str = Field(min_length=1)
    filename: str = Field(pattern=r"^[a-z0-9_-]+\.md$")
    text: str = Field(min_length=10)
    kc_refs: tuple[str, ...] = Field(min_length=1)


class RelevanceJudgment(Contract):
    source_id: str = Field(min_length=1)
    line: int = Field(ge=1)


class BenchmarkQuery(Contract):
    query_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    kc_refs: tuple[str, ...] = Field(min_length=1)
    relevant: tuple[RelevanceJudgment, ...] = Field(min_length=1)
    split: Literal["development", "heldout"]
    rationale: str = Field(min_length=10)


class RetrievalBenchmark(Contract):
    benchmark_ref: str = Field(min_length=1)
    source_kind: Literal["synthetic_ai_generated"] = "synthetic_ai_generated"
    qrel_source: Literal["developer_frozen_fixture_not_teacher_gold"] = "developer_frozen_fixture_not_teacher_gold"
    documents: tuple[BenchmarkDocument, ...] = Field(min_length=3)
    queries: tuple[BenchmarkQuery, ...] = Field(min_length=4)

    @model_validator(mode="after")
    def references(self):
        docs = {document.source_id: document for document in self.documents}
        if len(docs) != len(self.documents) or len({document.filename for document in self.documents}) != len(self.documents):
            raise ValueError("duplicate benchmark document identity")
        if len({query.query_id for query in self.queries}) != len(self.queries):
            raise ValueError("duplicate benchmark query identity")
        if {query.split for query in self.queries} != {"development", "heldout"}:
            raise ValueError("benchmark requires development and heldout queries")
        for query in self.queries:
            refs = [(row.source_id, row.line) for row in query.relevant]
            if len(refs) != len(set(refs)):
                raise ValueError("duplicate relevance judgment")
            for row in query.relevant:
                if row.source_id not in docs:
                    raise ValueError("unknown relevant benchmark source")
                document = docs[row.source_id]
                if row.line > len(document.text.splitlines()) or not document.text.splitlines()[row.line - 1].strip():
                    raise ValueError("invalid relevant line")
                if not set(query.kc_refs) & set(document.kc_refs):
                    raise ValueError("relevant source KC conflicts with query")
        return self

    @property
    def content_hash(self):
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True,
            ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()

    @classmethod
    def load(cls, path: Path | str):
        return cls.model_validate_json(Path(path).read_text(encoding="utf-8-sig"))


class QueryMetric(Contract):
    query_id: str
    split: str
    reciprocal_rank: float
    recall_at_k: float
    source_refs: tuple[dict, ...]
    ungrounded_or_conflicting_count: int


class ModeMetric(Contract):
    mode: str
    retrieval_version: str
    split: str
    query_count: int
    mrr_at_k: float
    mean_recall_at_k: float


class BenchmarkReport(Contract):
    benchmark_ref: str
    benchmark_sha256: str
    as_of: AwareDatetime
    k: int
    source_kind: Literal["synthetic_ai_generated"] = "synthetic_ai_generated"
    metrics: tuple[ModeMetric, ...]
    query_metrics: dict[str, tuple[QueryMetric, ...]]
    lexical_feature_model: str = LEXICAL_FEATURE_MODEL
    semantic_embedding_installed: Literal[False] = False
    causal_or_educational_effect_claim: Literal[False] = False
    limitations: tuple[str, ...] = ("tiny_synthetic_corpus_not_teacher_gold",
        "heldout_query_labels_are_developer_fixtures", "no_neural_embedding_comparison",
        "retrieval_quality_not_teaching_effect", "not_external_domain_generalization")


def run_benchmark(benchmark: RetrievalBenchmark, *, workspace: Path, as_of: datetime, k: int = 3) -> BenchmarkReport:
    """Only create an isolated new corpus directory; never read personal knowledge."""
    if not 1 <= k <= 50:
        raise ValueError("k must be between one and fifty")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("explicit timezone-aware as_of required")
    root = workspace.resolve()
    root.mkdir(parents=True, exist_ok=True)
    corpus = root / "frozen-benchmark-corpus"
    corpus.mkdir(exist_ok=False)
    adapter = LocalRetrieval(corpus)
    for document in benchmark.documents:
        path = corpus / document.filename
        path.write_text(document.text, encoding="utf-8")
        adapter.import_document(path, source_id=document.source_id, kc_refs=list(document.kc_refs))
    metrics, queries_by_mode = [], {}
    for mode in RETRIEVAL_VERSIONS:
        query_metrics = []
        for query in benchmark.queries:
            results = adapter.retrieve(query.text, RetrievalContext(mode=mode, kc_refs=list(query.kc_refs), limit=k))
            relevant = {(row.source_id, row.line) for row in query.relevant}
            ranks = []
            hits = set()
            citations = []
            for rank, result in enumerate(results, 1):
                identity = (result.source_id, result.citation.line_start)
                if identity in relevant and result.usable_for_teaching:
                    ranks.append(rank)
                    hits.add(identity)
                citations.append(dict(source_id=result.source_id, source_sha256=result.source_version,
                    line=result.citation.line_start, chunk_id=result.chunk_id,
                    grounding_status=result.grounding_status, retrieval_version=result.retrieval_version))
            query_metrics.append(QueryMetric(query_id=query.query_id, split=query.split,
                reciprocal_rank=1 / min(ranks) if ranks else 0, recall_at_k=len(hits) / len(relevant),
                source_refs=tuple(citations), ungrounded_or_conflicting_count=sum(not row.usable_for_teaching for row in results)))
        queries_by_mode[mode] = tuple(query_metrics)
        for split in ("development", "heldout", "all"):
            selected = [row for row in query_metrics if split == "all" or row.split == split]
            metrics.append(ModeMetric(mode=mode, retrieval_version=RETRIEVAL_VERSIONS[mode], split=split,
                query_count=len(selected), mrr_at_k=sum(row.reciprocal_rank for row in selected) / len(selected),
                mean_recall_at_k=sum(row.recall_at_k for row in selected) / len(selected)))
    return BenchmarkReport(benchmark_ref=benchmark.benchmark_ref, benchmark_sha256=benchmark.content_hash,
        as_of=as_of, k=k, metrics=tuple(metrics), query_metrics=queries_by_mode)
