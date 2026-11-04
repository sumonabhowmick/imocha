// JSON API client. Authentication uses HttpOnly cookies set by the server; this module
// never sees session tokens. State-changing requests echo the CSRF token cookie in the
// X-CSRF-Token header, as the backend requires.

export class ApiError extends Error {
  constructor(status, message, code, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function cookie(name) {
  const hit = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
  return hit ? decodeURIComponent(hit.slice(name.length + 1)) : null;
}

function isStudentPath(path) {
  return path.startsWith("/api/portal") || path.startsWith("/api/invitations") || path.startsWith("/api/student");
}

function friendlyValidation(body) {
  if (!body || !Array.isArray(body.errors) || !body.errors.length) return body?.detail;
  const parts = body.errors.slice(0, 3).map((e) => {
    const field = (e.loc || []).filter((p) => p !== "body").join(".");
    return field ? `${field}: ${e.msg}` : e.msg;
  });
  return `Please check the form. ${parts.join("; ")}`;
}

export async function api(method, path, { body, query, raw = false } = {}) {
  const url = new URL(path, location.origin);
  for (const [k, v] of Object.entries(query || {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  }
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    const token = cookie(isStudentPath(path) ? "af_csrf" : "af_admin_csrf");
    if (token) headers["X-CSRF-Token"] = token;
  }
  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check your connection and try again.", "network");
  }
  if (response.status === 401 && !path.endsWith("/me") && !path.includes("/login") && !path.includes("/session")) {
    window.dispatchEvent(new CustomEvent("auth:expired", { detail: { student: isStudentPath(path) } }));
  }
  if (!response.ok) {
    let data = null;
    try { data = await response.json(); } catch { /* not JSON */ }
    const message = friendlyValidation(data) || `Request failed (HTTP ${response.status}).`;
    throw new ApiError(response.status, message, data?.code, data);
  }
  if (raw) return response;
  if (response.status === 204) return null;
  const text = await response.text();
  return text ? JSON.parse(text) : null;
}

export const get = (path, query) => api("GET", path, { query });
export const post = (path, body, query) => api("POST", path, { body: body ?? {}, query });
export const patch = (path, body) => api("PATCH", path, { body });
export const put = (path, body) => api("PUT", path, { body });
export const del = (path) => api("DELETE", path);

/** Download a file from an authenticated endpoint (e.g. CSV/XLSX export). */
export async function download(path, query, fallbackName) {
  const response = await api("GET", path, { query, raw: true });
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") || "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] || fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

/** Fetch an authenticated image and return an object URL for an <img>. */
export async function imageUrl(path) {
  const response = await api("GET", path, { raw: true });
  return URL.createObjectURL(await response.blob());
}
