# Stage 1: download the CDK (Chemistry Development Kit) jars used for structure depictions
FROM maven:3.9-eclipse-temurin-21 AS cdk
WORKDIR /build
COPY java/pom.xml .
RUN mvn -q -B dependency:copy-dependencies -DoutputDirectory=/cdk/lib

FROM python:3.12-slim

# Headless Java runtime for the CDK, plus fontconfig, a font and HarfBuzz (needed by Java to draw atom labels)
RUN mkdir -p /usr/share/man/man1 \
    && apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless fontconfig fonts-dejavu-core libharfbuzz0b \
    && rm -rf /var/lib/apt/lists/*
COPY --from=cdk /cdk/lib /opt/cdk/lib

ENV CDK_CLASSPATH=/opt/cdk/lib \
    CDK_MAX_HEAP=256m \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PUBCHEM_DATA_DIR=/data \
    SQLITE_TMPDIR=/data/tmp \
    PORT=8080

WORKDIR /app

# Dependencies first for better layer caching
COPY requirements-server.txt .
RUN pip install -r requirements-server.txt

COPY setup.py README.md LICENSE default_db_layout.json build_pubchem_db.py ./
COPY pubchem2sqlite ./pubchem2sqlite
COPY pubchem_local ./pubchem_local
RUN pip install --no-deps . && rm -rf build *.egg-info

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '8080'), timeout=4)" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve"]
