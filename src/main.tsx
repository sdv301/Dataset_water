import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import App from './App.tsx';
import './index.css';

const originalFetch = window.fetch;
window.fetch = function () {
  let [resource, config] = arguments;
  if(config == null) {
    config = {};
  }
  if(config.credentials == null) {
    config.credentials = 'include';
  }
  return originalFetch(resource, config);
};

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
