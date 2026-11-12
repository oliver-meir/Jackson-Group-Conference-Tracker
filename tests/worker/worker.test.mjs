// Offline test of helper/worker.js with a fake GitHub API. Run: node tests/worker/worker.test.mjs
import assert from "node:assert/strict";
import fs from "node:fs";
import worker from "../../helper/worker.js";

const tracker = JSON.parse(fs.readFileSync(new URL("../../data/tracker.json", import.meta.url)));
let file = { text: JSON.stringify(tracker), sha: "s0" };
let dispatched = [];
let failNextPut = true;
const b64 = (s) => Buffer.from(s, "utf8").toString("base64");

globalThis.fetch = async (url, init = {}) => {
  const u = new URL(url);
  assert.equal(init.headers.Authorization, "Bearer TOKEN");
  if (u.pathname.endsWith("/contents/data/tracker.json") && (!init.method || init.method === "GET"))
    return new Response(JSON.stringify({ content: b64(file.text), sha: file.sha }), { status: 200 });
  if (u.pathname.endsWith("/contents/data/tracker.json") && init.method === "PUT") {
    const body = JSON.parse(init.body);
    if (failNextPut) { failNextPut = false; file.sha = "s-other"; return new Response("{}", { status: 409 }); }
    if (body.sha !== file.sha) return new Response("{}", { status: 409 });
    file = { text: Buffer.from(body.content, "base64").toString("utf8"), sha: "s" + Math.random() };
    return new Response("{}", { status: 200 });
  }
  if (u.pathname.endsWith("/actions/workflows/check-professor.yml/dispatches")) {
    dispatched.push(JSON.parse(init.body).inputs.prof_id);
    return new Response(null, { status: 204 });
  }
  return new Response("{}", { status: 404 });
};

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

console.log("worker tests passed");
