
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
See [What We Fixed](#what-we-fixed) below for the full list of issues and how each was resolved. The
Kubernetes deployment path documented further down is unchanged from upstream and has not been
revalidated by this fork.

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

> **Not covered by this fork's fixes.** This path deploys the project's original pre-built Docker Hub
> images (`codewisdom/*:latest`), which are the same multi-year-old images whose staleness this fork
> exists to fix on the Compose side — so you may hit similar issues here. Use the Docker Compose path
> above for a known-working setup; this section is preserved for reference.

**Prerequisites:** an existing Kubernetes cluster, [Helm](https://helm.sh/docs/helm/helm_install/),
and PVC support (e.g. [OpenEBS localPV](https://openebs.io/docs/2.12.x/user-guides/installation)).

```bash
git clone --depth=1 https://github.com/FudanSELab/train-ticket.git
cd train-ticket/
make deploy                          # or: make deploy Namespace=yourns
kubectl get pods                     # wait for Ready
```
Visit **http://[Node-IP]:32677**.

Other deploy variants (combine freely via `DeployArgs`):

| Variant | Command |
|---|---|
| Per-service MySQL clusters | `make deploy DeployArgs="--independent-db"` |
| With Prometheus monitoring | `make deploy DeployArgs="--with-monitoring"` |
| With SkyWalking tracing | `make deploy DeployArgs="--with-tracing"` |
| Everything | `make deploy DeployArgs="--all"` |

Tear down: `make reset-deploy` (add `Namespace=yourns` if you deployed to a custom namespace).

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

## Test scripts
Use scripts to test train-ticket: [train-ticket-auto-query](https://github.com/FudanSELab/train-ticket-auto-query)

## Screenshot
![screenshot](./image/main_interface.png)
See the original project's [User Guide](https://github.com/FudanSELab/train-ticket/wiki/User-Guide) for usage details.
