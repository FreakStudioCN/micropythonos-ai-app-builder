FROM node:22-bookworm-slim AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPOS_SESSION_ROOT=/data/sessions \
    MPOS_BILLING_ROOT=/data/billing \
    MPOS_BILLING_DEMO_MODE=false \
    MPOS_DEMO_ERROR_INJECTION=false

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY runner/ runner/
COPY vendor/MicroPython_Skills/ vendor/MicroPython_Skills/
COPY --from=frontend-build /app/frontend/dist/ frontend/dist/

RUN mkdir -p /data/sessions /data/billing

EXPOSE 10000
CMD ["sh", "-c", "uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-10000}"]
