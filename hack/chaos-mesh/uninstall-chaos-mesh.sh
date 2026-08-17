#!/usr/bin/env bash

set -euo pipefail

RELEASE_NAME="chaos-mesh"
NAMESPACE="chaos-mesh"

echo "==> Uninstalling Chaos Mesh..."

if helm status "${RELEASE_NAME}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    helm uninstall "${RELEASE_NAME}" -n "${NAMESPACE}"
    echo "==> Chaos Mesh uninstalled."
else
    echo "==> Release not found. Nothing to uninstall."
fi
