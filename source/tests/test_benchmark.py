"""The answer-key benchmark: keys only name real catalogue entries, and accuracy does not quietly regress."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "benchmark"))

import run_benchmark  # noqa: E402


@pytest.mark.parametrize("name,floor", [("briefs.json", 0.88), ("holdout.json", 0.90)])
def test_benchmark_stays_above_floor(name, floor):
    summary, rows = run_benchmark.run(name)
    assert summary["acceptable_layer_hits"] / summary["layer_decisions"] >= floor
    assert all(r["status"] in ("ok", "ok_with_warnings") for r in rows)
