// Small SVG charts drawn to scale, colored through CSS tokens (works in light and dark).
import { h } from "./dom.js";

function niceMax(v) {
  if (v <= 5) return Math.max(1, Math.ceil(v));
  const step = Math.pow(10, Math.floor(Math.log10(v)));
  return Math.ceil(v / step) * step;
}

/** Vertical bar chart. data: [{label, value}] */
export function barChart(data, { height = 200, xTitle = "" } = {}) {
  const width = 560, left = 34, right = 8, top = 12, bottom = xTitle ? 42 : 26;
  const plotH = height - top - bottom, plotW = width - left - right;
  const max = niceMax(Math.max(0, ...data.map((d) => d.value)));
  const ticks = [0, max / 2, max].map((t) => (max <= 5 ? Math.round(t) : t));
  const bw = plotW / Math.max(1, data.length);
  const y = (v) => top + plotH - (v / max) * plotH;
  return h("div", { class: "chart" },
    h("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": xTitle || "Bar chart" },
      ticks.map((t) => [
        h("line", { class: "grid-line", x1: left, x2: width - right, y1: y(t), y2: y(t) }),
        h("text", { class: "axis", x: left - 6, y: y(t) + 4, "text-anchor": "end" }, String(+t.toFixed(1))),
      ]),
      data.map((d, i) => {
        const x = left + i * bw;
        const barH = (d.value / max) * plotH;
        return h("g", null,
          h("rect", { class: "bar", x: x + bw * 0.16, y: top + plotH - barH, width: bw * 0.68, height: Math.max(barH, d.value ? 1 : 0), rx: 3 },
            h("title", null, `${d.label}: ${d.value}`)),
          h("text", { class: "axis", x: x + bw / 2, y: top + plotH + 16, "text-anchor": "middle" }, d.label));
      }),
      xTitle ? h("text", { class: "axis", x: left + plotW / 2, y: height - 4, "text-anchor": "middle" }, xTitle) : null));
}

/** Horizontal percentage bars. data: [{label, percent, hint}] */
export function percentBars(data) {
  return h("div", { class: "hbars" }, data.map((d) => {
    const p = d.percent === null || d.percent === undefined ? null : Math.max(0, Math.min(100, d.percent));
    const tone = p === null ? "" : p < 50 ? "bad" : p < 75 ? "warn" : "";
    return h("div", { class: "hbar", title: d.hint || "" },
      h("span", { class: "label" }, d.label),
      h("div", { class: "track" }, h("div", { class: `fill ${tone}`, style: { width: `${p ?? 0}%` } })),
      h("span", { class: "val" }, p === null ? "—" : `${p.toFixed(0)}%`));
  }));
}
