#!/bin/sh
# Production start without npm (npm breaks when node is copied piecemeal into python image).
set -e
cd /app

chmod +x docker/ensure-db.sh
./docker/ensure-db.sh

exec node node_modules/concurrently/dist/bin/concurrently.js -k -n api,web \
  "python python_code/api_server.py" \
  "node node_modules/tsx/dist/cli.mjs server/index.ts"
