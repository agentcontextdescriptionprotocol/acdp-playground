# Builds the playground image. Build context must be the parent of
# acdp-playground and acdp-rs so the path dependency on the Python SDK
# resolves (see docker-compose.yml).
FROM python:3.12-slim AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential curl pkg-config libssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv

# Install Rust toolchain (needed by maturin to build the acdp-py extension).
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --default-toolchain stable --profile minimal
ENV PATH="/root/.cargo/bin:${PATH}"

WORKDIR /workspace

# Bring in sibling repos that the path dep resolves to.
COPY acdp-rs            /workspace/acdp-rs
COPY acdp-playground    /workspace/acdp-playground

WORKDIR /workspace/acdp-playground

RUN uv sync --extra llm

EXPOSE 8000
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["uv", "run", "uvicorn", "playground.main:app", "--host", "0.0.0.0", "--port", "8000"]
