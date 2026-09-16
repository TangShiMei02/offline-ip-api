# 可选：Docker 部署
#   docker build -t ipapi .
#   docker run -d --name ipapi -p 8080:8080 ipapi
FROM python:3.11-alpine

# update_db.sh 需要 curl / tar（python3 镜像自带 python3）
RUN apk add --no-cache curl tar

WORKDIR /app

COPY app/     ./app/
COPY scripts/ ./scripts/
COPY deploy/  ./deploy/
COPY data/    ./data/

# 数据文件不随仓库分发（见 README「数据来源与许可」）。
# 构建上下文里的 data/ 为空时，在构建阶段下载一份，这样镜像开箱即用。
RUN if [ ! -f data/ip2region.db ] || [ ! -f data/ipv6wry.db ]; then \
        echo ">>> data/ 为空，构建时下载数据库..."; \
        bash scripts/update_db.sh; \
    fi

ENV IPAPI_KEY="" \
    IPAPI_RATE_LIMIT=60 \
    IPAPI_TRUST_PROXY=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health')" || exit 1

CMD ["python3","app/server.py","--host","0.0.0.0","--port","8080"]
