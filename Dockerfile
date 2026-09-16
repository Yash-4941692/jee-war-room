FROM python:3.12-slim

WORKDIR /app

# install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# application code
COPY . .

ENV PORT=8080
EXPOSE 8080

# Koyeb/Render-style cloud hosts check this; it also verifies the database
HEALTHCHECK --interval=30s --timeout=6s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/healthz',timeout=5).status==200 else 1)"

CMD ["python", "-u", "server.py"]
