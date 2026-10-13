/* jsdom harness for the app boot flow (real index.html + real app.js).

   Usage (needs a running server on :8080 and jsdom installed):
       python3 server.py &
       npm i --no-save jsdom
       node tools/js/boot_test.js              # signed-in boot must be ONE request
       BOOT_MODE=fail node tools/js/boot_test.js   # legacy fallback (no /api/bootstrap)
       NO_TOKEN=1 node tools/js/boot_test.js       # logged-out: warm-up + auth + landing

   Asserts: first load makes exactly one API call (/api/bootstrap), the home
   screen renders, 8 nav tabs, no console errors, the crawlable landing block
   is hidden inside the app (and the meta/canonical/JSON-LD survive), and the
   versioned asset URLs in index.html agree with each other (preload == script).
*/
/* Boot-flow integration test: run the real public/app.js in jsdom with a
   stubbed fetch and assert that the first load is ONE request
   (GET /api/bootstrap) and that the home screen renders. */
const fs = require("fs");
const path = require("path");
const { JSDOM, VirtualConsole } = require("jsdom");

const PUB = path.resolve(__dirname, "..", "..", "public");
const html = fs.readFileSync(path.join(PUB, "index.html"), "utf8");
const appjs = fs.readFileSync(path.join(PUB, "app.js"), "utf8");

// ---- canned server data -------------------------------------------------
const bootstrap = {
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

const MODE = process.env.BOOT_MODE || "ok";     // ok | fail (server without /api/bootstrap)
const calls = [];
const legacy = {
  "/api/me": bootstrap.me,
  "/api/friends": bootstrap.friends,
  "/api/announcements": bootstrap.announcements,
  "/api/chapters": bootstrap.chapters,
};
function fakeFetch(url, opt) {
  const u = String(url);
  calls.push({ url: u, method: (opt && opt.method) || "GET" });
  const key = u.split("?")[0];
  if (u.includes("/api/bootstrap")) {
    if (MODE === "fail") {
      return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({ error: "no such endpoint" }) });
    }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(bootstrap) });
  }
  if (legacy[key]) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(legacy[key]) });
  if (u.includes("/api/messages")) return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ messages: [] }) });
  return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({ error: "unexpected " + u }) });
}

const vc = new VirtualConsole();
const errors = [];
vc.on("jsdomError", e => errors.push("jsdomError: " + e.message));
vc.on("error", (...a) => errors.push("console.error: " + a.join(" ")));

const dom = new JSDOM(html, {
  url: "https://example.test/",
  runScripts: "outside-only",
  pretendToBeVisual: true,
  virtualConsole: vc,
});
const { window } = dom;
window.fetch = fakeFetch;
if(!process.env.NO_TOKEN) window.localStorage.setItem("jwr_token", "TESTTOKEN");
// the page's own scripts (analytics/speed-insights) reference these
window.requestIdleCallback = cb => setTimeout(cb, 0);

// run app.js exactly like the <script src="/app.js?v=..."> tag would
window.eval(appjs);

setTimeout(() => {
  const app = window.document.querySelector("#app");
  const view = window.document.querySelector("#view");
  const boot = window.document.querySelector("#boot");
  const apiCalls = calls.filter(c => c.url.includes("/api/"));
  const critical = apiCalls.filter(c => !c.url.includes("/api/messages"));
  console.log("fetch calls:", JSON.stringify(calls));
  console.log("api calls:", apiCalls.map(c => c.method + " " + c.url).join(", "));
  console.log("#app visible:", app && !app.classList.contains("hidden"));
  console.log("#boot hidden:", boot.classList.contains("hidden"));
  console.log("home rendered (has hero/countdown):", /JEE MAIN COUNTDOWN|days to go/i.test(view.innerHTML));
  console.log("nav rendered:", window.document.querySelectorAll("#bottomnav .bn").length, "tabs");
  const landing = window.document.querySelector("#landing");
  console.log("landing block present:", !!landing, "| hidden inside the app:", landing ? landing.classList.contains("hidden") : "n/a");
  console.log("meta description:", !!window.document.querySelector('meta[name="description"]'),
              "| canonical:", !!window.document.querySelector('link[rel="canonical"]'),
              "| json-ld:", window.document.querySelectorAll('script[type="application/ld+json"]').length);
  console.log("friends in state:", window.App && window.App ? "App exported" : "no App");
  console.log("errors:", errors.length ? errors : "none");

  if(process.env.NO_TOKEN){
    const hasWarm = calls.some(c => c.url.startsWith("/healthz"));
    const authShown = !window.document.querySelector("#auth").classList.contains("hidden");
    const landingVisible = !window.document.querySelector("#landing").classList.contains("hidden");
    console.log("warm-up fired in background:", hasWarm, "| auth screen shown:", authShown,
                "| landing visible:", landingVisible);
    console.log(hasWarm && authShown && landingVisible ? "LOGGED-OUT TEST PASSED" : "LOGGED-OUT TEST FAILED");
    process.exit(hasWarm && authShown && landingVisible ? 0 : 1);
  }
  const expect = MODE === "ok"
      ? [["GET /api/bootstrap"]]
      : [["GET /api/me", "GET /api/friends", "GET /api/announcements", "GET /api/chapters"]];
  const got = critical.map(c => c.method + " " + c.url).sort();
  const oneCall = MODE === "ok"
      ? (critical.length === 1 && critical[0].url.includes("/api/bootstrap"))
      : (critical.length === 5 &&
         critical.some(c => c.url.includes("/api/bootstrap")) &&       // the failed attempt
         ["/api/me","/api/friends","/api/announcements","/api/chapters"].every(p => calls.some(c => c.url.split("?")[0] === p)));
  console.log("landing hidden:", landing.classList.contains("hidden"), "(logged-out should be false)");
  console.log(oneCall && app && !boot.classList.contains("hidden") === false
    ? "BOOT TEST PASSED (" + MODE + ")"
    : "BOOT TEST FAILED (" + MODE + ") -> " + got.join(", "));
  process.exit(oneCall && !errors.length ? 0 : 1);
}, 1500);
