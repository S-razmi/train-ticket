#!/usr/bin/env bash

set -euo pipefail

RELEASE_NAME="loki"
NAMESPACE="observability"

echo "==> Uninstalling Loki..."

if helm status "${RELEASE_NAME}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    helm uninstall "${RELEASE_NAME}" -n "${NAMESPACE}"
    echo "==> Loki uninstalled."
else
    echo "==> Release not found. Nothing to uninstall."
fi
