// Tiny, safe DOM builder. Text is always inserted as text nodes (never innerHTML),
// so data from the server can never be interpreted as HTML (XSS protection).

const SVG_NS = "http://www.w3.org/2000/svg";
const SVG_TAGS = new Set(["svg", "g", "rect", "line", "text", "path", "circle", "polyline", "title"]);

function append(el, child) {
  if (child === null || child === undefined || child === false || child === true) return;
  if (Array.isArray(child)) { child.forEach((c) => append(el, c)); return; }
  el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
}

/** h("div", {class: "x", onclick: fn, dataset: {id: 1}}, "text", childNode, [more]) */
export function h(tag, attrs, ...children) {
  const isSvg = SVG_TAGS.has(tag);
  const el = isSvg ? document.createElementNS(SVG_NS, tag) : document.createElement(tag);
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value); // CSSOM: allowed by CSP
    else if (key === "value" && !isSvg) el.value = value;
    else if (key === "checked" || key === "selected" || key === "disabled" || key === "multiple") el[key] = Boolean(value);
    else el.setAttribute(key === "className" ? "class" : key, value === true ? "" : String(value));
  }
  children.forEach((c) => append(el, c));
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function mount(el, ...children) {
  clear(el);
  children.forEach((c) => append(el, c));
  return el;
}

export const $ = (sel, root = document) => root.querySelector(sel);
