FROM python:3.13-slim

WORKDIR /app

# install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# application code
COPY . .

# Hugging Face Spaces routes traffic to 8080 (PORT is also honoured if set)
ENV PORT=8080
EXPOSE 8080

# verifies both the web server and the cloud database connection
HEALTHCHECK --interval=30s --timeout=8s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/healthz',timeout=6).status==200 else 1)"

CMD ["python", "-u", "server.py"]
