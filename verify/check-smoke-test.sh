#!/usr/bin/env bash
# The actual end-to-end smoke test: drives one real request through the
# train-ticket app and verifies the resulting telemetry shows up correctly
# in all three backends. Existence/readiness of the pieces (checked by
# check-infra.sh and check-otel-obi.sh) does not prove data is flowing or
# that trace context propagates across service calls - only this does.

section "Smoke test: drive traffic, verify traces/logs/metrics"

GATEWAY_SVC="ts-gateway-service"
GATEWAY_PORT="18888"
TRIP_ENDPOINT="/api/v1/travelservice/trips/left"

# --- Step 1: drive a real request through the app ---------------------

if ! start_port_forward "${APP_NAMESPACE}" "svc/${GATEWAY_SVC}" 18888 "${GATEWAY_PORT}"; then
  record "smoke-test" "gateway reachable, trip-query request succeeds" "FAIL" "could not reach svc/${GATEWAY_SVC} in namespace ${APP_NAMESPACE}"
  return 0 2>/dev/null || exit 0
fi

http_code=""
for _ in 1 2 3 4 5; do
  http_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
    -X POST "http://127.0.0.1:18888${TRIP_ENDPOINT}" \
    -H "Content-Type: application/json" \
    -d '{"startingPlace":"shanghai","endPlace":"beijing","departureTime":"2026-08-18"}')"
  sleep 2
done

if [[ "${http_code}" == "200" ]]; then
  record "smoke-test" "gateway reachable, trip-query request succeeds" "PASS"
else
  record "smoke-test" "gateway reachable, trip-query request succeeds" "FAIL" "last HTTP status: ${http_code:-<no response>}"
  return 0 2>/dev/null || exit 0
fi

echo "  (waiting for telemetry pipeline to flush...)"
sleep 15

# --- Step 2: find the resulting trace and check span nesting -----------

if ! start_port_forward "${NAMESPACE}" "svc/jaeger" 16686 16686; then
  record "smoke-test" "trace with nested cross-service spans in Jaeger" "FAIL" "could not reach Jaeger query API"
else
  # Filter by operation (not just service): ts-gateway-service also emits
  # a constant stream of unrelated spans (nacos heartbeats/registration),
  # which at limit=10 by recency can crowd out the trip-query trace we
  # actually triggered above.
  traces_json="$(curl -sG --max-time 15 \
    --data-urlencode "service=${GATEWAY_SVC}" \
    --data-urlencode "operation=POST ${TRIP_ENDPOINT}" \
    --data-urlencode "limit=20" \
    "http://127.0.0.1:16686/api/traces" 2>/dev/null)"

  # Look for a trace containing the trip-query route with a span in
  # ts-gateway-service whose child is in a *different* service - proof
  # that trace context (traceparent) propagated across the wire, not just
  # that each service independently emitted isolated spans.
  cross_service_trace="$(echo "${traces_json}" | jq -r --arg route "${TRIP_ENDPOINT}" '
    .data[]? as $t
    | ($t.processes) as $procs
    | [ $t.spans[] | {spanID, svc: $procs[.processID].serviceName, parents: [.references[]? | select(.refType=="CHILD_OF") .spanID]} ] as $spans
    | ($spans | map(select(.svc == "'"${GATEWAY_SVC}"'")) | map(.spanID)) as $gw_span_ids
    | ($spans | map(select(
        (.parents | length) > 0
        and ([.parents[] | IN($gw_span_ids[])] | any)
        and (.svc != "'"${GATEWAY_SVC}"'")
      ))) as $downstream
    | select(($downstream | length) > 0)
    | $t.traceID
  ' | head -1)"

  if [[ -n "${cross_service_trace}" && "${cross_service_trace}" != "null" ]]; then
    record "smoke-test" "trace with nested cross-service spans in Jaeger" "PASS" "traceID=${cross_service_trace}"
  else
    record "smoke-test" "trace with nested cross-service spans in Jaeger" "FAIL" "no trace found where a ${GATEWAY_SVC} span parents a span in a different service"
  fi
fi

# --- Step 3: confirm logs for the app landed in Loki --------------------

if ! start_port_forward "${NAMESPACE}" "svc/loki-gateway" 3100 80; then
  record "smoke-test" "recent log lines present in Loki" "FAIL" "could not reach Loki gateway"
else
  loki_tenant="${LOKI_TENANT:-train-ticket}"
  loki_result="$(curl -s --max-time 15 -H "X-Scope-OrgID: ${loki_tenant}" \
    "http://127.0.0.1:3100/loki/api/v1/query" \
    --data-urlencode 'query=count_over_time({k8s_namespace_name="'"${APP_NAMESPACE}"'"}[5m])' 2>/dev/null)"

  line_count="$(echo "${loki_result}" | jq '[.data.result[]?.value[1] | tonumber] | add // 0')"
  if [[ "${line_count}" -gt 0 ]]; then
    record "smoke-test" "recent log lines present in Loki" "PASS" "${line_count} line(s) in the last 5m"
  else
    record "smoke-test" "recent log lines present in Loki" "FAIL" "no log lines found for namespace ${APP_NAMESPACE} under tenant '${loki_tenant}'"
  fi
fi

# --- Step 4: confirm HTTP metrics for the request landed on otelcol -----

if ! start_port_forward "${NAMESPACE}" "daemonset/otel-obi" 9464 9464; then
  record "smoke-test" "HTTP metrics for the request present" "FAIL" "could not reach otelcol metrics endpoint"
else
  metrics="$(curl -s --max-time 10 "http://127.0.0.1:9464/metrics" 2>/dev/null)"
  metric_lines="$(echo "${metrics}" | grep -c "http_.*${GATEWAY_SVC}" || true)"

  if [[ "${metric_lines}" -gt 0 ]]; then
    record "smoke-test" "HTTP metrics for the request present" "PASS" "${metric_lines} matching series"
  else
    record "smoke-test" "HTTP metrics for the request present" "FAIL" "no http_* series found for ${GATEWAY_SVC}"
  fi
fi
