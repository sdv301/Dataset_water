# ── 1. Frontend build (official Node image — надёжнее nodesource в python-slim) ──
FROM node:20-bookworm-slim AS frontend-build

WORKDIR /app

# NODE_ENV=production on server skips dev tooling; force full install for vite build
ENV NODE_ENV=development
ENV NPM_CONFIG_PRODUCTION=false

COPY package.json package-lock.json ./
# npm 11 иногда падает с "Exit handler never called" на нестабильной сети
RUN npm install -g npm@10 && \
    set -e; \
    npm config set fetch-retries 20; \
    npm config set fetch-retry-mintimeout 30000; \
    npm config set fetch-retry-maxtimeout 300000; \
    npm config set maxsockets 2; \
    npm config set fund false; \
    npm config set audit false; \
    success=0; \
    for reg in https://registry.npmjs.org https://registry.npmmirror.com; do \
      echo "=== npm install via $reg ==="; \
      npm config set registry "$reg"; \
      for attempt in 1 2 3; do \
        echo "--- attempt $attempt ---"; \
        rm -rf node_modules; \
        if npm ci --no-audit --no-fund --loglevel error 2>/dev/null; then \
          success=1; break 2; \
        fi; \
        rm -rf node_modules; \
        if npm install --no-audit --no-fund --loglevel error; then \
          success=1; break 2; \
        fi; \
        sleep 5; \
      done; \
    done; \
    test "$success" -eq 1 || (echo "ERROR: npm install failed on all registries" && exit 1); \
    test -f node_modules/vite/bin/vite.js || (echo "ERROR: vite not installed" && npm ls vite && exit 1)

COPY tsconfig.json vite.config.ts index.html metadata.json ./
COPY src ./src

RUN node node_modules/vite/bin/vite.js build

# ── 2. Python ML/API dependencies (полный образ — libgomp1 без apt-get) ──
FROM python:3.11-bookworm AS python-deps

WORKDIR /app

COPY python_code/requirements.txt ./python_code/requirements.txt

ARG PIP_INDEX_URL=
ENV PIP_TARGET=/opt/python-deps
RUN mkdir -p "$PIP_TARGET" \
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
FROM python:3.11-bookworm

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
