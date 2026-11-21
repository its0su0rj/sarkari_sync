// Run: node --test worker/test
import test from "node:test";
import assert from "node:assert/strict";
import * as B from "../src/bot.mjs";
import worker from "../src/index.mjs";

const TODAY = "2026-11-17";
const day = n => new Date(Date.UTC(2026, 10, 17 + n)).toISOString().slice(0, 10);
const PROFILE = { name: "Rahul", dob: "2001-03-10", gender: "male", category: "OBC", domicile: "UP",
  education: [{ level: "10th", percent: 78 }, { level: "12th", percent: 72 },
    { level: "graduate", degree: "B.Sc", subjects: ["physics"], percent: 65 }],
  certificates: [], certificates_complete: true, experience_years: 0 };

function codeFor(p) {
  const bytes = new TextEncoder().encode(JSON.stringify(p)); let bin = "";
  bytes.forEach(b => { bin += String.fromCharCode(b); });
  return "EM1." + btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
const job = (n, title, quals, last) => ({ link: `https://s/${n}`, title, categories: [], qualification_text: quals,
  dates: { last_date: last }, age: { min: 18, max: 30 }, date_notes: {},
  links: { apply: `https://apply/${n}`, notification: `https://n/${n}.pdf` } });

function fakeKV() {
  const m = new Map();
  return { m, writes: 0,
    async get(k, type) { const v = m.has(k) ? m.get(k) : null; return v === null ? null : type === "json" ? JSON.parse(v) : v; },
    async put(k, v) { this.writes++; m.set(k, String(v)); },
    async delete(k) { m.delete(k); } };
}
function fakeTG() {
  const sent = []; const blocked = new Set();
  return { sent, blocked, async send(cid, text) { if (blocked.has(String(cid))) return "gone"; sent.push([String(cid), text]); return "ok"; } };
}
function setup() {
  const jobs = [job(1, "Bank Clerk Online Form 2026", ["Bachelor Degree in Any Stream"], day(20)),
    job(2, "Railway Apprentice Online Form 2026", ["Class 10 and ITI Certificate"], day(20)),
    job(3, "SSC MTS Online Form 2026", ["10th Passed"], day(2))];
  const ctx = { kv: fakeKV(), tg: fakeTG(), jobs, site: "https://site/", today: TODAY };
  ctx.getJobs = async () => ctx.jobs;
  return ctx;
}
const msg = (id, text, type = "private") => ({ chat: { id, type }, text });

test("subscribe, matches, name not kept", async () => {
  const c = setup();
  await B.handleMessage(msg(11, "/start hi"), c);
  await B.handleMessage(msg(11, codeFor(PROFILE)), c);
  assert.match(c.tg.sent[0][1], /Copy my code/);
  const welcome = c.tg.sent[1][1];
  assert.match(welcome, /Subscribed/);
  assert.match(welcome, /Bank Clerk/);
  assert.match(welcome, /SSC MTS/);
  assert.doesNotMatch(welcome, /Railway Apprentice/);
  const sub = await c.kv.get("sub:11", "json");
  assert.equal(sub.profile.name, undefined);
  assert.deepEqual(Object.keys(sub.notified).sort(), ["https://s/1", "https://s/3"]);
  assert.deepEqual(await c.kv.get("subs:index", "json"), ["11"]);
  await B.handleMessage(msg(11, "/matches"), c);
  assert.match(c.tg.sent.at(-1)[1], /Your exams today/);
});

test("bad codes and group chats", async () => {
  const c = setup();
  await B.handleMessage(msg(12, "EM1.not-base64!!"), c);
  await B.handleMessage(msg(12, codeFor({ ...PROFILE, category: "XYZ" })), c);
  await B.handleMessage(msg(12, codeFor({ ...PROFILE, dob: "2099-01-01" })), c);
  await B.handleMessage(msg(13, codeFor(PROFILE), "group"), c);
  assert.equal(c.tg.sent.length, 3);
  assert.ok(c.tg.sent.every(([, t]) => /didn't work/.test(t)));
  assert.equal(c.kv.m.size, 0);
});

test("stop deletes everything", async () => {
  const c = setup();
  await B.handleMessage(msg(14, codeFor(PROFILE)), c);
  await B.handleMessage(msg(14, "/stop"), c);
  assert.equal(await c.kv.get("sub:14"), null);
  assert.deepEqual(await c.kv.get("subs:index", "json"), []);
  assert.match(c.tg.sent.at(-1)[1], /deleted/);
});

test("alerts: new job + reminder, sent once, batches with a cursor", async () => {
  const c = setup();
  for (const id of [21, 22, 23, 24]) await B.handleMessage(msg(id, codeFor(PROFILE)), c);
  c.tg.sent.length = 0;
  c.jobs.push(job(4, "UPPSC RO ARO Online Form 2026", ["Bachelor Degree in Any Stream"], day(10)));
  let st = await B.runAlertsBatch({ ...c, batch: 3 });
  assert.equal(st.pos, 3);
  assert.equal(c.tg.sent.length, 3);
  st = await B.runAlertsBatch({ ...c, batch: 3 });
  assert.equal(st.pos, 4);
  st = await B.runAlertsBatch({ ...c, batch: 3 });
  assert.equal(st.done, true);
  assert.equal(c.tg.sent.length, 4);
  const m = c.tg.sent[0][1];
  assert.match(m, /New exam/);
  assert.match(m, /UPPSC RO ARO/);
  assert.match(m, /Last date soon/);
  assert.match(m, /SSC MTS/);
  assert.doesNotMatch(m, /Bank Clerk/);
  const writes = c.kv.writes;
  st = await B.runAlertsBatch({ ...c, batch: 3 });          // finished for today: nothing sent, nothing written
  assert.equal(st.skipped, true);
  assert.equal(c.kv.writes, writes);
  // next day: nothing new, reminders already sent -> silent
  c.tg.sent.length = 0;
  for (let i = 0; i < 3; i++) await B.runAlertsBatch({ ...c, today: day(1), batch: 3 });
  assert.equal(c.tg.sent.length, 0);
});

test("blocked users are removed, closed jobs pruned", async () => {
  const c = setup();
  await B.handleMessage(msg(31, codeFor(PROFILE)), c);
  await B.handleMessage(msg(32, codeFor(PROFILE)), c);
  c.jobs = c.jobs.slice(1).concat([job(5, "Bank PO Online Form 2026", ["Graduate"], day(15))]);
  c.tg.blocked.add("31");
  const st = await B.runAlertsBatch({ ...c });
  assert.equal(st.removed, 1);
  assert.deepEqual(await c.kv.get("subs:index", "json"), ["32"]);
  const sub = await c.kv.get("sub:32", "json");
  assert.ok(!("https://s/1" in sub.notified));
});

test("telegram client: split, flood retry, gone", async () => {
  const calls = [];
  let flood = true;
  const fakeFetch = async (url, init) => {
    const p = JSON.parse(init.body); calls.push(p);
    if (flood) { flood = false; return new Response(JSON.stringify({ ok: false, parameters: { retry_after: 0 } }), { status: 429 }); }
    if (p.chat_id === "99") return new Response(JSON.stringify({ ok: false, description: "Forbidden: bot was blocked by the user" }), { status: 403 });
    return new Response(JSON.stringify({ ok: true }), { status: 200 });
  };
  const tg = B.telegram("T", fakeFetch, async () => {});
  const long = Array.from({ length: 40 }, (_, i) => `• <b>Job ${i}</b> ` + "x".repeat(300)).join("\n");
  assert.equal(await tg.send("1", long), "ok");
  assert.ok(B.splitMessage(long).every(p => p.length <= 3800));
  assert.equal(calls.length, B.splitMessage(long).length + 1);
  assert.equal(await tg.send("99", "hi"), "gone");
});

test("escaping and dates", () => {
  assert.equal(B.esc('<b>&"\''), "&lt;b&gt;&amp;&quot;&#x27;");
  assert.equal(B.niceDate("2026-12-05"), "5 Dec");
  assert.equal(B.daysBetween("2026-11-30", "2026-12-02"), 2);
  assert.equal(B.istToday(Date.UTC(2026, 10, 20, 19, 0)), "2026-11-21");     // 00:30 IST next day
});

test("website code with a Hindi name decodes", () => {
  const p = B.decodeCode(codeFor({ ...PROFILE, name: "राहुल", certificates: ["CCC"] }), TODAY);
  assert.equal(p.name, undefined);
  assert.deepEqual(p.certificates, ["CCC"]);
});

test("import moves old subscribers", async () => {
  const kv = fakeKV();
  const n = await B.importSubscribers(kv, { "41": { profile: PROFILE, notified: {} }, "42": { profile: PROFILE }, "43": {} });
  assert.equal(n, 2);
  assert.deepEqual(await kv.get("subs:index", "json"), ["41", "42"]);
});

test("worker: webhook secret checked, admin protected", async () => {
  const env = { KV: fakeKV(), WEBHOOK_SECRET: "s3cret-value", ADMIN_TOKEN: "admintoken", TELEGRAM_BOT_TOKEN: "T",
    JOBS_URL: "https://example/jobs.json", SITE_URL: "https://site/" };
  let r = await worker.fetch(new Request("https://w/telegram", { method: "POST", body: "{}" }), env);
  assert.equal(r.status, 403);
  r = await worker.fetch(new Request("https://w/telegram", { method: "POST", body: "{}", headers: { "X-Telegram-Bot-Api-Secret-Token": "s3cret-value" } }), env);
  assert.equal(r.status, 200);
  r = await worker.fetch(new Request("https://w/admin/stats"), env);
  assert.equal(r.status, 403);
  r = await worker.fetch(new Request("https://w/admin/stats", { headers: { Authorization: "Bearer admintoken" } }), env);
  assert.deepEqual(await r.json(), { subscribers: 0, alerts: null });
});
