// Offline test of helper/worker.js with a fake GitHub API. Run: node tests/worker/worker.test.mjs
import assert from "node:assert/strict";
import fs from "node:fs";
import worker from "../../helper/worker.js";

const tracker = JSON.parse(fs.readFileSync(new URL("../../data/tracker.json", import.meta.url)));
const files = { "data/tracker.json": { text: JSON.stringify(tracker), sha: "s0" } };
let dispatched = [];
let failNextPut = true;
const b64 = (s) => Buffer.from(s, "utf8").toString("base64");
const fileOf = (u) => decodeURIComponent(u.pathname.split("/contents/")[1] || "");

globalThis.fetch = async (url, init = {}) => {
  const u = new URL(url);
  assert.equal(init.headers.Authorization, "Bearer TOKEN");
  if (u.pathname.includes("/contents/") && (!init.method || init.method === "GET")) {
    const f = files[fileOf(u)];
    if (!f) return new Response("{}", { status: 404 });
    return new Response(JSON.stringify({ content: b64(f.text), sha: f.sha }), { status: 200 });
  }
  if (u.pathname.includes("/contents/") && init.method === "PUT") {
    const body = JSON.parse(init.body), name = fileOf(u), f = files[name];
    if (failNextPut) { failNextPut = false; if (f) f.sha = "s-other"; return new Response("{}", { status: 409 }); }
    if ((f && body.sha !== f.sha) || (!f && body.sha)) return new Response("{}", { status: 409 });
    files[name] = { text: Buffer.from(body.content, "base64").toString("utf8"), sha: "s" + Math.random() };
    return new Response("{}", { status: 200 });
  }
  const wf = u.pathname.match(/workflows\/([^/]+)\/dispatches/);
  if (wf) { dispatched.push(wf[1] === "check-professor.yml" ? JSON.parse(init.body).inputs.prof_id : "conf:" + JSON.parse(init.body).inputs.conf_id);
    return new Response(null, { status: 204 }); }
  return new Response("{}", { status: 404 });
};
const file = { get text() { return files["data/tracker.json"].text; }, set text(v) { files["data/tracker.json"].text = v; } };

const env = { GITHUB_TOKEN: "TOKEN", LAB_PASSPHRASE: "Jackson Lab", REPO: "o/r", ALLOWED_ORIGIN: "https://oliver-meir.github.io" };
const ORIGIN = "https://oliver-meir.github.io";
const call = async (path, body, origin = ORIGIN) => {
  const r = await worker.fetch(new Request("https://h.example" + path, {
    method: body ? "POST" : "GET", headers: { Origin: origin, "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined,
  }), env);
  return { status: r.status, body: await r.json(), headers: r.headers };
};

// read
let r = await call("/data");
assert.equal(r.status, 200); assert.equal(r.body.profs.length, 12);
assert.equal(r.headers.get("Access-Control-Allow-Origin"), ORIGIN);

// passphrase
r = await call("/verify", { passphrase: "nope" }); assert.equal(r.status, 401);
r = await call("/verify", { passphrase: "  jackson lab " }); assert.equal(r.status, 200);

// wrong origin
r = await call("/verify", { passphrase: "Jackson Lab" }, "https://evil.example"); assert.equal(r.status, 403);

// add professor (first PUT conflicts, should retry)
r = await call("/add-professor", { passphrase: "Jackson Lab", name: "Frances H. Arnold", affiliation: "Caltech", aliases: "F. H. Arnold, Frances Arnold" });
assert.equal(r.status, 200, JSON.stringify(r.body)); assert.equal(r.body.id, "frances-h-arnold");
let data = JSON.parse(file.text);
let fa = data.profs.find((p) => p.id === "frances-h-arnold");
assert.deepEqual(fa.aliases, ["F. H. Arnold", "Frances Arnold"]);
assert.ok(fa.checkRequested && fa.context.includes("Caltech"));
assert.deepEqual(dispatched, ["frances-h-arnold"]);

// adding the same name again re-checks instead of duplicating
r = await call("/add-professor", { passphrase: "Jackson Lab", name: "frances h arnold" });
data = JSON.parse(file.text);
assert.equal(data.profs.filter((p) => p.id.startsWith("frances")).length, 1);
assert.equal(dispatched.length, 2);

// too-short name
r = await call("/add-professor", { passphrase: "Jackson Lab", name: "x" }); assert.equal(r.status, 400);

// confirm + reject
data.apps.push({ id: "demo-2027-shohei-koide", profId: "shohei-koide", watchId: "demo-2027", conference: "Demo", confidence: "Unverified", check: "looks right" },
               { id: "demo-2027-feng-zhang", profId: "feng-zhang", watchId: "demo-2027", conference: "Demo", confidence: "Unverified", check: "check carefully" });
file.text = JSON.stringify(data);
r = await call("/edit", { passphrase: "Jackson Lab", ops: [{ op: "confirm", id: "demo-2027-shohei-koide" }, { op: "reject", id: "demo-2027-feng-zhang" }] });
assert.equal(r.status, 200, JSON.stringify(r.body));
data = JSON.parse(file.text);
const k = data.apps.find((a) => a.id === "demo-2027-shohei-koide");
assert.equal(k.confidence, "Confirmed"); assert.equal(k.check, undefined);
assert.ok(!data.apps.some((a) => a.id === "demo-2027-feng-zhang"));
assert.deepEqual(data.rejected, ["demo-2027:feng-zhang"]);

// setApp validation
r = await call("/edit", { passphrase: "Jackson Lab", ops: [{ op: "setApp", data: { profId: "nobody", conference: "X" } }] }); assert.equal(r.status, 400);
r = await call("/edit", { passphrase: "Jackson Lab", ops: [{ op: "setApp", data: { profId: "colin-jackson", conference: "X", sourceUrl: "javascript:alert(1)" } }] }); assert.equal(r.status, 400);
r = await call("/edit", { passphrase: "Jackson Lab", ops: [{ op: "setApp", data: { profId: "colin-jackson", conference: "Lorne 2027", startDate: "2027-02-07", evil: "x" } }] });
assert.equal(r.status, 200);
data = JSON.parse(file.text);
const added = data.apps.find((a) => a.conference === "Lorne 2027");
assert.ok(added && added.id && added.evil === undefined);

// delete professor removes their entries
r = await call("/edit", { passphrase: "Jackson Lab", ops: [{ op: "deleteProf", id: "frances-h-arnold" }] });
data = JSON.parse(file.text);
assert.ok(!data.profs.some((p) => p.id === "frances-h-arnold"));

// recheck unknown
r = await call("/recheck", { passphrase: "Jackson Lab", id: "nobody" }); assert.equal(r.status, 404);

// unicode survives round trip
r = await call("/add-professor", { passphrase: "Jackson Lab", name: "José Müller", affiliation: "ETH Zürich" });
data = JSON.parse(file.text);
assert.ok(data.profs.some((p) => p.name === "José Müller" && p.id === "jose-muller"));

// preflight
const pre = await worker.fetch(new Request("https://h.example/edit", { method: "OPTIONS", headers: { Origin: ORIGIN } }), env);
assert.equal(pre.status, 204);

// conferences: /data includes an empty list before any exist
r = await call("/data");
assert.deepEqual(r.body.conferences, []);

// add a conference: year in the link becomes a pattern, file is created, check is started
r = await call("/add-conference", { passphrase: "Jackson Lab", name: "Lorne Proteins Conference",
  urls: "https://proteins-2026.p.asnevents.com.au/\nhttps://www.lorneproteins.org/program" });
assert.equal(r.status, 200, JSON.stringify(r.body));
let confs = JSON.parse(files["data/conferences.json"].text);
assert.equal(confs[0].id, "lorne-proteins-conference");
assert.deepEqual(confs[0].urls, ["https://proteins-{year}.p.asnevents.com.au/", "https://www.lorneproteins.org/program"]);
assert.ok(confs[0].enabled && confs[0].checkRequested);
assert.equal(dispatched.at(-1), "conf:lorne-proteins-conference");
r = await call("/data"); assert.equal(r.body.conferences.length, 1);

// bad links are refused
r = await call("/add-conference", { passphrase: "Jackson Lab", name: "Bad", urls: "not a link" }); assert.equal(r.status, 400);
r = await call("/add-conference", { passphrase: "Jackson Lab", name: "Nothing", urls: "" }); assert.equal(r.status, 400);

// same name again updates instead of duplicating
r = await call("/add-conference", { passphrase: "Jackson Lab", name: "lorne proteins conference", urls: "https://proteins-2027.p.asnevents.com.au/days" });
confs = JSON.parse(files["data/conferences.json"].text);
assert.equal(confs.length, 1); assert.deepEqual(confs[0].urls, ["https://proteins-{year}.p.asnevents.com.au/days"]);

// disable / recheck / delete
r = await call("/conference", { passphrase: "Jackson Lab", op: "disable", id: "lorne-proteins-conference" });
assert.equal(JSON.parse(files["data/conferences.json"].text)[0].enabled, false);
const n = dispatched.length;
r = await call("/conference", { passphrase: "Jackson Lab", op: "recheck", id: "lorne-proteins-conference" });
assert.equal(JSON.parse(files["data/conferences.json"].text)[0].enabled, true); assert.equal(dispatched.length, n + 1);
r = await call("/conference", { passphrase: "Jackson Lab", op: "delete", id: "lorne-proteins-conference" });
assert.equal(JSON.parse(files["data/conferences.json"].text).length, 0);
r = await call("/conference", { passphrase: "Jackson Lab", op: "delete", id: "nope" }); assert.equal(r.status, 404);
r = await call("/conference", { passphrase: "nope", op: "delete", id: "x" }); assert.equal(r.status, 401);

console.log("worker tests passed");
