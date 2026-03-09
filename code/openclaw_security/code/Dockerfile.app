# Dockerfile.app — fully automated podcast generation image
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    requests \
    serpapi \
    beautifulsoup4 \
    lxml

COPY auto_run.py podcast_generator.py deepsearch.py trend_scout.py ./

RUN mkdir -p /app/output

# Smart entrypoint: SCHEDULE_HOURS=0 → single run; >0 → scheduled mode
ENTRYPOINT ["/bin/sh", "-c"]
CMD ["if [ \"${SCHEDULE_HOURS:-0}\" -gt 0 ]; then \
       python auto_run.py --schedule ${SCHEDULE_HOURS} --count ${TOPICS_PER_RUN:-1}; \
     else \
       python auto_run.py --count ${TOPICS_PER_RUN:-1}; \
     fi"]
