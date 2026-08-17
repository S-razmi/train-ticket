# Troubleshooting

Known issues encountered when deploying/running Train Ticket on Kubernetes (observed on a minikube cluster, nacos cluster mode with 3 replicas, external MySQL). Each entry lists the symptom, root cause, and the exact fix applied.

## 1. Nacos cluster stuck in "1.X mode" — new pods can't register

**Symptom**

A pod is stuck in `CrashLoopBackOff`. `kubectl logs <pod> --previous` shows:

```
org.springframework.context.ApplicationContextException: Failed to start bean 'webServerStartStop'
...
Caused by: com.alibaba.nacos.api.exception.NacosException: Request nacos server failed:
...
Caused by: com.alibaba.nacos.api.exception.NacosException: Nacos cluster is running with 1.X mode,
can't accept gRPC request temporarily. Please check the server status or close Double write to
force open 2.0 mode. Detail https://nacos.io/en-us/docs/2.0.0-upgrading.html.
```

Any Spring Boot service can hit this on startup, since they all register with nacos via gRPC (`nacos-client:2.0.3`). It tends to show up right after a nacos restart, a cluster-wide restart, or under normal operation once enough services are already connected.

**Root cause**

Nacos 2.x ships with a "gray upgrade" safety mechanism: new gRPC-based service registrations are rejected until the cluster's `readyToUpgrade` flag flips `true` on **all** nacos nodes. That flag only flips once nacos judges there's no more "legacy" (1.X-style) traffic hitting the cluster.

In this deployment (nacos-server `2.0.1`, 3 replicas, external MySQL for persistence) that flag can get stuck `false` on some or all nodes indefinitely — verified via:

```bash
kubectl exec <any-running-service-pod> -- curl -s http://nacos:8848/nacos/v1/ns/operator/servers \
  | grep -o '"readyToUpgrade":[a-z]*'
```

Restarting the nacos StatefulSet (`kubectl rollout restart statefulset/nacos`) can temporarily clear it for some services but is not reliable — it came back stuck (`false` on all 3 nodes) shortly after a full rolling restart in this deployment.

**Fix**

Nacos exposes an operator switch, `doubleWriteEnabled`, that the error message itself references. Disabling it forces the cluster into pure 2.0 gRPC mode, bypassing the stuck gray-upgrade check entirely:

```bash
# check current value
kubectl exec <any-running-service-pod> -- curl -s http://nacos:8848/nacos/v1/ns/operator/switches \
  | grep -o '"doubleWriteEnabled":[a-z]*'

# disable it (note: PUT, not POST — busybox wget can't do PUT, use curl)
kubectl exec <any-running-service-pod> -- curl -s -X PUT \
  "http://nacos:8848/nacos/v1/ns/operator/switches?entry=doubleWriteEnabled&value=false"

# restart the crashing pod(s) to pick it up
kubectl delete pod -l app=<service-name>
```

This is a cluster-wide nacos config change — it affects service registration for every service, not just the one that's currently crashing. No known downside in this deployment since every service already uses a gRPC-capable nacos client (`2.0.3`); there are no legacy 1.X clients relying on double-write.

**Notes for a fresh deployment**

- This issue is not tied to any specific service; it's a property of the nacos cluster's internal state. It's worth checking `doubleWriteEnabled` proactively after first bringing the cluster up, before assuming any individual service's crash is an app bug.
- A rolling restart of nacos (`kubectl rollout restart statefulset/nacos`) is a reasonable first thing to try since it's low-effort, but don't rely on it alone — verify `readyToUpgrade` afterward and fall back to disabling `doubleWriteEnabled` if it's still stuck.
- Restarting/reconfiguring nacos briefly disrupts service discovery cluster-wide (all ~50 microservices depend on it), so do this during a maintenance window if the cluster is otherwise healthy, not casually.

---

## 2. `ts-consign-price-service` crashes on every restart — duplicate key violation on init

**Symptom**

`ts-consign-price-service` is stuck in `CrashLoopBackOff`. `kubectl logs <pod> --previous` shows a Hibernate/MySQL error during startup, distinct from the nacos error above:

```
org.hibernate.exception.ConstraintViolationException: could not execute statement
...
Caused by: java.sql.SQLIntegrityConstraintViolationException: Duplicate entry '0' for key 'UK_lgier9psog5spnqru5luet1sv'
```

This happens on **every** restart after the service has successfully started at least once — not intermittently like the nacos issue above.

**Root cause**

`InitData.java` runs on every application startup (`CommandLineRunner`) and seeds a single config row (`index = 0`) by calling `ConsignPriceServiceImpl.createAndModifyPrice()`, passing a freshly generated `UUID` as the row's `id` every time.

That method correctly looks up the existing row by `index` first, but then unconditionally overwrote the looked-up row's `id` with the newly generated UUID before calling `repository.save(...)`:

```java
// ts-consign-price-service/src/main/java/consignprice/service/ConsignPriceServiceImpl.java (before fix)
ConsignPrice originalConfig;
if (repository.findByIndex(0) != null) {
    originalConfig = repository.findByIndex(0);
} else {
    originalConfig = new ConsignPrice();
}
originalConfig.setId(config.getId());   // <-- always overwrites the ID, even on an existing row
originalConfig.setIndex(0);
...
repository.save(originalConfig);
```

Because JPA/Hibernate identifies entities by `@Id`, changing the `id` of an already-persisted entity before `save()` makes Hibernate treat it as a *new* entity and issue an `INSERT` instead of an `UPDATE`. That `INSERT` collides with the unique constraint on `index` (since a row with `index = 0` already exists from the first successful startup), and the exception is not caught — it propagates up and crashes the app during startup, every time, forever.

**Fix**

Only set the `id` when actually creating a new row; leave the existing row's `id` untouched when updating:

```java
// ts-consign-price-service/src/main/java/consignprice/service/ConsignPriceServiceImpl.java (after fix)
ConsignPrice originalConfig;
if (repository.findByIndex(0) != null) {
    originalConfig = repository.findByIndex(0);
} else {
    originalConfig = new ConsignPrice();
    originalConfig.setId(config.getId());
}
originalConfig.setIndex(0);
...
repository.save(originalConfig);
```

Then rebuild and redeploy the service image:

```bash
JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64 mvn -pl ts-consign-price-service -am clean package -Dmaven.test.skip=true

eval $(minikube -p minikube docker-env)   # build straight into minikube's docker daemon
docker build -t siaraz/ts-consign-price-service:0.2.0 ts-consign-price-service

kubectl delete pod -l app=ts-consign-price-service   # imagePullPolicy: IfNotPresent won't re-pull on tag reuse — must delete the pod
```

**Notes for a fresh deployment**

- The project requires Java 8 to build (`<java.version>1.8</java.version>` in each service's `pom.xml`); if the build machine's default `mvn`/`java` resolves to a newer JDK, set `JAVA_HOME` explicitly per the command above.
- Deployment images use fixed tags (e.g. `:0.2.0`) with `imagePullPolicy: IfNotPresent`, so rebuilding the image alone does nothing until the running pod is deleted/recreated.
- This bug will resurface identically on a brand-new deployment as soon as the pod restarts a second time (e.g. after any node reschedule, OOM, or manual restart) — it is not a one-off, so the code fix (not just a one-time DB cleanup) is required for a durable deployment.

---

## 3. `sed: can't read s/nacos/nacos/g: No such file or directory` during `make deploy`

**Symptom**

Step `<3/3>` of `make deploy` prints:

```
sed: can't read s/nacos/nacos/g: No such file or directory
sed: can't read s/rabbitmq/rabbitmq/g: No such file or directory
```

**Root cause**

`hack/deploy/gen-mysql-secret.sh`'s `update_tt_dp_cm`/`update_tt_sw_dp_cm` functions detect the OS with:

```bash
if [ "$(uname)"="Darwin" ]; then
```

— missing the required spaces around `=` inside `[ ]`. Without them, `"$(uname)"="Darwin"` is parsed as a single non-empty string literal (e.g. `Linux=Darwin`), which `[ ]` always evaluates as true regardless of the actual OS. So this branch **always** runs the macOS-style `sed -i "" "script" file` form, even on Linux. GNU sed parses that differently from BSD sed: the empty string `""` becomes the sed script (a no-op) and the actual script string (`"s/nacos/${nacosCM}/g"`) is treated as the **filename** to edit — which doesn't exist, hence the error.

**Fix**

Add the missing spaces so the OS check actually works:

```bash
# hack/deploy/gen-mysql-secret.sh, both occurrences (update_tt_dp_cm and update_tt_sw_dp_cm)
if [ "$(uname)" = "Darwin" ]; then
```

**Notes for a fresh deployment**

- In the default-namespace case this bug is silently harmless — `nacosCM`/`rabbitmqCM` both equal their unsubstituted defaults (`nacos`/`rabbitmq`), so the intended substitution is a no-op anyway and `deploy.yaml` still ends up correct via the preceding `cp`. It will actually corrupt `deploy.yaml`/`sw_deploy.yaml` (leaving the literal placeholder names in place instead of the real per-namespace configmap names) the first time someone deploys into a **non-default namespace**, where the substitution is not a no-op. Worth fixing before that happens rather than after.

---


## 4. Services crash with `Connection refused` to MySQL — train-ticket's `tsdb-mysql` StatefulSet stuck at 0/0 replicas

**Symptom**

Several services with a MySQL dependency (e.g. `ts-auth-service`, `ts-config-service`, `ts-consign-price-service`, `ts-assurance-service`) are `Running` but their app logs show:

```
com.mysql.cj.exceptions.CJCommunicationsException: Communications link failure
...
Caused by: java.net.ConnectException: Connection refused
```

`kubectl get statefulset tsdb-mysql` shows `READY 0/0` — no MySQL pods at all — despite the `tsdb-mysql`/`tsdb-mysql-leader`/`tsdb-mysql-follower` Services existing and resolving fine.

**Root cause**

The `tsdb` Helm release (the train-ticket MySQL cluster, chart default `replicaCount: 3`) was healthy and had 3 bound PVCs (`data-tsdb-mysql-0/1/2`), but its StatefulSet's `spec.replicas` had been set to `0`. `helm get values tsdb` showed no values override that would explain it, so this was most likely a manual/accidental `kubectl scale ... --replicas=0` at some point, not something Helm did on its own. `kubectl describe statefulset tsdb-mysql` confirmed the pods had previously existed and been deleted (`SuccessfulDelete` events for ordinals 2, 1, 0 in sequence), consistent with a scale-down rather than a crash. See also issue #7 below — a leftover `tsdb` release from an incomplete `make reset-deploy` is a plausible way to end up here too.

**Fix**

The chart's own `values.yaml` says *"Please do not modify `replicaCount`, after the cluster is created"* — so the fix is to scale back to the original size, not reinstall (which is unnecessary and risks losing the Xenon/Raft cluster's established state):

```bash
kubectl scale statefulset tsdb-mysql -n default --replicas=3
kubectl rollout status statefulset/tsdb-mysql -n default --timeout=120s
```

The 3 pods reattach to their existing PVCs by name/ordinal and come back healthy; dependent services recover on their own (they retry the DB connection without needing a restart themselves).

**Notes for a fresh deployment**

- If you ever need to pause the cluster without tearing it down (e.g. freeing node resources), scaling `tsdb-mysql` (or any of the per-service MySQL StatefulSets, if using `--independent-db`) to `0` and back is safe **as long as you scale back to the exact original replica count** — don't treat it as a way to permanently resize the cluster.
- Check `kubectl get statefulset tsdb-mysql -n <namespace> -o jsonpath='{.spec.replicas}'` early if any MySQL-dependent service is crash-looping with a connection error — a one-line check that rules this cause in/out before digging into application logs.

---

## 5. `make reset-deploy` doesn't clean up the train-ticket MySQL release

**Symptom**

After `make reset-deploy` followed by `make deploy`, the mysql step fails:

```
level=ERROR msg="release name check failed" error="cannot reuse a name that is still in use"
Error: INSTALLATION FAILED: release name check failed: cannot reuse a name that is still in use
```

`helm list -A` shows a `tsdb` release still present from before the reset, often with a much older `LAST DEPLOYED` timestamp than the other releases the reset+redeploy cycle just created.

**Root cause**

`hack/deploy/reset.sh` uninstalls train-ticket-related Helm releases via:

```bash
helm ls -n $namespace | grep ts- | awk '{print $1}' | xargs helm uninstall -n $namespace
```

The train-ticket MySQL release is literally named `tsdb` (`tsMysqlName="tsdb"` in `hack/deploy/utils.sh`) — no `ts-` substring — so `grep ts-` never matches it, and it's silently skipped on every reset.

**Fix**

Promoted `tsMysqlName` from a variable local to one function into a shared top-level constant in `utils.sh` (alongside `tsUser`/`tsPassword`/`tsDB`), and added it to `reset.sh`'s cleanup:

```bash
# hack/deploy/reset.sh
helm ls -n $namespace | grep ts- | awk '{print $1}' | xargs helm uninstall -n $namespace
helm uninstall $tsMysqlName -n $namespace
```

**Notes for a fresh deployment**

- If you're running an older checkout without this fix, manually run `helm uninstall tsdb -n <namespace>` after `make reset-deploy`, before the next `make deploy`.
- `reset-deploy` intentionally does not delete the MySQL StatefulSets' PVCs (Helm's default behavior — avoids silent data loss on uninstall). If you want a fully clean slate including DB data, delete those PVCs manually after reset.

---

## 6. Docker Compose quickstart: `NAMESPACE`/`TAG` silently blank → invalid image reference

**Symptom**

`docker compose -f quickstart-docker-compose.yml up -d` (in `deployment/docker-compose-manifests/`) prints a wall of:

```
WARN[0000] The "NAMESPACE" variable is not set. Defaulting to a blank string.
WARN[0000] The "TAG" variable is not set. Defaulting to a blank string.
```

then fails with:

```
unable to get image '/ts-admin-basic-info-service:': Error response from daemon: invalid reference format
```

**Root cause**

`quickstart-docker-compose.yml` pulls pre-built images (`${NAMESPACE}/ts-xxx:${TAG}`) rather than building locally. It relies on a `.env` file in the same directory to supply `NAMESPACE`/`TAG`, but that `.env` is gitignored (root `.gitignore` excludes `.env`) and there's no committed `.env.example` — so a fresh clone never has one, and Compose silently defaults both vars to an empty string instead of failing loudly.

**Fix**

Create `deployment/docker-compose-manifests/.env`:

```bash
cat > deployment/docker-compose-manifests/.env <<'EOF'
NAMESPACE=codewisdom
TAG=latest
EOF
```

See issue #7 below before assuming any `NAMESPACE`/`TAG` combination will actually work end-to-end.

**Notes for a fresh deployment**

- This directory-local `.env` is separate from the root `.env` (which sets `IMG_REPO`/`IMG_TAG` for the *build-from-source* `docker-compose.yml` and is already correctly committed-equivalent via the `siaraz` namespace fix). Don't confuse the two — they're consumed by different compose files with different variable names.
- Since this `.env` is gitignored, it must be recreated on every fresh clone/new machine; consider committing a `.env.example` here to make this self-evident instead of failing silently on missing vars.

---

## 7. `codewisdom` Docker Hub images are ~4 years stale and tag-inconsistent across services

**Symptom**

After fixing issue #6, `docker compose -f quickstart-docker-compose.yml up -d` gets partway through pulling (`[+] up 47/47` shown, several images pulling) then fails on one specific service, e.g.:

```
Error response from daemon: failed to resolve reference "docker.io/codewisdom/ts-basic-service:1.0.1": ...not found
```

or the same for `ts-avatar-service:1.0.1`. Retrying with a different `TAG` fixes that one service but can break a different one.

**Root cause**

The `codewisdom` Docker Hub namespace (the upstream project's public pre-built images, last pushed ~4 years ago per `hub.docker.com/r/codewisdom/<service>/tags`) does not have a uniform tag across all ~47 `ts-*` repos. For example `codewisdom/ts-ui-dashboard` has tags up to `1.0.1`, but `codewisdom/ts-basic-service` and `codewisdom/ts-avatar-service` only go up to `1.0.0`/`latest`. There is no single `TAG` value guaranteed to exist for every service in this stack.

**Fix**

`TAG=latest` is present across every `codewisdom/ts-*` repo sampled and is the most reliable single value for the quickstart path. But the actually-recommended path for this fork is to **build from source** via the root `docker-compose.yml` instead of pulling from `codewisdom` at all — it builds every service locally and tags images under this fork's own maintained namespace (`siaraz`, per the root `.env`, fixed in commit `7d484dab`), sidestepping the stale/inconsistent upstream registry entirely:

```bash
JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64 mvn clean package -Dmaven.test.skip=true   # repo root
docker compose up -d --build                                                          # repo root
```

**Notes for a fresh deployment**

- Before trusting any `NAMESPACE`/`TAG` combination for the quickstart compose path, spot-check a couple of the less-common services (not just `ts-ui-dashboard`) on Docker Hub:
  ```bash
  curl -s "https://hub.docker.com/v2/repositories/codewisdom/<service>/tags/?page_size=20" | grep -o '"name":"[^"]*"'
  ```
- The `codewisdom` namespace appears effectively unmaintained; don't assume a fix here is durable — prefer the build-from-source path for anything beyond a quick disposable demo.

---

## 8. Fresh build machine has no Maven, and default Java is newer than the project targets

**Symptom**

`mvn clean package ...` fails with `mvn: command not found`, or (if some `mvn` is present) the build behaves inconsistently depending on which JDK it resolves.

**Root cause**

Every service's `pom.xml` pins `<java.version>1.8</java.version>`, but current OS installs (e.g. Ubuntu 24.04) ship a much newer default JDK (observed: OpenJDK 21) and don't include Maven at all by default.

**Fix**

Install both explicitly and pin `JAVA_HOME` for the build rather than relying on whatever `java`/`mvn` resolve to by default:

```bash
sudo apt update
sudo apt install maven openjdk-8-jdk -y
JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64 mvn clean package -Dmaven.test.skip=true   # run from repo root
```

**Notes for a fresh deployment**

- Sanity-check the JDK Maven will actually use before a full build: `JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64 mvn -version`.
- This is a one-time machine-setup step, not a code bug — it will recur on every new build machine/container until/unless the build is containerized (e.g. a Maven+JDK8 Docker image) so the host's installed JDK stops mattering.
- Also applies to rebuilding a single service after editing it (see issue #2 above) — same `JAVA_HOME` override is needed there.

---

## 9. Loki: `no org id` / empty results / Grafana "Unable to connect with Loki"

**Symptom**

Querying Loki directly through the gateway fails with a plain-text body:

```
no org id
```

Adding `X-Scope-OrgID` fixes that, but a query can still come back with an empty `data` array even though logs are definitely being pushed (e.g. `loki-canary` is visibly running). Separately, adding Loki as a Grafana data source and clicking "Save & test" gives a generic **"Unable to connect with Loki. Please check the server logs for more details."** — the gateway's own log for that request shows a `401`, not a connection-level error:

```
"GET /loki/api/v1/query?...vector(1)+vector(1)... HTTP/1.1" 401 ... "Grafana/13.1.3"
```

**Root cause**

This Loki install has `auth_enabled: true` (`kubectl get cm loki -n observability -o jsonpath='{.data.config\.yaml}'`), i.e. multi-tenant mode. Every request through the gateway must carry an `X-Scope-OrgID` header:

- Missing entirely → `no org id`.
- Present but for the wrong tenant → request succeeds (`"status":"success"`) but `data` is empty, since it's a valid query against a tenant that has no matching logs — easy to mistake for "Loki has no data" rather than "wrong tenant".
- Grafana doesn't send this header by default, so its own health-check query (`vector(1)+vector(1)`) gets rejected with `401`, which the UI only reports as a generic connection failure.

`loki-canary` pushes its logs under tenant `self-monitoring` (`-tenant-id=self-monitoring` in its DaemonSet args) — a convenient known-good tenant for sanity-checking a fresh install, but any app pushing to Loki without an explicit `-tenant-id`/tenant config will land under whatever that client's default tenant is, which may not be `self-monitoring`.

**Fix**

For direct API access, pass the header explicitly and use the tenant the data was actually written under:

```bash
kubectl port-forward -n observability svc/loki-gateway 3100:80
curl -s -H "X-Scope-OrgID: self-monitoring" "http://localhost:3100/loki/api/v1/labels"
```

For Grafana, add a custom HTTP header on the Loki data source (Connection settings → **Custom HTTP Headers**, not the Auth section):

- Header: `X-Scope-OrgID`
- Value: the tenant to query (e.g. `self-monitoring`)

Save & test again after adding it.

**Notes for a fresh deployment**

- `/loki/api/v1/query` is instant/metric-query only; log line queries (a plain LogQL stream selector) need `/loki/api/v1/query_range` instead — using the wrong endpoint returns `log queries are not supported as an instant query type`, not empty results. Don't mistake that for the tenant issue above.
- If you don't actually need multi-tenancy for this deployment, the alternative fix is setting `auth_enabled: false` in `deployment/observability/loki/values.yaml` so nothing needs the header at all — not done here since it wasn't clear multi-tenancy is unwanted, but worth considering if the header requirement keeps causing friction.

---

## 10. otel-obi: three separate first-deploy failures (bad config, OOM, no traces)

**Symptom / root cause / fix, three issues hit in sequence bringing up `deployment/observability/otel-obi/`:**

1. **`otelcol` `CrashLoopBackOff`**, logs show `unknown type: "loki" for id: "loki" (valid values: [... otlp])`. The dedicated `loki` exporter component has been removed from `otel/opentelemetry-collector-contrib` (confirmed absent at `0.122.0`). Fix: export logs via `otlphttp` against Loki's native OTLP endpoint instead — `endpoint: http://loki-gateway.observability.svc.cluster.local:80/otlp`, same `X-Scope-OrgID` header requirement as issue #9 above.
2. **`obi` `CrashLoopBackOff`**, logs show `wrong configuration: at least one of 'network', 'application' or 'stats' features must be enabled`. `OTEL_EBPF_METRICS_FEATURES` only accepts `network`, `application`, `stats` — not made-up values like `application_span`. Fix: set it to `application,network`.
3. **`obi` container stuck in "Network metrics mode" only** (no HTTP spans/traces produced) even after fixing #2, with only `starting OBI in Network metrics mode` in the logs and no `starting Application Observability mode` line. Root cause: `OTEL_EBPF_METRICS_FEATURES` controls the *metrics* pipeline; application-level span generation is a separate switch that needs a process-selection target — without one, OBI has nothing to attach uprobes to for HTTP tracing. Fix: add `OTEL_EBPF_AUTO_TARGET_EXE=*` to instrument every executable on the node (OBI's default excludes already skip itself, `otelcol`, and `kube-system`/`kube-node-lease` namespaces, so this is safe to leave broad on a single-purpose cluster like this one).
4. **`obi` `OOMKilled`** (exit code 137) within ~20-30s of restart, right after switching to Application Observability mode with the `*` target. Instrumenting ~50 microservices' processes simultaneously needs more than the initial `512Mi` limit. Fix: bumped `obi` container to `requests: 512Mi` / `limits: 2Gi` in `daemonset.yaml`.

**Notes for a fresh deployment**

- Discovery of already-running processes is not instant: after a clean start with `OTEL_EBPF_AUTO_TARGET_EXE=*`, `curl http://localhost:16686/api/services` initially reported only a couple of services and grew to the full ~56-service list over roughly a minute of driving traffic + waiting — don't conclude something's broken from an incomplete service list moments after rollout.
- Log records tailed by the `filelog` receiver carry no `trace_id` attribute, since none of the train-ticket services emit W3C trace context into their own log lines (they're not OTel-SDK-instrumented — that's the whole reason OBI/eBPF is used here). Trace-to-log correlation by timestamp + `k8s.pod.name`/`k8s.namespace.name` labels is possible; correlation by shared `trace_id` in Loki is not, unless the apps are changed to log it themselves.
- Verified end-to-end: a `POST /api/v1/travelservice/trips/left` request through `ts-gateway-service` produced a single Jaeger trace ID with properly nested parent/child spans across `ts-gateway-service` → `ts-travel-service` (proves W3C `traceparent` propagates through OBI's eBPF capture, not just isolated per-service spans); the same request's log lines appeared in Loki under the `train-ticket` tenant with correct `k8s_*` labels; and `http_client_request_duration_seconds` metrics for the same route/service pair appeared on the collector's `:9464` Prometheus endpoint, scraped successfully by kube-prometheus-stack via the `PodMonitor` (`podMonitor/observability/otel-obi/0` reports `health: up`).

---

## General diagnostic workflow used

```bash
# 1. Find unhealthy pods across all namespaces
kubectl get pods --all-namespaces | grep -v Running

# 2. Get the crash reason from the previous (crashed) container instance
kubectl logs <pod> --previous --tail=60

# 3. Cross-check: is it a nacos registration error, or something else (DB, config, etc.)?
#    grep for "NacosException" vs. other exception types before assuming it's the nacos cluster issue.

# 4. For nacos-related errors, check cluster state directly:
kubectl exec <any-running-service-pod> -- curl -s http://nacos:8848/nacos/v1/ns/operator/servers
kubectl exec <any-running-service-pod> -- curl -s http://nacos:8848/nacos/v1/ns/operator/switches

# 5. For ImagePullBackOff, get the exact pull error (bad tag vs. rate limit vs. something else)
#    from the pod's events, not just its status:
kubectl describe pod <pod> | tail -15

# 6. Cross-check a specific image:tag actually exists on Docker Hub before assuming the cluster/kubelet
#    is at fault:
curl -s "https://hub.docker.com/v2/repositories/<user>/<repo>/tags/<tag>/"                # 200 or 404
curl -s "https://hub.docker.com/v2/repositories/<user>/<repo>/tags/?page_size=20"         # what's actually pushed

# 7. For "connection refused" to a StatefulSet-backed dependency (MySQL, etc.), check desired vs.
#    actual replica count before assuming it's an application-level bug:
kubectl get statefulset <name> -n <namespace>
kubectl get statefulset <name> -n <namespace> -o jsonpath='{.spec.replicas}'
kubectl describe statefulset <name> -n <namespace> | tail -20   # recent scale/create/delete events

# 8. Check for stale/orphaned Helm releases (e.g. after a partial reset) before a fresh `helm install`
#    fails with "name still in use":
helm list -A
helm status <release> -n <namespace>
```
