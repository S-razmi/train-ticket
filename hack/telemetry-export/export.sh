#!/usr/bin/env bash
# Convenience wrapper: opens the three port-forwards run_writer.py needs
# (Prometheus/Jaeger/Loki, all in the observability namespace) and runs it
# with those local ports as defaults, cleaning up the port-forwards on
# exit regardless of how run_writer.py exits.
#
# Usage: same flags as run_writer.py, e.g.
#   hack/telemetry-export/export.sh --service ts-travel-service \
#     --fault-type network-delay --start-time 1786971406 --end-time 1786971586
#
# If you're calling run_writer.py from somewhere that already has network
# access to those backends (e.g. from inside the cluster, via Service
# DNS), skip this wrapper and call it directly with
# --prometheus-url/--jaeger-url/--loki-url pointed at that DNS instead.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
NAMESPACE="observability"

PF_PIDS=()
cleanup() {
  for pid in "${PF_PIDS[@]:-}"; do
    [[ -n "${pid}" ]] && kill "${pid}" > /dev/null 2>&1 || true
  done
}
trap cleanup EXIT

start_port_forward() {
  local target="$1" local_port="$2" remote_port="$3"
  local logfile="/tmp/telemetry-export-pf-${local_port}.log"
  kubectl port-forward -n "${NAMESPACE}" "${target}" "${local_port}:${remote_port}" \
    > "${logfile}" 2>&1 &
  local pid=$!
  PF_PIDS+=("${pid}")

  local waited=0
  while ! (exec 3<>"/dev/tcp/127.0.0.1/${local_port}") 2>/dev/null; do
    sleep 0.5
    waited=$((waited + 1))
    if [[ ${waited} -ge 20 ]]; then
      echo "error: port-forward to ${target} on :${local_port} did not come up in time" >&2
      exit 1
    fi
  done
  exec 3>&- 2>/dev/null || true

  # The readiness check above only proves *something* is listening on
  # that port - if it's a stray process from an earlier run rather than
  # the kubectl we just launched, our PID is already dead and cleanup()
  # won't actually be able to tear down whatever's really serving this
  # port. Fail loudly instead of silently proceeding against someone
  # else's forward.
  if ! kill -0 "${pid}" 2>/dev/null; then
    echo "error: port ${local_port} was already in use by another process (not the port-forward this script just started - see ${logfile})" >&2
    exit 1
  fi
}

echo "==> Opening port-forwards to Prometheus/Jaeger/Loki..."
start_port_forward "svc/kube-prometheus-stack-prometheus" 9090 9090
start_port_forward "svc/jaeger" 16686 16686
start_port_forward "svc/loki-gateway" 3100 80

echo "==> Running run_writer.py..."
python3 "${REPO_ROOT}/deployment/telemetry-export/run_writer.py" \
  --prometheus-url "http://localhost:9090" \
  --jaeger-url "http://localhost:16686" \
  --loki-url "http://localhost:3100" \
  "$@"
