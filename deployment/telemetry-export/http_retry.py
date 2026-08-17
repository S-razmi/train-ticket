"""Shared retry helper for the three exporters' HTTP calls.

Not just belt-and-suspenders: a chaos-mesh fault run against any service
on this single-node cluster shares the same node's CPU/memory/disk as
Prometheus/Jaeger/Loki themselves (there's nowhere else for it to run).
A stress-cpu experiment competing for that CPU is exactly the kind of
thing that makes a backend query run slow or time out - and that's most
likely to happen exactly when experiments/orchestrate.py is calling
these exporters right after a fault, which is the whole point of
running them. A transient timeout there shouldn't crash a multi-run
sweep outright.
"""
import time


def with_retries(fn, attempts=3, base_delay_seconds=2, retry_on=(Exception,)):
    """Calls fn() and returns its result, retrying on any exception in
    `retry_on` with exponential backoff. Re-raises the last exception if
    every attempt fails - this does not swallow a persistent failure,
    only smooths over a transient one."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return fn()
        except retry_on as e:
            last_exc = e
            if attempt < attempts - 1:
                delay = base_delay_seconds * (2 ** attempt)
                print(f"  (retrying after {type(e).__name__}: {e}; waiting {delay}s, attempt {attempt + 2}/{attempts})")
                time.sleep(delay)
    raise last_exc
