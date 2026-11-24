# syntax=docker/dockerfile:1

# Two stages so build tooling does not ship in the runtime image. The runtime has
# no third-party Python dependencies, only the wheel built here.
FROM python:3.15.2-alpine3.24 AS build

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .


FROM python:3.15.2-alpine3.24 AS runtime

ARG VERSION=0.0.0-dev
LABEL org.opencontainers.image.title="gh-pr-dashboard" \
      org.opencontainers.image.description="Open GitHub pull requests across users and orgs on one page" \
      org.opencontainers.image.source="https://github.com/gx1400/gh-pr-dashboard" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}"

COPY --from=build /wheels /wheels
RUN python -m pip install --no-cache-dir --no-compile /wheels/*.whl && rm -rf /wheels

# nobody:nobody. The app writes nothing, so the container runs with a read-only
# root filesystem.
USER 65534:65534

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status==200 else 1)"

ENTRYPOINT ["gh-pr-dashboard"]
