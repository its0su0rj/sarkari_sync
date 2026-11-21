// Cloudflare Worker entry: Telegram webhook, daily alert batches, and two admin endpoints.
import { handleMessage, runAlertsBatch, importSubscribers, countSubscribers, telegram, istToday } from "./bot.mjs";

async function getJobsFrom(env) {
  // GitHub Pages copy of the job list; cached at Cloudflare's edge for 10 minutes.
  const res = await fetch(env.JOBS_URL, { cf: { cacheTtl: 600, cacheEverything: true } });
  if (!res.ok) throw new Error(`jobs.json HTTP ${res.status}`);
  return (await res.json()).jobs || [];
}

function sameSecret(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

function deps(env) {
  let jobs = null;
  return {
    kv: env.KV,
    tg: telegram(env.TELEGRAM_BOT_TOKEN),
    getJobs: async () => (jobs ??= await getJobsFrom(env)),
    site: env.SITE_URL,
    today: istToday(),
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/telegram" && request.method === "POST") {
      if (!sameSecret(request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "", env.WEBHOOK_SECRET || "")) {
        return new Response("forbidden", { status: 403 });
      }
      let update;
      try { update = await request.json(); } catch { return new Response("ok"); }
      if (update && update.message) {
        try { await handleMessage(update.message, deps(env)); }
        catch (e) { console.log("message failed", String(e)); }      // never make Telegram retry forever
      }
      return new Response("ok");
    }

    const admin = sameSecret((request.headers.get("Authorization") || "").replace(/^Bearer /, ""), env.ADMIN_TOKEN || "");
    if (url.pathname === "/admin/import" && request.method === "POST") {
      if (!admin) return new Response("forbidden", { status: 403 });
      const body = await request.json();
      const n = await importSubscribers(env.KV, body.subscribers);
      return Response.json({ imported: n });
    }
    if (url.pathname === "/admin/stats") {
      if (!admin) return new Response("forbidden", { status: 403 });
      return Response.json({ subscribers: await countSubscribers(env.KV), alerts: await env.KV.get("alerts:state", "json") });
    }
    if (url.pathname === "/") return new Response("ExamMatch bot is running.");
    return new Response("not found", { status: 404 });
  },

  // Runs every minute from 07:30 to 09:29 IST; each run sends alerts to a few subscribers.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runAlertsBatch(deps(env)).catch(e => console.log("alerts failed", String(e))));
  },
};
