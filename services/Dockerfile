FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY services/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home appuser
COPY --chown=appuser:appuser gaphopper/__init__.py gaphopper/config.py gaphopper/llm.py gaphopper/model_catalog.py /app/gaphopper/
COPY --chown=appuser:appuser config/vocab.yaml /app/config/vocab.yaml
COPY --chown=appuser:appuser services /app/services
USER appuser
CMD ["sh", "-c", "exec uvicorn services.api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
