// Hash router: "#/admin/assessments/12" -> handler({id: "12"}, query).

const routes = [];
let notFound = null;

export function route(pattern, handler) {
  const keys = [];
  const regex = new RegExp("^" + pattern.replace(/\//g, "\\/").replace(/:(\w+)/g, (_, k) => { keys.push(k); return "([^/]+)"; }) + "$");
  routes.push({ regex, keys, handler });
}
export function fallback(handler) { notFound = handler; }

export function currentPath() {
  const raw = location.hash.replace(/^#/, "") || "/";
  return raw.split("?")[0];
}
export function currentQuery() {
  const raw = location.hash.split("?")[1] || "";
  return Object.fromEntries(new URLSearchParams(raw));
}

export function navigate(path, { replace = false } = {}) {
  const target = "#" + path;
  if (replace) history.replaceState(null, "", location.pathname + target);
  else if (location.hash !== target) { location.hash = target; return; }
  resolve();
}

export async function resolve() {
  const path = currentPath();
  for (const r of routes) {
    const m = r.regex.exec(path);
    if (m) {
      const params = Object.fromEntries(r.keys.map((k, i) => [k, decodeURIComponent(m[i + 1])]));
      return r.handler(params, currentQuery());
    }
  }
  return notFound?.(path);
}

export function startRouter() {
  window.addEventListener("hashchange", resolve);
  return resolve();
}
