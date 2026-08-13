FROM radondb/xenon:1.1.5-helm

USER root

RUN sed -i 's/"host": "localhost"/"host": "127.0.0.1"/' \
    /usr/local/bin/docker-entrypoint

USER mysql