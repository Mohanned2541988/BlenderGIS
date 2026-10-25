/* AI Documentary Studio - single-page UI (no framework, no build step). */
"use strict";

// ---------------------------------------------------------------- state & utils
const S = {
  status: null, settings: null, project: null, pid: null, view: "projects",
  lang: "en", assetFilter: { q: "", type: "", status: "", scene: "" }, selAsset: null,
  openScenes: new Set(), selBlock: null, lastPreview: null, wizard: { step: 0, data: {} },
};
const NAV = [
  ["projects", "nav_projects", "Projects", false],
  ["new", "nav_new", "New Documentary", false],
  ["research", "nav_research", "Research", true],
  ["script", "nav_script", "Script Editor", true],
  ["storyboard", "nav_storyboard", "Storyboard", true],
  ["media", "nav_media", "Media Library", true],
  ["voice", "nav_voice", "Voice & Subtitles", true],
  ["timeline", "nav_timeline", "Timeline", true],
  ["export", "nav_export", "Export", true],
  ["settings", "nav_settings", "Settings", false],
];
const STEP_FOR = { research: "research", script: "script", storyboard: "storyboard", media: "media",
  voice: "voice", timeline: "subtitles", export: "render" };

const t = (k, en) => (S.lang !== "en" && window.I18N[S.lang] && window.I18N[S.lang][k]) || en || k;
const $ = (sel, root = document) => root.querySelector(sel);
const fmtT = (s) => {
  if (s == null || isNaN(s)) return "–";
  const m = Math.floor(s / 60), r = (s - m * 60);
  return `${m}:${r.toFixed(1).padStart(4, "0")}`;
};
const isAr = (s) => /[؀-ۿ]/.test(s || "");

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "html") el.innerHTML = v;
    else if (k in el && typeof v !== "string") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

async function api(method, path, body, raw) {
  const opts = { method, headers: { "X-Studio": "1" } };
  if (raw) { opts.body = raw; }
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (!res.ok) {
    const err = new Error((data && data.error) || `Request failed (${res.status})`);
    err.status = res.status; err.data = data || {};
    throw err;
  }
  return data;
}
const P = (path) => `/api/projects/${encodeURIComponent(S.pid)}${path}`;

function toast(msg, kind = "") {
  const el = h("div", { class: `toast ${kind}`, role: kind === "err" ? "alert" : "status" }, msg);
  $("#toasts").append(el);
  setTimeout(() => el.remove(), kind === "err" ? 9000 : 4000);
}
const fail = (e) => toast(e.message + (e.data && e.data.errors ? "\n- " + e.data.errors.join("\n- ") : ""), "err");

function modal(title, bodyNodes, buttons) {
  return new Promise((resolve) => {
    const dlg = $("#modal"), form = $("#modal-form");
    form.innerHTML = "";
    form.append(h("h2", {}, title), ...bodyNodes,
      h("div", { class: "row", style: { justifyContent: "flex-end", marginTop: "18px" } },
        buttons.map(([label, val, cls]) => h("button", { class: cls || "", value: val }, label))));
    dlg.onclose = () => resolve(dlg.returnValue);
    dlg.returnValue = "";
    dlg.showModal();
  });
}
async function confirmBox(title, msg, okLabel = "Confirm", danger = false) {
  const v = await modal(title, [h("p", {}, msg)], [[t("cancel", "Cancel"), "no", "ghost"], [okLabel, "yes", danger ? "danger" : ""]]);
  return v === "yes";
}
async function promptBox(title, fields) {
  const inputs = fields.map(f => {
    const inp = f.type === "textarea" ? h("textarea", { name: f.name, class: f.rtl ? "rtl" : "" }, f.value || "")
      : h("input", { name: f.name, value: f.value || "", placeholder: f.placeholder || "" });
    return [h("label", {}, f.label), inp];
  });
  const v = await modal(title, inputs.flat(), [[t("cancel", "Cancel"), "no", "ghost"], [t("save", "OK"), "yes"]]);
  if (v !== "yes") return null;
  const out = {};
  for (const f of fields) out[f.name] = $(`#modal-form [name="${f.name}"]`).value;
  return out;
}

// ---------------------------------------------------------------- jobs
let jobTimer = null;
function runJob(job, onDone) {
  const bar = $("#jobbar");
  bar.hidden = false;
  $("#job-cancel").onclick = () => api("POST", `/api/jobs/${job.id}/cancel`).catch(fail);
  const poll = async () => {
    let j;
    try { j = await api("GET", `/api/jobs/${job.id}`); } catch (e) { bar.hidden = true; return fail(e); }
    $("#job-msg").textContent = j.message || j.kind.replace(/_/g, " ") + "…";
    $("#job-eta").textContent = j.eta != null ? `· about ${j.eta}s left` : `· ${j.elapsed}s`;
    $("#job-progress").style.width = `${Math.round((j.progress || 0) * 100)}%`;
    if (j.status === "running" || j.status === "queued") { jobTimer = setTimeout(poll, 700); return; }
    bar.hidden = true;
    if (j.status === "done") { onDone && onDone(j.result); }
    else if (j.status === "cancelled") toast("Cancelled.");
    else toast(j.error || "Job failed", "err");
  };
  poll();
}

// ---------------------------------------------------------------- saving
let saveTimers = {};
function setSave(state) { $("#save-state").textContent = state === "saving" ? t("saving", "Saving…") : state === "saved" ? t("saved", "Saved") : state; }
function debounceSave(key, fn, ms = 600) {
  setSave("saving");
  clearTimeout(saveTimers[key]);
  saveTimers[key] = setTimeout(async () => {
    try { await fn(); setSave("saved"); } catch (e) { setSave("Save failed"); fail(e); }
  }, ms);
}
async function reloadProject() {
  if (!S.pid) return;
  S.project = await api("GET", P(""));
  renderNav();
}
async function saveScene(sid, changes, immediate) {
  const doit = async () => {
    const rec = await api("PUT", P(`/scenes/${sid}`), changes);
    const i = S.project.scenes.findIndex(s => s.id === sid);
    if (i >= 0) S.project.scenes[i] = rec;
    return rec;
  };
  if (immediate) { setSave("saving"); const r = await doit(); setSave("saved"); return r; }
  debounceSave("scene" + sid + Object.keys(changes).join(), doit);
}

// ---------------------------------------------------------------- shell
function renderNav() {
  const nav = $("#nav"); nav.innerHTML = "";
  const steps = (S.project && S.project.status.steps) || {};
  NAV.forEach(([id, key, en, needsProject], i) => {
    const step = STEP_FOR[id];
    nav.append(h("a", {
      href: `#/${id}`, class: `${S.view === id ? "active" : ""} ${needsProject && !S.project ? "disabled" : ""}`,
      "aria-current": S.view === id ? "page" : null,
    }, h("span", { class: "num" }, String(i + 1).padStart(2, "0")), t(key, en),
      step && S.project ? h("span", { class: `dot ${steps[step] ? "ok" : ""}`, title: steps[step] ? "Complete" : "Not complete" }) : null));
  });
  $("#current-project").textContent = S.project ? S.project.meta.title : "—";
  document.documentElement.lang = S.lang;
  document.documentElement.dir = S.lang === "ar" ? "rtl" : "ltr";
  $("#lang-toggle").textContent = S.lang === "ar" ? "English" : "العربية";
  document.querySelectorAll("[data-i18n]").forEach(el => el.textContent = t(el.dataset.i18n, el.textContent));
}

async function route() {
  const [, view] = (location.hash || "#/projects").split("/");
  S.view = NAV.some(n => n[0] === view) ? view : "projects";
  const need = NAV.find(n => n[0] === S.view)[3];
  if (need && !S.project) { location.hash = "#/projects"; return; }
  renderNav();
  const v = $("#view"); v.innerHTML = "";
  try {
    await (VIEWS[S.view])(v);
  } catch (e) { fail(e); }
  $("#main").focus({ preventScroll: true });
}

async function openProject(pid, go = "script") {
  S.pid = pid;
  try { localStorage.setItem("ads.pid", pid); } catch (e) {}
  await reloadProject();
  S.openScenes.clear(); S.selAsset = null;
  location.hash = `#/${go}`;
}

function header(kicker, title, lede, actions) {
  return h("div", { class: "row spread", style: { alignItems: "flex-end", marginBottom: "18px" } },
    h("div", {}, h("div", { class: "kicker" }, kicker), h("h1", {}, title), lede ? h("p", { class: "lede" }, lede) : null),
    h("div", { class: "row" }, actions || []));
}
function select(options, value, onchange, attrs = {}) {
  return h("select", { ...attrs, onchange: (e) => onchange(e.target.value) },
    options.map(([v, l]) => h("option", { value: v, selected: String(v) === String(value) }, l)));
}
function field(label, input, hint) { return h("div", {}, h("label", {}, label), input, hint ? h("div", { class: "hint" }, hint) : null); }
const assetById = (id) => S.project.assets.find(a => a.id === id);
const thumbUrl = (a) => a && a.thumb ? `/media/${encodeURIComponent(S.pid)}/${a.thumb}` : null;
const mediaUrl = (a) => `/media/${encodeURIComponent(S.pid)}/${a.path.split("/").map(encodeURIComponent).join("/")}`;

// ---------------------------------------------------------------- views
const VIEWS = {};

VIEWS.projects = async (v) => {
  const list = await api("GET", "/api/projects");
  v.append(header("01 · Library", t("projects_title", "Your documentaries"),
    t("projects_lede", "Every project lives on this Mac and saves automatically."),
    [h("button", { class: "ghost", title: "Builds a 35-second demo from original generated media", onclick: async () => {
      try { runJob(await api("POST", "/api/sample"), (r) => { toast(r.voiced ? "Sample created with the macOS Arabic voice." : "Sample created. Narration is a silent placeholder (no Arabic system voice found) - import a recording or install a voice.", "ok"); openProject(r.id, "timeline"); }); } catch (e) { fail(e); }
    } }, "Create sample project"),
     h("button", { onclick: () => location.hash = "#/new" }, "+ " + t("nav_new", "New Documentary"))]));
  if (!S.status.ffmpeg.available) v.append(h("div", { class: "card hl", style: { marginBottom: "14px" } },
    h("strong", {}, "FFmpeg is missing. "), "Install it with ", h("code", {}, "brew install ffmpeg"), " then restart the app. Import and rendering need it."));
  if (!list.length) { v.append(h("div", { class: "empty" }, "No projects yet. Create your first documentary.")); return; }
  const grid = h("div", { class: "grid cols-auto" });
  for (const p of list) {
    const active = S.pid === p.id;
    grid.append(h("div", { class: `card collage ${active ? "hl" : ""}` },
      h("div", { class: "kicker" }, new Date(p.updated * 1000).toLocaleString()),
      h("h2", { style: { margin: "6px 0 4px" }, dir: "auto" }, p.title),
      p.error ? h("div", { class: "badge err" }, p.error) : null,
      h("div", { class: "muted small", style: { wordBreak: "break-all" } }, p.path),
      h("div", { class: "row", style: { marginTop: "12px" } },
        h("button", { class: "small", onclick: () => openProject(p.id) }, t("open", "Open")),
        h("button", { class: "ghost small", onclick: async () => {
          const r = await promptBox("Duplicate project", [{ name: "title", label: "Title of the copy", value: p.title + " (copy)" }]);
          if (!r) return;
          try { const np = await api("POST", `/api/projects/${encodeURIComponent(p.id)}/duplicate`, { title: r.title }); toast("Duplicated.", "ok"); openProject(np.id, "projects"); route(); } catch (e) { fail(e); }
        } }, t("duplicate", "Duplicate")),
        h("button", { class: "ghost small", onclick: async () => {
          const r = await promptBox("Rename project", [{ name: "title", label: "Title", value: p.title }]);
          if (!r) return;
          try { await api("POST", `/api/projects/${encodeURIComponent(p.id)}/rename`, { title: r.title }); if (S.pid === p.id) await reloadProject(); route(); } catch (e) { fail(e); }
        } }, t("rename", "Rename")),
        h("button", { class: "ghost small", title: "Zip the project folder into its exports folder", onclick: async () => {
          try { const r = await api("POST", `/api/projects/${encodeURIComponent(p.id)}/package`); toast("Packaged:\n" + r.path, "ok"); } catch (e) { fail(e); }
        } }, "Package"),
        h("button", { class: "ghost small", onclick: async () => {
          try { const r = await api("POST", `/api/projects/${encodeURIComponent(p.id)}/export-metadata`); toast("Metadata exported:\n" + r.path, "ok"); } catch (e) { fail(e); }
        } }, "Export metadata"))));
  }
  v.append(grid);
  v.append(h("p", { class: "hint", style: { marginTop: "16px" } },
    "Projects folder: ", S.status.projects_dir, ". To delete a project, move its folder to the Trash in Finder (the app never deletes projects itself)."));
};

VIEWS.new = async (v) => {
  const W = S.wizard, d = W.data;
  const styles = Object.entries(S.status.visual_styles);
  const set = (k) => (e) => { d[k] = e.target.value; };
  const inp = (k, ph, attrs = {}) => h("input", { value: d[k] || "", placeholder: ph || "", oninput: set(k), ...attrs });
  const ta = (k, ph, attrs = {}) => h("textarea", { placeholder: ph || "", oninput: set(k), ...attrs }, d[k] || "");
  const sel = (k, opts, def) => { if (d[k] == null) d[k] = def; return select(opts, d[k], (val) => { d[k] = val; if (k === "duration_preset") VIEWS.new(clear(v)); }); };
  const pages = [
    ["Story", () => [
      field("Documentary title *", inp("title", "e.g. The City That Vanished", { required: true, dir: "auto" })),
      field("Topic", ta("topic", "What is the documentary about?", { dir: "auto" })),
      field("Main question", inp("main_question", "The one question the film answers", { dir: "auto" })),
      field("Target audience", inp("audience", "e.g. curious Arabic-speaking 18–35 viewers")),
      field("Tone", inp("tone", "investigative, calm, precise")),
    ]],
    ["Format", () => [
      h("div", { class: "grid cols-2" },
        field("Platform", sel("platform", [["instagram_reels", "Instagram Reels"], ["tiktok", "TikTok"], ["youtube_shorts", "YouTube Shorts"], ["youtube", "YouTube (landscape)"]], "instagram_reels")),
        field("Aspect ratio", sel("aspect_ratio", [["9:16", "Vertical 9:16"], ["16:9", "Landscape 16:9"], ["1:1", "Square 1:1"]], "9:16")),
        field("Target duration", sel("duration_preset", [["30", "30 seconds"], ["60", "60 seconds"], ["90", "90 seconds"], ["180", "3 minutes"], ["300", "5 minutes"], ["custom", "Custom…"]], "60")),
        d.duration_preset === "custom" ? field("Custom duration (seconds)", inp("custom_duration", "e.g. 75", { type: "number", min: 5, max: 3600 })) : h("div"),
        field("Narration language", sel("narration_language", [["ar", "Arabic"], ["en", "English"]], "ar")),
        field("Subtitle language", sel("subtitle_language", [["en", "English"], ["ar", "Arabic"], ["bilingual", "Arabic + English"]], "en")),
      ),
      field("Visual style", h("div", { class: "pill-select" }, styles.map(([k, l]) => {
        if (!d.visual_style) d.visual_style = "editorial_collage";
        return h("label", { class: d.visual_style === k ? "on" : "" }, h("input", { type: "radio", name: "vs", value: k, checked: d.visual_style === k,
          onchange: () => { d.visual_style = k; VIEWS.new(clear(v)); } }), l);
      }))),
    ]],
    ["Production", () => [
      field("Research requirements", ta("research_requirements", "e.g. primary sources only; at least 2 sources per claim")),
      field("Available budget", inp("budget", "e.g. free / local only, or $10")),
      field("Preferred AI providers", inp("preferred_providers", "e.g. Claude for script, macOS voices, whisper.cpp"),
        "Providers are configured in Settings. Nothing is sent to an external service unless you enable it there."),
    ]],
  ];
  v.append(header("02 · Wizard", t("nav_new", "New Documentary"), "The project is created and saved as soon as you press Create."));
  v.append(h("div", { class: "steps" }, pages.map(([n], i) => h("span", { class: i === W.step ? "on" : "" }, `${i + 1}. ${n}`))));
  const card = h("div", { class: "card", style: { maxWidth: "760px" } }, pages[W.step][1]());
  const nav = h("div", { class: "row", style: { marginTop: "18px" } },
    W.step > 0 ? h("button", { class: "ghost", onclick: () => { W.step--; VIEWS.new(clear(v)); } }, t("back", "Back")) : null,
    W.step < pages.length - 1 ? h("button", { onclick: () => { if (!d.title) return toast("Please enter a title.", "err"); W.step++; VIEWS.new(clear(v)); } }, t("next", "Next")) : null,
    h("button", { class: W.step === pages.length - 1 ? "" : "ghost", onclick: async () => {
      if (!(d.title || "").trim()) return toast("Please enter a title.", "err");
      const dur = d.duration_preset === "custom" ? Number(d.custom_duration) : Number(d.duration_preset || 60);
      if (!dur || dur < 5) return toast("Please enter a valid duration.", "err");
      try {
        const p = await api("POST", "/api/projects", { ...d, target_duration: dur });
        S.wizard = { step: 0, data: {} };
        toast("Project created and saved.", "ok");
        openProject(p.id, "research");
      } catch (e) { fail(e); }
    } }, t("create", "Create project")));
  card.append(nav);
  v.append(card);
};
function clear(v) { v.innerHTML = ""; return v; }

// ---------------- research
VIEWS.research = async (v) => {
  const pr = S.project;
  const info = await api("GET", P("/research"));
  v.append(header("03 · Research", t("research_title", "Research workspace"),
    "Collect sources, record claims with an honest status, and link them to scenes. The app never marks a source as checked for you."));
  const left = h("div", { class: "list" }), right = h("div", { class: "list" });
  v.append(h("div", { class: "split" }, left, right));

  // notes
  left.append(h("div", { class: "card" }, h("h2", {}, "Research notes"),
    h("textarea", { dir: "auto", style: { minHeight: "140px" }, placeholder: "Paste notes, timelines, names, quotations…",
      oninput: (e) => debounceSave("notes", () => api("PATCH", P(""), { notes: e.target.value })) }, pr.meta.notes || ""),
    pr.meta.research_requirements ? h("div", { class: "hint" }, "Requirements: " + pr.meta.research_requirements) : null));

  // sources
  const srcCard = h("div", { class: "card" }, h("div", { class: "row spread" }, h("h2", {}, `Sources (${pr.sources.length})`),
    h("button", { class: "small", onclick: () => editSource(null) }, "+ Add source")));
  if (!pr.sources.length) srcCard.append(h("div", { class: "empty" }, "No sources yet. Add URLs, books, interviews or archives."));
  for (const s of pr.sources) {
    srcCard.append(h("div", { class: "card flat", style: { marginTop: "8px" } },
      h("div", { class: "row spread" },
        h("div", { style: { minWidth: 0 } }, h("strong", { dir: "auto" }, s.title || s.url), " ",
          h("span", { class: `badge ${s.reviewed ? "ok" : "warn"}` }, s.reviewed ? "reviewed by you" : "not reviewed"),
          s.added_by === "web_search" ? h("span", { class: "badge ext", style: { marginInlineStart: "6px" } }, "found by web search") : null),
        h("div", { class: "row" },
          h("button", { class: "ghost small", onclick: () => editSource(s) }, "Edit"),
          h("button", { class: "danger small", onclick: async () => {
            if (!await confirmBox("Delete source", "Remove this source and unlink it from claims and scenes?", "Delete", true)) return;
            await api("DELETE", P(`/sources/${s.id}`)).catch(fail); await reloadProject(); route();
          } }, t("delete", "Delete")))),
      s.url ? h("a", { href: s.url, target: "_blank", rel: "noopener noreferrer", class: "small", style: { color: "var(--blue)", wordBreak: "break-all" } }, s.url) : null,
      h("div", { class: "muted small" }, [s.publisher, s.published].filter(Boolean).join(" · ") || "No date recorded", " · id ", h("code", {}, s.id)),
      s.summary ? h("p", { class: "small", dir: "auto" }, s.summary) : null,
      (s.quotes || []).map(q => h("blockquote", { class: "small muted", dir: "auto", style: { borderInlineStart: "2px solid var(--line)", margin: "4px 0", paddingInlineStart: "8px" } }, q))));
  }
  left.append(srcCard);

  // web search
  const prov = S.settings.providers.search;
  left.append(h("div", { class: "card" }, h("h2", {}, "Web search"),
    prov === "none" ? h("p", { class: "muted" }, "No search provider is enabled. You can enable Claude web search in Settings (sends your query to Anthropic). Results are added as unreviewed sources.")
      : (() => { const q = h("input", { placeholder: "Search query", dir: "auto" });
        return h("div", { class: "row" }, q, h("button", { onclick: async () => {
          try { runJob(await api("POST", P("/research/search"), { query: q.value }), async (r) => { toast(`Found ${r.found}, added ${r.added} new sources (marked unreviewed).`, "ok"); await reloadProject(); route(); }); } catch (e) { fail(e); }
        } }, "Search")); })()));

  // claims
  const claimCard = h("div", { class: "card" }, h("div", { class: "row spread" }, h("h2", {}, `Claims (${pr.claims.length})`),
    h("button", { class: "small", onclick: () => editClaim(null) }, "+ Add claim")),
    h("p", { class: "hint" }, "Verified = supported by a source you reviewed · Unverified · Interpretation · Reconstruction (creative/AI) · Disputed (conflicting evidence)"));
  for (const c of pr.claims) {
    claimCard.append(h("div", { class: "card flat", style: { marginTop: "8px" } },
      h("div", { class: "row spread" }, h("span", { class: `badge ${c.status}` }, c.status),
        h("div", { class: "row" }, h("button", { class: "ghost small", onclick: () => editClaim(c) }, "Edit"),
          h("button", { class: "danger small", onclick: async () => { if (!await confirmBox("Delete claim", "Delete this claim?", "Delete", true)) return; await api("DELETE", P(`/claims/${c.id}`)).catch(fail); await reloadProject(); route(); } }, t("delete", "Delete")))),
      h("p", { dir: "auto", style: { margin: "6px 0" } }, c.text),
      h("div", { class: "small muted" }, "Sources: ", (c.source_ids || []).length ? c.source_ids.map(id => (pr.sources.find(s => s.id === id) || {}).title || id).join("; ") : h("span", { style: { color: "var(--red)" } }, "none"),
        (c.conflicting_source_ids || []).length ? [" · Conflicts: ", c.conflicting_source_ids.map(id => (pr.sources.find(s => s.id === id) || {}).title || id).join("; ")] : null)));
  }
  right.append(claimCard);

  // warnings + brief
  right.append(h("div", { class: "card" }, h("h2", {}, "Fact-check warnings"),
    info.warnings.length ? h("ul", { class: "warn-list" }, info.warnings.map(w => h("li", { class: w.level === "error" ? "error" : "warn" }, w.msg)))
      : h("p", { class: "muted" }, "No warnings.")));
  right.append(h("div", { class: "card" }, h("div", { class: "row spread" }, h("h2", {}, "Research brief"),
    h("button", { class: "small", onclick: async () => {
      try { runJob(await api("POST", P("/research/brief")), () => { toast("Brief saved to research/brief.md", "ok"); route(); }); } catch (e) { fail(e); }
    } }, info.brief ? "Regenerate" : "Generate")),
    h("p", { class: "hint" }, S.status.llm_is_ai ? `Uses ${S.status.llm}; restricted to your sources, notes and claims.` : "Offline mode: compiled from your notes, sources and claims (no AI)."),
    info.brief ? h("div", { class: "brief", dir: "auto" }, info.brief.text) : h("div", { class: "empty" }, "No brief yet.")));

  async function editSource(s) {
    const r = await promptBox(s ? "Edit source" : "Add source", [
      { name: "title", label: "Title", value: s && s.title }, { name: "url", label: "URL (http/https)", value: s && s.url },
      { name: "publisher", label: "Publisher / author", value: s && s.publisher }, { name: "published", label: "Publication date", value: s && s.published, placeholder: "YYYY-MM-DD" },
      { name: "summary", label: "Summary / relevant content", type: "textarea", value: s && s.summary },
      { name: "quotes", label: "Quotations (one per line)", type: "textarea", value: s && (s.quotes || []).join("\n") },
      { name: "reviewed", label: "Have you personally reviewed this source? (yes / no)", value: s && s.reviewed ? "yes" : "no" },
    ]);
    if (!r) return;
    r.reviewed = /^(y|yes|true|1|نعم)$/i.test(r.reviewed.trim());
    try { await api(s ? "PUT" : "POST", P(s ? `/sources/${s.id}` : "/sources"), r); await reloadProject(); route(); } catch (e) { fail(e); }
  }
  async function editClaim(c) {
    const srcs = S.project.sources;
    const sel = new Set((c && c.source_ids) || []), conf = new Set((c && c.conflicting_source_ids) || []);
    const text = h("textarea", { dir: "auto" }, (c && c.text) || "");
    const status = select([["unverified", "Unverified"], ["verified", "Verified"], ["interpretation", "Interpretation"], ["reconstruction", "Creative / AI reconstruction"], ["disputed", "Disputed"]], (c && c.status) || "unverified", () => {});
    const pills = (set) => h("div", { class: "pill-select" }, srcs.length ? srcs.map(s => {
      const lab = h("label", { class: set.has(s.id) ? "on" : "" }, h("input", { type: "checkbox", checked: set.has(s.id),
        onchange: (e) => { e.target.checked ? set.add(s.id) : set.delete(s.id); lab.className = e.target.checked ? "on" : ""; } }), s.title || s.url);
      return lab;
    }) : h("span", { class: "muted small" }, "Add sources first."));
    const v2 = await modal(c ? "Edit claim" : "Add claim", [h("label", {}, "Claim"), text, h("label", {}, "Status"), status,
      h("label", {}, "Supporting sources"), pills(sel), h("label", {}, "Conflicting sources"), pills(conf)],
      [[t("cancel", "Cancel"), "no", "ghost"], [t("save", "Save"), "yes"]]);
    if (v2 !== "yes") return;
    try {
      await api(c ? "PUT" : "POST", P(c ? `/claims/${c.id}` : "/claims"), { text: text.value, status: status.value, source_ids: [...sel], conflicting_source_ids: [...conf] });
      await reloadProject(); route();
    } catch (e) { fail(e); }
  }
};

// ---------------- script editor
VIEWS.script = async (v) => {
  const pr = S.project, st = S.settings;
  const scenes = pr.scenes;
  const tl = (await api("GET", P("/timeline"))).timeline;
  const est = Object.fromEntries(tl.entries.map(e => [e.scene_id, e]));
  const target = pr.meta.target_duration;
  v.append(header("04 · Script", t("script_title", "Script editor"),
    `Estimated ${fmtT(tl.total)} of ${fmtT(target)} target · Arabic ${st.speaking_rate_ar_wpm} wpm / English ${st.speaking_rate_en_wpm} wpm (estimates — real timing comes from the recorded voice).`,
    [h("button", { class: "ghost", onclick: () => addScene(null) }, "+ " + t("add_scene", "Add scene")),
     h("a", { class: "btn ghost", href: "#", onclick: async (e) => { e.preventDefault(); try { const r = await api("GET", P("/export/scenes?format=md")); window.open(r.url, "_blank"); } catch (er) { fail(er); } } }, "Export script (.md)"),
     h("button", { onclick: generate }, S.status.llm_is_ai ? `✦ ${t("generate_script", "Generate script")} (${S.status.llm})` : t("generate_script", "Generate structure"))]));
  if (!S.status.llm_is_ai) v.append(h("p", { class: "hint" }, "No AI provider is enabled, so “Generate structure” creates an editable scene outline with placeholders (hook → question → context → evidence → counterpoint → conclusion). Configure Claude or Ollama in Settings for AI drafting."));
  const list = h("div", { class: "list" });
  if (!scenes.length) list.append(h("div", { class: "empty" }, "No scenes yet. Generate a structure or add a scene."));
  scenes.forEach((s, i) => list.append(sceneCard(s, i, est[s.id])));
  v.append(list);

  async function generate() {
    let body = {};
    if (scenes.length) {
      const c = await modal("Replace the script?", [h("p", {}, "The project already has scenes. Replace them all, or append the new scenes after them?")],
        [[t("cancel", "Cancel"), "no", "ghost"], ["Append", "append", "ghost"], ["Replace", "replace", "danger"]]);
      if (c === "replace") body.replace = true; else if (c === "append") body.append = true; else return;
    }
    try { runJob(await api("POST", P("/script/generate"), body), async (r) => { toast(`${r.scenes.length} scenes created (${r.ai ? "AI draft — review every fact" : "template"}).`, "ok"); await reloadProject(); route(); }); }
    catch (e) { fail(e); }
  }
  async function addScene(after) {
    try { const s = await api("POST", P("/scenes"), { after_id: after }); S.openScenes.add(s.id); await reloadProject(); route(); } catch (e) { fail(e); }
  }
  function sceneCard(s, i, e) {
    const open = S.openScenes.has(s.id);
    const card = h("div", { class: "card scene", draggable: true, "data-id": s.id });
    dragReorder(card, list, s.id);
    const durLabel = e ? `${e.duration.toFixed(1)}s · ${e.duration_source}` : "";
    card.append(h("div", { class: "scene-head", onclick: (ev) => { if (ev.target.closest("button")) return; open ? S.openScenes.delete(s.id) : S.openScenes.add(s.id); route(); } },
      h("span", { class: "handle", title: "Drag to reorder", "aria-hidden": true }, "⋮⋮"),
      h("span", { class: "scene-num" }, String(i + 1).padStart(2, "0")),
      h("span", { class: "badge" }, s.purpose),
      h("span", { class: "scene-title", dir: "auto" }, s.title || (s.narration_ar || s.narration_en || "Untitled scene").slice(0, 80)),
      s.generated_by === "template" ? h("span", { class: "badge warn" }, "placeholder") : null,
      ["anthropic", "ollama"].includes(s.generated_by) ? h("span", { class: "badge ext", title: "Drafted by AI — check facts" }, "AI draft") : null,
      h("span", { class: "muted small" }, durLabel),
      h("button", { class: "ghost small", "aria-label": "Move up", disabled: i === 0, onclick: () => move(i, -1) }, "↑"),
      h("button", { class: "ghost small", "aria-label": "Move down", disabled: i === scenes.length - 1, onclick: () => move(i, 1) }, "↓")));
    if (!open) return card;
    const upd = (k) => (ev) => saveScene(s.id, { [k]: ev.target.type === "checkbox" ? ev.target.checked : ev.target.value });
    const body = h("div", { class: "scene-body" },
      h("div", { class: "grid cols-2" },
        field("Title", h("input", { value: s.title, dir: "auto", oninput: upd("title") })),
        field("Narrative purpose", select(S.status.purposes.map(p => [p, p]), s.purpose, (val) => saveScene(s.id, { purpose: val })))),
      h("div", { class: "grid cols-2" },
        field(t("narration_ar", "Arabic narration"), h("textarea", { class: "rtl", style: { minHeight: "110px" }, oninput: upd("narration_ar") }, s.narration_ar)),
        field(t("narration_en", "English translation"), h("textarea", { style: { minHeight: "110px" }, oninput: upd("narration_en") }, s.narration_en))),
      h("div", { class: "grid cols-3" },
        field("Duration override (s)", h("input", { type: "number", min: 0.5, step: 0.1, value: s.duration ?? "", placeholder: e ? e.duration.toFixed(1) : "",
          oninput: (ev) => saveScene(s.id, { duration: ev.target.value === "" ? null : Number(ev.target.value) }) }), "Empty = narration audio length, or estimate."),
        field("Transition out", select([["cut", "Cut"], ["fade", "Fade through black"], ["crossfade", "Crossfade"]], s.transition.type, (val) => saveScene(s.id, { transition: { type: val } }))),
        field("Transition length (s)", h("input", { type: "number", min: 0.1, max: 3, step: 0.1, value: s.transition.duration, oninput: (ev) => saveScene(s.id, { transition: { duration: Number(ev.target.value) } }) }))),
      h("div", { class: "grid cols-2" },
        field("On-screen text", h("input", { value: s.on_screen_text, dir: "auto", oninput: upd("on_screen_text") })),
        field("Required assets", h("input", { value: s.required_assets, oninput: upd("required_assets") }))),
      field("Visual description", h("textarea", { dir: "auto", oninput: upd("visual_description") }, s.visual_description)),
      field("Sound design notes", h("input", { value: s.sound_notes, oninput: upd("sound_notes") })),
      h("label", { style: { display: "flex", gap: "8px", alignItems: "center" } }, h("input", { type: "checkbox", checked: s.factual, onchange: upd("factual") }), "This scene states facts (warn if no claim is linked)"),
      field("Linked claims", linkPills(pr.claims, s.claim_ids, (ids) => saveScene(s.id, { claim_ids: ids }, true), (c) => `[${c.status}] ${c.text.slice(0, 60)}`), pr.claims.length ? null : "Add claims in Research to link evidence."),
      field("Linked sources", linkPills(pr.sources, s.source_ids, (ids) => saveScene(s.id, { source_ids: ids }, true), (x) => x.title || x.url)),
      h("div", { class: "row", style: { marginTop: "14px" } },
        h("button", { class: "ghost small", onclick: () => addScene(s.id) }, "+ Scene after"),
        h("button", { class: "ghost small", onclick: async () => { try { await api("POST", P(`/scenes/${s.id}/duplicate`)); await reloadProject(); route(); } catch (er) { fail(er); } } }, t("duplicate", "Duplicate")),
        h("button", { class: "ghost small", disabled: !S.status.llm_is_ai, title: S.status.llm_is_ai ? "" : "Needs an AI provider (Settings)", onclick: async () => {
          const r = await promptBox("Regenerate this scene", [{ name: "instruction", label: "Instruction (optional)", type: "textarea", placeholder: "e.g. make the hook sharper; keep it under 20 words" }]);
          if (!r) return;
          try { runJob(await api("POST", P(`/scenes/${s.id}/regenerate`), r), async () => { toast("Scene rewritten. The previous text is kept in the project history.", "ok"); await reloadProject(); route(); }); } catch (er) { fail(er); }
        } }, "✦ Regenerate"),
        h("button", { class: "danger small", onclick: async () => {
          if (!await confirmBox("Delete scene", "Delete this scene and its subtitle cues? Media files stay in the library.", "Delete", true)) return;
          try { await api("DELETE", P(`/scenes/${s.id}`)); await reloadProject(); route(); } catch (er) { fail(er); }
        } }, t("delete", "Delete"))));
    card.append(body);
    return card;
  }
  async function move(i, d) {
    const ids = scenes.map(s => s.id);
    [ids[i], ids[i + d]] = [ids[i + d], ids[i]];
    try { await api("POST", P("/scenes/reorder"), { ids }); await reloadProject(); route(); } catch (e) { fail(e); }
  }
};

function linkPills(items, selected, onChange, labelFn) {
  const set = new Set(selected || []);
  return h("div", { class: "pill-select" }, items.map(it => {
    const lab = h("label", { class: set.has(it.id) ? "on" : "", dir: "auto" }, h("input", { type: "checkbox", checked: set.has(it.id), onchange: (e) => {
      e.target.checked ? set.add(it.id) : set.delete(it.id); lab.className = e.target.checked ? "on" : ""; onChange([...set]);
    } }), labelFn(it));
    return lab;
  }));
}

function dragReorder(el, container, id) {
  el.addEventListener("dragstart", (e) => { if (e.target !== el) return; el.classList.add("dragging"); e.dataTransfer.setData("text/scene", id); e.dataTransfer.effectAllowed = "move"; });
  el.addEventListener("dragend", () => el.classList.remove("dragging"));
  el.addEventListener("dragover", (e) => { if (e.dataTransfer.types.includes("text/scene")) { e.preventDefault(); el.classList.add("drop"); } });
  el.addEventListener("dragleave", () => el.classList.remove("drop"));
  el.addEventListener("drop", async (e) => {
    e.preventDefault(); el.classList.remove("drop");
    const from = e.dataTransfer.getData("text/scene");
    if (!from || from === id) return;
    const ids = S.project.scenes.map(s => s.id).filter(x => x !== from);
    ids.splice(ids.indexOf(id), 0, from);
    try { await api("POST", P("/scenes/reorder"), { ids }); await reloadProject(); route(); } catch (er) { fail(er); }
  });
}

// ---------------- storyboard
VIEWS.storyboard = async (v) => {
  const pr = S.project;
  v.append(header("05 · Storyboard", t("storyboard_title", "Storyboard & prompts"),
    `Style: ${S.status.visual_styles[pr.meta.visual_style]}. Prompts are generated locally from your script and style; copy them into any image or video generator, then import the results.`,
    [select(Object.entries(S.status.visual_styles), pr.meta.visual_style, async (val) => { await api("PATCH", P(""), { visual_style: val }).catch(fail); await reloadProject(); toast("Style changed. Regenerate prompts to apply it."); }),
     h("a", { class: "btn ghost", href: "#", onclick: (e) => exportScenes(e, "csv") }, "CSV"),
     h("a", { class: "btn ghost", href: "#", onclick: (e) => exportScenes(e, "json") }, "JSON"),
     h("button", { onclick: async () => {
       if (pr.scenes.some(s => s.storyboard.image_prompt) && !await confirmBox("Regenerate prompts", "Overwrite the shot fields and prompts of every scene?", "Regenerate")) return;
       try { const r = await api("POST", P("/storyboard/generate"), {}); toast(`${r.updated} scenes updated.`, "ok"); await reloadProject(); route(); } catch (e) { fail(e); }
     } }, t("generate_storyboard", "Generate storyboard"))]));
  if (!pr.scenes.length) { v.append(h("div", { class: "empty" }, "Write a script first.")); return; }
  const vert = pr.meta.aspect_ratio === "9:16";
  const grid = h("div", { class: "list" });
  pr.scenes.forEach((s, i) => {
    const sb = s.storyboard, vis = assetById(s.media.visual_asset_id);
    const upd = (k) => (e) => saveScene(s.id, { storyboard: { [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value } });
    const copy = (txt) => h("button", { class: "ghost small", onclick: () => navigator.clipboard.writeText(txt || "").then(() => toast("Copied.")) }, "Copy");
    grid.append(h("div", { class: "card" },
      h("div", { class: "row spread" }, h("div", { class: "row" }, h("span", { class: "scene-num" }, String(i + 1).padStart(2, "0")), h("strong", { dir: "auto" }, s.title || "Scene"), h("span", { class: "badge" }, s.purpose)),
        h("span", { class: "muted small" }, s.duration ? `${s.duration}s` : "")),
      h("div", { style: { display: "grid", gridTemplateColumns: vert ? "160px 1fr" : "260px 1fr", gap: "14px", marginTop: "10px" } },
        h("div", {},
          h("div", { class: `thumb ${vert ? "v" : ""}`, style: vis && thumbUrl(vis) ? { backgroundImage: `url("${thumbUrl(vis)}")` } : {} }, vis ? "" : "No visual"),
          vis && vis.placeholder ? h("div", { class: "badge warn", style: { marginTop: "6px" } }, "placeholder") : null,
          field("Visual", select([["", "— none —"], ...pr.assets.filter(a => ["image", "video", "graphics"].includes(a.type)).map(a => [a.id, a.name])], s.media.visual_asset_id || "",
            async (val) => { await saveScene(s.id, { media: { visual_asset_id: val || null } }, true); await reloadProject(); route(); })),
          uploadButton("Upload visual", "image/*,video/*", { scene_id: s.id, slot: "visual_asset_id" }),
          h("button", { class: "ghost small", style: { marginTop: "6px" }, onclick: async () => { try { await api("POST", P("/assets/placeholder"), { scene_id: s.id }); await reloadProject(); route(); } catch (e) { fail(e); } } }, "Make placeholder"),
          field("Motion", select(S.status.motions.map(m => [m, m.replace("_", " ")]), s.motion, (val) => saveScene(s.id, { motion: val, motion_locked: true }))),
          field("Asset status", select([["missing", "Missing"], ["sourcing", "Sourcing"], ["generated", "Generated"], ["approved", "Approved"], ["replace", "Needs replacement"]], s.asset_status, (val) => saveScene(s.id, { asset_status: val })))),
        h("div", {},
          h("div", { class: "grid cols-3" },
            field("Shot", h("input", { value: sb.shot, oninput: upd("shot") })),
            field("Camera movement", h("input", { value: sb.camera, oninput: upd("camera") })),
            field("Composition", h("input", { value: sb.composition, oninput: upd("composition") })),
            field("Lighting", h("input", { value: sb.lighting, oninput: upd("lighting") })),
            field("Color palette", h("input", { value: sb.palette, oninput: upd("palette") })),
            field("Motion direction", h("input", { value: sb.motion_direction, oninput: upd("motion_direction") }))),
          h("label", {}, "Image-generation prompt ", copy(sb.image_prompt)), h("textarea", { class: "prompt", oninput: upd("image_prompt") }, sb.image_prompt),
          h("label", {}, "Video-generation prompt ", copy(sb.video_prompt)), h("textarea", { class: "prompt", oninput: upd("video_prompt") }, sb.video_prompt),
          h("label", {}, "Negative prompt ", copy(sb.negative_prompt)), h("textarea", { class: "prompt", style: { minHeight: "40px" }, oninput: upd("negative_prompt") }, sb.negative_prompt),
          h("label", { style: { display: "flex", gap: "8px", alignItems: "center" } }, h("input", { type: "checkbox", checked: sb.ai_reconstruction, onchange: upd("ai_reconstruction") }),
            "AI-generated historical reconstruction — must be labelled on screen (e.g. on-screen text “AI reconstruction”)"),
          h("h3", {}, "Graphic card"),
          h("div", { class: "grid cols-3" },
            field("Card type", select(Object.entries(S.status.card_types), s.card.type, (val) => saveScene(s.id, { card: { type: val } }))),
            field("Card title", h("input", { value: s.card.title, dir: "auto", oninput: (e) => saveScene(s.id, { card: { title: e.target.value } }) })),
            field("Card subtitle / attribution", h("input", { value: s.card.subtitle, dir: "auto", oninput: (e) => saveScene(s.id, { card: { subtitle: e.target.value } }) })))))));
  });
  v.append(grid);
  async function exportScenes(e, fmt) { e.preventDefault(); try { const r = await api("GET", P(`/export/scenes?format=${fmt}`)); toast("Saved to " + r.path, "ok"); window.open(r.url, "_blank"); } catch (er) { fail(er); } }
};

function uploadButton(label, accept, params, after) {
  const input = h("input", { type: "file", accept, multiple: !params.slot, class: "sr-only", onchange: async (e) => {
    await uploadFiles([...e.target.files], params); e.target.value = ""; after ? after() : route();
  } });
  return h("label", { class: "btn ghost small", style: { marginTop: "6px", display: "inline-flex", color: "var(--text)" } }, label, input);
}
async function uploadFiles(files, params = {}) {
  let ok = 0;
  for (const f of files) {
    const qs = new URLSearchParams({ filename: f.name, ...Object.fromEntries(Object.entries(params).filter(([, v]) => v)) });
    setSave(`Uploading ${f.name}…`);
    try { await api("POST", P(`/assets/upload?${qs}`), undefined, f); ok++; } catch (e) { fail(e); }
  }
  setSave(ok ? "saved" : "");
  if (ok) toast(`${ok} file(s) imported. Originals were copied, not moved.`, "ok");
  await reloadProject();
}

// ---------------- media library
VIEWS.media = async (v) => {
  const pr = S.project, F = S.assetFilter;
  v.append(header("06 · Media", t("media_title", "Media library"), "Imported files are copied into the project; your originals are never modified."));
  const zone = h("div", { class: "drop-zone" }, "Drop images, videos or audio here — or ",
    uploadButton("choose files", "image/*,video/*,audio/*", { kind: "" }), " · ",
    h("button", { class: "ghost small", onclick: async () => {
      const r = await promptBox("Import from a path on this Mac", [{ name: "path", label: "Absolute file path", placeholder: "/Users/you/Movies/clip.mov" }, { name: "kind", label: "Import as (image, video, audio, music, graphics — empty = auto)" }]);
      if (!r) return;
      try { await api("POST", P("/assets/import-path"), { path: r.path.trim(), kind: r.kind.trim() || null }); toast("Imported.", "ok"); await reloadProject(); route(); } catch (e) { fail(e); }
    } }, "Import by path"), " · ",
    uploadButton("Import music", "audio/*", { kind: "music" }));
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", async (e) => { e.preventDefault(); zone.classList.remove("over"); await uploadFiles([...e.dataTransfer.files]); route(); });
  v.append(zone);
  const missing = pr.status.missing_assets;
  const filters = h("div", { class: "row", style: { margin: "14px 0" } },
    h("input", { placeholder: "Search by name or tag", value: F.q, style: { maxWidth: "240px" }, oninput: (e) => { F.q = e.target.value; drawGrid(); } }),
    select([["", "All types"], ["image", "Images"], ["video", "Videos"], ["audio", "Audio"], ["music", "Music"], ["graphics", "Graphics"]], F.type, (x) => { F.type = x; drawGrid(); }, { style: { maxWidth: "150px" } }),
    select([["", "Any status"], ["pending", "Pending"], ["approved", "Approved"], ["rejected", "Rejected"], ["replace", "Needs replacement"]], F.status, (x) => { F.status = x; drawGrid(); }, { style: { maxWidth: "170px" } }),
    select([["", "Any scene"], ["none", "Unassigned"], ...pr.scenes.map((s, i) => [s.id, `Scene ${i + 1}`])], F.scene, (x) => { F.scene = x; drawGrid(); }, { style: { maxWidth: "150px" } }),
    h("button", { class: "ghost small", onclick: async () => { const r = await api("GET", P("/assets/check")); toast(r.missing.length ? `${r.missing.length} file(s) missing:\n` + r.missing.map(a => a.path).join("\n") : "All media files are present.", r.missing.length ? "err" : "ok"); } }, "Check for missing files"),
    missing ? h("span", { class: "badge err" }, `${missing} missing`) : null);
  v.append(filters);
  const wrap = h("div", { class: "split" }), grid = h("div", { class: "grid cols-auto" }), detail = h("div", { class: "sticky" });
  wrap.append(grid, detail); v.append(wrap);
  function drawGrid() {
    grid.innerHTML = "";
    const q = F.q.toLowerCase();
    const items = pr.assets.filter(a => (!F.type || a.type === F.type) && (!F.status || a.status === F.status)
      && (!F.scene || (F.scene === "none" ? !a.scene_id : a.scene_id === F.scene))
      && (!q || a.name.toLowerCase().includes(q) || (a.tags || []).join(" ").toLowerCase().includes(q)));
    if (!items.length) grid.append(h("div", { class: "empty" }, pr.assets.length ? "No assets match the filters." : "No media yet."));
    for (const a of items) {
      grid.append(h("div", { class: `card asset ${S.selAsset === a.id ? "sel" : ""}`, tabindex: 0, onclick: () => { S.selAsset = a.id; drawGrid(); drawDetail(); },
        onkeydown: (e) => { if (e.key === "Enter") { S.selAsset = a.id; drawGrid(); drawDetail(); } } },
        h("div", { class: "thumb", style: thumbUrl(a) ? { backgroundImage: `url("${thumbUrl(a)}")` } : {} }, thumbUrl(a) ? "" : a.type),
        h("div", { class: "name", dir: "auto", title: a.name }, a.name),
        h("div", { class: "row small" }, h("span", { class: "badge" }, a.type), h("span", { class: `badge ${a.status}` }, a.status),
          a.placeholder ? h("span", { class: "badge warn" }, "placeholder") : null, a.ai_generated ? h("span", { class: "badge reconstruction" }, "AI") : null)));
    }
  }
  function drawDetail() {
    detail.innerHTML = "";
    const a = assetById(S.selAsset);
    if (!a) { detail.append(h("div", { class: "empty" }, "Select an asset to preview it.")); return; }
    const i = a.info || {};
    const url = mediaUrl(a);
    const preview = a.type === "video" ? h("video", { src: url, controls: true, preload: "metadata" })
      : ["audio", "music"].includes(a.type) ? h("audio", { src: url, controls: true, preload: "metadata" })
      : h("img", { src: url, alt: a.name, style: { maxWidth: "100%", borderRadius: "8px" } });
    const used = pr.scenes.map((s, k) => Object.entries(s.media).filter(([, id]) => id === a.id).map(([slot]) => `Scene ${k + 1} (${slot.split("_")[0]})`)).flat();
    const slotSel = select([["visual_asset_id", "as main visual"], ["overlay_asset_id", "as overlay layer"], ["narration_asset_id", "as narration"], ["sfx_asset_id", "as sound effect"]],
      ["audio", "music"].includes(a.type) ? "narration_asset_id" : "visual_asset_id", () => {});
    const sceneSel = select(pr.scenes.map((s, k) => [s.id, `Scene ${k + 1}: ${(s.title || "").slice(0, 30)}`]), a.scene_id || (pr.scenes[0] || {}).id, () => {});
    detail.append(h("div", { class: "card" }, h("div", { class: "player" }, preview),
      field("Name", h("input", { value: a.name, dir: "auto", onchange: (e) => putAsset({ name: e.target.value }) })),
      h("div", { class: "row", style: { marginTop: "10px" } }, ["approved", "rejected", "replace", "pending"].map(st =>
        h("button", { class: `small ${a.status === st ? "" : "ghost"}`, onclick: () => putAsset({ status: st }) }, st === "replace" ? "needs replacement" : st))),
      h("label", { style: { display: "flex", gap: "8px", alignItems: "center" } }, h("input", { type: "checkbox", checked: a.ai_generated, onchange: (e) => putAsset({ ai_generated: e.target.checked }) }), "AI-generated (will be flagged if not labelled on screen)"),
      field("Tags (comma separated)", h("input", { value: (a.tags || []).join(", "), onchange: (e) => putAsset({ tags: e.target.value.split(",").map(x => x.trim()).filter(Boolean) }) })),
      h("h3", {}, "Use in scene"),
      pr.scenes.length ? h("div", { class: "row" }, sceneSel, slotSel, h("button", { class: "small", onclick: async () => {
        await saveScene(sceneSel.value, { media: { [slotSel.value]: a.id } }, true); await putAsset({ scene_id: sceneSel.value }); toast("Assigned.", "ok");
      } }, "Assign")) : h("p", { class: "muted" }, "No scenes yet."),
      used.length ? h("p", { class: "small" }, "Used in: " + used.join(", ")) : h("p", { class: "small muted" }, "Not used in the timeline."),
      ["audio", "music"].includes(a.type) ? h("div", { class: "row" },
        h("button", { class: "ghost small", onclick: () => processAudio("normalize") }, "Normalize loudness (copy)"),
        h("button", { class: "ghost small", onclick: () => processAudio("trim_silence") }, "Trim leading/trailing silence (copy)")) : null,
      h("h3", {}, "Metadata"),
      h("div", { class: "kv" }, "Type", a.type, "File", a.path, "Original", a.original_name, "Duration", i.duration ? fmtT(i.duration) : "–",
        "Size", i.width ? `${i.width}×${i.height}` : "–", "Codec", i.codec || "–", "File size", i.size ? `${(i.size / 1e6).toFixed(1)} MB` : "–", "Origin", a.origin),
      h("div", { class: "row", style: { marginTop: "14px" } }, h("button", { class: "danger small", onclick: delAsset }, "Remove from project"))));
    async function putAsset(ch) { try { await api("PUT", P(`/assets/${a.id}`), ch); await reloadProject(); drawGrid(); drawDetail(); } catch (e) { fail(e); } }
    async function processAudio(op) { try { runJob(await api("POST", P(`/assets/${a.id}/process`), { op }), async (r) => { toast("Created " + r.name, "ok"); await reloadProject(); S.selAsset = r.id; drawGrid(); drawDetail(); }); } catch (e) { fail(e); } }
    async function delAsset() {
      const body = {};
      try { await api("DELETE", P(`/assets/${a.id}`), body); }
      catch (e) {
        if (e.data.code === "asset_in_use") {
          if (!await confirmBox("Asset is in use", `${e.message} Removing it will leave those scenes without this media. Continue?`, "Continue", true)) return;
          body.confirm_in_use = true;
        } else if (e.data.code !== "confirm_delete") return fail(e);
        if (!await confirmBox("Remove asset", "Remove the project's copy of this file? It is moved to media/.trash inside the project; the original you imported from is untouched.", "Remove", true)) return;
        body.confirm = true;
        try { await api("DELETE", P(`/assets/${a.id}`), body); } catch (e2) { return fail(e2); }
      }
      S.selAsset = null; await reloadProject(); route();
    }
  }
  drawGrid(); drawDetail();
};

// ---------------- voice & subtitles
VIEWS.voice = async (v) => {
  const pr = S.project, st = S.settings;
  const subs = await api("GET", P("/subtitles"));
  const ttsName = st.providers.tts, sttName = st.providers.stt;
  v.append(header("07 · Voice", t("voice_title", "Voice-over & subtitles"),
    `Text-to-speech: ${ttsName}${ttsName === "mock" ? " (silent placeholder — for timing only)" : ""} · Transcription: ${sttName}. Import your own recordings at any time.`));
  // voice table
  const tlEntries = Object.fromEntries(subs.timeline.entries.map(e => [e.scene_id, e]));
  const vt = h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "#"), h("th", {}, "Narration"), h("th", {}, "Audio"), h("th", {}, "Actions"))));
  const tb = h("tbody");
  pr.scenes.forEach((s, i) => {
    const narr = assetById(s.media.narration_asset_id);
    tb.append(h("tr", {},
      h("td", { class: "scene-num" }, String(i + 1).padStart(2, "0")),
      h("td", { style: { maxWidth: "380px" } }, h("div", { class: "rtl small" }, (s.narration_ar || "").slice(0, 160)), h("div", { class: "small muted" }, (s.narration_en || "").slice(0, 120))),
      h("td", { style: { minWidth: "220px" } }, narr ? [h("audio", { src: mediaUrl(narr), controls: true, preload: "none" }),
        h("div", { class: "small muted" }, `${fmtT(narr.info.duration)} · ${narr.origin}`, narr.placeholder ? h("span", { class: "badge warn", style: { marginInlineStart: "6px" } }, "silent placeholder") : null)]
        : h("span", { class: "muted small" }, `No audio · ${tlEntries[s.id] ? tlEntries[s.id].duration.toFixed(1) + "s estimated" : ""}`)),
      h("td", {}, h("div", { class: "row" },
        uploadButton(narr ? "Replace recording" : "Import recording", "audio/*", { scene_id: s.id, slot: "narration_asset_id", kind: "audio" }),
        h("button", { class: "ghost small", onclick: () => tts(s.id, "ar") }, "TTS Arabic"),
        h("button", { class: "ghost small", onclick: () => tts(s.id, "en") }, "TTS English"),
        narr ? h("button", { class: "ghost small", onclick: async () => { await saveScene(s.id, { media: { narration_asset_id: null } }, true); await reloadProject(); route(); } }, "Unlink") : null))));
  });
  vt.append(tb);
  const audioAssets = pr.assets.filter(a => ["audio", "music"].includes(a.type));
  const tls = pr.meta.timeline;
  v.append(h("div", { class: "card" }, h("div", { class: "row spread" }, h("h2", {}, "Narration per scene"),
    h("div", { class: "row" },
      h("button", { class: "ghost small", onclick: async () => { try { runJob(await api("POST", P("/tts/all"), { lang: "ar" }), async (r) => { toast(`Generated ${r.generated}.` + (r.errors.length ? "\n" + r.errors.join("\n") : ""), r.errors.length ? "err" : "ok"); await reloadProject(); route(); }); } catch (e) { fail(e); } } }, "Generate missing (Arabic)"))),
    pr.scenes.length ? vt : h("div", { class: "empty" }, "No scenes yet."),
    h("h3", {}, "Or: one continuous narration track"),
    h("div", { class: "row" }, select([["", "— none (use per-scene audio) —"], ...audioAssets.map(a => [a.id, a.name])], tls.narration_asset_id || "",
      async (val) => { await api("PATCH", P(""), { timeline: { narration_asset_id: val || null } }).catch(fail); await reloadProject(); route(); }, { style: { maxWidth: "360px" } }),
      uploadButton("Import full narration", "audio/*", { kind: "audio" })),
    h("p", { class: "hint" }, "A continuous track plays from 0:00 under all scenes. Set scene durations to match it, or use “Transcribe audio” to time subtitles from the real recording.")));

  // subtitles
  const subCard = h("div", { class: "card", style: { marginTop: "14px" } });
  subCard.append(h("div", { class: "row spread" }, h("h2", {}, "Subtitles"), h("div", { class: "row" },
    h("button", { class: "ghost small", onclick: async () => {
      if ((subs.ar.length || subs.en.length) && !await confirmBox("Replace subtitles", "Rebuild Arabic and English cues from the script text? Manual edits will be replaced.", "Rebuild")) return;
      try { const r = await api("POST", P("/subtitles/from-script"), { langs: ["ar", "en"] }); toast(`Created ${r.ar || 0} Arabic and ${r.en || 0} English cues.`, "ok"); route(); } catch (e) { fail(e); }
    } }, "Generate from script"),
    h("button", { class: "ghost small", onclick: async () => {
      if (subs.ar.length && !await confirmBox("Transcribe", "Replace Arabic cues with a transcription of the actual narration audio?", "Transcribe")) return;
      try { runJob(await api("POST", P("/subtitles/transcribe"), { lang: "ar" }), (r) => { toast(`Transcribed ${r.cues} cues (${r.mode}). Review the text.`, "ok"); route(); }); } catch (e) { fail(e); }
    } }, "Transcribe audio (Arabic)"),
    h("button", { class: "ghost small", onclick: async () => {
      if (subs.en.length && !await confirmBox("Translate", "Replace English cues with translations of the Arabic cues (same timing)?", "Translate")) return;
      try { runJob(await api("POST", P("/subtitles/translate")), (r) => { toast(`Translated ${r.cues} cues. Review them.`, "ok"); route(); }); } catch (e) { fail(e); }
    } }, "Translate AR → EN"),
    srtImport("ar"), srtImport("en"),
    h("button", { class: "small", onclick: async () => { try { const r = await api("POST", P("/subtitles/export")); toast(r.files.length ? "Saved:\n" + r.files.join("\n") : "Nothing to export.", r.files.length ? "ok" : "err"); } catch (e) { fail(e); } } }, "Export SRT + VTT"))));
  subCard.append(h("p", { class: "hint" }, "“Generate from script” spreads your text across the real narration length when audio exists (otherwise an estimate). For precise timing, transcribe the recording with whisper.cpp, then correct times below."));
  subCard.append(subtitleStyleEditor());
  subCard.append(h("div", { class: "grid cols-2", style: { marginTop: "12px" } }, cueEditor("ar", subs.ar, subs.problems.ar), cueEditor("en", subs.en, subs.problems.en)));
  v.append(subCard);

  // preview
  const prevCard = h("div", { class: "card", style: { marginTop: "14px" } }, h("div", { class: "row spread" }, h("h2", {}, "Preview with subtitles"),
    h("button", { class: "small", onclick: () => renderPreview(() => route()) }, "Render quick preview")));
  const last = (S.project.renders || []).filter(r => r.status === "done" && r.preview).slice(-1)[0];
  if (last) {
    const video = h("video", { controls: true, src: `/api/projects/${encodeURIComponent(S.pid)}/renders/${last.id}/file`, crossorigin: "anonymous" });
    for (const [lang, label] of [["ar", "العربية"], ["en", "English"]]) {
      if (!subs[lang].length) continue;
      const url = URL.createObjectURL(new Blob([subs.vtt[lang]], { type: "text/vtt" }));
      video.append(h("track", { kind: "subtitles", srclang: lang, label, src: url, default: lang === "en" }));
    }
    prevCard.append(h("div", { class: "player" }, video), h("p", { class: "hint" }, "The preview is rendered without burned-in subtitles; the player shows the current cues as a live track (use the CC menu to switch language). Rendered " + new Date(last.finished * 1000).toLocaleTimeString() + "."));
  } else prevCard.append(h("div", { class: "empty" }, "No preview yet."));
  v.append(prevCard);

  async function tts(sid, lang) {
    try { runJob(await api("POST", P(`/scenes/${sid}/tts`), { lang }), async () => { toast("Voice generated.", "ok"); await reloadProject(); route(); }); } catch (e) { fail(e); }
  }
  function srtImport(lang) {
    return h("label", { class: "btn ghost small", style: { color: "var(--text)" } }, `Import ${lang.toUpperCase()} .srt`, h("input", { type: "file", accept: ".srt,.vtt", class: "sr-only", onchange: async (e) => {
      const f = e.target.files[0]; if (!f) return;
      if (!await confirmBox("Import subtitles", `Replace all ${lang.toUpperCase()} cues with ${f.name}? Imported times are absolute (from 0:00).`, "Import")) return;
      try { const r = await api("POST", P("/subtitles/import"), { lang, text: await f.text() }); toast(`Imported ${r.imported} cues.`, "ok"); route(); } catch (er) { fail(er); }
    } }));
  }
};

function subtitleStyleEditor() {
  const ss = S.project.meta.subtitle_style;
  const upd = (k, num) => (e) => debounceSave("substyle", () => api("PATCH", P(""), { subtitle_style: { [k]: num ? Number(e.target.value) : (e.target.type === "checkbox" ? e.target.checked : e.target.value) } }).then(reloadProject));
  return h("details", {}, h("summary", { class: "muted", style: { cursor: "pointer", marginTop: "10px" } }, "Subtitle style (burned-in)"),
    h("div", { class: "grid cols-3" },
      field("Arabic font", h("input", { value: ss.font_ar, oninput: upd("font_ar") }), "Installed font name, e.g. Geeza Pro, Arial"),
      field("English font", h("input", { value: ss.font_en, oninput: upd("font_en") })),
      field("Size (at 1080 px width)", h("input", { type: "number", value: ss.size, min: 20, max: 140, oninput: upd("size", true) })),
      field("Position", select([["bottom", "Bottom"], ["middle", "Middle"], ["top", "Top"]], ss.position, (x) => api("PATCH", P(""), { subtitle_style: { position: x } }).then(reloadProject))),
      field("Vertical margin", h("input", { type: "number", value: ss.margin_v, oninput: upd("margin_v", true) })),
      field("Outline", h("input", { type: "number", value: ss.outline, min: 0, max: 10, oninput: upd("outline", true) })),
      field("Text color", h("input", { type: "color", value: ss.color, oninput: upd("color") })),
      field("Background box opacity", h("input", { type: "range", min: 0, max: 1, step: 0.05, value: ss.box_opacity, oninput: upd("box_opacity", true) })),
      field("Max characters per line", h("input", { type: "number", value: ss.max_chars, min: 20, max: 60, oninput: upd("max_chars", true) })),
      h("label", { style: { display: "flex", gap: "8px", alignItems: "center" } }, h("input", { type: "checkbox", checked: ss.box, onchange: upd("box") }), "Background box")));
}

function cueEditor(lang, cues, problems) {
  const rows = cues.map(c => ({ ...c }));
  const tbody = h("tbody");
  const draw = () => {
    tbody.innerHTML = "";
    rows.forEach((c, i) => tbody.append(h("tr", {},
      h("td", { class: "time" }, h("input", { type: "number", step: 0.05, value: c.start.toFixed(2), oninput: (e) => { c.start = Number(e.target.value); } })),
      h("td", { class: "time" }, h("input", { type: "number", step: 0.05, value: c.end.toFixed(2), oninput: (e) => { c.end = Number(e.target.value); } })),
      h("td", {}, h("textarea", { class: lang === "ar" ? "rtl" : "", style: { minHeight: "44px" }, oninput: (e) => { c.text = e.target.value; } }, c.text)),
      h("td", {}, h("button", { class: "ghost small", "aria-label": "Delete cue", onclick: () => { rows.splice(i, 1); draw(); } }, "×")))));
  };
  draw();
  return h("div", {}, h("div", { class: "row spread" }, h("h3", {}, lang === "ar" ? "Arabic cues" : "English cues"),
    h("div", { class: "row" },
      h("button", { class: "ghost small", onclick: () => { const last = rows[rows.length - 1]; rows.push({ start: last ? last.end + 0.1 : 0, end: last ? last.end + 2.1 : 2, text: "" }); draw(); } }, "+ Cue"),
      h("button", { class: "ghost small", onclick: () => { const d = Number(prompt("Shift all cues by seconds (negative = earlier):", "0.2")); if (!isNaN(d)) { rows.forEach(c => { c.start = Math.max(0, c.start + d); c.end = Math.max(0.05, c.end + d); }); draw(); } } }, "Shift all"),
      h("button", { class: "small", onclick: async () => { try { const r = await api("PUT", P(`/subtitles/${lang}`), { cues: rows }); toast(`Saved ${r.saved} cues.`, "ok"); route(); } catch (e) { fail(e); } } }, t("save", "Save")))),
    problems.length ? h("ul", { class: "warn-list small" }, problems.slice(0, 6).map(p => h("li", { class: "warn" }, p))) : null,
    rows.length ? h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Start"), h("th", {}, "End"), h("th", {}, "Text"), h("th", {}))), tbody)
      : h("div", { class: "empty" }, "No cues."), h("p", { class: "hint" }, "Times are seconds from the start of the video."));
}

async function renderPreview(done, sceneIds) {
  try {
    runJob(await api("POST", P("/render"), { preview: true, quality: "draft", subtitle_mode: "none", filename: "_preview.mp4", overwrite: true, scene_ids: sceneIds || null, graphics: true }),
      async () => { await reloadProject(); toast("Preview ready.", "ok"); done && done(); });
  } catch (e) { fail(e); }
}

// ---------------- timeline
VIEWS.timeline = async (v) => {
  const pr = S.project;
  const { timeline: tl, settings: tset } = await api("GET", P("/timeline"));
  v.append(header("08 · Timeline", t("timeline_title", "Scene timeline"),
    `A scene-based editor: each scene is one visual (with motion, an optional overlay layer and a graphic card), its narration, and a transition. Total ${fmtT(tl.total)}.`,
    [h("button", { class: "ghost", onclick: () => renderPreview(() => route(), S.selBlock ? [S.selBlock] : null) }, S.selBlock ? "Preview selected scene" : "Preview whole timeline")]));
  if (!pr.scenes.length) { v.append(h("div", { class: "empty" }, "No scenes yet.")); return; }
  const scale = Math.max(14, Math.min(60, 900 / Math.max(tl.total, 1)));
  const strip = h("div", { class: "timeline", role: "list" });
  tl.entries.forEach((e, i) => {
    const s = pr.scenes[i], vis = assetById(s.media.visual_asset_id);
    const b = h("div", { class: `tblock ${S.selBlock === s.id ? "sel" : ""}`, role: "listitem", tabindex: 0, draggable: true, "data-id": s.id,
      style: { width: `${e.duration * scale}px`, backgroundImage: thumbUrl(vis) ? `url("${thumbUrl(vis)}")` : "none" },
      onclick: () => { S.selBlock = s.id; route(); }, onkeydown: (ev) => { if (ev.key === "Enter") { S.selBlock = s.id; route(); } } },
      h("div", { class: "t", dir: "auto" }, `${i + 1}. ${s.title || ""}`),
      h("div", { class: "small" }, s.media.narration_asset_id ? "🎙" : "", s.card.type !== "none" ? " ▣" : "", e.transition.type !== "cut" ? ` ⟶ ${e.transition.type}` : ""),
      h("div", { class: "d" }, `${e.duration.toFixed(1)}s`));
    dragReorder(b, strip, s.id);
    strip.append(b);
  });
  v.append(h("div", { class: "card" }, h("div", { class: "ruler" }, `0:00 ${"·".repeat(20)} ${fmtT(tl.total)} · drag blocks to reorder · click to edit`), strip));
  const s = pr.scenes.find(x => x.id === S.selBlock);
  const panels = h("div", { class: "grid cols-2", style: { marginTop: "14px" } });
  if (s) {
    const e = tl.entries.find(x => x.scene_id === s.id);
    const visuals = pr.assets.filter(a => ["image", "video", "graphics"].includes(a.type));
    const audios = pr.assets.filter(a => ["audio", "music"].includes(a.type));
    const mediaSel = (slot, items) => select([["", "— none —"], ...items.map(a => [a.id, a.name])], s.media[slot] || "", async (val) => { await saveScene(s.id, { media: { [slot]: val || null } }, true); await reloadProject(); route(); });
    panels.append(h("div", { class: "card" }, h("h2", { dir: "auto" }, `Scene ${e.index + 1}: ${s.title || ""}`),
      h("div", { class: "muted small" }, `Starts ${fmtT(e.start)} · ${e.duration.toFixed(2)}s (${e.duration_source})`),
      h("div", { class: "grid cols-2" },
        field("Main visual", mediaSel("visual_asset_id", visuals)),
        field("Overlay layer (35% opacity)", mediaSel("overlay_asset_id", visuals.filter(a => a.type !== "video"))),
        field("Narration", mediaSel("narration_asset_id", audios)),
        field("Sound effect", mediaSel("sfx_asset_id", audios)),
        field("Duration override (s)", h("input", { type: "number", step: 0.1, min: 0.5, value: s.duration ?? "", placeholder: e.duration.toFixed(1), onchange: async (ev) => { await saveScene(s.id, { duration: ev.target.value === "" ? null : Number(ev.target.value) }, true); route(); } })),
        field("Motion (stills)", select(S.status.motions.map(m => [m, m.replace("_", " ")]), s.motion, async (val) => { await saveScene(s.id, { motion: val, motion_locked: true }, true); })),
        field("Transition out", select([["cut", "Cut"], ["fade", "Fade through black"], ["crossfade", "Crossfade"]], s.transition.type, async (val) => { await saveScene(s.id, { transition: { type: val } }, true); route(); })),
        field("Transition length (s)", h("input", { type: "number", step: 0.1, min: 0.1, max: 3, value: s.transition.duration, onchange: async (ev) => { await saveScene(s.id, { transition: { duration: Number(ev.target.value) } }, true); route(); } }))),
      field(`Crop focus — horizontal ${Math.round((s.crop.x) * 100)}%`, h("input", { type: "range", min: 0, max: 1, step: 0.05, value: s.crop.x, onchange: (ev) => saveScene(s.id, { crop: { x: Number(ev.target.value) } }, true).then(route) })),
      field(`Crop focus — vertical ${Math.round((s.crop.y) * 100)}%`, h("input", { type: "range", min: 0, max: 1, step: 0.05, value: s.crop.y, onchange: (ev) => saveScene(s.id, { crop: { y: Number(ev.target.value) } }, true).then(route) }),
        "Where to keep the frame when the visual is cropped to the output aspect ratio."),
      field("On-screen text", h("input", { value: s.on_screen_text, dir: "auto", oninput: (ev) => saveScene(s.id, { on_screen_text: ev.target.value }) }))));
  } else panels.append(h("div", { class: "card empty" }, "Click a scene block to edit its media, timing and motion."));
  const audioAssets = pr.assets.filter(a => ["audio", "music"].includes(a.type));
  const updT = (k, num) => (ev) => debounceSave("tl", () => api("PATCH", P(""), { timeline: { [k]: ev.target.type === "checkbox" ? ev.target.checked : (num ? Number(ev.target.value) : ev.target.value) } }).then(reloadProject));
  panels.append(h("div", { class: "card" }, h("h2", {}, "Audio mix"),
    field("Background music", select([["", "— none —"], ...audioAssets.map(a => [a.id, a.name])], tset.music_asset_id || "", async (val) => { await api("PATCH", P(""), { timeline: { music_asset_id: val || null } }).catch(fail); await reloadProject(); route(); }),
      "Music loops to fill the video and fades in/out. Use music you have the rights to."),
    field(`Music volume (${tset.music_volume_db} dB)`, h("input", { type: "range", min: -40, max: 0, step: 1, value: tset.music_volume_db, onchange: (ev) => api("PATCH", P(""), { timeline: { music_volume_db: Number(ev.target.value) } }).then(async () => { await reloadProject(); route(); }) })),
    h("label", { style: { display: "flex", gap: "8px", alignItems: "center" } }, h("input", { type: "checkbox", checked: tset.music_duck, onchange: updT("music_duck") }), "Duck music under narration"),
    field(`Continuous narration track volume (${tset.narration_volume_db} dB)`, h("input", { type: "range", min: -20, max: 10, step: 1, value: tset.narration_volume_db, onchange: (ev) => api("PATCH", P(""), { timeline: { narration_volume_db: Number(ev.target.value) } }).then(async () => { await reloadProject(); route(); }) })),
    h("label", { style: { display: "flex", gap: "8px", alignItems: "center" } }, h("input", { type: "checkbox", checked: tset.fade_in_start, onchange: updT("fade_in_start") }), "Fade in from black at the start"),
    h("label", { style: { display: "flex", gap: "8px", alignItems: "center" } }, h("input", { type: "checkbox", checked: tset.fade_out_end, onchange: updT("fade_out_end") }), "Fade out at the end"),
    h("h3", {}, "Graphics style"), graphicsStyleEditor()));
  v.append(panels);
  const last = (pr.renders || []).filter(r => r.status === "done" && r.preview).slice(-1)[0];
  if (last) v.append(h("div", { class: "card", style: { marginTop: "14px" } }, h("h2", {}, "Latest preview"),
    h("div", { class: "player" }, h("video", { controls: true, src: `/api/projects/${encodeURIComponent(S.pid)}/renders/${last.id}/file` })),
    h("p", { class: "hint" }, "Half-resolution draft render including graphics cards (no burned subtitles).")));
};

function graphicsStyleEditor() {
  const g = S.project.meta.graphics_style;
  const upd = (k, num) => (e) => debounceSave("gstyle", () => api("PATCH", P(""), { graphics_style: { [k]: num ? Number(e.target.value) : e.target.value } }).then(reloadProject));
  return h("div", { class: "grid cols-2" },
    field("Title font", h("input", { value: g.font_title, oninput: upd("font_title") })),
    field("Body font", h("input", { value: g.font_body, oninput: upd("font_body") })),
    field("Arabic font", h("input", { value: g.font_ar, oninput: upd("font_ar") })),
    field("Accent color", h("input", { type: "color", value: g.accent, oninput: upd("accent") })),
    field("Text color", h("input", { type: "color", value: g.text, oninput: upd("text") })),
    field("Margin (px at 1080)", h("input", { type: "number", value: g.margin, oninput: upd("margin", true) })),
    field("Title size", h("input", { type: "number", value: g.title_size, oninput: upd("title_size", true) })),
    field("Body size", h("input", { type: "number", value: g.body_size, oninput: upd("body_size", true) })),
    h("p", { class: "hint", style: { gridColumn: "1 / -1" } }, "Fonts must be installed on this Mac. Check the licence of any font you use in published videos."));
}

// ---------------- export
VIEWS.export = async (v) => {
  const pr = S.project;
  const saved = (() => { try { return JSON.parse(localStorage.getItem("ads.export." + S.pid) || "{}"); } catch (e) { return {}; } })();
  const presetFor = { "9:16": "vertical", "16:9": "landscape", "1:1": "square" }[pr.meta.aspect_ratio];
  const o = Object.assign({ preset: presetFor, fps: 30, quality: "standard", subtitle_mode: pr.meta.subtitle_language === "bilingual" ? "bilingual" : (pr.meta.subtitle_language || "en"),
    sidecar_srt: true, audio_level_db: 0, normalize_audio: true, graphics: true, film_grain: pr.meta.visual_style === "editorial_collage",
    encoder: "libx264", filename: pr.id, output_dir: pr.root + "/exports", retain_intermediates: false }, saved);
  const keep = () => { try { localStorage.setItem("ads.export." + S.pid, JSON.stringify(o)); } catch (e) {} };
  const set = (k, num) => (e) => { o[k] = e.target.type === "checkbox" ? e.target.checked : (num ? Number(e.target.value) : e.target.value); keep(); };
  const sel = (k, opts) => select(opts, o[k], (val) => { o[k] = /^\d+$/.test(val) ? Number(val) : val; keep(); });
  v.append(header("09 · Export", t("export_title", "Render & export"), "H.264 / AAC MP4. The app checks media, durations, subtitles and disk space before rendering, and never overwrites a file without asking."));
  const res = h("div", { class: "list" });
  const form = h("div", { class: "card" },
    h("div", { class: "grid cols-3" },
      field("Preset", sel("preset", [["vertical", "Vertical social 1080×1920"], ["landscape", "Landscape 1920×1080"], ["square", "Square 1080×1080"], ["project", "Project aspect ratio"]])),
      field("Frame rate", sel("fps", [[24, "24 fps"], [25, "25 fps"], [30, "30 fps"], [60, "60 fps"]])),
      field("Quality", sel("quality", [["draft", "Draft (fast)"], ["standard", "Standard"], ["high", "High (slow)"]])),
      field("Subtitles burned in", sel("subtitle_mode", [["none", "None"], ["ar", "Arabic"], ["en", "English"], ["bilingual", "Arabic + English"]])),
      field("Audio level (dB)", h("input", { type: "number", min: -20, max: 12, step: 0.5, value: o.audio_level_db, oninput: set("audio_level_db", true) })),
      field("Encoder", sel("encoder", [["libx264", "x264 (best quality)"], ["h264_videotoolbox", "Apple VideoToolbox (fast, hardware)"]])),
      field("File name", h("input", { value: o.filename, oninput: set("filename") })),
      field("Output folder", h("input", { value: o.output_dir, oninput: set("output_dir") }), "Absolute path to an existing folder.")),
    h("div", { class: "checklist", style: { marginTop: "12px" } },
      ...[["sidecar_srt", "Also save .ar.srt / .en.srt files"], ["normalize_audio", "Loudness-normalize (−16 LUFS)"], ["graphics", "Include graphic cards & on-screen text"],
        ["film_grain", "Subtle film grain"], ["retain_intermediates", "Keep intermediate files"]].map(([k, l]) =>
        h("label", { class: "badge", style: { display: "inline-flex", gap: "6px", alignItems: "center", padding: "6px 10px", margin: 0 } }, h("input", { type: "checkbox", checked: o[k], onchange: set(k) }), l))),
    h("div", { class: "row", style: { marginTop: "16px" } },
      h("button", { class: "ghost", onclick: check }, t("validate", "Check project")),
      h("button", { onclick: () => go(false) }, "▶ " + t("render", "Render video"))));
  v.append(h("div", { class: "split" }, form, res));
  const renders = (pr.renders || []).filter(r => !r.preview).slice().reverse();
  v.append(h("div", { class: "card", style: { marginTop: "14px" } }, h("h2", {}, "Previous exports"),
    renders.length ? h("table", {}, h("tbody", {}, renders.map(r => h("tr", {},
      h("td", {}, h("span", { class: `badge ${r.status === "done" ? "ok" : r.status === "running" ? "warn" : "err"}` }, r.status)),
      h("td", { style: { wordBreak: "break-all" } }, r.output, r.error ? h("div", { class: "small", style: { color: "var(--red)" } }, r.error) : null),
      h("td", { class: "small muted" }, r.duration ? fmtT(r.duration) : "", r.elapsed ? ` · rendered in ${r.elapsed}s` : ""),
      h("td", {}, r.status === "done" ? h("a", { class: "btn ghost small", href: `/api/projects/${encodeURIComponent(S.pid)}/renders/${r.id}/file` }, "Download") : null)))))
      : h("div", { class: "empty" }, "No exports yet.")));
  async function check() {
    res.innerHTML = "";
    try {
      const r = await api("POST", P("/render/validate"), o);
      res.append(h("div", { class: "card" }, h("h2", {}, r.ok ? "Ready to render" : "Fix before rendering"),
        r.errors.length ? h("ul", { class: "warn-list" }, r.errors.map(e => h("li", { class: "error" }, e))) : null,
        r.warnings.length ? h("ul", { class: "warn-list" }, r.warnings.map(e => h("li", { class: "warn" }, e))) : null,
        r.ok && !r.warnings.length ? h("p", { class: "muted" }, "No problems found.") : null,
        r.options ? h("p", { class: "small muted" }, `${r.options.width}×${r.options.height} · ${r.options.fps} fps · ${r.options.quality} · subtitles: ${r.options.subtitle_mode}`) : null));
      return r.ok;
    } catch (e) { fail(e); return false; }
  }
  async function go(overwrite) {
    if (!overwrite && !await check()) return;
    try {
      const job = await api("POST", P("/render"), { ...o, overwrite });
      runJob(job, async (r) => {
        await reloadProject();
        res.innerHTML = "";
        res.append(h("div", { class: "card hl" }, h("h2", {}, "Export complete"), h("p", { style: { wordBreak: "break-all" } }, r.output),
          h("div", { class: "player" }, h("video", { controls: true, src: `/api/projects/${encodeURIComponent(S.pid)}/renders/${r.id}/file` })),
          (r.sidecars || []).length ? h("p", { class: "small muted" }, "Subtitle files: " + r.sidecars.join(", ")) : null));
        toast("Video rendered.", "ok");
      });
    } catch (e) {
      if (e.data && e.data.code === "exists") {
        if (await confirmBox("File exists", e.message, "Replace", true)) go(true);
      } else fail(e);
    }
  }
};

// ---------------- settings
VIEWS.settings = async (v) => {
  const st = await api("GET", "/api/settings"), prov = await api("GET", "/api/providers");
  S.settings = st;
  const save = async (ch) => { try { S.settings = await api("PUT", "/api/settings", ch); S.status = await api("GET", "/api/status"); setSave("saved"); } catch (e) { fail(e); } };
  v.append(header("10 · Settings", t("settings_title", "Settings & AI providers"), "Everything works offline. External services are used only when you select them here, and the badge “sends data” marks each one."));
  const cats = [["llm", "Language model (script, regenerate, translation, brief)"], ["tts", "Text-to-speech"], ["stt", "Speech recognition"],
    ["search", "Web research"], ["translation", "Translation"], ["image", "Image generation"], ["video", "Video generation"]];
  const pcard = h("div", { class: "card" }, h("h2", {}, "Providers"));
  for (const [cat, label] of cats) {
    const items = prov.providers[cat];
    pcard.append(h("h3", {}, label), h("div", { class: "list" }, items.map(p => h("label", { class: "card flat", style: { display: "flex", gap: "10px", alignItems: "flex-start", margin: 0, cursor: "pointer" } },
      h("input", { type: "radio", name: "p_" + cat, checked: st.providers[cat] === p.name, onchange: () => save({ providers: { [cat]: p.name } }).then(() => route()) }),
      h("div", {}, h("div", { style: { color: "var(--text)", fontWeight: 600 } }, p.label, " ",
        h("span", { class: `badge ${p.available ? "ok" : "warn"}` }, p.available ? "ready" : "not ready"), " ",
        p.external ? h("span", { class: "badge ext" }, "sends data to the internet") : p.external === false ? h("span", { class: "badge" }, "local") : null),
        h("div", { class: "small" }, p.detail), p.cost ? h("div", { class: "small muted" }, p.cost) : null)))));
  }
  const keyInput = h("input", { type: "password", placeholder: "sk-ant-…", autocomplete: "off" });
  const sec = prov.secrets.ANTHROPIC_API_KEY;
  const ccard = h("div", { class: "card" }, h("h2", {}, "Credentials"),
    h("p", { class: "small muted" }, "Keys are read from environment variables first, then the macOS Keychain. They are never written to settings or project files."),
    h("div", {}, "Anthropic API key: ", h("span", { class: `badge ${sec.configured ? "ok" : "warn"}` }, sec.configured ? `configured (${sec.source})` : "not set")),
    S.status.is_mac ? h("div", { class: "row", style: { marginTop: "8px" } }, keyInput,
      h("button", { class: "small", onclick: async () => { try { await api("POST", "/api/providers/secret", { name: "ANTHROPIC_API_KEY", value: keyInput.value }); keyInput.value = ""; toast("Stored in Keychain.", "ok"); route(); } catch (e) { fail(e); } } }, "Save to Keychain"),
      sec.source === "keychain" ? h("button", { class: "danger small", onclick: async () => { if (await confirmBox("Remove key", "Delete the key from the Keychain?", "Delete", true)) { await api("DELETE", "/api/providers/secret/ANTHROPIC_API_KEY").catch(fail); route(); } } }, "Remove") : null)
      : h("p", { class: "small" }, "On this system set the environment variable ANTHROPIC_API_KEY before launching."),
    h("div", { class: "grid cols-2" },
      field("Claude model", h("input", { value: st.anthropic.model, onchange: (e) => save({ anthropic: { model: e.target.value } }) })),
      field("Effort", select([["low", "low"], ["medium", "medium"], ["high", "high"]], st.anthropic.effort, (x) => save({ anthropic: { effort: x } })))),
    h("div", { class: "grid cols-2" },
      field("Ollama URL", h("input", { value: st.ollama.url, onchange: (e) => save({ ollama: { url: e.target.value } }) })),
      field("Ollama model", h("input", { value: st.ollama.model, onchange: (e) => save({ ollama: { model: e.target.value } }) }))),
    h("div", { class: "row", style: { marginTop: "10px" } }, h("button", { class: "ghost small", onclick: async () => {
      try { const r = await api("POST", "/api/providers/test", { kind: "llm" }); toast((r.ok ? "Connection works: " : "Unexpected reply: ") + r.detail, r.ok ? "ok" : "err"); } catch (e) { fail(e); }
    } }, "Test selected language model")),
    h("h3", {}, "whisper.cpp (speech recognition)"),
    h("div", { class: "grid cols-2" },
      field("Binary", h("input", { value: st.whisper_cpp.binary, onchange: (e) => save({ whisper_cpp: { binary: e.target.value } }) })),
      field("Model file (.bin)", h("input", { value: st.whisper_cpp.model_path, placeholder: "/Users/you/models/ggml-small.bin", onchange: (e) => save({ whisper_cpp: { model_path: e.target.value } }) }))),
    h("h3", {}, "Voices (macOS)"), voicePicker(st));
  const gcard = h("div", { class: "card" }, h("h2", {}, "General"),
    h("div", { class: "grid cols-2" },
      field("Projects folder", h("input", { value: st.projects_dir || S.status.projects_dir, onchange: (e) => save({ projects_dir: e.target.value }) }), "Changing it shows projects from the new folder."),
      field("Interface language", select([["en", "English"], ["ar", "العربية"]], S.lang, (x) => setLang(x))),
      field("Arabic speaking rate (words/min)", h("input", { type: "number", min: 60, max: 250, value: st.speaking_rate_ar_wpm, onchange: (e) => save({ speaking_rate_ar_wpm: Number(e.target.value) }) })),
      field("English speaking rate (words/min)", h("input", { type: "number", min: 60, max: 250, value: st.speaking_rate_en_wpm, onchange: (e) => save({ speaking_rate_en_wpm: Number(e.target.value) }) })),
      field("Pause per scene (s)", h("input", { type: "number", min: 0, max: 3, step: 0.1, value: st.scene_pause_seconds, onchange: (e) => save({ scene_pause_seconds: Number(e.target.value) }) }))),
    h("h3", {}, "System"),
    h("div", { class: "kv" }, "FFmpeg", S.status.ffmpeg.available ? S.status.ffmpeg.version : "NOT FOUND — brew install ffmpeg",
      "libass (subtitles)", S.status.ffmpeg.libass ? "yes" : "no", "HarfBuzz (Arabic shaping)", S.status.ffmpeg.harfbuzz ? "yes" : "no — Arabic letters may not join",
      "App version", S.status.version),
    h("h3", {}, "Privacy"),
    h("p", { class: "small muted" }, "Projects, media and renders stay on this Mac. With Claude selected, the script brief, your research notes/sources and narration text are sent to Anthropic when you press a generate/translate button; with web search enabled, your query is sent too. Media files are never uploaded."));
  v.append(h("div", { class: "split" }, pcard, h("div", { class: "list" }, gcard, ccard)));
};

function voicePicker(st) {
  const box = h("div", { class: "grid cols-2" }, h("span", { class: "muted small" }, "Loading voices…"));
  api("GET", "/api/tts/voices").then(r => {
    box.innerHTML = "";
    for (const lang of ["ar", "en"]) {
      const vs = r.voices.filter(x => x.lang.toLowerCase().startsWith(lang) || x.lang === "any");
      box.append(field(lang === "ar" ? "Arabic voice" : "English voice", select([["", "Automatic"], ...vs.map(x => [x.name, `${x.name} (${x.lang})`])], st.tts_voices[lang] || "",
        (val) => api("PUT", "/api/settings", { tts_voices: { [lang]: val } }).then(() => toast("Saved.", "ok")).catch(fail)),
        lang === "ar" && r.provider === "macos_say" && !vs.length ? "No Arabic voice installed: System Settings → Accessibility → Spoken Content → System Voice → Manage Voices → Arabic." : null));
    }
  }).catch(e => { box.innerHTML = ""; box.append(h("span", { class: "small muted" }, e.message)); });
  return box;
}

function setLang(l) {
  S.lang = l; try { localStorage.setItem("ads.lang", l); } catch (e) {}
  api("PUT", "/api/settings", { ui_language: l }).catch(() => {});
  route();
}

// ---------------------------------------------------------------- boot
(async function boot() {
  $("#lang-toggle").onclick = () => setLang(S.lang === "ar" ? "en" : "ar");
  try {
    [S.status, S.settings] = await Promise.all([api("GET", "/api/status"), api("GET", "/api/settings")]);
  } catch (e) { document.body.append(h("p", { style: { padding: "40px" } }, "Cannot reach the studio server: " + e.message)); return; }
  try { S.lang = localStorage.getItem("ads.lang") || S.settings.ui_language || "en"; } catch (e) { S.lang = S.settings.ui_language || "en"; }
  let pid = null;
  try { pid = localStorage.getItem("ads.pid"); } catch (e) {}
  if (pid) { S.pid = pid; try { await reloadProject(); } catch (e) { S.pid = null; S.project = null; } }
  for (const j of S.status.jobs || []) if (j.project_id === S.pid) runJob(j, () => reloadProject().then(route));
  window.addEventListener("hashchange", route);
  route();
})();
