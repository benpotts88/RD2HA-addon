ARG BUILD_FROM=mcr.microsoft.com/playwright/python:v1.54.0-noble
FROM ${BUILD_FROM}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_STORAGE_STATE=/config/storage_state.json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
