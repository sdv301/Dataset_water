import express from 'express';
import fs from 'fs';
import http from 'http';
import path from 'path';

const app = express();
const port = 3547;
const apiPort = Number(process.env.INTERNAL_API_PORT ?? 8000);

const distPath = path.resolve(process.cwd(), 'dist');
const indexHtml = path.join(distPath, 'index.html');

if (!fs.existsSync(indexHtml)) {
  console.error(`[dataset-water] ERROR: ${indexHtml} not found — rebuild Docker image with npm run build`);
  process.exit(1);
}

/** Прокси /flood/v2/api → FastAPI (fallback: прямой доступ к :3547 без nginx) */
function proxyApiRequest(req: express.Request, res: express.Response) {
  const suffix = req.url.startsWith('/') ? req.url : `/${req.url}`;
  const targetPath = `/api${suffix}`;
  const headers = { ...req.headers, host: `127.0.0.1:${apiPort}` };
  delete headers['host'];

  const proxyReq = http.request(
    {
      hostname: '127.0.0.1',
      port: apiPort,
      path: targetPath,
      method: req.method,
      headers,
    },
    (proxyRes) => {
      res.writeHead(proxyRes.statusCode ?? 502, proxyRes.headers);
      proxyRes.pipe(res);
    },
  );
  proxyReq.on('error', (err) => {
    console.error('[dataset-water] API proxy error:', err.message);
    if (!res.headersSent) {
      res.status(502).json({ error: 'API unavailable', detail: err.message });
    }
  });
  req.pipe(proxyReq);
}

app.use('/flood/v2/api', proxyApiRequest);
app.use('/api', proxyApiRequest);

// SPA: маршруты без расширения → index.html
app.use('/flood/v2', (req, res, next) => {
  if (path.extname(req.path)) {
    next();
  } else {
    res.sendFile(indexHtml, (err) => {
      if (err) {
        console.error('[dataset-water] sendFile error:', err);
        res.status(500).send('Frontend dist missing');
      }
    });
  }
});

app.use('/flood/v2', express.static(distPath));

app.get('/', (_req, res) => {
  res.redirect('/flood/v2/');
});

app.listen(port, '0.0.0.0', () => {
  console.log(`Dataset Water React production server on :${port}, dist=${distPath}`);
});
