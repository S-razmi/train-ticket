#!/usr/bin/env bash
# Checks Chaos Mesh (deployment/fault-inject-deployment/chaos-mesh/) is
# installed and actually able to inject and recover a fault - not just
# that its pods are up, but that the whole inject.py -> Chaos Mesh ->
# kernel path works end to end against a real train-ticket pod.

section "Chaos Mesh: install and CRDs"

check_helm_release "chaos-mesh" "chaos-mesh" "${CHAOS_MESH_NAMESPACE}"
check_workload_kind_ready "chaos-mesh" "${CHAOS_MESH_NAMESPACE}" "deployments" "replicas" "readyReplicas"
check_workload_kind_ready "chaos-mesh" "${CHAOS_MESH_NAMESPACE}" "daemonsets" "desiredNumberScheduled" "numberReady"

check_crd_exists() {
  local crd="$1"
  if kubectl get crd "${crd}" > /dev/null 2>&1; then
    record "chaos-mesh" "CRD ${crd} registered" "PASS"
  else
    record "chaos-mesh" "CRD ${crd} registered" "FAIL" "not found"
  fi
}

check_crd_exists "stresschaos.chaos-mesh.org"
check_crd_exists "iochaos.chaos-mesh.org"
check_crd_exists "networkchaos.chaos-mesh.org"

section "Chaos Mesh: fault injection end-to-end (inject.py)"

INJECT_PY="${SCRIPT_DIR}/../deployment/fault-inject-deployment/chaos-mesh/inject.py"
TARGET_SERVICE="ts-travel-service"

if ! python3 -c "import yaml" 2>/dev/null; then
  record "chaos-mesh" "inject.py end-to-end injection/cleanup" "FAIL" "PyYAML not installed (pip install -r deployment/fault-inject-deployment/chaos-mesh/requirements.txt)"
elif ! kubectl get pods -n "${APP_NAMESPACE}" -l "app=${TARGET_SERVICE}" --no-headers 2>/dev/null | grep -q .; then
  record "chaos-mesh" "inject.py end-to-end injection/cleanup" "FAIL" "no ${TARGET_SERVICE} pod found in namespace ${APP_NAMESPACE} to target"
else
  experiment_name="$(python3 "${INJECT_PY}" inject "${TARGET_SERVICE}" network-delay \
    --duration 30s --param latency=200ms 2>/tmp/verify-inject-stderr.log)"

  if [[ -z "${experiment_name}" ]]; then
    record "chaos-mesh" "inject.py creates a NetworkChaos experiment" "FAIL" "$(cat /tmp/verify-inject-stderr.log)"
  else
    record "chaos-mesh" "inject.py creates a NetworkChaos experiment" "PASS" "name=${experiment_name}"

    injected="false"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      injected="$(kubectl get networkchaos "${experiment_name}" -n "${APP_NAMESPACE}" -o json 2>/dev/null \
        | jq -r '[.status.conditions[]? | select(.type=="AllInjected") | .status] | first // "false"')"
      [[ "${injected}" == "True" ]] && break
      sleep 1
    done

    if [[ "${injected}" == "True" ]]; then
      record "chaos-mesh" "experiment actually injected into target pod (AllInjected)" "PASS"
    else
      record "chaos-mesh" "experiment actually injected into target pod (AllInjected)" "FAIL" "condition never reached True within 10s"
    fi

    python3 "${INJECT_PY}" cleanup "${experiment_name}" 2>/tmp/verify-cleanup-stderr.log
    cleanup_exit=$?

    if [[ ${cleanup_exit} -ne 0 ]]; then
      record "chaos-mesh" "cleanup() removes the experiment" "FAIL" "$(cat /tmp/verify-cleanup-stderr.log)"
    elif kubectl get networkchaos "${experiment_name}" -n "${APP_NAMESPACE}" > /dev/null 2>&1; then
      record "chaos-mesh" "cleanup() removes the experiment" "FAIL" "object still exists after cleanup()"
    else
      record "chaos-mesh" "cleanup() removes the experiment" "PASS"
    fi
  fi
fi
