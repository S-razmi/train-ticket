# siaraz Train-Ticket system

Repo=siaraz
Tag=latest
Namespace="default"
DeployArgs=""


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

.PHONY: publish-image
publish-image:
	@script/publish-docker-images.sh $(Repo) $(Tag)

# deploy
# DeployArgs ""                    : deploy train-ticket with all-in-one mysql cluster
# DeployArgs "--independent-db"    : deploy train-ticket with mysql cluster each service
# DeployArgs "--with-monitoring"   : deploy train-ticket with prometheus
# DeployArgs "--with-tracing"      : deploy train-ticket with skywalking
# DeployArgs "--all"               : deploy train-ticket with mysql cluster each service
.PHONY: deploy
deploy:
	@hack/deploy/deploy.sh $(Namespace) "$(DeployArgs)"

# deploy
.PHONY: reset-deploy
reset-deploy:
	@hack/deploy/reset.sh $(Namespace)

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