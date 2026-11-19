# AgentCore Runtime image: Python 3.13, pinned Kiro CLI, git and make.
# Build context is `agent/` (see infra/stacks/agent_stack.py).

FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.13-slim-bookworm

ARG KIRO_CLI_VERSION=3.4.0
ARG UV_VERSION=0.9.2
ARG PYPI_INDEX_URL=
ARG ARTIFACTORY_KIRO_CLI_URL=

ENV PYTHONUNBUFFERED=1
ENV KIRO_CLI=/usr/local/bin/kiro-cli

RUN apt-get update \
 && apt-get install --no-install-recommends -y git make ca-certificates curl unzip \
 && rm -rf /var/lib/apt/lists/*

# Install Kiro CLI (ARM64). Default CDN is desktop-release (desktop.kiro.dev often NXDOMAIN).
# Override ARTIFACTORY_KIRO_CLI_URL in air-gapped pipelines.
RUN set -eux; \
    archive=/tmp/kiro-cli.tgz; \
    if [ -n "$ARTIFACTORY_KIRO_CLI_URL" ]; then \
      curl -fsSL "$ARTIFACTORY_KIRO_CLI_URL" -o "$archive"; \
    else \
      curl -fsSL \
        "https://desktop-release.q.us-east-1.amazonaws.com/${KIRO_CLI_VERSION}/kirocli-aarch64-linux.tar.gz" \
        -o "$archive"; \
    fi; \
  extract="$(mktemp -d)"; \
  tar -xzf "$archive" -C "$extract"; \
  if [ -f "$extract/kirocli/bin/kiro-cli" ]; then \
    install -m 0755 "$extract/kirocli/bin/kiro-cli" /usr/local/bin/kiro-cli; \
  elif [ -f "$extract/kiro-cli" ]; then \
    install -m 0755 "$extract/kiro-cli" /usr/local/bin/kiro-cli; \
  else \
    found="$(find "$extract" -name kiro-cli -type f | head -n1)"; \
    test -n "$found"; \
    install -m 0755 "$found" /usr/local/bin/kiro-cli; \
  fi; \
  rm -rf "$extract" "$archive"; \
  kiro-cli --version

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN if [ -n "$PYPI_INDEX_URL" ]; then \
      pip install --no-cache-dir --index-url "$PYPI_INDEX_URL" .; \
    else \
      pip install --no-cache-dir .; \
    fi

EXPOSE 8080
CMD ["python", "-m", "coding_agent.server"]