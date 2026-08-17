"""Pulls the feature table in metrics_map.py from kube-prometheus-stack's
Prometheus over a time window and writes metrics.csv.

CSV schema (long format - one row per timeseries point, not a wide pivot):
    timestamp, feature, category, pod, node, instance, service_name, value

Only the identifying label(s) a given series actually carries are filled
in (e.g. node_* series have no `pod`, container_* series have no
`service_name`) - the rest are left empty, not zero-filled, so missing
identity is visibly missing rather than mistakable for a real 0/None pod.
"""
import argparse
import csv
import sys

import requests

from metrics_map import FEATURE_QUERIES

IDENTITY_LABELS = ["pod", "node", "instance", "service_name"]


class MetricsExportError(RuntimeError):
    pass


def _query_range(prometheus_url, promql, start, end, step):
    resp = requests.get(
        f"{prometheus_url}/api/v1/query_range",
        params={"query": promql, "start": start, "end": end, "step": step},
        timeout=30,
    )
    if resp.status_code != 200:
        raise MetricsExportError(f"Prometheus query_range HTTP {resp.status_code}: {resp.text}")
    body = resp.json()
    if body.get("status") != "success":
        raise MetricsExportError(f"Prometheus query_range failed: {body}")
    return body["data"]["result"]


def export_metrics(prometheus_url, namespace, start, end, step, window, output_path):
    """Queries every feature in FEATURE_QUERIES over [start, end] and
    writes them to output_path as CSV. Returns a dict of
    {feature: series_count} so callers/CLI can report which features
    came back empty (not scraped in this cluster, or genuinely no data
    in this window) without that being silently swallowed."""
    series_counts = {}

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "feature", "category", "pod", "node", "instance", "service_name", "value"])

        for feature, (category, is_counter, template) in FEATURE_QUERIES.items():
            promql = template.format(namespace=namespace, window=window)
            if is_counter:
                promql = f"rate({promql}[{window}])"

            try:
                result = _query_range(prometheus_url, promql, start, end, step)
            except MetricsExportError as e:
                print(f"warning: feature '{feature}' query failed: {e}", file=sys.stderr)
                series_counts[feature] = 0
                continue

            series_counts[feature] = len(result)
            if not result:
                print(f"note: feature '{feature}' returned 0 series in this window (metric likely not scraped in this cluster, or genuinely no matching data)", file=sys.stderr)
                continue

            for series in result:
                labels = series["metric"]
                identity = [labels.get(label, "") for label in IDENTITY_LABELS]
                for ts, value in series["values"]:
                    writer.writerow([ts, feature, category, *identity, value])

    return series_counts


def _main():
    parser = argparse.ArgumentParser(description="Export Prometheus metrics for a time window to CSV")
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--start", required=True, type=int, help="unix timestamp (seconds)")
    parser.add_argument("--end", required=True, type=int, help="unix timestamp (seconds)")
    parser.add_argument("--step", default=15, type=int, help="query_range step, seconds")
    parser.add_argument("--window", default="1m", help="rate() window for counter metrics")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    counts = export_metrics(
        args.prometheus_url, args.namespace, args.start, args.end, args.step, args.window, args.output
    )
    empty = [f for f, c in counts.items() if c == 0]
    print(f"wrote {args.output}: {len(counts) - len(empty)}/{len(counts)} features returned data")
    if empty:
        print(f"features with no data: {', '.join(sorted(empty))}", file=sys.stderr)


if __name__ == "__main__":
    _main()
