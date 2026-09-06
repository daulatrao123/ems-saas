import axios from 'axios';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: API_URL,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
});

let refreshing = false;
let refreshPromise: Promise<void> | null = null;

async function refreshSession() {
  await axios.post(`${API_URL}/api/auth/refresh`, {}, { withCredentials: true });
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    if (error.response?.status !== 401 || original?._retry || original?.url?.includes('/api/auth/refresh') || original?.url?.includes('/api/auth/login')) {
      return Promise.reject(error);
    }
    original._retry = true;
    if (!refreshing) {
      refreshing = true;
      refreshPromise = refreshSession().finally(() => {
        refreshing = false;
        refreshPromise = null;
      });
    }
    try {
      await refreshPromise;
      return api(original);
    } catch {
      if (typeof window !== 'undefined') {
        localStorage.removeItem('role');
        localStorage.removeItem('name');
        localStorage.removeItem('society_id');
        window.location.href = '/login';
      }
      return Promise.reject(error);
    }
  },
);

export default api;
