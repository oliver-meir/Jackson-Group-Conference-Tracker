// Conference Tracker helper (Cloudflare Worker, free plan).
//
// The website is static, so it cannot save changes or start GitHub Actions by itself.
// This small service does it on the website's behalf, keeping the GitHub token secret.
//
// Settings (Cloudflare dashboard > the Worker > Settings > Variables and Secrets):
//   GITHUB_TOKEN    secret  fine-grained token for this one repo (Contents + Actions: read and write)
//   LAB_PASSPHRASE  secret  phrase lab members type once on the website before making changes
//   REPO            text    e.g. oliver-meir/Jackson-Group-Conference-Tracker
//   ALLOWED_ORIGIN  text    e.g. https://oliver-meir.github.io  (comma-separate several)
//
// Endpoints:
//   GET  /data             latest data/tracker.json (no passphrase needed; the data is public anyway)
//   POST /verify           {passphrase}                         check the passphrase
//   POST /add-professor    {passphrase, name, affiliation, ...}  add (or re-check) a professor and start the search
//   POST /recheck          {passphrase, id}                     start the search again for one professor
//   POST /edit             {passphrase, ops: [...]}             confirm / remove hits, edit entries
//   POST /add-conference   {passphrase, name, urls, city?, country?}  add a conference to search, and check it now
//   POST /conference       {passphrase, op, id}                 recheck | enable | disable | delete a conference

const FILE = "data/tracker.json";
const CONF_FILE = "data/conferences.json";
const WORKFLOW = "check-professor.yml";
const CONF_WORKFLOW = "check-conference.yml";
const MAX_BODY = 50_000;

export default {
  async fetch(request, env) {
    const cors = corsHeaders(request, env);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    const url = new URL(request.url);
    try {
      if (!env.GITHUB_TOKEN || !env.REPO) return json({ error: "Helper is not configured yet (GITHUB_TOKEN / REPO missing)." }, 500, cors);
      if (request.method === "GET" && url.pathname === "/data") {
        const [{ data }, conf] = await Promise.all([getTracker(env), getJsonFile(env, CONF_FILE, [])]);
        data.conferences = conf.data;
        return json(data, 200, cors);
      }
      if (request.method === "GET" && url.pathname === "/") {
        return json({ ok: true, service: "conference-tracker-helper" }, 200, cors);
      }
      if (request.method !== "POST") return json({ error: "Not found" }, 404, cors);
      if (cors["Access-Control-Allow-Origin"] === undefined) return json({ error: "This website is not allowed to use the helper (check ALLOWED_ORIGIN)." }, 403, cors);

      const text = await request.text();
      if (text.length > MAX_BODY) return json({ error: "Request too large" }, 413, cors);
      let body;
      try { body = JSON.parse(text || "{}"); } catch { return json({ error: "Bad request" }, 400, cors); }
      if (!passphraseOk(body.passphrase, env.LAB_PASSPHRASE)) return json({ error: "Wrong lab passphrase." }, 401, cors);

      if (url.pathname === "/verify") return json({ ok: true }, 200, cors);
      if (url.pathname === "/add-professor") return json(await addProfessor(body, env), 200, cors);
      if (url.pathname === "/recheck") return json(await recheck(body, env), 200, cors);
      if (url.pathname === "/edit") return json(await edit(body, env), 200, cors);
      if (url.pathname === "/add-conference") return json(await addConference(body, env), 200, cors);
      if (url.pathname === "/conference") return json(await conferenceOp(body, env), 200, cors);
      return json({ error: "Not found" }, 404, cors);
    } catch (e) {
      return json({ error: String(e && e.message || e) }, e && e.status || 500, cors);
    }
  },
};

// ---------- actions ----------

async function addProfessor(body, env) {
  const name = clean(body.name, 120);
  if (!name || name.length < 3) throw httpError(400, "Enter the professor's full name.");
  const prof = {
    name,
    affiliation: clean(body.affiliation, 160),
    field: clean(body.field, 160),
    aliases: list(body.aliases, 10, 120),
    context: list(body.context, 10, 80),
    notes: clean(body.notes, 500),
  };
  if (!prof.context.length && prof.affiliation) prof.context = affiliationWords(prof.affiliation);
  const requestedAt = new Date().toISOString().replace(/\.\d+Z$/, "Z");
  let id;
  await updateTracker(env, `Add professor: ${name}`, (data) => {
    const same = data.profs.find((p) => normName(p.name) === normName(name));
    if (same) { id = same.id; Object.assign(same, pickNonEmpty(prof)); same.checkRequested = requestedAt; return; }
    id = uniqueId(slug(name), data.profs);
    data.profs.push({ id, ...prof, checkRequested: requestedAt });
  });
  await dispatch(env, id);
  return { ok: true, id, requestedAt };
}

async function recheck(body, env) {
  const id = clean(body.id, 80);
  const requestedAt = new Date().toISOString().replace(/\.\d+Z$/, "Z");
  await updateTracker(env, `Re-check ${id}`, (data) => {
    const p = data.profs.find((x) => x.id === id);
    if (!p) throw httpError(404, "Unknown professor");
    p.checkRequested = requestedAt;
  });
  await dispatch(env, id);
  return { ok: true, id, requestedAt };
}

const APP_FIELDS = ["profId", "conference", "startDate", "endDate", "city", "country", "role", "confidence", "sourceUrl", "notes", "watchId", "check"];
const PROF_FIELDS = ["name", "affiliation", "field", "aliases", "context", "notes", "paused", "photo"];

async function edit(body, env) {
  const ops = Array.isArray(body.ops) ? body.ops.slice(0, 50) : [];
  if (!ops.length) throw httpError(400, "Nothing to change");
  const summary = ops.map((o) => `${o.op} ${o.id || ""}`.trim()).join(", ").slice(0, 120);
  await updateTracker(env, `Website edit: ${summary}`, (data) => {
    data.rejected = data.rejected || [];
    for (const o of ops) applyOp(data, o);
  });
  return { ok: true };
}

function applyOp(data, o) {
  const id = clean(o.id, 120);
  const appIdx = data.apps.findIndex((a) => a.id === id);
  const profIdx = data.profs.findIndex((p) => p.id === id);
  switch (o.op) {
    case "confirm":
      if (appIdx < 0) throw httpError(404, "Entry not found (it may have been removed already)");
      data.apps[appIdx].confidence = "Confirmed";
      delete data.apps[appIdx].check;
      break;
    case "reject": {
      if (appIdx < 0) return;
      const a = data.apps[appIdx];
      if (a.watchId) {
        const key = `${a.watchId}:${a.profId}`;
        if (!data.rejected.includes(key)) data.rejected.push(key);
      }
      data.apps.splice(appIdx, 1);
      break;
    }
    case "deleteApp":
      if (appIdx >= 0) data.apps.splice(appIdx, 1);
      break;
    case "setApp": {
      const rec = sanitize(o.data, APP_FIELDS);
      if (!rec.profId || !data.profs.some((p) => p.id === rec.profId)) throw httpError(400, "Pick a professor");
      if (rec.sourceUrl && !/^https?:\/\//i.test(rec.sourceUrl)) throw httpError(400, "Source link must start with http");
      const newId = id || `a${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
      const full = { id: newId, ...rec };
      if (appIdx >= 0) data.apps[appIdx] = full; else data.apps.push(full);
      break;
    }
    case "setProf": {
      const rec = sanitize(o.data, PROF_FIELDS);
      if (!rec.name) throw httpError(400, "Enter a name");
      if (profIdx >= 0) data.profs[profIdx] = { ...data.profs[profIdx], ...rec, id };
      else data.profs.push({ id: uniqueId(id || slug(rec.name), data.profs), ...rec });
      break;
    }
    case "deleteProf":
      if (profIdx >= 0) {
        data.profs.splice(profIdx, 1);
        data.apps = data.apps.filter((a) => a.profId !== id);
      }
      break;
    default:
      throw httpError(400, `Unknown change: ${o.op}`);
  }
}

async function addConference(body, env) {
  const name = clean(body.name, 160);
  if (!name || name.length < 3) throw httpError(400, "Enter the conference name.");
  const rawUrls = list(typeof body.urls === "string" ? body.urls.split(/[\n,]+/) : body.urls, 10, 500);
  if (!rawUrls.length) throw httpError(400, "Paste the link to the program or speakers page.");
  for (const u of rawUrls) if (!/^https?:\/\/[^\s]+\.[^\s]+/i.test(u)) throw httpError(400, `That does not look like a web link: ${u}`);
  const urls = rawUrls.map((u) => templatize(u));
  const requestedAt = new Date().toISOString().replace(/\.\d+Z$/, "Z");
  let id;
  await updateJsonFile(env, CONF_FILE, [], `Add conference: ${name}`, (list_) => {
    const same = list_.find((c) => normName(c.name) === normName(name));
    if (same) { id = same.id; same.urls = urls; same.enabled = true; same.checkRequested = requestedAt;
      if (body.city) same.city = clean(body.city, 80); if (body.country) same.country = clean(body.country, 80); return list_; }
    id = uniqueId(slug(name), list_);
    list_.push({ id, name, urls, city: clean(body.city, 80), country: clean(body.country, 80), enabled: true,
      added: requestedAt.slice(0, 10), checkRequested: requestedAt });
    return list_;
  });
  await dispatchWorkflow(env, CONF_WORKFLOW, { conf_id: id });
  return { ok: true, id, requestedAt, urls };
}

async function conferenceOp(body, env) {
  const id = clean(body.id, 80), op = body.op;
  const requestedAt = new Date().toISOString().replace(/\.\d+Z$/, "Z");
  if (!["recheck", "enable", "disable", "delete"].includes(op)) throw httpError(400, "Unknown change");
  await updateJsonFile(env, CONF_FILE, [], `Conference ${op}: ${id}`, (list_) => {
    const i = list_.findIndex((c) => c.id === id);
    if (i < 0) throw httpError(404, "Conference not found");
    if (op === "delete") list_.splice(i, 1);
    else if (op === "enable") list_[i].enabled = true;
    else if (op === "disable") list_[i].enabled = false;
    else { list_[i].enabled = true; list_[i].checkRequested = requestedAt; }
    return list_;
  });
  if (op === "recheck" || op === "enable") await dispatchWorkflow(env, CONF_WORKFLOW, { conf_id: id });
  return { ok: true, id, requestedAt };
}

// a link with a year in it becomes a pattern, so next year's edition is checked automatically
function templatize(u) {
  const years = u.match(/(?<!\d)20\d\d(?!\d)/g);
  if (!years) return u;
  return u.split(years[years.length - 1]).join("{year}");
}

// ---------- GitHub ----------

function gh(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${env.REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "conference-tracker-helper",
      ...(init.headers || {}),
    },
  });
}

async function getJsonFile(env, path, fallback) {
  const branch = env.BRANCH || "main";
  const r = await gh(env, `/contents/${path}?ref=${branch}`);
  if (r.status === 404 && fallback !== undefined) return { data: structuredClone(fallback), sha: undefined };
  if (!r.ok) throw httpError(502, `GitHub said ${r.status} when reading ${path}. Check REPO and the token.`);
  const j = await r.json();
  return { data: JSON.parse(b64decode(j.content)), sha: j.sha };
}

async function getTracker(env) {
  const { data, sha } = await getJsonFile(env, FILE);
  data.profs = data.profs || [];
  data.apps = data.apps || [];
  return { data, sha };
}

async function updateJsonFile(env, path, fallback, message, mutate) {
  const branch = env.BRANCH || "main";
  for (let attempt = 0; attempt < 4; attempt++) {
    const { data, sha } = await getJsonFile(env, path, fallback);
    const out = mutate(data);
    const payload = { message, content: b64encode(JSON.stringify(out, null, 2) + "\n"), branch };
    if (sha) payload.sha = sha;
    const r = await gh(env, `/contents/${path}`, { method: "PUT", body: JSON.stringify(payload), headers: { "Content-Type": "application/json" } });
    if (r.ok) return out;
    if (r.status === 409 || r.status === 422) { await sleep(400 * (attempt + 1)); continue; }
    throw httpError(502, `GitHub said ${r.status} when saving. Check the token has Contents: read and write.`);
  }
  throw httpError(503, "Too many changes at once. Try again in a moment.");
}

async function updateTracker(env, message, mutate) {
  const branch = env.BRANCH || "main";
  for (let attempt = 0; attempt < 4; attempt++) {
    const { data, sha } = await getTracker(env);
    mutate(data);
    data.generated = new Date().toISOString().slice(0, 10);
    const body = JSON.stringify({ message, content: b64encode(JSON.stringify(data, null, 2) + "\n"), sha, branch });
    const r = await gh(env, `/contents/${FILE}`, { method: "PUT", body, headers: { "Content-Type": "application/json" } });
    if (r.ok) return data;
    if (r.status === 409 || r.status === 422) { await sleep(400 * (attempt + 1)); continue; } // someone saved at the same time
    throw httpError(502, `GitHub said ${r.status} when saving. Check the token has Contents: read and write.`);
  }
  throw httpError(503, "Too many changes at once. Try again in a moment.");
}

async function dispatch(env, profId) { return dispatchWorkflow(env, WORKFLOW, { prof_id: profId }); }

async function dispatchWorkflow(env, workflow, inputs) {
  const r = await gh(env, `/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ref: env.BRANCH || "main", inputs }),
  });
  if (r.status !== 204) throw httpError(502, `Saved, but GitHub said ${r.status} when starting the search. Check the token has Actions: read and write.`);
}

// ---------- helpers ----------

function corsHeaders(request, env) {
  const origin = request.headers.get("Origin") || "";
  const allowed = String(env.ALLOWED_ORIGIN || "").split(",").map((s) => s.trim().replace(/\/$/, "")).filter(Boolean);
  const h = { "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "Content-Type", "Access-Control-Max-Age": "86400", Vary: "Origin" };
  if (allowed.includes("*")) h["Access-Control-Allow-Origin"] = "*";
  else if (allowed.includes(origin)) h["Access-Control-Allow-Origin"] = origin;
  return h;
}

function json(obj, status, headers) {
  return new Response(JSON.stringify(obj), { status, headers: { ...headers, "Content-Type": "application/json", "Cache-Control": "no-store" } });
}

function passphraseOk(given, expected) {
  if (!expected || typeof given !== "string") return false;
  const a = new TextEncoder().encode(given.trim().toLowerCase());
  const b = new TextEncoder().encode(String(expected).trim().toLowerCase());
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) diff |= (a[i] || 0) ^ (b[i] || 0);
  return diff === 0;
}

function httpError(status, message) { const e = new Error(message); e.status = status; return e; }
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
function clean(v, max) { return typeof v === "string" ? v.replace(/[\u0000-\u001f]/g, " ").trim().slice(0, max) : ""; }
function list(v, n, max) {
  const arr = Array.isArray(v) ? v : typeof v === "string" ? v.split(",") : [];
  return arr.map((x) => clean(String(x), max)).filter(Boolean).slice(0, n);
}
function sanitize(obj, fields) {
  const out = {};
  for (const k of fields) {
    if (!obj || obj[k] === undefined) continue;
    if (k === "aliases" || k === "context") out[k] = list(obj[k], 10, 120);
    else if (k === "paused") out[k] = !!obj[k];
    else out[k] = clean(String(obj[k]), k === "notes" ? 600 : 300);
  }
  return out;
}
function pickNonEmpty(o) { const r = {}; for (const [k, v] of Object.entries(o)) if (Array.isArray(v) ? v.length : v) r[k] = v; return r; }
function normName(s) { return String(s).normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z]+/g, " ").trim(); }
function slug(s) { return normName(s).replace(/ /g, "-").slice(0, 60) || "prof"; }
function uniqueId(base, profs) { let id = base, n = 2; while (profs.some((p) => p.id === id)) id = `${base}-${n++}`; return id; }
function affiliationWords(aff) {
  // e.g. "University of Tokyo" -> ["University of Tokyo", "Tokyo"]
  const words = aff.split(/[\/,;()]/).map((s) => s.trim()).filter((s) => s.length > 2);
  const extra = words.flatMap((w) => w.split(/\s+/).filter((t) => t.length > 3 && !/^(university|institute|college|school|department|of|the|and|for|research|center|centre)$/i.test(t)));
  return [...new Set([...words, ...extra])].slice(0, 8);
}
function b64decode(b64) {
  const bin = atob(String(b64).replace(/\n/g, ""));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}
function b64encode(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

