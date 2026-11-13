FROM python:3.14-slim

# 内网离线部署：requirements.txt 包含后台 Excel 导入导出需要的解析库。
# requirements-etl.txt 中的测试工具不进入生产镜像。
# 完全无外网的机器有两种做法：
#   1) 联网机先用 Python 3.14 把 wheel 下载到 wheels/，构建上下文带上 wheels/，
#      把下面的 pip install 改成：
#      RUN python -m pip install --no-cache-dir --no-index --find-links wheels/ -r requirements.txt
#   2) 在联网机构建好整包镜像，docker save / docker load 分发到内网直接运行。
# 启动路径不做任何 pip install；前端为本地静态资源（原生 ESM，无 CDN 外链）。

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    EXAM_DB_PATH=/app/data/exam.db

# 只装运行时依赖
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

# 只拷运行时需要的代码与资源；data/ 由卷挂载，绝不把宿主机 .db / -wal 打进镜像
COPY app/ ./app/
COPY etl/ ./etl/
COPY static/ ./static/
COPY fixtures/ ./fixtures/
COPY migrations/ ./migrations/
RUN mkdir -p /app/data

# 运行时数据目录：exam.db、导入报告、备份（WAL 一致性由应用保证）
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
