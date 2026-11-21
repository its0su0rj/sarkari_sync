// ExamMatch Telegram bot logic for Cloudflare Workers.
// Same commands and messages as bot.py, but answers instantly (Telegram calls us through a webhook).
// Everything outside is passed in (kv, telegram, jobs), so the logic is testable in plain Node.

import E from "../../site/engine.js";

export const MAX_CODE = 6000;
export const REMIND_DAYS = 3;
export const MAX_LIST = 10;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const ALLOWED = new Set(["name", "dob", "gender", "category", "pwd", "ex_serviceman", "domicile", "education",
  "pursuing", "certificates", "certificates_complete", "experience_years"]);

// ---------------------------------------------------------------- dates (India time)
export function istToday(now = Date.now()) {
  return new Date(now + 5.5 * 3600e3).toISOString().slice(0, 10);
}
const dayNum = iso => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) / 864e5;
export const daysBetween = (from, to) => Math.round(dayNum(to) - dayNum(from));
export const niceDate = iso => (iso ? `${+iso.slice(8, 10)} ${MONTHS.at(+iso.slice(5, 7) - 1)}` : "see notification");

// ---------------------------------------------------------------- messages
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#x27;" }[c]));

export function jobLine(r, today) {
  const parts = [`• <b>${esc(r.title)}</b>`];
  if (r.last_date) {
    const left = daysBetween(today, r.last_date);
    const when = left === 0 ? "today" : left === 1 ? "tomorrow" : `${left} days left`;
    parts.push(`  Last date: ${niceDate(r.last_date)} (${when})`);
  }
  const links = [];
  for (const [key, label] of [["apply", "Apply"], ["notification", "Notification"], ["link", "Details"]]) {
    const u = r[key];
    if (u && /^https?:\/\//.test(String(u))) links.push(`<a href="${esc(u)}">${label}</a>`);
  }
  if (links.length) parts.push("  " + links.join(" · "));
  return parts.join("\n");
}

export function footer(site) {
  const out = [];
  if (site) out.push(`Full details: ${esc(site)}`);
  out.push("Always confirm in the official notification. /stop to stop alerts.");
  return "\n\n" + out.join("\n");
}

export const HELP = "<b>ExamMatch alerts</b> · सरकारी परीक्षा सूचना\n\n"
  + "1. Open the ExamMatch website, fill in your details and tap <b>Find my exams</b>.\n"
  + "2. Tap <b>Copy my code</b> and send the code here.\n\n"
  + "I will message you when a new exam you are eligible for comes out, and 3 days before each last date.\n\n"
  + "/matches – exams you can apply for now\n/stop – stop alerts and delete your details\n/help – this message";

export function summaryText(results, today, site, intro) {
  const elig = results.filter(r => r.status === "eligible");
  const chk = results.filter(r => r.status === "check_manually");
  const lines = [intro, ""];
  if (elig.length) {
    lines.push(`✅ <b>You can apply for ${elig.length} open exam${elig.length !== 1 ? "s" : ""}:</b>`);
    elig.slice(0, MAX_LIST).forEach(r => lines.push(jobLine(r, today)));
    if (elig.length > MAX_LIST) lines.push(`…and ${elig.length - MAX_LIST} more on the website.`);
  } else {
    lines.push("No open exam matches all your details right now. I'll tell you when one comes out.");
  }
  if (chk.length) lines.push(`\n🔎 ${chk.length} more need a manual check (see the website).`);
  return lines.join("\n") + footer(site);
}

export function splitMessage(text, limit = 3800) {
  const parts = []; let cur = "";
  for (let line of text.split("\n")) {
    while (line.length > limit) { parts.push(line.slice(0, limit)); line = line.slice(limit); }
    if (cur && cur.length + 1 + line.length > limit) { parts.push(cur); cur = line; }
    else cur = cur ? `${cur}\n${line}` : line;
  }
  if (cur) parts.push(cur);
  return parts.length ? parts : [""];
}

// ---------------------------------------------------------------- profile codes
export function decodeCode(text, today) {
  text = text.trim();
  if (!text.startsWith("EM1.") || text.length > MAX_CODE) throw new Error("not a profile code");
  let raw = text.slice(4).replace(/-/g, "+").replace(/_/g, "/");
  raw += "=".repeat((4 - (raw.length % 4)) % 4);
  const bytes = Uint8Array.from(atob(raw), c => c.charCodeAt(0));
  const data = JSON.parse(new TextDecoder().decode(bytes));
  if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("bad code");
  const clean = {};
  for (const [k, v] of Object.entries(data)) if (ALLOWED.has(k) && k !== "name") clean[k] = v;
  E.loadProfile(clean);                                         // throws if invalid
  if (!(clean.dob >= "1940-01-01" && clean.dob <= today)) throw new Error("date of birth looks wrong");
  if ((clean.education || []).length > 10 || (clean.certificates || []).length > 30) throw new Error("too many entries");
  return clean;
}

export function openMatches(profile, jobs, today) {
  return E.match(E.loadProfile(profile), jobs, today).filter(r => r.status === "eligible" || r.status === "check_manually");
}

// ---------------------------------------------------------------- storage (Workers KV)
const subKey = cid => `sub:${cid}`;
async function getIndex(kv) { return (await kv.get("subs:index", "json")) || []; }
async function addToIndex(kv, cid) {
  const idx = await getIndex(kv);
  if (!idx.includes(cid)) { idx.push(cid); await kv.put("subs:index", JSON.stringify(idx)); }
}
async function removeFromIndex(kv, cids) {
  const idx = await getIndex(kv);
  const next = idx.filter(c => !cids.includes(c));
  if (next.length !== idx.length) await kv.put("subs:index", JSON.stringify(next));
}

// ---------------------------------------------------------------- telegram
export function telegram(token, fetchFn = fetch, sleep = ms => new Promise(r => setTimeout(r, ms))) {
  const url = `https://api.telegram.org/bot${token}`;
  async function call(method, params) {
    for (let i = 0; i < 3; i++) {
      const res = await fetchFn(`${url}/${method}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(params) });
      let body; try { body = await res.json(); } catch { body = { ok: false, description: "bad response" }; }
      if (res.status === 429) { await sleep(Math.min(((body.parameters || {}).retry_after || 1), 5) * 1000); continue; }
      return [res.status, body];
    }
    return [429, { ok: false, description: "rate limited" }];
  }
  async function sendOne(chatId, text) {
    const [code, body] = await call("sendMessage", { chat_id: chatId, text, parse_mode: "HTML", disable_web_page_preview: true });
    if (body.ok) return "ok";
    if ((code === 400 || code === 403) && /blocked|deactivated|chat not found|kicked/i.test(body.description || "")) return "gone";
    return "error";
  }
  return {
    call,
    async send(chatId, text) {
      let result = "ok";
      for (const part of splitMessage(text)) { result = await sendOne(chatId, part); if (result !== "ok") break; }
      return result;
    },
  };
}

// ---------------------------------------------------------------- commands (webhook)
export async function handleMessage(msg, { kv, tg, getJobs, site, today }) {
  const chat = msg.chat || {};
  if (chat.type !== "private") return;
  const cid = String(chat.id);
  const text = String(msg.text || "").trim();

  if (text.startsWith("/stop")) {
    const existed = (await kv.get(subKey(cid))) !== null;
    if (existed) { await kv.delete(subKey(cid)); await removeFromIndex(kv, [cid]); }
    await tg.send(cid, existed ? "Stopped. Your details have been deleted." : "You are not subscribed.");
    return;
  }
  if (text.startsWith("/matches")) {
    const sub = await kv.get(subKey(cid), "json");
    if (!sub) { await tg.send(cid, "You are not subscribed yet.\n\n" + HELP); return; }
    const res = openMatches(sub.profile, await getJobs(), today);
    await tg.send(cid, summaryText(res, today, site, "<b>Your exams today</b>"));
    return;
  }
  if (text.startsWith("EM1.")) {
    let profile;
    try { profile = decodeCode(text, today); }
    catch { await tg.send(cid, "That code didn't work. Please copy it again from the website (tap <b>Copy my code</b>) and send the whole code."); return; }
    const res = openMatches(profile, await getJobs(), today);
    const now = new Date().toISOString().slice(0, 19) + "+00:00";
    const old = await kv.get(subKey(cid), "json");
    const notified = {};
    res.filter(r => r.status === "eligible").forEach(r => { notified[r.link] = { new: today }; });
    await kv.put(subKey(cid), JSON.stringify({ profile, since: (old && old.since) || now, updated: now, notified }));
    await addToIndex(kv, cid);
    await tg.send(cid, summaryText(res, today, site, old ? "✅ <b>Subscribed!</b> Your details were updated." : "✅ <b>Subscribed!</b>"));
    return;
  }
  await tg.send(cid, HELP);
}

// ---------------------------------------------------------------- daily alerts, a few subscribers per run
// Each cron run has 10 ms of CPU on the free plan, so subscribers are processed in small batches;
// a cursor in KV remembers where the previous run stopped.
export async function runAlertsBatch({ kv, tg, getJobs, site, today, batch = 3 }) {
  let st = (await kv.get("alerts:state", "json")) || {};
  if (st.date !== today) st = { date: today, pos: 0, done: false, sent: 0, removed: 0 };
  if (st.done) return { ...st, skipped: true };
  const idx = await getIndex(kv);
  const slice = idx.slice(st.pos, st.pos + batch);
  if (!slice.length) { st.done = true; await kv.put("alerts:state", JSON.stringify(st)); return st; }
  const jobs = await getJobs();
  const live = new Set(jobs.map(j => j.link));
  const gone = [];
  for (const cid of slice) {
    const sub = await kv.get(subKey(cid), "json");
    if (!sub) { gone.push(cid); continue; }
    const notified = Object.fromEntries(Object.entries(sub.notified || {}).filter(([k]) => live.has(k)));
    let res;
    try { res = openMatches(sub.profile, jobs, today); } catch { continue; }
    const elig = res.filter(r => r.status === "eligible");
    const fresh = elig.filter(r => !(r.link in notified));
    const freshLinks = new Set(fresh.map(r => r.link));
    const remind = elig.filter(r => r.last_date && !freshLinks.has(r.link)
      && daysBetween(today, r.last_date) >= 0 && daysBetween(today, r.last_date) <= REMIND_DAYS
      && !("remind" in (notified[r.link] || {})));
    if (!fresh.length && !remind.length) {
      if (Object.keys(notified).length !== Object.keys(sub.notified || {}).length) {
        sub.notified = notified; await kv.put(subKey(cid), JSON.stringify(sub));
      }
      continue;
    }
    const lines = [];
    if (fresh.length) {
      lines.push(`🆕 <b>New exam${fresh.length > 1 ? "s" : ""} you can apply for</b>`);
      fresh.slice(0, MAX_LIST).forEach(r => lines.push(jobLine(r, today)));
      if (fresh.length > MAX_LIST) lines.push(`…and ${fresh.length - MAX_LIST} more on the website.`);
    }
    if (remind.length) {
      if (lines.length) lines.push("");
      lines.push("⏰ <b>Last date soon</b>");
      remind.slice(0, MAX_LIST).forEach(r => lines.push(jobLine(r, today)));
    }
    const result = await tg.send(cid, lines.join("\n") + footer(site));
    if (result === "gone") { await kv.delete(subKey(cid)); gone.push(cid); continue; }
    if (result === "ok") {
      st.sent++;
      fresh.forEach(r => { notified[r.link] = { ...(notified[r.link] || {}), new: today }; });
      remind.forEach(r => { notified[r.link] = { ...(notified[r.link] || {}), remind: today }; });
    }
    sub.notified = notified;
    await kv.put(subKey(cid), JSON.stringify(sub));
  }
  if (gone.length) { await removeFromIndex(kv, gone); st.removed += gone.length; }
  st.pos += slice.length - gone.length;
  await kv.put("alerts:state", JSON.stringify(st));
  return st;
}

// ---------------------------------------------------------------- import (one-time move from bot_state.json)
export async function importSubscribers(kv, subscribers) {
  let n = 0;
  for (const [cid, sub] of Object.entries(subscribers || {})) {
    if (!sub || !sub.profile) continue;
    await kv.put(subKey(cid), JSON.stringify(sub));
    await addToIndex(kv, String(cid));
    n++;
  }
  return n;
}

export async function countSubscribers(kv) { return (await getIndex(kv)).length; }
