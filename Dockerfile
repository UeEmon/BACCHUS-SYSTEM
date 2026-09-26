FROM python:3.12-slim
WORKDIR /app
RUN useradd --create-home --uid 10001 bacchus && mkdir /data && chown bacchus:bacchus /data
COPY --chown=bacchus:bacchus server ./server
COPY --chown=bacchus:bacchus web ./web
USER bacchus
ENV HOST=0.0.0.0 PORT=8000 DATABASE_PATH=/data/bacchus.sqlite3 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "server.app"]
