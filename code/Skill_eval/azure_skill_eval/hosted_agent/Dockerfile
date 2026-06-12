FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

COPY shared ./shared
COPY hosted_agent ./hosted_agent

RUN pip install -r hosted_agent/requirements.txt

EXPOSE 8000

CMD ["python", "-m", "hosted_agent.main"]
