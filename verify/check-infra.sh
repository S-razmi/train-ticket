#!/usr/bin/env bash
# Checks the three Helm-installed backends (kube-prometheus-stack, jaeger,
# loki) are actually deployed and their pods are Running/Ready. Does not
# check whether data is flowing into them - that's check-smoke-test.sh.

section "Infra: Helm releases and pod readiness"

check_helm_release() {
  local release="$1"
  local status_json
  status_json="$(helm status "${release}" -n "${NAMESPACE}" -o json 2>/dev/null)"

  if [[ -z "${status_json}" ]]; then
    record "infra" "helm release '${release}' deployed" "FAIL" "not found in namespace ${NAMESPACE}"
    return
  fi

  local release_status
  release_status="$(echo "${status_json}" | jq -r '.info.status')"
  if [[ "${release_status}" == "deployed" ]]; then
    record "infra" "helm release '${release}' deployed" "PASS"
  else
    record "infra" "helm release '${release}' deployed" "FAIL" "status is '${release_status}', not 'deployed'"
  fi
}

check_helm_release "kube-prometheus-stack"
check_helm_release "jaeger"
check_helm_release "loki"

# Workload-level readiness (Deployments/StatefulSets/DaemonSets), not
# per-pod-name matching: this stays correct regardless of how each chart
# names its pods, so it holds up on a fresh deploy or a chart version bump.
check_workload_kind_ready() {
  local kind="$1" desired_field="$2" ready_field="$3"
  local items
  items="$(kubectl get "${kind}" -n "${NAMESPACE}" -o json 2>/dev/null | jq '.items')"

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
    record "infra" "${kind} workloads ready (${total} found)" "PASS"
  else
    record "infra" "${kind} workloads ready (${total} found)" "FAIL" "not ready: ${not_ready_names}"
  fi
}

check_workload_kind_ready "deployments" "replicas" "readyReplicas"
check_workload_kind_ready "statefulsets" "replicas" "readyReplicas"
check_workload_kind_ready "daemonsets" "desiredNumberScheduled" "numberReady"
