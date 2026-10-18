# Minimal scrubber container
# This container holds secrets and should be hardened

FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --target=/app/deps -r requirements.txt

FROM python:3.12-slim

# Security: run as non-root user
RUN useradd -r -u 1000 scrubber

WORKDIR /app

# Copy only what we need
COPY --from=builder /app/deps /app/deps
COPY main.py .

# Set Python path to include deps
ENV PYTHONPATH=/app/deps

# Security: make filesystem read-only where possible
# The secrets.json will be copied here at deploy time
RUN mkdir -p /app/secrets && chown scrubber:scrubber /app/secrets

USER scrubber

EXPOSE 8001

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
