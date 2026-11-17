FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
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
