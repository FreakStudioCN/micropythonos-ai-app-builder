ARG NODE_IMAGE=m.daocloud.io/docker.io/library/node:22-bookworm-slim
ARG PYTHON_IMAGE=m.daocloud.io/docker.io/library/python:3.12-slim

FROM ${NODE_IMAGE} AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
ARG NPM_REGISTRY=https://registry.npmmirror.com
RUN npm ci --registry=${NPM_REGISTRY}
COPY frontend/ ./
RUN npm run build


FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPOS_SESSION_ROOT=/tmp/mpos-sessions \
    MPOS_DEMO_ERROR_INJECTION=false

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_EXTRA_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --retries 5 --timeout 60 \
    --index-url ${PIP_INDEX_URL} \
    --extra-index-url ${PIP_EXTRA_INDEX_URL} \
    -r backend/requirements.txt

COPY backend/ backend/
COPY runner/ runner/
COPY vendor/MicroPython_Skills/ vendor/MicroPython_Skills/
COPY scripts/provision_superadmin.py scripts/provision_superadmin.py
COPY --from=frontend-build /app/frontend/dist/ frontend/dist/

# Non-root runtime user. Only the session root and the local sqlite fallback
# directory are writable; code and vendored assets stay root-owned read-only.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin mpos \
    && mkdir -p /tmp/mpos-sessions backend/sessions \
    && chown -R mpos:mpos /tmp/mpos-sessions backend/sessions

USER mpos

EXPOSE 10000
CMD ["sh", "-c", "uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-10000}"]
