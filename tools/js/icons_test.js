/* Icons / installability test — guards the exact regression that made the app
   ship without an icon anywhere but a browser tab.

   What it checks, against a running server:
     1. index.html and tour.html link real, fetchable icon files — NOT the old
        inline SVG data URI (no URL => unusable by an installed app, iOS, or
        Google Search).
     2. Every linked file answers 200 with an image/* (or manifest) type.
     3. public/manifest.webmanifest parses, is served as application/manifest+json,
        and declares what Chrome requires to install: name, start_url, display and
        both a 192px and a 512px PNG — plus maskable variants for Android.
     4. favicon.ico really contains 16/32/48 frames (48 is Google's floor).
     5. sw.js is served as JavaScript and stays inert (has a fetch handler, never
        calls respondWith — it must not cache the live study data).

   Usage (server on :8080):
       python3 server.py &
       node tools/js/icons_test.js
       BASE=http://127.0.0.1:8080 node tools/js/icons_test.js
   No npm dependencies (Node 18+ fetch). Exit code 1 on any failure.
*/
const BASE = (process.env.BASE || "http://127.0.0.1:8080").replace(/\/$/, "");

let failures = 0;
const ok = (cond, label, detail) => {
  console.log((cond ? "  ok   " : "  FAIL ") + label + (detail === undefined ? "" : "  [" + detail + "]"));
  if (!cond) failures++;
};

async function get(path) {
  const r = await fetch(BASE + path);
  const buf = Buffer.from(await r.arrayBuffer());
  return { status: r.status, type: r.headers.get("content-type") || "", buf };
}

const ICON_LINKS = [
  ["/favicon.ico", /image\/(x-icon|vnd\.microsoft\.icon|png)/],
  ["/icon.svg", /image\/svg\+xml/],
  ["/favicon-96x96.png", /image\/png/],
  ["/apple-touch-icon.png", /image\/png/],
];
const MANIFEST_LINKS = ["/icon.svg", "/icon-192.png", "/icon-512.png",
                        "/icon-maskable-192.png", "/icon-maskable-512.png"];

/* --- 1/2. the HTML declares real icons and every one of them resolves ----- */
async function checkHtml(page) {
  const { status, buf } = await get(page);
  const html = buf.toString("utf8");
  ok(status === 200, page + " loads", status);

  const iconTags = [...html.matchAll(/<link[^>]+rel="[^"]*\bicon\b[^"]*"[^>]*>/gi)].map(m => m[0]);
  ok(iconTags.length > 0, page + " declares rel=icon links", iconTags.length);
  ok(!/rel="[^"]*icon[^"]*"[^>]*href="data:/i.test(html),
     page + " has no data-URI favicon (the bug)");
  ok(iconTags.some(t => /href="\/favicon\.ico"/.test(t)), page + " links /favicon.ico");
  ok(iconTags.some(t => /href="\/icon\.svg"/.test(t)), page + " links the vector /icon.svg");
  ok(iconTags.some(t => /href="\/favicon-96x96\.png"/.test(t)), page + " links the 96px PNG (Google size)");
  ok(/rel="apple-touch-icon"[^>]*href="\/apple-touch-icon\.png"/.test(html),
     page + " links /apple-touch-icon.png for iOS");
  ok(/rel="manifest"\s+href="\/manifest\.webmanifest"/.test(html) ||
     /href="\/manifest\.webmanifest"[^>]*rel="manifest"/.test(html),
     page + " links /manifest.webmanifest");

  for (const [path, wanted] of ICON_LINKS) {
    const r = await get(path);
    ok(r.status === 200 && wanted.test(r.type), path + " serves an image",
       r.status + " " + r.type + " " + r.buf.length + "B");
  }
  // The preload URL must match the <script src> exactly or it double-downloads.
  const pre = (html.match(/rel="preload"[^>]+href="([^"]+)"/) || [])[1];
  const src = (html.match(/<script[^>]+src="(\/app\.js[^"]*)"/) || [])[1];
  if (pre && src) ok(pre === src, page + " preload URL matches the script URL", pre + " vs " + src);
}

/* --- 3. manifest: parseable, correct type, complete for installability --- */
async function checkManifest() {
  const { status, type, buf } = await get("/manifest.webmanifest");
  ok(status === 200, "/manifest.webmanifest loads", status);
  ok(/application\/manifest\+json/.test(type), "served as application/manifest+json", type);

  let m = null;
  try { m = JSON.parse(buf.toString("utf8")); } catch (e) { m = null; }
  ok(!!m, "manifest is valid JSON");
  if (!m) return;

  ok(!!(m.name || m.short_name), "manifest declares name", m.name);
  ok(m.start_url === "/", "start_url is /", m.start_url);
  ok(["standalone", "fullscreen", "minimal-ui"].includes(m.display),
     "display is app-like (Chrome requires it)", m.display);
  ok(/^#[0-9a-f]{6}$/i.test(m.theme_color || ""), "theme_color set", m.theme_color);
  ok(/^#[0-9a-f]{6}$/i.test(m.background_color || ""), "background_color set", m.background_color);

  const icons = m.icons || [];
  const has = (size, purpose) => icons.some(i => i.sizes === size && i.type === "image/png" &&
    (!purpose || String(i.purpose || "any").split(/\s+/).includes(purpose)));
  ok(has("192x192"), "manifest has a 192x192 PNG (Chrome install requirement)");
  ok(has("512x512"), "manifest has a 512x512 PNG (Chrome install requirement)");
  ok(has("192x192", "maskable") && has("512x512", "maskable"),
     "manifest has maskable 192 + 512 for Android adaptive icons");

  for (const path of MANIFEST_LINKS) {
    const r = await get(path);
    ok(r.status === 200 && /image\//.test(r.type), "manifest icon " + path + " resolves",
       r.status + " " + r.type + " " + r.buf.length + "B");
  }
}

/* --- 4. favicon.ico really carries 16/32/48 ------------------------------ */
function icoFrames(buf) {
  if (buf.length < 6 || buf.readUInt16LE(0) !== 0 || buf.readUInt16LE(2) !== 1) return null;
  const count = buf.readUInt16LE(4);
  const out = [];
  for (let i = 0; i < count; i++) {
    const o = 6 + i * 16;
    out.push((buf[o] || 256) + "x" + (buf[o + 1] || 256));
  }
  return out;
}

async function checkIco() {
  const { status, buf } = await get("/favicon.ico");
  ok(status === 200, "/favicon.ico loads", status);
  const frames = icoFrames(buf);
  ok(frames !== null, "favicon.ico has a valid ICO directory");
  if (!frames) return;
  ok(frames.includes("16x16") && frames.includes("32x32"),
     "favicon.ico carries 16 and 32 (tab, bookmark, taskbar)", frames.join(", "));
  ok(frames.includes("48x48"), "favicon.ico carries 48 (Google Search floor)", frames.join(", "));
  // PNG-compressed entries: every frame starts with the PNG signature.
  const png = Buffer.from([0x89, 0x50, 0x4e, 0x47]);
  const count = buf.readUInt16LE(4);
  let allPng = true;
  for (let i = 0; i < count; i++) {
    const off = buf.readUInt32LE(6 + i * 16 + 12);
    if (!buf.subarray(off, off + 4).equals(png)) allPng = false;
  }
  ok(allPng, "every ICO frame is PNG-compressed (small, still universal)");
}

/* --- 5. service worker: present, JavaScript, and inert ------------------- */
const stripComments = src => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

async function checkSw() {
  const { status, type, buf } = await get("/sw.js");
  const js = stripComments(buf.toString("utf8"));
  ok(status === 200, "/sw.js loads", status);
  ok(/javascript/.test(type), "sw.js served as JavaScript", type);
  ok(/addEventListener\(\s*["']fetch["']/.test(js),
     "sw.js registers a fetch handler (Chrome install criteria)");
  ok(!/respondWith/.test(js),
     "sw.js never calls respondWith — it must not cache pages or /api data");
  // And the app must register it.
  const app = (await get("/app.js?v=26")).buf.toString("utf8");
  ok(/serviceWorker[^]{0,120}register\(\s*["']\/sw\.js["']/.test(app),
     "app.js registers /sw.js");
}

(async () => {
  console.log("icons_test against " + BASE);
  await checkHtml("/");
  await checkHtml("/tour.html");
  console.log("-- manifest --");
  await checkManifest();
  console.log("-- favicon.ico --");
  await checkIco();
  console.log("-- service worker --");
  await checkSw();
  console.log(failures === 0 ? "ICONS TEST PASSED" : "ICONS TEST FAILED (" + failures + ")");
  process.exit(failures === 0 ? 0 : 1);
})().catch(e => { console.log("icons_test crashed:", e.message); process.exit(1); });
