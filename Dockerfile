# The public viewer, for Dokploy. Data (current.json, sessions/, password hashes,
# .session_secret) is the volume at /data -- the same host folder the CLI's
# WORK_TRACKER_SSH rsyncs into. Runs as uid 1000 so files it writes stay the SSH
# user's. Zodiak is not in this repo (licence); mount it at /app/web/ui/dist/fonts.
FROM node:22-slim AS ui
WORKDIR /ui
COPY web/ui/package.json web/ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ui/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY . .
COPY --from=ui /ui/dist web/ui/dist
USER 1000:1000
ENV PYTHONUNBUFFERED=1
# The planner reads working hours ("10:00-22:00") in the server's local time, so
# the server must keep yours, not UTC. Override with a TZ variable in Dokploy.
ENV TZ=Europe/Helsinki
EXPOSE 8765
CMD ["python", "web/server.py", "--root", "/data", "--host", "0.0.0.0", "--port", "8765", \
     "--password-file", "/data/.password", "--viewer-password-file", "/data/.password-viewer", \
     "--allow-origin", "tracker.d-m-g.dev"]
