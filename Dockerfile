# Local QZip web UI + API (CPU). Build: docker build -t qzip .   Run: docker run --rm -p 127.0.0.1:5000:5000 qzip
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends build-essential make && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN pip install --no-cache-dir --no-deps -e .
# native codecs (default engine qzmf, x18 qzc2/qzs/qzb, libbsc helpers)
RUN make native ARCH=x86-64-v3
ENV QZIP_NO_RAY=1 QZIP_LOG_FILE= QZIP_REQUIRE_API_KEY=0
EXPOSE 5000
# Inside the container bind the UI to 0.0.0.0 so the published (127.0.0.1-only) port reaches it.
CMD ["python", "-m", "qzip.web", "--host", "0.0.0.0"]
