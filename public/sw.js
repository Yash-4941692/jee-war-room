/* JEE WAR ROOM — service worker. Deliberately inert: it exists so the browser
   can install the site as an app, and it caches NOTHING.

   Why it exists
   -------------
   To offer the "Install" flow with our own icon and its own window (and to
   build a WebAPK on Android), Chromium requires a registered service worker
   with a `fetch` handler, in addition to a manifest that declares 192px and
   512px icons, a start_url and a display mode. Without one, Chrome on Android
   only creates a plain shortcut and may draw a letter tile instead of the
   target mark.

   Why it caches nothing
   ---------------------
   The app is a live study tracker: chapter marks, targets, timers and streaks
   change constantly, and the API must never be served from a cache. A
   cache-first service worker is exactly how PWAs end up showing yesterday's
   data or a stale HTML shell after a deploy. The fetch handler below has NO
   respondWith() call, so every request — page, script, icon, /api/* — goes to
   the network exactly as it did before this file existed. The server's own
   ETag/`?v=` cache policy in server.py stays the single source of truth for
   caching.

   If offline support is ever wanted, add it deliberately: cache the shell in
   `install`, serve navigations network-first with a cached shell fallback, and
   NEVER touch /api/ (that data is private and per-user). Until then, inert. */

self.addEventListener("install", function () {
  // Take over as soon as possible: this worker has no versions to migrate.
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  // Control pages that are already open, so DevTools shows the app as
  // controlled (the state installability checks and reviewers look for).
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", function () {
  // Intentionally empty — no respondWith(), so the browser performs its normal
  // network fetch for every request. This handler's existence (not its
  // behaviour) is what Chrome's install criteria require.
});
