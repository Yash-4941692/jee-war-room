/* Submits the REAL login form over HTTP (jsdom loads index.html and app.js
   from the running server, including the inline onsubmit handler) and asserts
   the request the app makes: straight to POST /api/auth/login while the
   /healthz warm-up is still pending — i.e. the wake never blocks sign-in.

   Usage (server on :8080 + jsdom):
       node tools/js/login_form_test.js

   This test exists because a stale `oldBtn` reference in authSubmit() was only
   visible at runtime ("oldBtn is not defined" shown in the error box, zero
   login POSTs — a total login outage) and neither a syntax check nor the boot
   test would have caught it. Verified: reintroducing that line fails this test.
*/
/* Click the real login form (logged out, no token) and assert the request the
   app actually makes: straight to POST /api/auth/login, with no blocking
   /healthz warm-up in the way, and the button re-enabled afterwards.
   This test exists because a stale `oldBtn` reference in authSubmit() was only
   visible at RUNTIME ("oldBtn is not defined" -> shown in the error box), which
   no syntax check or boot test would have caught. */
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");


const calls = [];
const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", e => errors.push("jsdomError: " + e.message.split("\n")[0]));
vc.on("error", (...a) => errors.push("console.error: " + String(a[0]).slice(0, 120)));

const BOOTSTRAP = {
  me: {
    user: { id: 1, name: "perf", code: "UBVD4D", partner_id: null, role: "user",
            exam_date: "2027-01-24", avatar_color: "#22d3ee", created_at: "2026-10-01T00:00:00Z" },
    settings: { xp: {}, weights: {}, sharing: {}, cards: [{ key: "air", enabled: true }],
                activities: [], customActivities: [], metrics: [], templates: [],
                streakThreshold: 70, airEMA: 0.88, bestStreak: 3, sweepDay: "2026-10-13",
                seenAnnouncements: [] },
    global: { xp: {}, weights: {} },
    summary: { minutes: 120, questions: 40, distractionMin: 10, planned: 3, done: 2, partial: 0,
               missed: 0, execution: 67, mocks: 0, errors: 1, custom: {} },
    xp: { level: 4, title: "GRINDER", xp: 300, into: 150, span: 200, nextAt: 500 },
    streak: { current: 3, best: 3 },
    air: { air: 300000, airFormatted: "3,00,000", score: 50, trend7: -233334, yesterdayAir: 1,
           history: [{ day: "2026-10-12", score: 40, air: 400000 }, { day: "2026-10-13", score: 50, air: 300000 }],
           label: "estimate" },
  },
  friends: { connected: true, count: 1, max: 10,
             friends: [{ connected: true, uid: 2, name: "ansh", avatarColor: "#fff",
                         examDate: "2027-01-24", code: "BUD002", unread: 2, shareBlock: true,
                         summary: { minutes: 60, questions: 25, planned: 2, done: 1, partial: 0,
                                    missed: 1, execution: 50, mocks: 1, errors: 1, custom: {} },
                         streak: { current: 3, best: 3 }, xp: { level: 4, title: "STARTING LINE" },
                         week: { minutes: 200, questions: 75, planned: 9, done: 7, execution: 78 } }] },
  announcements: { announcements: [{ id: 1, body: "Welcome", read: false }] },
  chapters: { subjects: ["Physics", "Chemistry", "Mathematics"],
              chapters: { Physics: [{ id: 1, subject: "Physics", name: "Kinematics", status: "completed",
                                       hidden: 0, sort: 0, custom: 0, lectures_total: 10, lectures_done: 10 },
                                    { id: 2, subject: "Physics", name: "Optics", status: "in_progress",
                                       hidden: 0, sort: 1, custom: 0, lectures_total: 8, lectures_done: 2 }],
                          Chemistry: [], Mathematics: [] } },
};

let healthStarted = false, healthResolve, healthDone = false;
const healthPromise = new Promise(r => { healthResolve = r; });

function installMocks(window) {
  window.fetch = async (url, opt) => {
  const u = String(url);
  calls.push({ url: u, method: (opt && opt.method) || "GET" });
  if (u.startsWith("/healthz")) {                 // the wake: deliberately slow
    healthStarted = true;
    await healthPromise;
    healthDone = true;
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  }
  if (u.startsWith("/api/auth/login")) {
    return { ok: true, status: 200, json: async () => ({ token: "TOK", name: "Tester", recoveryCodes: null }) };
  }
  if (u.startsWith("/api/bootstrap")) {
    return { ok: true, status: 200, json: async () => (BOOTSTRAP) };
  }
  return { ok: true, status: 200, json: async () => ({}) };
  };
  window.confirm = () => true;
}

/* Load the page from the live server so the real <script src="/app.js?v=25">
   and the inline onsubmit="return App.authSubmit(event)" wiring are exercised
   exactly as a browser runs them. */
JSDOM.fromURL("http://127.0.0.1:8080/", {
  runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc,
  beforeParse: installMocks,
}).then(dom => {
const { window } = dom;

setTimeout(async () => {
  const doc = window.document;
  doc.querySelector("#auth-name").value = "Tester";
  doc.querySelector("#auth-pass").value = "Xk7#marble9Qz";
  const form = doc.querySelector("#auth-form");
  if(form.requestSubmit) form.requestSubmit();
  else form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));

  await new Promise(r => setTimeout(r, 900));      // login POST settles while /healthz is still stuck

  const btn = doc.querySelector("#auth-btn");
  const errBox = (doc.querySelector("#auth-err") || {}).textContent || "";
  const loginCalls = calls.filter(c => c.url.startsWith("/api/auth/login"));
  const orderedBefore = calls.findIndex(c => c.url.startsWith("/api/auth/login")) <
                        calls.findIndex(c => c.url.startsWith("/api/bootstrap"));

  console.log("fetch calls:", calls.map(c => c.method + " " + c.url).join(" | "));
  console.log("warm-up running in parallel:", healthStarted, "(still pending:", !healthDone, ")");
  console.log("login error box:", JSON.stringify(errBox));
  console.log("login button re-enabled:", !btn.disabled, "| login POSTs:", loginCalls.length,
              "| login before bootstrap:", orderedBefore);

  const ok = loginCalls.length === 1 && errBox === "" && !btn.disabled && healthStarted && orderedBefore
             && !errors.some(e => /oldBtn|is not defined/i.test(e));
  console.log(ok ? "LOGIN FORM TEST PASSED" : "LOGIN FORM TEST FAILED");
  process.exit(ok ? 0 : 1);
}, 1200);
}).catch(e => { console.log("load failed:", e.message); process.exit(1); });
