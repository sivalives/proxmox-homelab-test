FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir flask requests gunicorn

COPY filmon_iptv.py .

ENV PYTHONUNBUFFERED=1
EXPOSE 8765

# 1 worker only: the channel list lives in memory and a background thread refreshes it
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:8765", "--timeout", "60", "filmon_iptv:app"]