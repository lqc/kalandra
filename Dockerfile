# syntax=docker/dockerfile:1


# --- Builder stage ---
# Use a Python image with uv pre-installed
FROM ghcr.io/astral-sh/uv:python3.14-trixie-slim AS builder


ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Omit development dependencies
ENV UV_NO_DEV=1

# Disable Python downloads, because we want to use the system interpreter
# across both images. If using a managed Python version, it needs to be
# copied from the build image into the final image; see `standalone.Dockerfile`
# for an example.
ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /opt/app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=README.md,target=README.md \
    --mount=type=bind,source=src,target=src \
    uv venv && uv pip install '.[github]'

# --- Final stage ---
FROM python:3.14-slim-trixie

LABEL org.opencontainers.image.source=https://github.com/lqc/kalandra
LABEL org.opencontainers.image.description="Kalandra is a tool to mirror Git repositories."
LABEL org.opencontainers.image.licenses="Apache 2.0"

# Setup a non-root user
RUN groupadd --system --gid 999 nonroot \
 && useradd --system --gid 999 --uid 999 --create-home nonroot

# Copy the application from the builder
COPY --from=builder --chown=nonroot:nonroot /opt/app /opt/app

# Place executables in the environment at the front of the path
ENV PATH="/opt/app/.venv/bin:$PATH"

# Use the non-root user to run our application
USER nonroot

# Use `/opt/app` as the working directory
WORKDIR /opt/app

ENTRYPOINT [ "kalandra" ]
