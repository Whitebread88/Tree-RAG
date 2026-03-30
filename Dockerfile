FROM python:3.11-slim-bookworm

# Allow statements and log messages to immediately appear in the logs
ENV PYTHONUNBUFFERED=True \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
ARG REQUIREMENTS_FILE=requirements-service.txt
COPY requirements.txt requirements-service.txt requirements-job.txt ./

RUN set -eux; \
    apt-get update; \
    if [ "${REQUIREMENTS_FILE}" = "requirements-job.txt" ]; then \
        apt-get install -y --no-install-recommends libreoffice-writer; \
    fi; \
    # Sanity-check Python interpreter so we don't accidentally ship multiple versions.
    python --version; \
    python -c "import sys; assert sys.version_info[:2] == (3, 11), sys.version"; \
    # Guardrail: fail the build if apt dependencies introduce Python 3.13 packages.
    if dpkg-query -W -f='${Package}\n' 'python3.13*' 2>/dev/null | grep -q '^python3\.13'; then \
        echo 'Unexpected python3.13 package detected from apt dependencies'; \
        exit 1; \
    fi; \
    pip install -r "${REQUIREMENTS_FILE}"; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--http", "h11"]
