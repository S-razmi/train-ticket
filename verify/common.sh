#!/usr/bin/env bash
# Shared helpers for the verification scripts. Sourced (not executed) by
# run-all.sh and by each check script, so they share one process, one
# RESULTS array, and one set of port-forward PIDs to clean up.

NAMESPACE="observability"
APP_NAMESPACE="default"

# Each entry: "component|check|status|detail"
RESULTS=()
PF_PIDS=()

record() {
  local component="$1" check="$2" outcome="$3" detail="${4:-}"
  RESULTS+=("${component}|${check}|${outcome}|${detail}")
  if [[ "${outcome}" == "PASS" ]]; then
    printf '  [\033[32mPASS\033[0m] %s\n' "${check}"
  else
    printf '  [\033[31mFAIL\033[0m] %s%s\n' "${check}" "${detail:+ - ${detail}}"
  fi
}

section() {
  echo
  echo "== $1 =="
}

# start_port_forward <namespace> <k8s-target> <local-port> <remote-port>
# e.g. start_port_forward observability svc/jaeger 16686 16686
# Registers the background kubectl process for cleanup on exit and blocks
# until the local port actually accepts connections (or times out).
start_port_forward() {
  local ns="$1" target="$2" local_port="$3" remote_port="$4"
  kubectl port-forward -n "${ns}" "${target}" "${local_port}:${remote_port}" \
    > "/tmp/verify-pf-${local_port}.log" 2>&1 &
  PF_PIDS+=("$!")

  local waited=0
  while ! (exec 3<>"/dev/tcp/127.0.0.1/${local_port}") 2>/dev/null; do
    sleep 0.5
    waited=$((waited + 1))
    if [[ ${waited} -ge 20 ]]; then
      return 1
    fi
  done
  exec 3>&- 2>/dev/null || true
  return 0
}

cleanup_port_forwards() {
  for pid in "${PF_PIDS[@]:-}"; do
    [[ -n "${pid}" ]] && kill "${pid}" > /dev/null 2>&1 || true
  done
}
