"""Generate every app icon the site ships — favicon, PWA, iOS, Google Search.

Why this file exists
--------------------
The site used to advertise its icon as an inline SVG *data URI* holding the 🎯
emoji:

    <link rel="icon" href="data:image/svg+xml,<svg …><text>🎯</text></svg>">

That works in a browser tab and nowhere else: a data URI has no URL, so

  * Chrome/Edge cannot use it for an installed app — manifest icons must be
    real, fetchable files, and installability needs a 192px and a 512px PNG,
  * iOS has nothing to put on the home screen (apple-touch-icon),
  * Google Search ignores it — its favicon must be a crawlable URL, square and
    at a multiple of 48px, referenced by <link rel="icon">.

This module renders the real files from one shared geometry, so every surface —
tab strip, taskbar, home screen, Android launcher, search result — shows the
same target mark, drawn from the app's own palette:

    background #0a0d13 (the app's theme-color) with a warm radial glow,
    outer ring #ff7a1a (accent), middle ring #e8edf5, bullseye #ff7a1a.

Everything is supersampled (drawn at 8x, downscaled with LANCZOS) and purely
deterministic — no randomness, no font, no network — so running the script on
any machine reproduces the committed bytes:

    python3 tools/make_icons.py          # writes into public/

Outputs (all committed, all referenced from public/index.html):
    public/icon.svg                  vector, "any" (modern browsers, crisp at any size)
    public/favicon.ico               16 + 32 + 48, PNG-compressed entries
    public/favicon-96x96.png         96 (multiple of 48) — what Google Search reads
    public/icon-192.png              192  "any"      — manifest
    public/icon-512.png              512  "any"      — manifest, app stores
    public/icon-maskable-192.png     192  "maskable" — Android adaptive icons
    public/icon-maskable-512.png     512  "maskable"
    public/apple-touch-icon.png      180  — iOS home screen (opaque: iOS fills
                                             transparency with black)

Needs Pillow (`pip install pillow`). Build-time only: the server never imports
it and the deployment does not need it installed.
"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "public")

# ---- palette (must match public/styles.css and public/og.png) --------------
BG = (10, 13, 19)        # #0a0d13 — theme-color, app background
ACCENT = (255, 122, 26)  # #ff7a1a — accent orange
LIGHT = (232, 237, 245)  # #e8edf5 — light ring
GLOW_MIX = 0.11          # how much accent bleeds into the radial glow

SS = 8                   # supersample factor: draw big, downscale with LANCZOS

# Ring geometry as fractions of the icon, so 16px and 512px are the same
# drawing at different resolutions:
#   (outer radius, outer stroke, middle radius ÷ outer radius, middle stroke,
#    bullseye radius ÷ outer radius)
GEO_NORMAL = (0.300, 0.044, 0.620, 0.033, 0.280)
# At 16px the middle ring turns to mush: drop it and thicken what remains.
GEO_TINY = (0.330, 0.085, 0.0, 0.0, 0.120)

# Maskable icons get cropped to whatever shape the launcher prefers (circle,
# squircle, teardrop…). Anything that matters must sit inside the central 80%
# circle, so the mark is drawn a little smaller there.
MASKABLE_SCALE = 0.80

ICO_SIZES = ((16, 16), (32, 32), (48, 48))


def geometry(size):
    """Ring geometry in pixels for an icon that ends up `size` px on screen."""
    if size < 24:
        return (size * GEO_TINY[0], size * GEO_TINY[1], 0.0, 0.0, size * GEO_TINY[4])
    r, o, mr, mo, br = GEO_NORMAL
    return (size * r, size * o, size * mr * r, size * mo, size * br * r)


def _paint_background(d, master):
    """Dark base + a warm radial glow behind the mark, like og.png.

    Drawn as a stack of filled circles, largest first: cheap, smooth, and
    identical on every machine (no gradient library, no dithering).
    """
    d.rectangle([0, 0, master, master], fill=BG)
    centre = tuple(int(round(BG[i] + (ACCENT[i] - BG[i]) * GLOW_MIX)) for i in range(3))
    c = master / 2.0
    steps = 90
    for i in range(steps):
        f = i / (steps - 1.0)          # 0 = outermost, 1 = centre
        r = (0.72 * c) * (1.0 - f) + master * 0.001
        col = tuple(int(round(centre[j] + (BG[j] - centre[j]) * (1.0 - f))) for j in range(3))
        d.ellipse([c - r, c - r, c + r, c + r], fill=col)


def _paint_mark(d, master, size, scale=1.0):
    """The target mark: outer ring, middle ring, bullseye.

    `master` is the supersampled canvas, `size` the final icon size: geometry
    is computed for the icon and scaled up for drawing. Strokes have a floor of
    ~1.2 final pixels so the 16px icon never fades into a grey smudge.
    """
    R, s1, r2, s2, r3 = [v * SS for v in geometry(size)]
    R, r2, r3 = R * scale, r2 * scale, r3 * scale
    c = master / 2.0
    d.ellipse([c - R, c - R, c + R, c + R], outline=ACCENT,
              width=max(int(round(s1)), int(round(1.2 * SS))))
    if r2 > 0:
        d.ellipse([c - r2, c - r2, c + r2, c + r2], outline=LIGHT,
                  width=max(int(round(s2)), int(round(1.0 * SS))))
    d.ellipse([c - r3, c - r3, c + r3, c + r3], fill=ACCENT)


def render(size, maskable=False):
    """Render one square icon at `size` px — opaque RGB, all surfaces need it."""
    master = size * SS
    img = Image.new("RGB", (master, master), BG)
    d = ImageDraw.Draw(img)
    _paint_background(d, master)
    _paint_mark(d, master, size, MASKABLE_SCALE if maskable else 1.0)
    return img.resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------------------
# Vector twin of the PNGs: same 64-unit canvas, same fractions.
# ---------------------------------------------------------------------------
def _hex(rgb):
    """(10, 13, 19) -> '#0a0d13'."""
    return "#" + "".join("%02x" % max(0, min(255, int(round(v)))) for v in rgb)


def _num(v):
    """Trim a float for the SVG (19.20 -> 19.2, 20.0 -> 20)."""
    return ("%.2f" % v).rstrip("0").rstrip(".") or "0"


def svg():
    R, s1, r2, s2, r3 = geometry(64.0)
    glow = _hex(tuple(BG[i] + (ACCENT[i] - BG[i]) * GLOW_MIX for i in range(3)))
    return "\n".join((
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64"'
        ' role="img" aria-label="JEE War Room — target mark">',
        '<title>JEE War Room</title>',
        '<defs>',
        '<radialGradient id="g" cx="50%" cy="50%" r="36%" fx="50%" fy="50%">',
        '<stop offset="0" stop-color="%s"/>' % glow,
        '<stop offset="1" stop-color="%s"/>' % _hex(BG),
        '</radialGradient>',
        '</defs>',
        '<rect width="64" height="64" fill="%s"/>' % _hex(BG),
        '<rect width="64" height="64" fill="url(#g)"/>',
        '<circle cx="32" cy="32" r="%s" fill="none" stroke="%s" stroke-width="%s"/>'
        % (_num(R), _hex(ACCENT), _num(s1)),
        '<circle cx="32" cy="32" r="%s" fill="none" stroke="%s" stroke-width="%s"/>'
        % (_num(r2), _hex(LIGHT), _num(s2)),
        '<circle cx="32" cy="32" r="%s" fill="%s"/>' % (_num(r3), _hex(ACCENT)),
        '</svg>',
        '',
    ))


def main():
    written = []

    def save(img, name):
        path = os.path.join(OUT, name)
        img.save(path, optimize=True)
        written.append((name, img.size, os.path.getsize(path)))

    # favicon.ico — 16 (tab), 32 (taskbar/bookmark), 48 (the Google Search
    # floor). PNG-compressed entries: fine on every browser and on Windows
    # Vista+, and far smaller than the legacy BMP payload.
    frames = {s[0]: render(s[0]) for s in ICO_SIZES}
    ico = os.path.join(OUT, "favicon.ico")
    frames[48].save(ico, format="ICO", sizes=list(ICO_SIZES),
                    append_images=[frames[16], frames[32]])
    written.append(("favicon.ico", (48, 48), os.path.getsize(ico)))

    # 96 = 2 x 48: the size Google Search actually reads (multiples of 48 only).
    save(render(96), "favicon-96x96.png")
    # Manifest "any" icons — 192 and 512 are the two Chrome requires to install.
    save(render(192), "icon-192.png")
    save(render(512), "icon-512.png")
    # Manifest maskable icons — full-bleed background, mark inside the safe zone.
    save(render(192, maskable=True), "icon-maskable-192.png")
    save(render(512, maskable=True), "icon-maskable-512.png")
    # iOS home screen.
    save(render(180), "apple-touch-icon.png")

    path = os.path.join(OUT, "icon.svg")
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg())
    written.append(("icon.svg", (64, 64), os.path.getsize(path)))

    for name, size, nbytes in written:
        print("wrote public/%-24s %3dx%-3d  %6.1f KB"
              % (name, size[0], size[1], nbytes / 1024.0))


if __name__ == "__main__":
    main()
