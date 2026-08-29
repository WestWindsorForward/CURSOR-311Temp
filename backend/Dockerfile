FROM python:3.14-slim

LABEL org.opencontainers.image.title="Pinpoint 311 Backend" \
      org.opencontainers.image.description="API, task workers and Open311 endpoints for the Pinpoint 311 municipal service-request platform" \
      org.opencontainers.image.source="https://github.com/Pinpoint-311/Pinpoint-311" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

# tesseract-ocr backs the free, offline licence-plate detector (Apache 2.0).
# libGL/libglib are what opencv-python-headless still links against.
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    gcc \
    libpq-dev \
    postgresql-client \
    git \
    curl \
    ca-certificates \
    gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Emit the license notices for everything installed above. This image is
# redistributed as a binary artifact, so the notices have to travel inside it --
# including the LGPL text and source pointer for psycopg2-binary.
RUN python scripts/generate_licenses.py /app/THIRD-PARTY-LICENSES.txt

EXPOSE 8000

# The entrypoint reconciles the database schema before the API serves anything.
# It refuses to start on a schema it could not bring to head -- see
# app/db/migrate.py for why that is better than starting and 500ing later.
RUN chmod +x /app/scripts/entrypoint.sh
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
