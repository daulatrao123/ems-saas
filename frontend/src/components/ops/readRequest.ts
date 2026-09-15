import api from "@/lib/api";

export const READ_TIMEOUT_MS = 12000;

// Bound the complete read (including client interceptors), not just transport.
// Aborting stops ownership changes/unmounts from publishing late responses.
export function readRequest(url: string, signal?: AbortSignal): Promise<{ data: unknown }> {
  return new Promise((resolve, reject) => {
    const controller = new AbortController();
    let settled = false;
    const finish = (error?: unknown, response?: { data: unknown }) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", cancel);
      if (error) { controller.abort(); reject(error); }
      else resolve(response!);
    };
    const cancel = () => finish({ code: "ERR_CANCELED" });
    const timer = setTimeout(() => finish({ code: "ECONNABORTED" }), READ_TIMEOUT_MS);
    if (signal?.aborted) { cancel(); return; }
    signal?.addEventListener("abort", cancel, { once: true });
    Promise.resolve().then(() => api.get(url, { timeout: READ_TIMEOUT_MS, signal: controller.signal }))
      .then((response) => finish(undefined, response), (error) => finish(error));
  });
}

export const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
export const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
export const nullableNumber = (value: unknown) => value === null || finite(value);
export const text = (value: unknown): value is string => typeof value === "string";