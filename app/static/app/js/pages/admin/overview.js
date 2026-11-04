import { get } from "../../core/api.js";
import { adminLayout } from "../../core/layout.js";
import { fmtDateTime, fmtPct, fmtRelative, plural } from "../../core/format.js";
import { navigate } from "../../core/router.js";
import { session } from "../../core/session.js";
import { categoryPill, emptyState, errorBox, h, loading, mount, pageHead, stat, statusPill, table } from "../../core/ui.js";
import { percentBars } from "../../core/charts.js";

export async function overviewPage() {
  const main = adminLayout("overview");
  mount(main, loading());
  let overview, results, assessments;
  try {
    [overview, results, assessments] = await Promise.all([
      get("/api/admin/overview"),
      get("/api/admin/results", { limit: 8 }),
      get("/api/assessments"),
    ]);
  } catch (err) {
    return mount(main, errorBox(err, overviewPage));
  }

  const live = assessments.filter((a) => ["active", "scheduled"].includes(a.display_status))
    .sort((a, b) => new Date(a.access_start_at) - new Date(b.access_start_at));
  const attention = [];
  if (overview.results_needing_review) attention.push({ tone: "warn", text: `${plural(overview.results_needing_review, "result")} awaiting manual review`, sub: "Written answers need marking before results are final.", href: "#/admin/results?status=provisional", action: "Review" });
  if (overview.failed_emails) attention.push({ tone: "bad", text: `${plural(overview.failed_emails, "email")} failed to send`, sub: "Retry them from the email log.", href: "#/admin/emails?status=failed", action: "Open" });
  if (overview.failed_feedback) attention.push({ tone: "bad", text: `${plural(overview.failed_feedback, "AI feedback report")} failed`, sub: "Scores are saved; retry the feedback from the result page.", href: "#/admin/results", action: "Open" });
  for (const a of live.filter((x) => x.display_status === "scheduled").slice(0, 2)) {
    attention.push({ tone: "info", text: `${a.title} opens ${fmtRelative(a.access_start_at)}`, sub: fmtDateTime(a.access_start_at), href: `#/admin/assessments/${a.id}`, action: "View" });
  }
  const totalCats = Object.values(overview.category_counts || {}).reduce((s, n) => s + n, 0);
  const hour = Number(new Date().toLocaleString("en-IN", { hour: "numeric", hour12: false, timeZone: "Asia/Kolkata" }));
  const greeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";

  mount(main,
    pageHead({
      title: `${greeting}, ${(session.admin?.name || "").split(" ")[0] || "admin"}`,
      sub: live.some((a) => a.display_status === "active")
        ? `${plural(live.filter((a) => a.display_status === "active").length, "assessment")} live right now.`
        : "No assessment is live right now.",
      actions: [h("a", { class: "btn primary", href: "#/admin/assessments/new" }, "+ New assessment")],
    }),
    h("div", { class: "grid cols-4", style: { marginBottom: "14px" } },
      stat("Candidates", overview.total_candidates, `${overview.total_invitations} invitations sent`),
      stat("Attempts started", overview.started_attempts, `${overview.pending_assessments} assignments pending`),
      stat("Completed", overview.completed_assessments, `${overview.results_needing_review} awaiting review`),
      stat("Average score", fmtPct(overview.average_score_percent), "final results only")),
    h("div", { class: "grid wide-left", style: { marginBottom: "14px" } },
      h("section", { class: "panel" },
        h("div", { class: "panel-head" }, h("h2", null, "Needs attention"), h("span", { class: "hint" }, plural(attention.length, "item"))),
        attention.length
          ? h("ul", { class: "attention" }, attention.map((i) => h("li", null,
              h("span", { class: `stripe ${i.tone}` }),
              h("div", null, h("p", null, i.text), h("p", { class: "small muted" }, i.sub)),
              h("a", { class: "btn sm", href: i.href }, i.action))))
          : emptyState("All clear", "Nothing needs your attention right now.")),
      h("section", { class: "panel" },
        h("div", { class: "panel-head" }, h("h2", null, "Results by category"), h("span", { class: "hint" }, `${totalCats} final results`)),
        totalCats
          ? percentBars(["Competitive", "Developer", "Adaptive"].map((c) => ({ label: c, percent: ((overview.category_counts?.[c] || 0) / totalCats) * 100, hint: `${overview.category_counts?.[c] || 0} candidates` })))
          : emptyState("No final results yet", "Categories appear once results are final."),
        h("p", { class: "small muted", style: { marginTop: "12px" } }, "Adaptive 0–49% · Developer 50–74% · Competitive 75–100%"))),
    h("div", { class: "grid wide-left" },
      h("section", { class: "panel" },
        h("div", { class: "panel-head" }, h("h2", null, "Latest results"), h("a", { class: "btn sm ghost", href: "#/admin/results" }, "All results →")),
        table({
          rows: results.items,
          empty: emptyState("No results yet", "Results appear here as soon as candidates submit."),
          onRowClick: (r) => navigate(`/admin/results/${r.result_id}`),
          columns: [
            { label: "Candidate", render: (r) => [h("div", null, r.candidate_name), h("div", { class: "sub" }, r.assessment_title)] },
            { label: "Score", class: "num", render: (r) => `${r.raw_score} / ${r.max_score}` },
            { label: "%", class: "num", render: (r) => fmtPct(r.percentage) },
            { label: "Category", render: (r) => (r.result_status === "final" ? categoryPill(r.category) : statusPill("provisional")) },
            { label: "Submitted", render: (r) => h("span", { class: "nowrap" }, fmtRelative(r.submitted_at)) },
          ],
        })),
      h("section", { class: "panel" },
        h("div", { class: "panel-head" }, h("h2", null, "Live and upcoming"), h("a", { class: "btn sm ghost", href: "#/admin/assessments" }, "All →")),
        live.length
          ? h("div", { class: "stack" }, live.slice(0, 6).map((a) => h("a", { href: `#/admin/assessments/${a.id}`, style: { textDecoration: "none", color: "inherit" } },
              h("div", { class: "row between" }, h("b", null, a.title), statusPill(a.display_status)),
              h("div", { class: "small muted" }, `${fmtDateTime(a.access_start_at)} – ${fmtDateTime(a.access_end_at)} · ${a.duration_minutes} min`))))
          : emptyState("Nothing scheduled", "Publish an assessment with an access window to see it here."))));
}
