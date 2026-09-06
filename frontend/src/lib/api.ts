import axios, { AxiosError, InternalAxiosRequestConfig } from "axios";

const CSRF_COOKIE = "ems_csrf";
const CSRF_HEADER = "X-CSRF-Token";
const UNSAFE = new Set(["post", "put", "patch", "delete"]);

function readCookie(name: string): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : "";
}

// Same-origin: Next.js rewrites proxy /api/* to the backend, cookies ride along.
const api = axios.create({
  baseURL: "",
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  if (UNSAFE.has((config.method || "get").toLowerCase())) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) config.headers[CSRF_HEADER] = csrf;
  }
  return config;
});

type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean };
let refreshing: Promise<void> | null = null;

function isAuthEndpoint(url = ""): boolean {
  return url.includes("/api/auth/login") || url.includes("/api/auth/refresh") || url.includes("/api/auth/logout");
}

function redirectToLogin() {
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.href = "/login?expired=1";
  }
}

api.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const config = error.config as RetriableConfig | undefined;
    if (!config || error.response?.status !== 401 || config._retried || isAuthEndpoint(config.url)) {
      return Promise.reject(error);
    }
    config._retried = true;
    try {
      refreshing ??= api.post("/api/auth/refresh").then(() => undefined).finally(() => { refreshing = null; });
      await refreshing;
    } catch {
      redirectToLogin();
      return Promise.reject(error);
    }
    return api(config);
  },
);

export default api;
