"""Pulls all logs for a namespace over a time window from Loki
(deployment/observability/loki/) and writes logs.jsonl.

JSON Lines rather than CSV: log lines are arbitrary free text that can
contain commas, embedded newlines, and quotes - CSV escaping that
correctly is more failure-prone than just writing one JSON object per
line.

Each line: {timestamp, pod, namespace, container, node, level, message}
`level` comes from Loki's own `detected_level` label (its built-in log
level parser), not a level we compute ourselves.

Loki's query_range has a per-request line limit (Loki server default is
5000); this paginates using the last returned entry's timestamp as the
next window's start until the window is exhausted, so a run that emits
more than one page of logs doesn't silently truncate.
"""
import argparse
import json
import sys

import requests

from http_retry import with_retries

LOKI_LINE_LIMIT = 5000
REQUEST_TIMEOUT = 60


class LogsExportError(RuntimeError):
    pass


def _query_range(loki_url, tenant, logql, start_ns, end_ns, limit):
    def _do_request():
        return requests.get(
            f"{loki_url}/loki/api/v1/query_range",
            params={"query": logql, "start": start_ns, "end": end_ns, "limit": limit, "direction": "forward"},
            headers={"X-Scope-OrgID": tenant},
            timeout=REQUEST_TIMEOUT,
        )

    resp = with_retries(_do_request, retry_on=(requests.exceptions.RequestException,))
    if resp.status_code != 200:
        raise LogsExportError(f"Loki query_range HTTP {resp.status_code}: {resp.text}")
    body = resp.json()
    if body.get("status") != "success":
        raise LogsExportError(f"Loki query_range failed: {body}")
    return body["data"]["result"]


def export_logs(loki_url, tenant, namespace, start_ns, end_ns, output_path):
    """Pulls every log line for `namespace` in [start_ns, end_ns) (both
    nanosecond unix timestamps, matching Loki's native precision) and
    writes them to output_path as JSON Lines. Returns the line count."""
    logql = f'{{k8s_namespace_name="{namespace}"}}'
    line_count = 0
    window_start = start_ns

    with open(output_path, "w") as f:
        while True:
            streams = _query_range(loki_url, tenant, logql, window_start, end_ns, LOKI_LINE_LIMIT)

            page_lines = 0
            latest_ts = window_start
            for stream in streams:
                labels = stream["stream"]
                for ts, message in stream["values"]:
                    record = {
                        "timestamp": ts,
                        "pod": labels.get("k8s_pod_name", ""),
                        "namespace": labels.get("k8s_namespace_name", ""),
                        "container": labels.get("k8s_container_name", ""),
                        "node": labels.get("k8s_node_name", ""),
                        "level": labels.get("detected_level", ""),
                        "message": message,
                    }
                    f.write(json.dumps(record) + "\n")
                    page_lines += 1
                    latest_ts = max(latest_ts, int(ts))

            line_count += page_lines

            if page_lines < LOKI_LINE_LIMIT:
                break
            # advance past the last line seen so the next page doesn't
            # re-fetch it (Loki's range end/start are inclusive/exclusive
            # at nanosecond precision, so +1ns is enough to move past it)
            window_start = latest_ts + 1

    return line_count


def _main():
    parser = argparse.ArgumentParser(description="Export Loki logs for a time window to JSON Lines")
    parser.add_argument("--loki-url", default="http://localhost:3100")
    parser.add_argument("--tenant", default="train-ticket", help="X-Scope-OrgID header value")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--start", required=True, type=int, help="unix timestamp (nanoseconds)")
    parser.add_argument("--end", required=True, type=int, help="unix timestamp (nanoseconds)")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    try:
        count = export_logs(args.loki_url, args.tenant, args.namespace, args.start, args.end, args.output)
    except LogsExportError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"wrote {args.output}: {count} log lines")


if __name__ == "__main__":
    _main()
