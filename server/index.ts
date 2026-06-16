import express from 'express';
import fs from 'fs';
import path from 'path';

const app = express();
const port = 3547;

const distPath = path.resolve(process.cwd(), 'dist');
const indexHtml = path.join(distPath, 'index.html');

if (!fs.existsSync(indexHtml)) {
  console.error(`[dataset-water] ERROR: ${indexHtml} not found — rebuild Docker image with npm run build`);
  process.exit(1);
}

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
