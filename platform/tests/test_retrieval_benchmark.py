from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.learning.retrieval_benchmark import RetrievalBenchmark, run_benchmark


SEED = Path(__file__).parents[1] / "seeds/review/retrieval_benchmark_frozen_v3_20261006.json"
NOW = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)


@pytest.fixture
def benchmark():
    return RetrievalBenchmark.load(SEED)


def test_frozen_benchmark_three_modes_exact_source_citations_and_no_semantic_claim(benchmark, tmp_path):
    report = run_benchmark(benchmark, workspace=tmp_path, as_of=NOW)
    assert report.benchmark_sha256 == "24741c6c9770e9eb20d7d24a4946da8cff7d188be5ba3aae5e252c3f19f2f539"
    assert len(report.metrics) == 9
    assert set(report.query_metrics) == {"bm25", "hashed_lexical", "hybrid"}
    assert all(metric.mrr_at_k == metric.mean_recall_at_k == 1 for metric in report.metrics)
    assert not report.semantic_embedding_installed
    assert not report.causal_or_educational_effect_claim
    for mode, results in report.query_metrics.items():
        assert len(results) == 8
        for query in results:
            assert all(len(row["source_sha256"]) == 64 for row in query.source_refs)
            assert all(row["line"] and row["chunk_id"] for row in query.source_refs)
            assert all("path" not in row and "text" not in row for row in query.source_refs)


def test_replay_metrics_and_refs_independent_of_absolute_temp_path(benchmark, tmp_path):
    first = run_benchmark(benchmark, workspace=tmp_path / "first", as_of=NOW)
    second = run_benchmark(benchmark, workspace=tmp_path / "second", as_of=NOW)
    assert first == second
    with pytest.raises(FileExistsError):
        run_benchmark(benchmark, workspace=tmp_path / "first", as_of=NOW)


def test_metrics_measure_failures_not_hardcoded_perfect_result(benchmark, tmp_path):
    payload = benchmark.model_dump(mode="json")
    payload["queries"][0]["text"] = "water condensation"
    changed = RetrievalBenchmark.model_validate(payload)
    report = run_benchmark(changed, workspace=tmp_path, as_of=NOW)
    assert any(metric.mrr_at_k < 1 for metric in report.metrics)
    assert report.query_metrics["bm25"][0].reciprocal_rank == 0
    assert report.query_metrics["bm25"][0].recall_at_k == 0
    assert report.benchmark_sha256 != benchmark.content_hash


@pytest.mark.parametrize("corruption", ["path", "unknown", "badline", "duplicate", "missing_split", "conflict", "duplicate_qrel", "real"])
def test_invalid_frozen_benchmark_rejected(benchmark, corruption):
    payload = benchmark.model_dump(mode="json")
    if corruption == "path":
        payload["documents"][0]["filename"] = "../private.md"
    elif corruption == "unknown":
        payload["queries"][0]["relevant"][0]["source_id"] = "unknown"
    elif corruption == "badline":
        payload["queries"][0]["relevant"][0]["line"] = 99
    elif corruption == "duplicate":
        payload["documents"].append(payload["documents"][0])
    elif corruption == "missing_split":
        for query in payload["queries"]:
            query["split"] = "development"
    elif corruption == "conflict":
        payload["queries"][0]["kc_refs"] = ["UNKNOWN.KC"]
    elif corruption == "duplicate_qrel":
        payload["queries"][0]["relevant"].append(payload["queries"][0]["relevant"][0])
    else:
        payload["source_kind"] = "real_learning_data"
    with pytest.raises(ValueError):
        RetrievalBenchmark.model_validate(payload)


@pytest.mark.parametrize("k", [0, 51])
def test_invalid_benchmark_parameters(benchmark, tmp_path, k):
    with pytest.raises(ValueError):
        run_benchmark(benchmark, workspace=tmp_path, as_of=NOW, k=k)


def test_naive_timestamp_rejected(benchmark, tmp_path):
    with pytest.raises(ValueError, match="timezone"):
        run_benchmark(benchmark, workspace=tmp_path, as_of=NOW.replace(tzinfo=None))
