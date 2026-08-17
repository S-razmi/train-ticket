"""Orchestrates metrics_export.py + logs_export.py + traces_export.py for
one time window and writes everything to disk under a single run
directory, alongside a run_info.json manifest.

This doesn't record anything itself - Prometheus/Jaeger/Loki are already
continuously recording (that's what deployment/observability/ is for).
This just pulls a specific window of what they've already recorded and
materializes it to local files, so a run's telemetry survives independent
of Prometheus/Jaeger's retention settings and can be handed off/archived
per experiment.

Typical use: call this right after a Chaos Mesh experiment
(deployment/fault-inject-deployment/chaos-mesh/inject.py) finishes,
passing the experiment's actual start/end wall-clock times.

Usage:
  python3 run_writer.py \\
      --service ts-travel-service --fault-type network-delay \\
      --start-time 1786971107 --end-time 1786971407 \\
      --output-dir data

writes to: data/<service>_<fault-type>/<run_id>/{metrics.csv, logs.jsonl, traces.csv, run_info.json}
run_id defaults to the start time if not given, so re-running with the
same window doesn't collide with a fresh timestamp-based id.
"""
import argparse
import json
import os
import sys
import time

from logs_export import export_logs
from metrics_export import export_metrics
from traces_export import export_traces


def run(
    service,
    fault_type,
    start_time,
    end_time,
    output_dir,
    run_id=None,
    prometheus_url="http://localhost:9090",
    jaeger_url="http://localhost:16686",
    loki_url="http://localhost:3100",
    loki_tenant="train-ticket",
    namespace="default",
    step=15,
    rate_window="1m",
    inject_time=None,
):
    if end_time <= start_time:
        raise ValueError(f"end_time ({end_time}) must be after start_time ({start_time})")

    run_id = run_id or str(start_time)
    run_dir = os.path.join(output_dir, f"{service}_{fault_type}", run_id)
    os.makedirs(run_dir, exist_ok=True)

    metrics_path = os.path.join(run_dir, "metrics.csv")
    logs_path = os.path.join(run_dir, "logs.jsonl")
    traces_path = os.path.join(run_dir, "traces.csv")
    manifest_path = os.path.join(run_dir, "run_info.json")

    print(f"==> writing metrics to {metrics_path}")
    metric_counts = export_metrics(
        prometheus_url, namespace, start_time, end_time, step, rate_window, metrics_path
    )

    print(f"==> writing logs to {logs_path}")
    log_count = export_logs(
        loki_url, loki_tenant, namespace, start_time * 1_000_000_000, end_time * 1_000_000_000, logs_path
    )

    print(f"==> writing traces to {traces_path}")
    span_counts = export_traces(
        jaeger_url, start_time * 1_000_000, end_time * 1_000_000, traces_path
    )

    manifest = {
        "service": service,
        "fault_type": fault_type,
        "run_id": run_id,
        "namespace": namespace,
        "start_time": start_time,
        "end_time": end_time,
        "duration_seconds": end_time - start_time,
        "inject_time": inject_time,
        "written_at": int(time.time()),
        "metrics": {
            "path": "metrics.csv",
            "features_with_data": sum(1 for c in metric_counts.values() if c > 0),
            "features_total": len(metric_counts),
            "empty_features": sorted(f for f, c in metric_counts.items() if c == 0),
        },
        "logs": {"path": "logs.jsonl", "line_count": log_count},
        "traces": {
            "path": "traces.csv",
            "total_spans": sum(span_counts.values()),
            "services": {s: c for s, c in span_counts.items() if c > 0},
        },
    }
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"==> wrote {manifest_path}")
    return manifest


def _main():
    parser = argparse.ArgumentParser(description="Export metrics+logs+traces for a fault-injection run")
    parser.add_argument("--service", required=True, help="target service the fault was injected against")
    parser.add_argument("--fault-type", required=True, help="e.g. network-delay, stress-cpu (see inject.py)")
    parser.add_argument("--start-time", required=True, type=int, help="unix timestamp (seconds)")
    parser.add_argument("--end-time", required=True, type=int, help="unix timestamp (seconds)")
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--jaeger-url", default="http://localhost:16686")
    parser.add_argument("--loki-url", default="http://localhost:3100")
    parser.add_argument("--loki-tenant", default="train-ticket")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--step", default=15, type=int)
    parser.add_argument("--rate-window", default="1m")
    parser.add_argument("--inject-time", default=None, type=int, help="unix timestamp fault injection actually started, if different from --start-time")
    args = parser.parse_args()

    try:
        manifest = run(
            args.service, args.fault_type, args.start_time, args.end_time, args.output_dir,
            run_id=args.run_id, prometheus_url=args.prometheus_url, jaeger_url=args.jaeger_url,
            loki_url=args.loki_url, loki_tenant=args.loki_tenant, namespace=args.namespace,
            step=args.step, rate_window=args.rate_window, inject_time=args.inject_time,
        )
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    _main()
