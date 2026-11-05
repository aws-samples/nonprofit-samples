# ========== THREADED WORKLOAD RUNNER ==========
# Fires a function across multiple threads to parallelize Bedrock API calls.
# Import this from any feature file to speed up batch processing.

import concurrent.futures


def run_threaded_workload(fn, items, threads=10):
    """Execute fn(item) in parallel across threads. Returns results in input order.

    Args:
        fn: callable that takes a single item and returns a result dict.
        items: list of work items to process.
        threads: number of concurrent workers (default 10).

    Returns:
        List of results in the same order as items.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as pool:
        futures = [pool.submit(fn, item) for item in items]
        return [f.result() for f in futures]
