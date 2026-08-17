"""Maps feature keys to PromQL queries against kube-prometheus-stack's
Prometheus (the same instance otel-obi's PodMonitor feeds - see
deployment/observability/otel-obi/).

Every key here is either a literal Prometheus/OBI metric name, or - only
where a single number genuinely can't stand for a metric on its own,
i.e. one quantile of a histogram, or a status-filtered view of a
counter - that metric's real name plus a `:` suffix naming exactly what
was derived from it (`:p50`, `:4xx_5xx`). There is no separate alias
layer renaming one metric to look like a different one: earlier this
mapped `istio-latency-50`/`istio-request-total`/etc. to OBI's HTTP
histogram under fabricated Istio-shaped names (this cluster has no
Istio: no istio-injection, no istio_* series anywhere - confirmed via
`kubectl get pods/crd -A`); that layer is gone. Query and stored name
now match a single real signal (OBI's http_server_request_duration_seconds
histogram) instead of pretending to be two different sources
(`istio-latency-50`/`istio-latency-90`/... and `latency-50`/`latency-90`
collapsed into one quantile set, since only one real signal backs them).
Generic non-metric aliases that existed only to fill out a category
(`cpu`, `mem`, `socket`, `diskio`) were dropped outright rather than
pointed at a fabricated name - the real signals they stood in for
(node_cpu_seconds_total, node_memory_*_bytes, container_sockets,
node_disk_*_bytes_total) are already present here under their own names.

Every query was checked against this cluster's actual Prometheus
(`/api/v1/label/__name__/values`, ~2077 series) before being written
down, not assumed from the metric's "usual" name.

Only container_network_* remains genuinely unavailable here. This is NOT
a scrape-config drop (unlike the rest of this table's history - see
deployment/observability/prometheus/values.yaml's `kubelet.serviceMonitor.
cAdvisorMetricRelabelings` override, which fixed
container_cpu_user_seconds_total, container_cpu_system_seconds_total,
container_memory_swap, container_memory_mapped_file,
container_spec_cpu_quota, and container_spec_memory_limit_bytes - all
six confirmed present with real per-pod labels at cAdvisor's raw output,
`kubectl get --raw /api/v1/nodes/minikube/proxy/metrics/cadvisor`, and
now flowing into Prometheus after the override). container_network_* is
different: at that same raw endpoint it only ever appears at the root
cgroup level (`id="/"`, `pod=""`), never associated with an actual pod,
on this minikube+docker-driver cluster. Prometheus's relabeling
correctly drops that root-level noise (its "drop cgroup metrics with no
pod" rule) - there's no real per-pod series underneath for it to keep.
Its entries below keep the textbook-correct PromQL (portable to a
cluster where cAdvisor does associate it with pods) - metrics_export.py
reports these as empty rather than omitting them or faking a value.

Each entry is (category, is_counter, promql_template). `is_counter`
entries get wrapped in rate(...[{window}]) by metrics_export.py; gauges
and already-self-contained rate()/histogram_quantile() expressions are
queried as-is (is_counter=False). Templates use {namespace} and
{window} placeholders.
"""

# Container-level (cAdvisor, via kube-prometheus-stack's kubelet job) and
# node-level (node-exporter) metrics both exist in this cluster and use
# their textbook Prometheus names directly.
#
# NOTE: this cluster's cAdvisor scrape has no `container` label (only
# `pod`/`namespace`/`node`) - confirmed via /api/v1/query - so container_*
# queries below identify by pod, not by container name. Fine for
# train-ticket since almost every pod here runs exactly one container.

FEATURE_QUERIES = {
    # ---- Latency: OBI's own HTTP server duration histogram, the only
    # real latency signal this cluster has (no Istio) -----------------------
    "http_server_request_duration_seconds:p50": ("latency", False,
        'histogram_quantile(0.50, sum(rate(http_server_request_duration_seconds_bucket[{window}])) by (le, service_name))'),
    "http_server_request_duration_seconds:p90": ("latency", False,
        'histogram_quantile(0.90, sum(rate(http_server_request_duration_seconds_bucket[{window}])) by (le, service_name))'),
    "http_server_request_duration_seconds:p95": ("latency", False,
        'histogram_quantile(0.95, sum(rate(http_server_request_duration_seconds_bucket[{window}])) by (le, service_name))'),
    "http_server_request_duration_seconds:p99": ("latency", False,
        'histogram_quantile(0.99, sum(rate(http_server_request_duration_seconds_bucket[{window}])) by (le, service_name))'),

    # ---- Errors -------------------------------------------------------------
    "container_network_transmit_packets_dropped_total": ("errors", True,
        'container_network_transmit_packets_dropped_total{{namespace="{namespace}"}}'),
    "container_network_receive_packets_dropped_total": ("errors", True,
        'container_network_receive_packets_dropped_total{{namespace="{namespace}"}}'),
    "container_network_transmit_errors_total": ("errors", True,
        'container_network_transmit_errors_total{{namespace="{namespace}"}}'),
    "container_network_receive_errors_total": ("errors", True,
        'container_network_receive_errors_total{{namespace="{namespace}"}}'),
    "node_network_transmit_errs_total": ("errors", True, "node_network_transmit_errs_total"),
    "node_network_receive_errs_total": ("errors", True, "node_network_receive_errs_total"),
    "node_network_transmit_drop_total": ("errors", True, "node_network_transmit_drop_total"),
    "node_network_receive_drop_total": ("errors", True, "node_network_receive_drop_total"),
    # rate() of the same duration histogram's request count, filtered to
    # 4xx/5xx - a real counter selector (label filter, not aggregation),
    # so it takes the normal is_counter=True rate()-wrapping path same as
    # any other counter below; per-pod/service_name labels are preserved
    # automatically without needing an explicit sum(...) by (...).
    "http_server_request_duration_seconds_count:4xx_5xx": ("errors", True,
        'http_server_request_duration_seconds_count{{http_response_status_code=~"4..|5.."}}'),
    "container_memory_failures_total": ("errors", True,
        'container_memory_failures_total{{namespace="{namespace}"}}'),

    # ---- Traffic --------------------------------------------------------------
    "http_server_request_duration_seconds_count": ("traffic", True,
        "http_server_request_duration_seconds_count"),
    "http_server_response_body_size_bytes:p50": ("traffic", False,
        'histogram_quantile(0.50, sum(rate(http_server_response_body_size_bytes_bucket[{window}])) by (le, service_name))'),
    "http_server_response_body_size_bytes:p90": ("traffic", False,
        'histogram_quantile(0.90, sum(rate(http_server_response_body_size_bytes_bucket[{window}])) by (le, service_name))'),
    "http_server_response_body_size_bytes:p95": ("traffic", False,
        'histogram_quantile(0.95, sum(rate(http_server_response_body_size_bytes_bucket[{window}])) by (le, service_name))'),
    "http_server_response_body_size_bytes:p99": ("traffic", False,
        'histogram_quantile(0.99, sum(rate(http_server_response_body_size_bytes_bucket[{window}])) by (le, service_name))'),
    "container_network_receive_bytes_total": ("traffic", True,
        'container_network_receive_bytes_total{{namespace="{namespace}"}}'),
    "container_network_transmit_bytes_total": ("traffic", True,
        'container_network_transmit_bytes_total{{namespace="{namespace}"}}'),
    "container_network_receive_packets_total": ("traffic", True,
        'container_network_receive_packets_total{{namespace="{namespace}"}}'),
    "container_network_transmit_packets_total": ("traffic", True,
        'container_network_transmit_packets_total{{namespace="{namespace}"}}'),
    "container_fs_reads_total": ("traffic", True, 'container_fs_reads_total{{namespace="{namespace}"}}'),
    "container_fs_writes_total": ("traffic", True, 'container_fs_writes_total{{namespace="{namespace}"}}'),
    "container_fs_reads_bytes_total": ("traffic", True, 'container_fs_reads_bytes_total{{namespace="{namespace}"}}'),
    "container_fs_writes_bytes_total": ("traffic", True, 'container_fs_writes_bytes_total{{namespace="{namespace}"}}'),
    "node_disk_reads_completed_total": ("traffic", True, "node_disk_reads_completed_total"),
    "node_disk_writes_completed_total": ("traffic", True, "node_disk_writes_completed_total"),
    "node_disk_read_bytes_total": ("traffic", True, "node_disk_read_bytes_total"),
    "node_disk_written_bytes_total": ("traffic", True, "node_disk_written_bytes_total"),
    "node_network_receive_packets_total": ("traffic", True, "node_network_receive_packets_total"),
    "node_network_transmit_packets_total": ("traffic", True, "node_network_transmit_packets_total"),
    "kube_pod_status_phase": ("traffic", False,
        'kube_pod_status_phase{{namespace="{namespace}", phase="Running"}}'),

    # ---- Saturation -----------------------------------------------------------
    "node_cpu_seconds_total": ("saturation", True, "node_cpu_seconds_total"),
    "container_cpu_user_seconds_total": ("saturation", True,
        'container_cpu_user_seconds_total{{namespace="{namespace}"}}'),
    "container_cpu_system_seconds_total": ("saturation", True,
        'container_cpu_system_seconds_total{{namespace="{namespace}"}}'),
    "container_cpu_usage_seconds_total": ("saturation", True,
        'container_cpu_usage_seconds_total{{namespace="{namespace}"}}'),
    "container_spec_cpu_quota": ("saturation", False, 'container_spec_cpu_quota{{namespace="{namespace}"}}'),
    "container_memory_swap": ("saturation", False, 'container_memory_swap{{namespace="{namespace}"}}'),
    "container_memory_working_set_bytes": ("saturation", False,
        'container_memory_working_set_bytes{{namespace="{namespace}"}}'),
    "container_memory_max_usage_bytes": ("saturation", False,
        'container_memory_max_usage_bytes{{namespace="{namespace}"}}'),
    "container_memory_cache": ("saturation", False, 'container_memory_cache{{namespace="{namespace}"}}'),
    "container_memory_rss": ("saturation", False, 'container_memory_rss{{namespace="{namespace}"}}'),
    "container_memory_mapped_file": ("saturation", False,
        'container_memory_mapped_file{{namespace="{namespace}"}}'),
    "container_memory_usage_bytes": ("saturation", False,
        'container_memory_usage_bytes{{namespace="{namespace}"}}'),
    "container_spec_memory_limit_bytes": ("saturation", False,
        'container_spec_memory_limit_bytes{{namespace="{namespace}"}}'),
    "node_memory_Active_bytes": ("saturation", False, "node_memory_Active_bytes"),
    "node_memory_Inactive_bytes": ("saturation", False, "node_memory_Inactive_bytes"),
    "container_sockets": ("saturation", False, 'container_sockets{{namespace="{namespace}"}}'),
    "container_blkio_device_usage_total": ("saturation", True,
        'container_blkio_device_usage_total{{namespace="{namespace}"}}'),
}
