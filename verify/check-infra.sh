#!/usr/bin/env bash
# Checks the three Helm-installed backends (kube-prometheus-stack, jaeger,
# loki) are actually deployed and their pods are Running/Ready. Does not
# check whether data is flowing into them - that's check-smoke-test.sh.

section "Infra: Helm releases and pod readiness"

check_helm_release "infra" "kube-prometheus-stack" "${NAMESPACE}"
check_helm_release "infra" "jaeger" "${NAMESPACE}"
check_helm_release "infra" "loki" "${NAMESPACE}"

check_workload_kind_ready "infra" "${NAMESPACE}" "deployments" "replicas" "readyReplicas"
check_workload_kind_ready "infra" "${NAMESPACE}" "statefulsets" "replicas" "readyReplicas"
check_workload_kind_ready "infra" "${NAMESPACE}" "daemonsets" "desiredNumberScheduled" "numberReady"
