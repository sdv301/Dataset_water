const trimSlash = (value: string) => value.replace(/\/+$/, '');

/** Базовый URL API. Dev: Vite proxy /api → :8000. Prod: nginx /flood/v2/api → FastAPI */
export const API_BASE = trimSlash(
  (import.meta.env.VITE_API_BASE as string | undefined)
    || (import.meta.env.DEV ? '/api' : '/flood/v2/api'),
);

/** Офлайн-спутник Якутии (map-tiles), без внешних CDN */
export const MAP_SATELLITE_TILES_URL = '/maps/tiles/satellite/{z}/{x}/{y}';
