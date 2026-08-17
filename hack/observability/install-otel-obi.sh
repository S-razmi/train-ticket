#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

NAMESPACE="observability"
MANIFEST_DIR="${REPO_ROOT}/deployment/observability/otel-obi"

echo "==> Applying otel-obi manifests..."
kubectl apply -f "${MANIFEST_DIR}"

echo "==> Waiting for otel-obi DaemonSet rollout..."
kubectl rollout status daemonset/otel-obi -n "${NAMESPACE}" --timeout=180s

echo "==> otel-obi installation completed."

echo
echo "==> Pods:"
kubectl get pods -n "${NAMESPACE}" -l app.kubernetes.io/name=otel-obi
