export function getApiBase(): string {
  if (process.env.NEXT_PUBLIC_API_URL) {
    return process.env.NEXT_PUBLIC_API_URL.replace(/\/$/, '');
  }
  if (typeof window === 'undefined') {
    return 'http://localhost:8000';
  }
  return '';
}

export function apiUrl(path: string): string {
  const base = getApiBase();
  return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}

/** Pull a human-readable message out of a FastAPI error body (`detail` may be a string or a validation list). */
export function errorDetail(data: unknown, fallback: string): string {
  if (data && typeof data === 'object') {
    const d = data as { detail?: unknown; message?: unknown };
    if (typeof d.detail === 'string' && d.detail) return d.detail;
    if (Array.isArray(d.detail) && d.detail.length > 0) {
      return d.detail
        .map((item) =>
          item && typeof item === 'object' && 'msg' in item ? String((item as { msg: unknown }).msg) : String(item)
        )
        .join('; ');
    }
    if (typeof d.message === 'string' && d.message) return d.message;
  }
  return fallback;
}

/** fetch + JSON with FastAPI-style error handling. Throws Error(detail) on HTTP errors. */
export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), init);
  } catch {
    throw new Error('Network error — is the inspection server running?');
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    throw new Error(errorDetail(data, `Request failed (HTTP ${res.status})`));
  }
  return data as T;
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return apiJson<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
