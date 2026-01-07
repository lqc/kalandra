# syntax=docker/dockerfile:1

ARG python_version=3.13.2
FROM python:${python_version}-slim

LABEL org.opencontainers.image.source=https://github.com/lqc/kalandra
LABEL org.opencontainers.image.description="Kalandra is a tool to mirror Git repositories."
LABEL org.opencontainers.image.licenses="Apache 2.0"

WORKDIR /opt/app
RUN \
    --mount=type=cache,target=/root/.cache/pip \
    pip install uv

RUN \
    --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/opt/app \
    uv pip install --system --cache-dir /root/.cache/uv '.[github]'

ENTRYPOINT ["kalandra"]
