"""Tests for the parallel.py threaded workload runner."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from parallel import run_threaded_workload


def test_results_returned_in_input_order():
    """Results must come back in the same order as the input items."""
    items = [1, 2, 3, 4, 5]
    results = run_threaded_workload(lambda x: x * 10, items, threads=3)
    assert results == [10, 20, 30, 40, 50]


def test_runs_faster_than_sequential():
    """With sleeping tasks, threaded execution should be much faster than sequential."""
    def slow_fn(item):
        time.sleep(0.2)
        return item

    items = list(range(10))
    start = time.time()
    results = run_threaded_workload(slow_fn, items, threads=10)
    elapsed = time.time() - start

    assert results == items
    # Sequential would take 2.0s; threaded should finish well under 1s
    assert elapsed < 1.0


def test_empty_items_returns_empty_list():
    """Empty input produces empty output."""
    results = run_threaded_workload(lambda x: x, [], threads=5)
    assert results == []


def test_single_item():
    """Single item list works correctly."""
    results = run_threaded_workload(lambda x: x + 1, [41], threads=5)
    assert results == [42]


def test_exception_propagates():
    """If fn raises, the exception propagates to the caller."""
    def explode(item):
        raise ValueError("boom")

    try:
        run_threaded_workload(explode, [1], threads=1)
        assert False, "Should have raised"
    except ValueError as e:
        assert "boom" in str(e)
