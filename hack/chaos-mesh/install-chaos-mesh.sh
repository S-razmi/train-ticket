#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

RELEASE_NAME="chaos-mesh"
NAMESPACE="chaos-mesh"

CHART_REPO="chaos-mesh"
CHART_REPO_URL="https://charts.chaos-mesh.org"
CHART_NAME="${CHART_REPO}/chaos-mesh"
CHART_VERSION="2.8.3"

VALUES_FILE="${REPO_ROOT}/deployment/fault-inject-deployment/chaos-mesh/values.yaml"

echo "==> Adding Chaos Mesh Helm repository..."
helm repo add "${CHART_REPO}" "${CHART_REPO_URL}" 2>/dev/null || true

echo "==> Updating Helm repositories..."
helm repo update

echo "==> Installing/upgrading Chaos Mesh..."

helm upgrade --install "${RELEASE_NAME}" \
    "${CHART_NAME}" \
    --version "${CHART_VERSION}" \
    --namespace "${NAMESPACE}" \
    --create-namespace \
    --values "${VALUES_FILE}" \
    --wait

echo "==> Chaos Mesh installation completed."

echo
echo "==> Helm release:"
helm list -n "${NAMESPACE}"

echo
echo "==> Pods:"
kubectl get pods -n "${NAMESPACE}"
