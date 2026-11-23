FROM node:22-bookworm-slim AS frontend
WORKDIR /opt/guiziwenjin/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --legacy-peer-deps --no-audit
COPY web/ ./
COPY deeptutor/__version__.py /opt/guiziwenjin/deeptutor/__version__.py
ENV NEXT_TELEMETRY_DISABLED=1 AUTH_ENABLED=true NEXT_PUBLIC_AUTH_ENABLED=true DEEPTUTOR_AUTH_ENABLED=true NODE_OPTIONS=--max-old-space-size=3072
RUN npm run build

FROM python:3.13-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 NODE_ENV=production \
    DEEPTUTOR_HOME=/opt/guiziwenjin DEEPTUTOR_IGNORE_PROCESS_ENV_OVERRIDES=0 \
    HOME=/home/wenjin XDG_CACHE_HOME=/opt/guiziwenjin/data/system/cache \
    MPLCONFIGDIR=/opt/guiziwenjin/data/system/matplotlib \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git ffmpeg fonts-noto-cjk fonts-liberation libgl1 libglib2.0-0 \
    libatomic1 libreoffice-writer libreoffice-calc libreoffice-impress tini \
    && rm -rf /var/lib/apt/lists/*
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node
WORKDIR /opt/guiziwenjin
COPY requirements/ requirements/
RUN python -m pip install --no-cache-dir -r requirements/wenjin-production.txt -c requirements/wenjin-linux-constraints.txt
COPY deeptutor/ deeptutor/
COPY deeptutor_cli/ deeptutor_cli/
COPY README.md pyproject.toml build_metadata.py ./
COPY scripts/ scripts/
COPY deploy/wenjin/container_entrypoint.py deploy/wenjin/container_entrypoint.py
COPY --from=frontend /opt/guiziwenjin/web/.next/standalone/ web/.next/standalone/
COPY --from=frontend /opt/guiziwenjin/web/scripts/render-slide-formula.mjs web/scripts/render-slide-formula.mjs
COPY --from=frontend /opt/guiziwenjin/web/node_modules/ web/node_modules/
RUN python -m pip install --no-cache-dir --no-deps -e . \
    && groupadd -g 10001 wenjin && useradd -u 10001 -g 10001 -m -s /usr/sbin/nologin wenjin \
    && mkdir -p data web/.next/standalone/.next/cache \
    && chown -R wenjin:wenjin data web/.next/standalone/.next/cache
USER wenjin
EXPOSE 3782
HEALTHCHECK --interval=20s --timeout=5s --start-period=45s --retries=6 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8001/health/ready',timeout=4);urllib.request.urlopen('http://127.0.0.1:3782/login',timeout=4)"
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["python","deploy/wenjin/container_entrypoint.py"]
