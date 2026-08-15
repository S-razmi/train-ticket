#!/usr/bin/env bash

set -euo pipefail

RELEASE_NAME="kube-prometheus-stack"
NAMESPACE="observability"

echo "==> Uninstalling kube-prometheus-stack..."

if helm status "${RELEASE_NAME}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    helm uninstall "${RELEASE_NAME}" -n "${NAMESPACE}"
    echo "==> kube-prometheus-stack uninstalled."
else
    echo "==> Release not found. Nothing to uninstall."
fi