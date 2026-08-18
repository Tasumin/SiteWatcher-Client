FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sitewatch_agent ./sitewatch_agent

CMD ["python", "-m", "sitewatch_agent.main"]
