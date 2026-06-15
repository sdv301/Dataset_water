FROM python:3.11-slim AS builder-python

WORKDIR /app

# Install Node.js for building the React assets
RUN apt-get update && apt-get install -y curl build-essential && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

COPY package*.json ./

RUN npm config set registry https://registry.npmmirror.com && \
    npm config set strict-ssl false && \
    npm ci

COPY python_code/requirements.txt ./python_code/requirements.txt

ARG PIP_INDEX_URL=
ENV PIP_TARGET=/opt/python-deps
RUN mkdir -p "$PIP_TARGET" && if [ -n "$PIP_INDEX_URL" ]; then \
      pip install --no-cache-dir --default-timeout=300 --target="$PIP_TARGET" -r python_code/requirements.txt -i "$PIP_INDEX_URL"; \
    else \
      pip install --no-cache-dir --default-timeout=300 --target="$PIP_TARGET" -r python_code/requirements.txt \
        -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn \
      || pip install --no-cache-dir --default-timeout=300 --target="$PIP_TARGET" -r python_code/requirements.txt \
        -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com \
      || pip install --no-cache-dir --default-timeout=300 --target="$PIP_TARGET" -r python_code/requirements.txt \
        -i https://pypi.org/simple --trusted-host pypi.org --trusted-host files.pythonhosted.org; \
    fi

COPY . .

RUN npm run build

# Production stage
FROM python:3.11-slim

# Install system dependencies (including supervisor, Node.js, and curl)
RUN apt-get update && apt-get install -y \
    curl \
    libgomp1 \
    build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy built frontend assets and dependencies
COPY --from=builder-python /app/dist ./dist
COPY --from=builder-python /app/package*.json ./
COPY --from=builder-python /app/node_modules ./node_modules
COPY --from=builder-python /app/python_code ./python_code
COPY --from=builder-python /app/server ./server
COPY --from=builder-python /app/tsconfig.json ./tsconfig.json
# data/, models/, Реки/ come from the bind mount in docker-compose

COPY --from=builder-python /opt/python-deps /opt/python-deps
ENV PYTHONPATH=/opt/python-deps \
    PATH=/opt/python-deps/bin:$PATH

EXPOSE 3547 8000

CMD ["npm", "run", "start:prod"]
