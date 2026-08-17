#!/usr/bin/env bash
# Runs the full observability + Chaos Mesh verification suite and prints a
# summary report. Exits non-zero if any check failed.
#
# Usage: verify/run-all.sh
# (or: make verify-observability)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"
trap cleanup_port_forwards EXIT

echo "Verification suite"
echo "observability namespace: ${NAMESPACE}  |  chaos-mesh namespace: ${CHAOS_MESH_NAMESPACE}  |  app namespace: ${APP_NAMESPACE}"

# shellcheck source=check-infra.sh
source "${SCRIPT_DIR}/check-infra.sh"
# shellcheck source=check-otel-obi.sh
source "${SCRIPT_DIR}/check-otel-obi.sh"
# shellcheck source=check-smoke-test.sh
source "${SCRIPT_DIR}/check-smoke-test.sh"
# shellcheck source=check-chaos-mesh.sh
source "${SCRIPT_DIR}/check-chaos-mesh.sh"

section "Summary"

pass_count=0
fail_count=0
declare -A component_seen

for row in "${RESULTS[@]}"; do
  IFS='|' read -r component check outcome detail <<< "${row}"
  component_seen["${component}"]=1
  if [[ "${outcome}" == "PASS" ]]; then
    pass_count=$((pass_count + 1))
  else
    fail_count=$((fail_count + 1))
  fi
done

for component in "${!component_seen[@]}"; do
  total=0
  failed=0
  for row in "${RESULTS[@]}"; do
    IFS='|' read -r c _ outcome _ <<< "${row}"
    [[ "${c}" != "${component}" ]] && continue
    total=$((total + 1))
    [[ "${outcome}" != "PASS" ]] && failed=$((failed + 1))
  done
  if [[ "${failed}" -eq 0 ]]; then
    printf '  %-14s %d/%d checks passed\n' "${component}" "${total}" "${total}"
  else
    printf '  %-14s %d/%d checks passed (\033[31m%d failing\033[0m)\n' "${component}" "$((total - failed))" "${total}" "${failed}"
  fi
done

echo
echo "Total: ${pass_count} passed, ${fail_count} failed"

if [[ "${fail_count}" -gt 0 ]]; then
  echo
  echo "Failing checks:"
  for row in "${RESULTS[@]}"; do
    IFS='|' read -r component check outcome detail <<< "${row}"
    [[ "${outcome}" == "PASS" ]] && continue
    printf '  - [%s] %s%s\n' "${component}" "${check}" "${detail:+ - ${detail}}"
  done
  exit 1
fi

exit 0
