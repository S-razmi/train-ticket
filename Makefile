# siaraz Train-Ticket system

Repo=siaraz
Tag=latest
Namespace="default"
DeployArgs=""

# `make` with no target builds everything: the application, the monitoring
# stack, and Chaos Mesh.
.DEFAULT_GOAL := all

.PHONY: all
all: app monitoring chaos-mesh

.PHONY: reset-all
reset-all: chaos-mesh-reset monitoring-reset app-reset

# build image
.PHONY: build
build: clean-image package build-image

.PHONY: package
package:
	@mvn clean package -Dmaven.test.skip=true

.PHONY: build-image
build-image:
	@hack/build-image.sh $(Repo) $(Tag)

# push image
.PHONY: push-image
push-image:
	@hack/push-image.sh $(Repo)

# build every ts-* image and push it in one pass (mvn build + per-service
# docker build + push - see build_upload_image.py)
.PHONY: publish-image
publish-image:
	@python3 build_upload_image.py

# deploy
# DeployArgs ""                    : deploy train-ticket with all-in-one mysql cluster
# DeployArgs "--independent-db"    : deploy train-ticket with mysql cluster each service
# DeployArgs "--all"               : deploy train-ticket with mysql cluster each service
#
# Monitoring and Chaos Mesh used to be deploy-time flags here
# (--with-monitoring, --with-tracing); they're now the separate `monitoring`
# and `chaos-mesh` targets below, since both are independently
# installable/removable stacks, not properties of the app deployment itself.
.PHONY: deploy
deploy:
	@hack/deploy/deploy.sh $(Namespace) "$(DeployArgs)"

.PHONY: reset-deploy
reset-deploy:
	@hack/deploy/reset.sh $(Namespace)

# the train-ticket application alone
.PHONY: app
app: deploy

.PHONY: app-reset
app-reset: reset-deploy

.PHONY: clean
clean:
	@mvn clean
	@hack/clean-image.sh $(Repo)

# clean image
.PHONY: clean-image
clean-image:
	@hack/clean-image.sh $(Repo)


.PHONY: prometheus
prometheus:
	@hack/observability/install-prometheus.sh

.PHONY: prometheus-reset
prometheus-reset:
	@hack/observability/uninstall-prometheus.sh

.PHONY: jaeger
jaeger:
	@hack/observability/install-jaeger.sh

.PHONY: jaeger-reset
jaeger-reset:
	@hack/observability/uninstall-jaeger.sh

.PHONY: loki
loki:
	@hack/observability/install-loki.sh

.PHONY: loki-reset
loki-reset:
	@hack/observability/uninstall-loki.sh

.PHONY: otel-obi
otel-obi:
	@hack/observability/install-otel-obi.sh

.PHONY: otel-obi-reset
otel-obi-reset:
	@hack/observability/uninstall-otel-obi.sh

# the full monitoring stack alone: Prometheus, Jaeger, Loki, OBI/OTel Collector
.PHONY: monitoring
monitoring: prometheus jaeger loki otel-obi

.PHONY: monitoring-reset
monitoring-reset: otel-obi-reset loki-reset jaeger-reset prometheus-reset

.PHONY: verify-observability
verify-observability:
	@verify/run-all.sh

# chaos mesh alone
.PHONY: chaos-mesh
chaos-mesh:
	@hack/chaos-mesh/install-chaos-mesh.sh

.PHONY: chaos-mesh-reset
chaos-mesh-reset:
	@hack/chaos-mesh/uninstall-chaos-mesh.sh

.PHONY: load-generator
load-generator:
	@hack/load-generator/deploy.sh

.PHONY: load-generator-reset
load-generator-reset:
	@hack/load-generator/undeploy.sh

.PHONY: load-generator-job
load-generator-job:
	@hack/load-generator/run-job.sh

.PHONY: telemetry-export
telemetry-export:
	@hack/telemetry-export/export.sh $(TelemetryArgs)
