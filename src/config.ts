const trimSlash = (value: string) => value.replace(/\/+$/, '');

/** Базовый URL API. Dev: Vite proxy /api → :8000. Prod: nginx /flood/v2/api → FastAPI */
export const API_BASE = trimSlash(
  (import.meta.env.VITE_API_BASE as string | undefined)
    || (import.meta.env.DEV ? '/api' : '/flood/v2/api'),
);

/** Спутник Esri World Imagery через same-origin API (сервер → arcgisonline.com) */
export const MAP_SATELLITE_TILES_URL = `${API_BASE}/tiles/arcgis/{z}/{y}/{x}`;

/** Схема Carto через API-прокси (без прямого доступа к fastly из браузера) */
export const MAP_SCHEME_TILES_URL = `${API_BASE}/tiles/carto/{z}/{x}/{y}`;
