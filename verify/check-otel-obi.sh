#!/usr/bin/env bash
# Checks the otel-obi DaemonSet (deployment/observability/otel-obi/) itself:
# resources exist, the DaemonSet is fully rolled out, and its metrics
# endpoint is actually being scraped by Prometheus.

section "otel-obi: DaemonSet, RBAC, and Prometheus scrape target"

check_resource_exists() {
  local kind="$1" name="$2" extra_args="${3:-}"
  if kubectl get "${kind}" "${name}" ${extra_args} > /dev/null 2>&1; then
    record "otel-obi" "${kind}/${name} exists" "PASS"
  else
    record "otel-obi" "${kind}/${name} exists" "FAIL" "not found"
  fi
}

check_resource_exists "serviceaccount" "otel-obi" "-n ${NAMESPACE}"
check_resource_exists "clusterrole" "otel-obi"
check_resource_exists "clusterrolebinding" "otel-obi"
check_resource_exists "configmap" "otel-obi-collector-config" "-n ${NAMESPACE}"
check_resource_exists "podmonitor" "otel-obi" "-n ${NAMESPACE}"

ds_status="$(kubectl get daemonset otel-obi -n "${NAMESPACE}" -o json 2>/dev/null)"
if [[ -z "${ds_status}" ]]; then
  record "otel-obi" "daemonset/otel-obi fully rolled out" "FAIL" "daemonset not found"
else
  desired="$(echo "${ds_status}" | jq '.status.desiredNumberScheduled')"
  ready="$(echo "${ds_status}" | jq '.status.numberReady')"
  if [[ "${desired}" -gt 0 && "${desired}" == "${ready}" ]]; then
    record "otel-obi" "daemonset/otel-obi fully rolled out (${ready}/${desired} nodes)" "PASS"
  else
    record "otel-obi" "daemonset/otel-obi fully rolled out" "FAIL" "${ready}/${desired} nodes ready"
  fi
fi

restarts="$(kubectl get pods -n "${NAMESPACE}" -l app.kubernetes.io/name=otel-obi -o json 2>/dev/null \
  | jq '[.items[].status.containerStatuses[]?.restartCount] | add // 0')"
if [[ "${restarts}" -eq 0 ]]; then
  record "otel-obi" "no container restarts" "PASS"
else
  record "otel-obi" "no container restarts" "FAIL" "${restarts} total restart(s) across otel-obi containers"
fi

# Confirm Prometheus is actually scraping the PodMonitor successfully, not
# just that the CRD object exists.
if start_port_forward "${NAMESPACE}" "svc/kube-prometheus-stack-prometheus" 19090 9090; then
  targets_json="$(curl -s --max-time 10 "http://127.0.0.1:19090/api/v1/targets" 2>/dev/null)"
  otel_obi_target="$(echo "${targets_json}" | jq -r '.data.activeTargets[]? | select(.scrapePool | test("otel-obi")) | .health' | head -1)"

  if [[ "${otel_obi_target}" == "up" ]]; then
    record "otel-obi" "Prometheus scrape target for otel-obi is up" "PASS"
  elif [[ -z "${otel_obi_target}" ]]; then
    record "otel-obi" "Prometheus scrape target for otel-obi is up" "FAIL" "no matching scrape target found yet"
  else
    record "otel-obi" "Prometheus scrape target for otel-obi is up" "FAIL" "target health is '${otel_obi_target}'"
  fi
else
  record "otel-obi" "Prometheus scrape target for otel-obi is up" "FAIL" "could not reach Prometheus API"
fi
