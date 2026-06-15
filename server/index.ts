import express from 'express';
import path from 'path';

const app = express();
const port = 3547;

// Serve static assets from Vite build output directory
const distPath = path.resolve(process.cwd(), 'dist');

// SPA Routing: For routes without file extensions, serve index.html
app.use('/flood/v2', (req, res, next) => {
  if (path.extname(req.path)) {
    next();
  } else {
    res.sendFile(path.join(distPath, 'index.html'));
  }
});

// Serve static files under /flood/v2/ path
app.use('/flood/v2', express.static(distPath));

// Redirect root to /flood/v2/
app.get('/', (req, res) => {
  res.redirect('/flood/v2/');
});

app.listen(port, () => {
  console.log(`Dataset Water React production server running on port ${port}`);
});
