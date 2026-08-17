#!/usr/bin/env bash
# Runs a one-off, finite-duration load burst (job.yaml) and waits for it
# to complete, printing its stats. For continuous load left running
# through an experiment, use deploy.sh instead.

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

echo "==> Deleting any previous load-generator-job..."
kubectl delete job load-generator-job -n "${NAMESPACE}" --ignore-not-found

echo "==> Starting load-generator-job..."
kubectl apply -f "${MANIFEST_DIR}/job.yaml"

echo "==> Waiting for job to complete..."
kubectl wait --for=condition=complete --timeout=600s job/load-generator-job -n "${NAMESPACE}" || true

echo
echo "==> Job logs:"
kubectl logs -n "${NAMESPACE}" job/load-generator-job --tail=60
