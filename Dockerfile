FROM node:24-bookworm-slim AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/requirements.lock.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 comgraf && mkdir -p /data/uploads /data/mail-secrets && chown -R comgraf:comgraf /data
COPY --chown=comgraf:comgraf backend/ ./
COPY --from=ui --chown=comgraf:comgraf /ui/dist /app/frontend/dist
USER comgraf
EXPOSE 8000
CMD ["python", "-m", "app.start"]
