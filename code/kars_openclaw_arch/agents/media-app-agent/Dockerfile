FROM python:3.12-slim-trixie

RUN apt-get update \
	&& apt-get upgrade --no-install-recommends -y \
	&& rm -rf /var/lib/apt/lists/* \
	&& useradd --create-home --uid 10001 appuser
WORKDIR /app
COPY agents/media-app-agent/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY agents/media-app-agent/src ./src
COPY apps/media-web ./static
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1 STATIC_DIR=/app/static
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "media_app_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
