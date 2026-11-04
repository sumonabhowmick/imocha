// Candidates (with CSV import) and batches.
import { del, get, patch, post } from "../../core/api.js";
import { adminLayout } from "../../core/layout.js";
import { fmtDate, plural } from "../../core/format.js";
import { navigate } from "../../core/router.js";
import {
  busy, checkbox, confirmDialog, emptyState, errorBox, field, h, input, loading, mount, openModal, pageHead, pill, select,
  statusPill, table, textarea, toast, toastError,
} from "../../core/ui.js";

/* ================================================================ candidates */
export async function candidatesPage(_p, query) {
  const main = adminLayout("candidates");
  const listHost = h("div");
  const search = input({ type: "search", class: "input grow", placeholder: "Search name or email" });
  const batchFilter = select([{ value: "", label: "All batches" }], query.batch || "");
  let batches = [];

  async function load() {
    mount(listHost, loading());
    try {
      const people = await get("/api/candidates", { search: search.value || null, batch_id: batchFilter.value || null });
      mount(listHost,
        h("p", { class: "muted small", style: { marginBottom: "8px" } }, plural(people.length, "candidate")),
        table({
          rows: people,
          empty: emptyState("No candidates", "Add candidates one by one or import a CSV file.", h("button", { class: "btn primary", onclick: () => editModal(null) }, "+ Add candidate")),
          columns: [
            { label: "Name", render: (c) => h("b", null, c.name) },
            { label: "Email", render: (c) => c.email },
            { label: "Batches", render: (c) => (c.batches?.length ? h("div", { class: "row", style: { gap: "4px" } }, c.batches.map((b) => h("a", { class: "pill neutral", href: `#/admin/batches/${b.id}`, style: { textDecoration: "none" } }, b.name))) : h("span", { class: "muted" }, "—")) },
            { label: "Status", render: (c) => (c.is_active ? statusPill("active", "Active") : pill("Inactive", "neutral")) },
            { label: "Added", render: (c) => fmtDate(c.created_at) },
            { label: "", class: "actions", render: (c) => [
                h("button", { class: "btn sm", type: "button", onclick: () => editModal(c) }, "Edit"),
                h("button", { class: "btn sm ghost", type: "button", onclick: () => toggleActive(c) }, c.is_active ? "Deactivate" : "Reactivate"),
                h("button", { class: "btn sm ghost danger", type: "button", onclick: () => remove(c) }, "Delete"),
              ] },
          ],
        }));
    } catch (err) { mount(listHost, errorBox(err, load)); }
  }

  function editModal(c) {
    const name = input({ value: c?.name || "", maxlength: 120 });
    const email = input({ type: "email", value: c?.email || "" });
    openModal({ title: c ? "Edit candidate" : "Add candidate", body: h("div", { class: "form" },
      field("Full name", name), field("Email", email, c ? "Changing the email cancels any unused invitation sent to the old address." : "Invitations and sign-in codes go to this address.")),
    actions: [{ label: "Cancel" }, { label: c ? "Save" : "Add candidate", primary: true, onClick: async (close) => {
      if (c) {
        const body = {};
        if (name.value.trim() !== c.name) body.name = name.value.trim();
        if (email.value.trim().toLowerCase() !== c.email) body.email = email.value.trim();
        if (Object.keys(body).length) await patch(`/api/candidates/${c.id}`, body);
      } else {
        await post("/api/candidates", { name: name.value.trim(), email: email.value.trim() });
      }
      close(); toast(c ? "Saved." : "Candidate added.", "good"); load();
    } }] });
  }
  async function toggleActive(c) {
    try { await patch(`/api/candidates/${c.id}`, { is_active: !c.is_active }); toast(c.is_active ? "Deactivated. Their results are kept." : "Reactivated."); load(); } catch (err) { toastError(err); }
  }
  async function remove(c) {
    if (!(await confirmDialog({ title: `Delete ${c.name}?`, message: "Only candidates who never started an assessment can be deleted; their assignments and unused invitations are removed too. Anyone with results can only be deactivated.", confirmLabel: "Delete", danger: true }))) return;
    try { await del(`/api/candidates/${c.id}`); toast("Candidate deleted."); load(); } catch (err) { toastError(err); }
  }

  try {
    batches = await get("/api/batches");
    batches.forEach((b) => batchFilter.appendChild(h("option", { value: b.id, selected: String(b.id) === query.batch }, b.name)));
  } catch { /* filter stays empty */ }
  let t;
  search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 250); });
  batchFilter.addEventListener("change", load);
  mount(main,
    pageHead({ title: "Candidates", sub: "People who can be assigned assessments. They sign in to the student portal with a one-time email code.",
      actions: [h("button", { class: "btn", type: "button", onclick: () => importModal(batches, load) }, "Import CSV"), h("button", { class: "btn primary", type: "button", onclick: () => editModal(null) }, "+ Add candidate")] }),
    h("div", { class: "toolbar" }, search, batchFilter), listHost);
  load();
}

function importModal(batches, onDone) {
  const file = h("input", { type: "file", accept: ".csv,text/csv", class: "input" });
  const text = textarea({ rows: 6, placeholder: "name,email\nAsha Rao,asha@example.com\nRavi Kumar,ravi@example.com" });
  const batch = select([{ value: "", label: "Do not add to a batch" }, ...batches.map((b) => ({ value: b.id, label: b.name }))], "");
  const preview = h("div");
  let previewed = false;
  file.addEventListener("change", async () => {
    const f = file.files[0];
    if (!f) return;
    if (f.size > 1_000_000) return toast("The file is larger than 1 MB.", "bad");
    text.value = await f.text();
    previewed = false;
  });
  text.addEventListener("input", () => { previewed = false; });
  const tone = { new: "good", existing: "info", duplicate: "warn", invalid: "bad" };
  const m = openModal({ title: "Import candidates from CSV", wide: true,
    body: h("div", { class: "form" },
      h("p", { class: "muted" }, "Columns: name, email (a header row is optional). Existing candidates are matched by email and never duplicated."),
      h("div", { class: "cols-2" }, field("CSV file", file), field("Add everyone to batch", batch)),
      field("…or paste CSV", text), preview),
    actions: [{ label: "Cancel" },
      { label: "Preview", onClick: async () => {
        const res = await post("/api/candidates/import", { csv_text: text.value, batch_id: batch.value ? Number(batch.value) : null, dry_run: true });
        previewed = true;
        mount(preview, h("div", { class: "stack" },
          h("div", { class: "row" }, Object.entries(res.summary).map(([k, v]) => pill(`${v} ${k}`, tone[k]))),
          table({ rows: res.rows, columns: [
            { label: "Line", class: "num", render: (r) => r.line },
            { label: "Name", render: (r) => r.name || "—" },
            { label: "Email", render: (r) => r.email || "—" },
            { label: "Result", render: (r) => [pill(r.status, tone[r.status]), r.reason ? h("div", { class: "sub" }, r.reason) : null] },
          ] })));
      } },
      { label: "Import", primary: true, onClick: async (close) => {
        if (!previewed) throw new Error("Preview the file first so you can check every row.");
        const res = await post("/api/candidates/import", { csv_text: text.value, batch_id: batch.value ? Number(batch.value) : null, dry_run: false });
        close(); toast(`${res.summary.new} added, ${res.summary.existing} already existed.`, "good"); onDone();
      } }] });
  void m;
}

/* ================================================================ batches */
export async function batchesPage() {
  const main = adminLayout("batches");
  mount(main, loading());
  let batches;
  try { batches = await get("/api/batches"); } catch (err) { return mount(main, errorBox(err, batchesPage)); }
  const create = () => batchModal(null, async (b) => navigate(`/admin/batches/${b.id}`));
  mount(main,
    pageHead({ title: "Batches", sub: "Group candidates (for example by class or section) and assign an assessment to the whole group at once.",
      actions: [h("button", { class: "btn primary", type: "button", onclick: create }, "+ New batch")] }),
    table({
      rows: batches,
      empty: emptyState("No batches yet", "Create a batch, then add candidates or import a CSV straight into it.", h("button", { class: "btn primary", onclick: create }, "+ New batch")),
      onRowClick: (b) => navigate(`/admin/batches/${b.id}`),
      columns: [
        { label: "Batch", render: (b) => [h("b", null, b.name), b.description ? h("div", { class: "sub" }, b.description) : null] },
        { label: "Members", class: "num", render: (b) => b.member_count },
        { label: "Created", render: (b) => fmtDate(b.created_at) },
      ],
    }));
}

function batchModal(b, onSaved) {
  const name = input({ value: b?.name || "", maxlength: 120, placeholder: "CSE 2026 – Section A" });
  const description = textarea({ value: b?.description || "", rows: 3 });
  openModal({ title: b ? "Edit batch" : "New batch", body: h("div", { class: "form" }, field("Name", name), field("Description", description)),
    actions: [{ label: "Cancel" }, { label: b ? "Save" : "Create batch", primary: true, onClick: async (close) => {
      const saved = b ? await patch(`/api/batches/${b.id}`, { name: name.value.trim(), description: description.value }) : await post("/api/batches", { name: name.value.trim(), description: description.value });
      close(); toast(b ? "Saved." : "Batch created.", "good"); onSaved(saved);
    } }] });
}

export async function batchPage(params) {
  const main = adminLayout("batches");
  mount(main, loading());
  let b;
  try { b = await get(`/api/batches/${params.id}`); } catch (err) { return mount(main, errorBox(err, () => batchPage(params))); }
  const reload = () => batchPage(params);

  async function addMembers() {
    const people = await get("/api/candidates");
    const existing = new Set(b.members.map((m) => m.id));
    const chosen = new Set();
    const search = input({ type: "search", placeholder: "Search candidates" });
    const list = h("div", { style: { maxHeight: "50vh", overflowY: "auto" } });
    const render = () => {
      const q = search.value.toLowerCase();
      mount(list, table({ rows: people.filter((p) => !existing.has(p.id) && (!q || p.name.toLowerCase().includes(q) || p.email.toLowerCase().includes(q))),
        empty: emptyState("Everyone is already in this batch"), columns: [
          { label: "", render: (p) => h("input", { type: "checkbox", checked: chosen.has(p.id), "aria-label": `Select ${p.name}`, onchange: (e) => (e.target.checked ? chosen.add(p.id) : chosen.delete(p.id)) }) },
          { label: "Name", render: (p) => p.name }, { label: "Email", render: (p) => p.email },
        ] }));
    };
    search.addEventListener("input", render);
    openModal({ title: `Add candidates to ${b.name}`, wide: true, body: h("div", { class: "stack" }, search, list),
      actions: [{ label: "Cancel" }, { label: "Add to batch", primary: true, onClick: async (close) => {
        if (!chosen.size) throw new Error("Select at least one candidate.");
        await post(`/api/batches/${b.id}/members`, { candidate_ids: [...chosen] });
        close(); toast(`${plural(chosen.size, "candidate")} added.`, "good"); reload();
      } }] });
    render();
  }
  async function assignTo() {
    const assessments = (await get("/api/assessments")).filter((x) => x.status !== "archived");
    if (!assessments.length) return toast("Create an assessment first.", "bad");
    const pick = select(assessments.map((x) => ({ value: x.id, label: `${x.title} (${x.display_status})` })), assessments[0].id);
    const invite = checkbox("Send invitation emails now (published assessments only)", true);
    openModal({ title: `Assign ${b.name} to an assessment`, body: h("div", { class: "form" }, field("Assessment", pick), invite.el),
      actions: [{ label: "Cancel" }, { label: "Assign", primary: true, onClick: async (close) => {
        const target = assessments.find((x) => String(x.id) === pick.value);
        const res = await post(`/api/assessments/${pick.value}/assign-batch`, { batch_id: b.id, send_invitations: invite.input.checked && target.status === "published" });
        close(); toast(`${plural(res.assignment_ids.length, "candidate")} assigned to ${target.title}.`, "good");
      } }] });
  }
  async function removeBatch() {
    if (!(await confirmDialog({ title: `Delete batch ${b.name}?`, message: "Only the group is deleted. Candidates, assignments and results stay.", confirmLabel: "Delete batch", danger: true }))) return;
    try { await del(`/api/batches/${b.id}`); toast("Batch deleted."); navigate("/admin/batches"); } catch (err) { toastError(err); }
  }
  async function removeMember(m, btn) {
    await busy(btn, async () => {
      try { await del(`/api/batches/${b.id}/members/${m.id}`); toast(`${m.name} removed from the batch.`); reload(); } catch (err) { toastError(err); }
    });
  }

  mount(main,
    pageHead({ title: b.name, crumbs: [{ label: "Batches", href: "#/admin/batches" }, { label: b.name }], sub: b.description || plural(b.member_count, "member"),
      actions: [
        h("button", { class: "btn", type: "button", onclick: () => batchModal(b, reload) }, "Edit"),
        h("button", { class: "btn danger", type: "button", onclick: removeBatch }, "Delete"),
        h("button", { class: "btn", type: "button", onclick: addMembers }, "+ Add members"),
        h("button", { class: "btn primary", type: "button", onclick: assignTo }, "Assign to assessment"),
      ] }),
    table({
      rows: b.members,
      empty: emptyState("No members yet", "Add existing candidates, or import a CSV on the Candidates page and choose this batch."),
      columns: [
        { label: "Name", render: (m) => h("b", null, m.name) },
        { label: "Email", render: (m) => m.email },
        { label: "Status", render: (m) => (m.is_active ? statusPill("active", "Active") : pill("Inactive")) },
        { label: "", class: "actions", render: (m) => h("button", { class: "btn sm ghost danger", type: "button", onclick: (e) => removeMember(m, e.currentTarget) }, "Remove") },
      ],
    }));
}
