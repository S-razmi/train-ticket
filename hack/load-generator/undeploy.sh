#!/usr/bin/env bash
# Stops the continuous load generator started by deploy.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MANIFEST_DIR="${REPO_ROOT}/deployment/load-generator"
NAMESPACE="default"

echo "==> Deleting load-generator Deployment..."
kubectl delete -f "${MANIFEST_DIR}/deployment.yaml" --ignore-not-found

echo "==> Deleting ConfigMap..."
kubectl delete configmap load-generator-script -n "${NAMESPACE}" --ignore-not-found

echo "==> load-generator stopped."
