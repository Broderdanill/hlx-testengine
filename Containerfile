FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y curl wget gnupg libnss3 libxss1 libasound2 libatk1.0-0 libgtk-3-0 libgbm-dev && \
    pip install --no-cache-dir playwright && \
    playwright install msedge

WORKDIR /app
COPY app/ /app/
RUN pip install --no-cache-dir -r requirements.txt

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
