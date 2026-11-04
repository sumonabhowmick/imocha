// Formatting helpers. All times are shown in the application timezone (default IST),
// whatever the viewer's computer is set to.

export const config = { timezone: "Asia/Kolkata" };

const opts = (o) => ({ timeZone: config.timezone, ...o });

export function fmtDateTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", opts({ day: "2-digit", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" }));
}
export function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", opts({ day: "2-digit", month: "short", year: "numeric" }));
}
export function fmtTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-IN", opts({ hour: "numeric", minute: "2-digit" }));
}
export function fmtClock(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("en-IN", opts({ hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }));
}
export function fmtRelative(iso) {
  if (!iso) return "—";
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  if (abs < 60) return rtf.format(Math.round(diff), "second");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}
export function fmtPct(v) {
  return v === null || v === undefined ? "—" : `${Number(v).toFixed(2).replace(/\.00$/, "")}%`;
}
export function fmtNum(v) {
  if (v === null || v === undefined || v === "") return "—";
  return Number(v).toLocaleString("en-IN", { maximumFractionDigits: 2 });
}
export function fmtDuration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(sec)}` : `${pad(m)}:${pad(sec)}`;
}
export function plural(n, word, pluralWord) {
  return `${n} ${n === 1 ? word : pluralWord || word + "s"}`;
}

// Offset (minutes) of the app timezone at a given UTC instant.
function tzOffsetMinutes(date) {
  const parts = new Intl.DateTimeFormat("en-US", opts({ year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }))
    .formatToParts(date).reduce((acc, p) => ({ ...acc, [p.type]: p.value }), {});
  const asUtc = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second);
  return Math.round((asUtc - date.getTime()) / 60000);
}

/** "2026-10-28T10:00" typed in the app timezone -> ISO string with offset. */
export function localInputToISO(value) {
  if (!value) return null;
  const [d, t] = value.split("T");
  const [y, mo, da] = d.split("-").map(Number);
  const [hh, mm] = t.split(":").map(Number);
  let guess = Date.UTC(y, mo - 1, da, hh, mm);
  guess -= tzOffsetMinutes(new Date(guess)) * 60000;
  return new Date(guess).toISOString();
}

/** ISO -> "YYYY-MM-DDTHH:MM" in the app timezone (for <input type="datetime-local">). */
export function isoToLocalInput(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  const shifted = new Date(date.getTime() + tzOffsetMinutes(date) * 60000);
  return shifted.toISOString().slice(0, 16);
}

export const TYPE_LABELS = { mcq_single: "Single choice", mcq_multi: "Multiple choice", text: "Written", coding: "Coding" };
