"""Run the frozen synthetic lexical retrieval benchmark in a temporary corpus."""
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.learning.retrieval_benchmark import RetrievalBenchmark, run_benchmark


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=root / "seeds/review/retrieval_benchmark_frozen_v3_20261006.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--k", type=int, default=3)
    arguments = parser.parse_args()
    if arguments.output and arguments.output.exists():
        parser.error("output exists; choose a new evaluation report path")
    benchmark = RetrievalBenchmark.load(arguments.seed)
    with tempfile.TemporaryDirectory(prefix="eduagent-retrieval-benchmark-") as directory:
        report = run_benchmark(benchmark, workspace=Path(directory), as_of=datetime.now(timezone.utc), k=arguments.k)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
