#!/usr/bin/env bash
# Shared helpers for the verification scripts. Sourced (not executed) by
# run-all.sh and by each check script, so they share one process, one
# RESULTS array, and one set of port-forward PIDs to clean up.

NAMESPACE="observability"
APP_NAMESPACE="default"
CHAOS_MESH_NAMESPACE="chaos-mesh"

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

# check_helm_release <component> <release> <namespace>
# Checks the release exists AND its Helm status is 'deployed' - `helm
# status` alone exits 0 even for a 'failed' release, so the status field
# itself has to be inspected.
check_helm_release() {
  local component="$1" release="$2" ns="$3"
  local status_json
  status_json="$(helm status "${release}" -n "${ns}" -o json 2>/dev/null)"

  if [[ -z "${status_json}" ]]; then
    record "${component}" "helm release '${release}' deployed" "FAIL" "not found in namespace ${ns}"
    return
  fi

  local release_status
  release_status="$(echo "${status_json}" | jq -r '.info.status')"
  if [[ "${release_status}" == "deployed" ]]; then
    record "${component}" "helm release '${release}' deployed" "PASS"
  else
    record "${component}" "helm release '${release}' deployed" "FAIL" "status is '${release_status}', not 'deployed'"
  fi
}

# check_workload_kind_ready <component> <namespace> <kind> <desired-field> <ready-field>
# Workload-level readiness (Deployments/StatefulSets/DaemonSets), not
# per-pod-name matching: this stays correct regardless of how each chart
# names its pods, so it holds up on a fresh deploy or a chart version bump.
check_workload_kind_ready() {
  local component="$1" ns="$2" kind="$3" desired_field="$4" ready_field="$5"
  local items
  items="$(kubectl get "${kind}" -n "${ns}" -o json 2>/dev/null | jq '.items')"

  local total
  total="$(echo "${items}" | jq 'length')"
  if [[ "${total}" -eq 0 ]]; then
    return
  fi

  local not_ready_names
  not_ready_names="$(echo "${items}" | jq -r --arg d "${desired_field}" --arg r "${ready_field}" '
    [.[] | select(((.status[$r] // 0)) < ((.status[$d] // .spec.replicas // 1))) | .metadata.name] | join(", ")
  ')"

  if [[ -z "${not_ready_names}" ]]; then
    record "${component}" "${kind} workloads ready (${total} found)" "PASS"
  else
    record "${component}" "${kind} workloads ready (${total} found)" "FAIL" "not ready: ${not_ready_names}"
  fi
}
