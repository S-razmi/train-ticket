#!/usr/bin/env bash
# Starts the continuous load generator (deployment.yaml) - meant to be run
# before a Chaos Mesh experiment and left running through it.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MANIFEST_DIR="${REPO_ROOT}/deployment/load-generator"
NAMESPACE="default"

echo "==> Generating ConfigMap from locustfile.py..."
kubectl create configmap load-generator-script \
    --from-file=locustfile.py="${MANIFEST_DIR}/locustfile.py" \
    -n "${NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f -

echo "==> Applying load-generator Deployment..."
kubectl apply -f "${MANIFEST_DIR}/deployment.yaml"

echo "==> Waiting for rollout..."
kubectl rollout status deployment/load-generator -n "${NAMESPACE}" --timeout=120s

echo "==> load-generator running."
echo
echo "==> Pods:"
kubectl get pods -n "${NAMESPACE}" -l app=load-generator
