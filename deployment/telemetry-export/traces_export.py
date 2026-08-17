"""Pulls trace history for every pod of the application from Jaeger
(deployment/observability/jaeger/) over a time window and writes
traces.csv.

"Every pod of the application" here means every service Jaeger currently
knows about: OBI's discovery is already scoped to the app namespace only
(deployment/observability/otel-obi/obi-config.yaml, k8s_namespace:
default) - see TROUBLESHOOTING.md #10 - so /api/services already can't
return anything outside the app. No extra service-name filtering is
needed or applied here.

CSV schema: one row per span.
    traceID, spanID, parentSpanID, service, operationName, startTime,
    durationMicros, statusCode, httpMethod, httpRoute

statusCode/httpMethod/httpRoute are read from OTel semantic-convention
span tags OBI actually attaches - confirmed by inspecting real spans, not
assumed: OBI (v0.10.0, current OTel semconv) uses
http.response.status_code / http.request.method / http.route, not the
older http.status_code / http.method. Not every span has these (only
HTTP-handling spans do, e.g. client-side library spans in this codebase
generally lack a route), so they're left empty rather than defaulted to
something that looks like a real value.
"""
import argparse
import csv
import sys

import requests

from http_retry import with_retries

DEFAULT_LIMIT_PER_SERVICE = 500
REQUEST_TIMEOUT = 60


class TracesExportError(RuntimeError):
    pass


def _get(jaeger_url, path, params=None):
    resp = with_retries(
        lambda: requests.get(f"{jaeger_url}{path}", params=params, timeout=REQUEST_TIMEOUT),
        retry_on=(requests.exceptions.RequestException,),
    )
    if resp.status_code != 200:
        raise TracesExportError(f"Jaeger GET {path} HTTP {resp.status_code}: {resp.text}")
    return resp.json()


def _list_services(jaeger_url):
    body = _get(jaeger_url, "/api/services")
    return body.get("data") or []


def _tag_value(tags, key):
    for tag in tags or []:
        if tag.get("key") == key:
            return tag.get("value")
    return None


def export_traces(jaeger_url, start_micros, end_micros, output_path, limit_per_service=DEFAULT_LIMIT_PER_SERVICE):
    """Pulls traces for every service Jaeger knows about within
    [start_micros, end_micros] (Jaeger's native microsecond precision)
    and writes one row per span to output_path as CSV. Returns
    {service: span_count}."""
    services = _list_services(jaeger_url)
    span_counts = {}

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "traceID", "spanID", "parentSpanID", "service", "operationName",
            "startTime", "durationMicros", "statusCode", "httpMethod", "httpRoute",
        ])

        for service in services:
            try:
                body = _get(jaeger_url, "/api/traces", params={
                    "service": service, "start": start_micros, "end": end_micros, "limit": limit_per_service,
                })
            except TracesExportError as e:
                print(f"warning: fetching traces for service '{service}' failed: {e}", file=sys.stderr)
                span_counts[service] = 0
                continue

            traces = body.get("data") or []
            span_count = 0
            for trace in traces:
                processes = trace.get("processes", {})
                for span in trace.get("spans", []):
                    parent_ids = [r["spanID"] for r in span.get("references", []) if r.get("refType") == "CHILD_OF"]
                    process_service = processes.get(span["processID"], {}).get("serviceName", service)
                    tags = span.get("tags", [])
                    writer.writerow([
                        trace["traceID"],
                        span["spanID"],
                        parent_ids[0] if parent_ids else "",
                        process_service,
                        span.get("operationName", ""),
                        span.get("startTime", ""),
                        span.get("duration", ""),
                        _tag_value(tags, "http.response.status_code") or "",
                        _tag_value(tags, "http.request.method") or "",
                        _tag_value(tags, "http.route") or "",
                    ])
                    span_count += 1

            span_counts[service] = span_count
            if span_count == 0:
                print(f"note: service '{service}' had no spans in this window", file=sys.stderr)

    return span_counts


def _main():
    parser = argparse.ArgumentParser(description="Export Jaeger traces for a time window to CSV")
    parser.add_argument("--jaeger-url", default="http://localhost:16686")
    parser.add_argument("--start", required=True, type=int, help="unix timestamp (microseconds)")
    parser.add_argument("--end", required=True, type=int, help="unix timestamp (microseconds)")
    parser.add_argument("--limit-per-service", default=DEFAULT_LIMIT_PER_SERVICE, type=int)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    counts = export_traces(args.jaeger_url, args.start, args.end, args.output, args.limit_per_service)
    total_spans = sum(counts.values())
    empty = [s for s, c in counts.items() if c == 0]
    print(f"wrote {args.output}: {total_spans} spans across {len(counts)} services")
    if empty:
        print(f"services with no spans in this window: {', '.join(sorted(empty))}", file=sys.stderr)


if __name__ == "__main__":
    _main()
