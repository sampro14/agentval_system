# Runtime for agent-generated code. Networking is disabled at run time, so test tooling is baked in.
FROM python:3.12-slim
RUN pip install --no-cache-dir pytest==9.1.1 \
    && useradd --create-home --uid 1000 sandbox
USER sandbox
WORKDIR /workspace
