# syntax=docker/dockerfile:1

ARG python_version=3.13.2
FROM python:${python_version}-slim

LABEL org.opencontainers.image.source=https://github.com/lqc/kalandra
LABEL org.opencontainers.image.description="Kalandra is a tool to mirror Git repositories."
LABEL org.opencontainers.image.licenses="Apache 2.0"

WORKDIR /opt/app
RUN \
    --mount=type=cache,target=/opt/cache/pip \
    --mount=type=bind,target=/opt/app \
    pip install --cache-dir /opt/cache/pip '.[github]'

ENTRYPOINT ["kalandra"]
