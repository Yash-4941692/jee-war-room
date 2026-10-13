/* Regression test for the tour page after its 971 KB inline player script was
   extracted to /tour.js: the external script loads and defines the global
   `Tour` binding the markup's inline onclick handlers call, all six scenes
   render, and the crawlable text/metadata are still in the HTML.

   Usage (server on :8080 + jsdom):
       node tools/js/tour_test.js
       TOUR_URL=http://127.0.0.1:8080/tour.html node tools/js/tour_test.js

   Note: `const Tour = (…)` at top level is a global LEXICAL binding, not a
   window property — it must be looked up with a bare identifier
   (window.eval("typeof Tour")), because window.Tour is undefined by design.
*/
const { JSDOM, VirtualConsole } = require("jsdom");

const URL = process.env.TOUR_URL || "http://127.0.0.1:8080/tour.html";
const API = ["goTo", "togglePlay", "seek", "replay", "toggleSubtitles", "toggleMute",
             "toggleFullscreen", "prev", "next", "downloadAudio"];

const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", e => errors.push("jsdomError: " + e.message.split("\n")[0]));
vc.on("error", (...a) => errors.push("console.error: " + String(a[0]).slice(0, 120)));

JSDOM.fromURL(URL, {
  runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc,
}).then(async dom => {
  const w = dom.window;
  // jsdom has no canvas: stub just enough for the player to initialise
  w.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
    get: (t, k) => k === "measureText" ? (() => ({ width: 10 }))
      : (k === "createLinearGradient" || k === "createRadialGradient") ? (() => ({ addColorStop() {} }))
      : (typeof k === "string" ? () => {} : undefined),
    set: () => true,
  });
  await new Promise(r => setTimeout(r, 2000));

  const doc = w.document;
  const type = w.eval("typeof Tour");
  const missing = API.filter(m => w.eval(`typeof Tour.${m}`) !== "function");
  const scenes = doc.querySelectorAll(".scene").length;
  const handlers = (doc.body.innerHTML.match(/onclick="Tour\./g) || []).length;
  const h1 = doc.querySelector("h1.tour-h1");
  const summary = doc.querySelector("p.tour-summary");
  const ld = doc.querySelector('script[type="application/ld+json"]');
  const external = [...doc.querySelectorAll("script[src]")].some(s => s.getAttribute("src").startsWith("/tour.js"));

  console.log("Tour binding:", type, "| missing API:", missing.length ? missing : "none");
  console.log("scenes:", scenes, "| inline onclick=Tour.* handlers:", handlers, "| /tour.js external:", external);
  console.log("crawlable h1:", h1 ? "yes" : "MISSING", "| summary chars:", summary ? summary.textContent.trim().length : 0,
              "| JSON-LD:", !!ld);
  console.log("errors:", errors.length ? errors.slice(0, 2) : "none");

  const ok = type === "object" && !missing.length && scenes >= 6 && handlers > 0 && external
             && h1 && summary && ld && !errors.some(e => /tour\.js/.test(e));
  console.log(ok ? "TOUR TEST PASSED" : "TOUR TEST FAILED");
  process.exit(ok ? 0 : 1);
}).catch(e => { console.log("load failed:", e.message); process.exit(1); });
