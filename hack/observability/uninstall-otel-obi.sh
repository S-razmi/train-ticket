#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MANIFEST_DIR="${REPO_ROOT}/deployment/observability/otel-obi"

echo "==> Deleting otel-obi manifests..."
kubectl delete -f "${MANIFEST_DIR}" --ignore-not-found

echo "==> otel-obi uninstalled."
