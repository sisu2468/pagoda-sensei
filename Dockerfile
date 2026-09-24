# ============================================================
# Pagoda Travel AI Matching Service — Dockerfile
# ============================================================

FROM python:3.12-slim

# Metadata
LABEL maintainer="Pagoda Travel Engineering"
LABEL description="AI operator matching microservice"
LABEL version="1.0.0"

# Set working directory
WORKDIR /service

# Create non-root user for security
RUN addgroup --system pagoda && adduser --system --ingroup pagoda pagoda

# Install system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (layer cache-friendly — requirements first)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app/ ./app/

# Set ownership
RUN chown -R pagoda:pagoda /service

# Switch to non-root user
USER pagoda

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
