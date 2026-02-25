FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /work

COPY requirements.txt /work/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY main.py /work/main.py

LABEL project="ghcli-blog-agent" \
      component="skill-server" \
      description="Skill Server Sidecar - Manages and serves SKILL.md for Copilot Agent"

EXPOSE 8002

CMD ["python", "main.py"]
