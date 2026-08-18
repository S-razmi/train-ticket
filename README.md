
# Train Ticket：A Benchmark Microservice System
# <img src="./image/logo.png">

The project is a train ticket booking system based on microservice architecture which contains 41+ microservices. The programming languages and frameworks it uses are:
- Java - Spring Boot, Spring Cloud
- Node.js - Express
- Python - Flask
- Go - Webgo
- DB - MongoDB, MySQL

You can get more details at the original project's [Wiki Pages](https://github.com/FudanSELab/train-ticket/wiki).

## About This Fork

The original project hasn't been updated in several years, and its deployment tooling had bit-rotted
to the point of not working at all: `docker-compose.yml` referenced source directories that had since
been deleted or renamed, several running services were never wired up to a database at all, Docker
Hub removed the base images the Dockerfiles depended on, and the bundled service-discovery/gateway
setup was incomplete. None of it ran out of the box anymore.

**This fork fixes all of that.** `docker-compose.yml` and the affected Dockerfiles/configs have been
repaired so the full application — all 44 services, service discovery, and every service's database —
builds and runs cleanly on current Docker/MySQL/Python versions, with a working login and booking flow.
The Kubernetes deployment path has also been fixed and verified end-to-end (tested on minikube):
images now point at this fork's own `siaraz` Docker Hub account instead of the abandoned `codewisdom`
ones, and several deploy-script bugs that broke a from-scratch deploy have been fixed. See
[What We Fixed](#what-we-fixed) below for the full list of issues and how each was resolved.

## Service Architecture Graph
![architecture](./image/2.png)

## Quick Start (Docker Compose — Recommended)

This is the verified, working path. It builds every service from source and runs the full stack
locally with Docker Compose.

**Prerequisites:** Java 8 + Maven, Docker, Docker Compose v2.

```bash
git clone --depth=1 https://github.com/FudanSELab/train-ticket.git
cd train-ticket/

# build all service jars
JAVA_HOME=<path-to-a-JDK-8-install> mvn clean package -Dmaven.test.skip=true

# build images and start everything
docker compose build
docker compose up -d
```

Give it a couple of minutes — this brings up ~90 containers, including ~45 JVMs, so first startup is
CPU-heavy and some services take up to a minute to finish registering with service discovery. Once
`docker compose ps` shows everything `Up`, open **http://localhost:8080**.

There's a seeded test account baked into the application: username `fdse_microservice`, password
`111111` (an `admin` / `222222` account also exists). You can also register a new account from the UI.

To stop everything without losing data: `docker compose stop`. To tear down entirely: `docker compose down`.

## Alternative: Kubernetes

This path has now been fixed and verified on a real cluster (tested on minikube) as part of this fork.
It no longer deploys the project's original abandoned Docker Hub images (`codewisdom/*:latest`) —
`deploy.yaml.sample` and `sw_deploy.yaml.sample` now point at this fork's own rebuilt images under the
`siaraz` Docker Hub account, all pinned to a single tag (`0.2.0`) matching what `build_upload_image.py`
actually pushes. See [What We Fixed](#what-we-fixed) for the specific bugs found and fixed while
getting this path working.

**Prerequisites:** an existing Kubernetes cluster, [Helm](https://helm.sh/docs/helm/helm_install/),
and PVC support (e.g. [OpenEBS localPV](https://openebs.io/docs/2.12.x/user-guides/installation)).

```bash
git clone --depth=1 https://github.com/S-razmi/train-ticket.git
cd train-ticket/
make deploy                          # or: make deploy Namespace=yourns (defaults to "default")
kubectl get pods                     # wait for Ready
```

**Visiting the site:**
- Real cluster: **http://[Node-IP]:32677**
- minikube: `minikube ip` for the node IP, or just run `minikube service ts-ui-dashboard -n default --url`
  to get a ready-to-use URL directly (`-n <namespace>` if you deployed elsewhere).

**If you're on minikube and services get stuck in `ImagePullBackOff`:** Docker Hub's anonymous pull
rate limit (`toomanyrequests`) can get exhausted pulling 46+ images from one node. If you've already
built the images locally (`python build_upload_image.py`), skip the registry entirely by loading them
straight into minikube's image cache:
```bash
docker images --format "{{.Repository}}:{{.Tag}}" | grep "^siaraz/" | xargs -n1 minikube image load
```

Other deploy variants (combine freely via `DeployArgs`):

| Variant | Command |
|---|---|
| Per-service MySQL clusters | `make deploy DeployArgs="--independent-db"` |
| Everything | `make deploy DeployArgs="--all"` |

Monitoring (Prometheus/Jaeger/Loki/OBI) and Chaos Mesh are no longer deploy-time flags — they're separate,
independently installable stacks: `make monitoring` and `make chaos-mesh` (see the `Makefile` for the full set of
targets, including `make all` to bring up everything at once).

Tear down: `make reset-deploy` (add `Namespace=yourns` if you deployed to a custom namespace). Note
this doesn't delete the underlying MySQL StatefulSets' PVCs (Helm's intentional default, to avoid
silent data loss) — if you want a fully clean slate including DB data, delete those PVCs manually.

## What We Fixed

Everything below was broken in the upstream repo before this fork; none of it is a regression this
fork introduced. Grouped by root cause:

**Stale references to deleted/renamed source directories**
`docker-compose.yml` still built `ts-ticketinfo-service` and `ts-food-map-service`, both removed
years ago (merged into `ts-basic-service`, and split into `ts-station-food-service` /
`ts-train-food-service`, respectively) — replaced with their actual successors. `ts-gateway-service`
was missing from the compose file entirely, which broke the whole UI container (nginx refuses to
start if it can't resolve an upstream host) — added it back.

**Deprecated Docker base images**
Every Java service's Dockerfile pinned `FROM java:8-jre`, an image Docker Hub deleted years ago —
switched to `eclipse-temurin:8-jre`. `ts-avatar-service`'s Dockerfile installed a since-renamed apt
package (`libgl1-mesa-glx` → `libgl1`) and built against the floating `python:3` tag, which now
resolves to Python 3.14 and breaks its ~2021-era pinned dependencies — pinned to `python:3.8`.

**No service discovery**
`ts-gateway-service` routes all `/api/v1/*` traffic through Nacos-based dynamic service discovery,
but no Nacos server was ever included in the compose file, so nothing could register — added one and
wired every service to it.

**25 services with no database**
Roughly half the Java services use a MySQL/JPA datasource, but the compose file either gave them no
database at all or a leftover MongoDB container the code doesn't use — every one of these was
silently crash-looping on every restart. Added a matching MySQL container for each, pinned to
`mysql:5.7` (modern MySQL's default auth plugin isn't supported by the pinned JDBC driver over a
non-SSL connection), with native AIO disabled per-container (running ~20 MySQL instances at once
exhausts the Linux kernel's `fs.aio-max-nr` limit otherwise).

**3 services missing from the compose file entirely**
`ts-delivery-service`, `ts-food-delivery-service`, and `ts-wait-order-service` had no compose block
at all. Added them, their databases, a RabbitMQ container (`ts-delivery-service`'s only message-queue
dependency), and the two missing gateway routes (`ts-delivery-service` is a pure queue consumer with
no REST API, so it needs no route).

**One app-level security bug**
`ts-wait-order-service`'s `SecurityConfig.java` only whitelisted `/api/v1/orderservice/order/**` — a
leftover from copy-pasting `ts-order-service`'s config that never got updated for its own
`/api/v1/waitorderservice/**` prefix — so every endpoint on the service, including ones meant to be
public, required authentication. Fixed to whitelist its own path (still requiring a role for the
one write endpoint, `POST /order`).

**Kubernetes path: image tags didn't match what was actually pushed**
`deploy.yaml.sample`/`sw_deploy.yaml.sample` still carried the original per-service version tags
(`1.0.0`, `1.0.1`, etc.) from the abandoned `codewisdom` images, while this fork's build/push flow
(`.env`, `build_upload_image.py`) uses one shared tag (`0.2.0`) for every service under `siaraz` — so
every pod but one hit `ImagePullBackOff` on a fresh deploy. Rewrote every image reference in both
manifests (and the generated `deploy.yaml`) to `siaraz/*:0.2.0`.

**Kubernetes path: OS-detection bug broke `sed` on Linux**
`hack/deploy/gen-mysql-secret.sh` checked `[ "$(uname)"="Darwin" ]` — missing the required spaces
around `=` inside `[ ]`, which makes the whole thing a single always-non-empty string that's always
"true" — so it *always* ran the macOS `sed -i ""` syntax, which GNU sed on Linux misparses (it reads
the empty string as the script and the actual script as a filename to open, hence
`sed: can't read s/nacos/nacos/g: No such file or directory`). Fixed to `[ "$(uname)" = "Darwin" ]`.

**Kubernetes path: `make reset-deploy` never cleaned up the train-ticket MySQL cluster**
`hack/deploy/reset.sh` uninstalled Helm releases via `helm ls | grep ts- | xargs helm uninstall`, but
the all-in-one train-ticket MySQL release is named `tsdb` — no `ts-` substring, so it was silently
skipped on every reset, leaving an orphaned release behind (and, in one observed case, its
StatefulSet ended up stuck at `0/0` replicas afterwards, taking down every service that depends on
it with `Connection refused` until manually scaled back up). Promoted the release name to a shared
`tsMysqlName` constant in `utils.sh` and added it to `reset.sh`'s cleanup.

## Test scripts
Use scripts to test train-ticket: [train-ticket-auto-query](https://github.com/FudanSELab/train-ticket-auto-query)

## Screenshot
![screenshot](./image/main_interface.png)
See the original project's [User Guide](https://github.com/FudanSELab/train-ticket/wiki/User-Guide) for usage details.
