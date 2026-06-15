# ── 1. Frontend build (official Node image — надёжнее nodesource в python-slim) ──
FROM node:20-bookworm-slim AS frontend-build

WORKDIR /app

COPY package*.json ./
RUN npm config set registry https://registry.npmmirror.com 2>/dev/null || true; \
    npm ci || npm install

COPY tsconfig.json vite.config.ts index.html metadata.json ./
COPY src ./src

RUN npm run build

# ── 2. Python ML/API dependencies ──
FROM python:3.11-slim AS python-deps

WORKDIR /app

COPY python_code/requirements.txt ./python_code/requirements.txt

ARG PIP_INDEX_URL=
ENV PIP_TARGET=/opt/python-deps
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p "$PIP_TARGET" \
    && if [ -n "$PIP_INDEX_URL" ]; then \
         pip install --no-cache-dir --default-timeout=300 --target="$PIP_TARGET" -r python_code/requirements.txt -i "$PIP_INDEX_URL"; \
       else \
         pip install --no-cache-dir --default-timeout=300 --target="$PIP_TARGET" -r python_code/requirements.txt \
           -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn \
         || pip install --no-cache-dir --default-timeout=300 --target="$PIP_TARGET" -r python_code/requirements.txt \
           -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
         || pip install --no-cache-dir --default-timeout=300 --target="$PIP_TARGET" -r python_code/requirements.txt \
           -i https://pypi.org/simple --trusted-host pypi.org --trusted-host files.pythonhosted.org; \
       fi

# ── 3. Production: Python API + Node static server ──
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Node.js копируем из frontend-build — без curl/nodesource (обход корпоративного SSL)
COPY --from=frontend-build /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-build /usr/local/bin/npm /usr/local/bin/npm
COPY --from=frontend-build /usr/local/bin/npx /usr/local/bin/npx
COPY --from=frontend-build /usr/local/lib/node_modules /usr/local/lib/node_modules

WORKDIR /app

COPY --from=frontend-build /app/dist ./dist
COPY --from=frontend-build /app/package*.json ./
COPY --from=frontend-build /app/node_modules ./node_modules
COPY --from=python-deps /opt/python-deps /opt/python-deps

COPY python_code ./python_code
COPY server ./server
COPY tsconfig.json ./tsconfig.json
# data/, models/, Реки/ — bind-mount в docker-compose

ENV PYTHONPATH=/opt/python-deps \
    PATH=/opt/python-deps/bin:$PATH

EXPOSE 3547 8000

CMD ["npm", "run", "start:prod"]
