#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

RELEASE_NAME="jaeger"
NAMESPACE="observability"

CHART_REPO="jaegertracing"
CHART_REPO_URL="https://jaegertracing.github.io/helm-charts"
CHART_NAME="${CHART_REPO}/jaeger"
CHART_VERSION="4.12.0"

VALUES_FILE="${REPO_ROOT}/deployment/observability/jaeger/values.yaml"

echo "==> Adding Jaeger Helm repository..."
helm repo add "${CHART_REPO}" "${CHART_REPO_URL}" 2>/dev/null || true

echo "==> Updating Helm repositories..."
helm repo update

echo "==> Installing/upgrading Jaeger..."

helm upgrade --install "${RELEASE_NAME}" \
    "${CHART_NAME}" \
    --version "${CHART_VERSION}" \
    --namespace "${NAMESPACE}" \
    --create-namespace \
    --values "${VALUES_FILE}" \
    --wait

echo "==> Jaeger installation completed."

echo
echo "==> Helm release:"
helm list -n "${NAMESPACE}"

echo
echo "==> Pods:"
kubectl get pods -n "${NAMESPACE}"
