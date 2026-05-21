/** Базовый URL API. В dev через Vite proxy: /api → localhost:8000 */
export const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') || '/api';
