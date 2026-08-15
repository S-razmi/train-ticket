#!/usr/bin/env bash

set -euo pipefail

RELEASE_NAME="kube-prometheus-stack"
NAMESPACE="observability"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VALUES_FILE="${REPO_ROOT}/deployment/observability/prometheus/values.yaml"

CHART_REPO="prometheus-community"
CHART_REPO_URL="https://prometheus-community.github.io/helm-charts"
CHART_NAME="${CHART_REPO}/kube-prometheus-stack"
CHART_VERSION="88.3.0"

echo "==> Adding Prometheus Community Helm repository..."
helm repo add "${CHART_REPO}" "${CHART_REPO_URL}" 2>/dev/null || true

echo "==> Updating Helm repositories..."
helm repo update

echo "==> Installing/upgrading kube-prometheus-stack..."

helm upgrade --install "${RELEASE_NAME}" \
    "${CHART_NAME}" \
    --version "${CHART_VERSION}" \
    --namespace "${NAMESPACE}" \
    --create-namespace \
    --values "${VALUES_FILE}" \
    --wait

echo "==> Prometheus installation completed."

echo
echo "==> Helm release:"
helm list -n "${NAMESPACE}"

echo
echo "==> Pods:"
kubectl get pods -n "${NAMESPACE}"