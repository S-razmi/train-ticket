#!/usr/bin/env bash

set -euo pipefail

RELEASE_NAME="jaeger"
NAMESPACE="observability"

echo "==> Uninstalling Jaeger..."

if helm status "${RELEASE_NAME}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    helm uninstall "${RELEASE_NAME}" -n "${NAMESPACE}"
    echo "==> Jaeger uninstalled."
else
    echo "==> Release not found. Nothing to uninstall."
fi
