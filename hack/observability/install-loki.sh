#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

RELEASE_NAME="loki"
NAMESPACE="observability"

CHART_REPO="grafana"
CHART_REPO_URL="https://grafana.github.io/helm-charts"
CHART_NAME="${CHART_REPO}/loki"
CHART_VERSION="7.3.0"

VALUES_FILE="${REPO_ROOT}/deployment/observability/loki/values.yaml"

echo "==> Adding Grafana Helm repository..."
helm repo add "${CHART_REPO}" "${CHART_REPO_URL}" 2>/dev/null || true

echo "==> Updating Helm repositories..."
helm repo update

echo "==> Installing/upgrading Loki..."

helm upgrade --install "${RELEASE_NAME}" \
    "${CHART_NAME}" \
    --version "${CHART_VERSION}" \
    --namespace "${NAMESPACE}" \
    --create-namespace \
    --values "${VALUES_FILE}" \
    --wait

echo "==> Loki installation completed."

echo
echo "==> Helm release:"
helm list -n "${NAMESPACE}"

echo
echo "==> Pods:"
kubectl get pods -n "${NAMESPACE}"
