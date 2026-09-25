# Runtime for browser (Playwright) tests. Chromium and its system deps come from the official image.
FROM mcr.microsoft.com/playwright/python:v1.63.0-noble
RUN pip install --no-cache-dir pytest==9.1.1 pytest-playwright==0.9.0 playwright==1.63.0
USER pwuser
WORKDIR /workspace
