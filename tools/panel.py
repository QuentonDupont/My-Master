"""The review page, served from this machine instead of a published artifact.

    make panel                      # http://localhost:8765
    python3 -m tools.panel --port 8765 --host 0.0.0.0

`tools/review_app.html` was written against `claude.use("db")`. Its whole use of
that API is two calls — `collection(name).onSnapshot(cb)` and
`doc(path).update(fields)` — so this serves the page unchanged with a small shim
injected ahead of it that implements those two against local HTTP. The UI is not
copied, forked or edited; if the page changes, this keeps working.

Where the data comes from
-------------------------
`agents.jira_leader.mobile.export` and `agents.slack_leader.mobile.export`
already write exactly the documents the page expects. This runs them on each
load and serves the result, so the panel can never drift from what the phone
flow sees.

Decisions go back through `mobile.apply`, which is the same path the batch file
uses: it records every edit in `knowledge/corrections.jsonl` and moves the
ledger. The panel has no Jira credentials and no execution path — approving here
marks a proposal APPROVED, exactly as editing the JSON by hand would.
Executing stays a separate, deliberate command.

Binding
-------
Defaults to 127.0.0.1. `--host 0.0.0.0` serves it to your phone on the same
wifi; it has no authentication, so only do that on a network you trust.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import config, ledger as ledger_mod, log

LOG = log.get("panel")

PAGE = Path(__file__).resolve().parent / "review_app.html"

#: What "Add to Home Screen" installs. standalone means it opens without Safari
#: chrome, which is the whole difference between a bookmark and an app.
MANIFEST = {
    "name": "PESD1 Triage",
    "short_name": "Triage",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#131217",
    "theme_color": "#131217",
    "icons": [
        {"src": "/icon-180.png", "sizes": "180x180", "type": "image/png"},
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png",
         "purpose": "any maskable"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "any maskable"},
    ],
}


def icon_png(size: int = 180) -> bytes:
    """A home-screen icon, drawn here rather than shipped as a binary.

    The repo has no image library and no build step, so this writes the PNG
    bytes directly: the accent ground with a white tick, which is what the app
    is for. Cached per size — iOS asks for the same one repeatedly.
    """
    import struct
    import zlib

    size = max(32, min(int(size), 512))
    cached = _ICON_CACHE.get(size)
    if cached:
        return cached

    bg = (0x13, 0x12, 0x17)          # the page's own ground
    fg = (0xFF, 0xFF, 0xFF)
    accent = (0x1C, 0x6B, 0x4A)      # the approve green

    s = size
    rows = []
    # A tick, described by two strokes in a unit square then scaled.
    for y in range(s):
        row = bytearray([0])          # PNG filter byte: none
        for x in range(s):
            u, v = x / s, y / s
            # rounded-square ground
            inset = 0.06
            in_tile = inset < u < 1 - inset and inset < v < 1 - inset
            colour = accent if in_tile else bg
            # short stroke of the tick, then the long one
            short = abs((v - 0.55) - (u - 0.32)) < 0.075 and 0.28 < u < 0.47
            long_ = abs((v - 0.62) + (u - 0.70) * 0.95) < 0.075 and 0.45 < u < 0.74
            if in_tile and (short or long_):
                colour = fg
            row.extend(colour)
        rows.append(bytes(row))

    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", s, s, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    _ICON_CACHE[size] = png
    return png


_ICON_CACHE: dict[int, bytes] = {}

#: Everything the page needs to behave as an app on a phone. review_app.html was
#: written as an artifact, where the platform supplies the viewport meta — served
#: standalone there is none, so iOS lays it out at 980px and scales down. That
#: alone is the zooming. The rest makes it installable: from Share -> Add to Home
#: Screen it opens standalone, with no Safari chrome.
MOBILE_HEAD = """
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#131217">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Triage">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon-180.png">
<style>
  /* iOS zooms the page when a control smaller than 16px takes focus, which is
     what made editing a draft jump the layout around. */
  textarea, select, input, button { font-size: 16px !important; }

  :root {
    padding-top: env(safe-area-inset-top, 0px);
    padding-bottom: env(safe-area-inset-bottom, 0px);
  }

  @media (max-width: 600px) {
    .wrap { padding-left: 14px; padding-right: 14px; }

    /* Apple's minimum comfortable touch target is 44px; approve and reject are
       the two things pressed most and the two worst to mis-tap. */
    .btn { min-height: 48px; padding: 14px 12px; }
    .actions { gap: 10px; }
    .chip { min-height: 40px; padding: 10px 14px; }

    /* The head row wrapped into an unreadable jumble on a narrow screen. */
    .head { padding: 14px 12px; }
    .keyrow { row-gap: 4px; }
    .conf { text-align: left; }

    /* Long evidence refs and permalinks used to force a sideways scroll. */
    .quote, ul.ev li, .req { overflow-wrap: anywhere; }
    .quote { font-size: 14.5px; }

    header.bar { padding-top: 10px; }
    h1 { font-size: 19px; }
    .counts { gap: 10px; flex-wrap: wrap; }
  }

  /* Standalone (added to the home screen) has no browser chrome to scroll
     against, so the sticky header needs the inset itself. */
  @media (display-mode: standalone) {
    header.bar { top: env(safe-area-inset-top, 0px); }
  }
</style>
"""

#: Swipe to decide. Delegated from the list, because the page re-renders its
#: cards on every snapshot and per-card listeners would not survive that.
#:
#: Right approves. Left does NOT reject — it opens the card and puts the cursor
#: in the reason box, because a rejection without a reason teaches the system
#: nothing, and a thumb is exactly how an empty one would get sent.
SWIPE = """
<style>
  article.card { touch-action: pan-y; }
  article.card.swiping { transition: none; }
  article.card.settling { transition: transform .18s ease; }
  .swipe-hint {
    position: absolute; top: 0; bottom: 0; display: flex; align-items: center;
    padding: 0 18px; font: 600 13px/1 var(--sans); letter-spacing: .04em;
    text-transform: uppercase; pointer-events: none; opacity: 0;
  }
  .swipe-hint.approve { left: 0;  color: var(--ok); }
  .swipe-hint.reason  { right: 0; color: var(--warn); }
  .swipe-hint.armed { font-size: 15px; letter-spacing: .06em; }
  article.card.armed { box-shadow: inset 0 0 0 2px currentColor; }
  .list { position: relative; }
  article.card { position: relative; background-clip: padding-box; }
  @media (prefers-reduced-motion: reduce) {
    article.card.settling { transition: none; }
  }
</style>
<script>
(function () {
  if (!("ontouchstart" in window)) return;   // pointer devices have buttons

  // Relative to the card, not a fixed number: 96px was a quarter of a wide
  // card and most of a narrow one, so "far enough" moved with the screen.
  function threshold(el) {
    return Math.max(64, Math.min(110, el.offsetWidth * 0.26));
  }
  var SLOP = 12;           // px before we decide horizontal vs vertical
  var card = null, x0 = 0, y0 = 0, dx = 0, locked = null, hints = null;
  var swallowClick = false;

  function decided(el) { return el.getAttribute("data-decided") === "true"; }

  function addHints(el) {
    var wrap = document.createElement("div");
    wrap.innerHTML =
      '<div class="swipe-hint approve">Approve</div>' +
      '<div class="swipe-hint reason">Reason</div>';
    while (wrap.firstChild) el.appendChild(wrap.firstChild);
    return el.querySelectorAll(".swipe-hint");
  }

  document.addEventListener("touchstart", function (e) {
    var el = e.target.closest && e.target.closest("article.card");
    if (!el || decided(el)) return;
    // The card header is itself a <button data-toggle>, and it is most of a
    // collapsed card — bailing on every button meant bailing on every swipe.
    // Only the real controls opt out.
    if (e.target.closest(".btn, .chip, .variant, textarea, select, a")) return;
    card = el; x0 = e.touches[0].clientX; y0 = e.touches[0].clientY;
    dx = 0; locked = null;
    hints = addHints(el);
  }, {passive: true});

  document.addEventListener("touchmove", function (e) {
    if (!card) return;
    var t = e.touches[0];
    var ddx = t.clientX - x0, ddy = t.clientY - y0;
    if (locked === null) {
      if (Math.abs(ddx) < SLOP && Math.abs(ddy) < SLOP) return;
      locked = Math.abs(ddx) > Math.abs(ddy) ? "x" : "y";
      if (locked === "x") card.classList.add("swiping");
    }
    if (locked !== "x") return;
    e.preventDefault();                       // we own the gesture now
    dx = ddx;
    card.style.transform = "translateX(" + dx + "px)";
    var limit = threshold(card);
    var progress = Math.min(Math.abs(dx) / limit, 1);
    var armed = Math.abs(dx) >= limit;
    if (hints) {
      hints[0].style.opacity = dx > 0 ? progress : 0;
      hints[1].style.opacity = dx < 0 ? progress : 0;
      // Solid and larger once it will actually fire, so the release is not a
      // guess — this is the feedback that was missing.
      hints[0].classList.toggle("armed", armed && dx > 0);
      hints[1].classList.toggle("armed", armed && dx < 0);
    }
    card.classList.toggle("armed", armed);
  }, {passive: false});

  function reset() {
    if (!card) return;
    var el = card;
    el.classList.remove("swiping");
    el.classList.add("settling");
    el.style.transform = "";
    setTimeout(function () {
      el.classList.remove("settling");
      el.querySelectorAll(".swipe-hint").forEach(function (h) { h.remove(); });
    }, 200);
    card = null; hints = null; locked = null;
  }

  document.addEventListener("touchend", function () {
    if (!card || locked !== "x") { reset(); return; }
    var el = card, travelled = dx, limit = threshold(el);
    swallowClick = Math.abs(travelled) > SLOP;
    if (travelled > limit) {
      // The buttons live inside .body, which is hidden while the card is
      // collapsed. Open it first: a click on a button in a hidden container
      // dispatches, but nothing confirms it to the person who swiped.
      var body = el.querySelector(".body");
      if (body && body.hidden) {
        var toggle = el.querySelector("[data-toggle]");
        if (toggle) toggle.click();
      }
      var approve = el.querySelector('[data-act="approve"]');
      if (approve) { approve.click(); }
    } else if (travelled < -limit) {
      // Open the card and ask for the reason rather than rejecting outright.
      var body = el.querySelector(".body");
      if (body && body.hidden) {
        var head = el.querySelector("[data-toggle]");
        if (head) head.click();
      }
      setTimeout(function () {
        var note = document.querySelector("#" + CSS.escape("note-" +
          (el.id || "").replace(/^card-/, "")))
          || el.querySelector("[data-note]");
        if (note) { note.focus({preventScroll: false}); }
      }, 60);
    }
    reset();
  }, {passive: true});

  document.addEventListener("touchcancel", reset, {passive: true});

  // A swipe that began on the header would otherwise finish as a tap and
  // toggle the card open or shut. Swallow that one click.
  document.addEventListener("click", function (e) {
    if (!swallowClick) return;
    swallowClick = false;
    if (e.target.closest("[data-toggle]")) {
      e.preventDefault();
      e.stopPropagation();
    }
  }, true);
})();
</script>
"""

#: implements the two db calls review_app.html makes, against this server
SHIM = """
<script>
window.claude = window.claude || {};
window.claude.use = async function (name) {
  if (name !== "db") return null;
  const load = async () => (await fetch("/api/collections")).text();
  const subs = [];
  let cache = {};
  let lastRaw = null;

  // The page re-renders by replacing innerHTML, and unsaved text lives only on
  // the JavaScript objects a snapshot replaces. So a snapshot delivered while
  // someone is typing throws away what they typed and the focus with it — the
  // card appears to close mid-sentence. Two guards, both necessary:
  const typing = () => {
    const el = document.activeElement;
    return !!el && /^(TEXTAREA|INPUT|SELECT)$/.test(el.tagName);
  };

  const push = async (force) => {
    // 1. never interrupt someone mid-edit
    if (!force && typing()) return;
    let raw;
    try { raw = await load(); } catch (e) { return; }
    // 2. and say nothing at all when nothing actually changed, which is the
    //    usual case on a 4s poll
    if (!force && raw === lastRaw) return;
    if (!force && typing()) return;        // focus may have moved while we waited
    lastRaw = raw;
    try { cache = JSON.parse(raw); } catch (e) { return; }
    for (const [coll, fn] of subs) {
      const docs = (cache[coll] || []).map(d => ({
        id: d.id, exists: true, data: () => d.data,
        metadata: {fromCache: false, hasPendingWrites: false},
      }));
      fn({docs, size: docs.length, empty: !docs.length,
          docChanges: () => [], metadata: {fromCache: false}});
    }
  };
  setInterval(() => push(false), 4000);
  setTimeout(() => push(true), 0);

  // Phones suspend timers the moment the tab isn't the one on screen — locked,
  // backgrounded, or (worse, on iOS home-screen PWAs) frozen outright. The 4s
  // poll above just stops firing, so whatever was current when it went away is
  // what you see when you come back, until the next tick happens to land.
  // Force an immediate catch-up on every signal that the page is back in
  // front of someone, rather than waiting on the interval to notice.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") push(true);
  });
  window.addEventListener("pageshow", () => push(true));   // bfcache restores
  window.addEventListener("focus", () => push(true));       // desktop/other
  return {
    collection: (coll) => ({
      onSnapshot: (next, _err) => { subs.push([coll, next]); push(); return () => {}; },
    }),
    doc: (path) => ({
      update: async (fields) => {
        const res = await fetch("/api/doc", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({path, fields}),
        });
        if (!res.ok) throw Object.assign(new Error("write failed"),
                                         {code: "unavailable"});
        lastRaw = null;          // a decision always changes something
        await push(true);
      },
    }),
  };
};
</script>
"""


def _documents() -> dict:
    """Every collection the page reads, freshly exported."""
    from agents.jira_leader import mobile as jira_mobile
    from agents.slack_leader import mobile as slack_mobile

    out: dict[str, list] = {"proposals": [], "slack_proposals": [],
                            "recommendations": [], "board_items": []}
    tmp = Path(tempfile.mkdtemp(prefix="panel-"))
    try:
        try:
            jira_mobile.export(tmp)
        except Exception as exc:
            LOG.warn("panel.jira_export_failed", error=str(exc)[:160])
        try:
            slack_mobile.export(tmp / "slack")
        except Exception as exc:
            LOG.warn("panel.slack_export_failed", error=str(exc)[:160])

        def collect(folder: Path, key: str) -> None:
            if not folder.is_dir():
                return
            for path in sorted(folder.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                out[key].append({"id": path.stem, "data": data})

        collect(tmp, "proposals")
        collect(tmp / "slack", "slack_proposals")
        collect(tmp / "recommendations", "recommendations")
        collect(tmp / "boards", "board_items")
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


#: past this, "approved but not executed" is worth a flag rather than assumed
#: to be mid-review. Execution stays a deliberate manual command by design —
#: this is a reminder, not evidence of a bug.
STUCK_APPROVAL_HOURS = 6


def _age_minutes(iso_ts: str) -> float | None:
    try:
        then = dt.datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(dt.timezone.utc) - then).total_seconds() / 60
    except (ValueError, AttributeError):
        return None


def _heartbeat_age(name: str) -> float | None:
    path = config.LOG_DIR / f".heartbeat_{name}"
    if not path.exists():
        return None
    return _age_minutes(path.read_text(encoding="utf-8").strip())


def _health() -> dict:
    """Everything 'is the flow actually working' that used to need a CLI.

    Every value here is defensive — a health check that can itself fail is
    worse than no health check, so each section is wrapped and degrades to
    null/empty rather than 500ing the whole page.
    """
    out: dict = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(
        timespec="seconds")}

    try:
        out["board_tick_minutes_ago"] = _heartbeat_age("board_tick")
    except Exception:
        out["board_tick_minutes_ago"] = None
    try:
        out["corpus_refresh_minutes_ago"] = _heartbeat_age("corpus_refresh")
    except Exception:
        out["corpus_refresh_minutes_ago"] = None

    try:
        markers = sorted(p.name for p in
                         (config.LOG_DIR / ".markers").glob("*")) \
            if (config.LOG_DIR / ".markers").is_dir() else []
        out["active_alerts"] = markers
    except Exception:
        out["active_alerts"] = []

    try:
        with ledger_mod.Ledger() as led:
            approved = led.by_state(ledger_mod.APPROVED, ledger_mod.CORRECTED)
        stuck = []
        for row in approved:
            age = _age_minutes(row["last_processed"])
            if age is not None and age > STUCK_APPROVAL_HOURS * 60:
                stuck.append({"ticket": row["ticket_key"],
                             "hours_waiting": round(age / 60, 1)})
        out["approved_not_executed"] = len(approved)
        out["approved_and_aging"] = stuck
    except Exception:
        out["approved_not_executed"] = None
        out["approved_and_aging"] = []

    try:
        from core import corrections as corrections_mod

        review_files = sorted(config.REVIEW_DIR.glob("rule_proposals_*.md"))
        last_review = (review_files[-1].stat().st_mtime if review_files else 0)
        since = dt.datetime.fromtimestamp(last_review, dt.timezone.utc)
        unreviewed = [c for c in corrections_mod.load()
                     if dt.datetime.fromisoformat(c["ts"]) > since]
        out["corrections_since_last_rule_review"] = len(unreviewed)
        out["last_rule_review"] = (review_files[-1].name if review_files
                                   else None)
    except Exception:
        out["corrections_since_last_rule_review"] = None
        out["last_rule_review"] = None

    try:
        from agents.chief_of_staff import brief as brief_mod

        errs = brief_mod.errors(days=1)
        out["errors_24h"] = errs.get("total_errors")
        out["warn_by_logger_24h"] = errs.get("warn_by_logger")
    except Exception:
        out["errors_24h"] = None
        out["warn_by_logger_24h"] = {}

    # Approved rules that the currently-active analyst cannot actually apply —
    # found live: 2 rules approved into rules.md while ANTHROPIC_API_KEY was
    # unset, so every worker ran on HeuristicAnalyst, which ignores rules_text
    # entirely. Not silently fixed (see analysis.HeuristicAnalyst's docstring);
    # surfaced here instead.
    try:
        from agents.jira_leader import analysis as analysis_mod

        approved = analysis_mod.count_approved_rules()
        out["approved_rules"] = approved
        out["rules_active_but_unused"] = (
            approved > 0 and not analysis_mod.ClaudeAnalyst.available())
    except Exception:
        out["approved_rules"] = None
        out["rules_active_but_unused"] = False

    return out


def _health_html(data: dict) -> str:
    """A small, standalone page — deliberately NOT part of review_app.html,
    which is kept byte-identical to the Artifact-hosted version so it never
    forks (see the module docstring). This is panel-only."""
    def row(label: str, value) -> str:
        return f'<div class="r"><span class="k">{label}</span><span class="v">{value}</span></div>'

    def fmt_age(minutes: float | None) -> str:
        if minutes is None:
            return "never recorded"
        if minutes < 60:
            return f"{minutes:.0f}m ago"
        return f"{minutes / 60:.1f}h ago"

    ok = (not data.get("active_alerts")
          and (data.get("board_tick_minutes_ago") or 9999) < 15
          and not data.get("approved_and_aging")
          and not data.get("rules_active_but_unused"))
    banner = ("all green" if ok else "needs a look")
    banner_class = "ok" if ok else "warn"

    alerts_html = "".join(
        f'<div class="r"><span class="k">⚠ {a}</span></div>'
        for a in data.get("active_alerts", []))
    aging_html = "".join(
        f'<div class="r"><span class="k">{a["ticket"]}</span>'
        f'<span class="v">{a["hours_waiting"]}h waiting</span></div>'
        for a in data.get("approved_and_aging", []))
    warn_html = "".join(
        f'<div class="r"><span class="k">{name}</span><span class="v">{count}</span></div>'
        for name, count in list((data.get("warn_by_logger_24h") or {}).items())[:5])

    return f"""<!doctype html>
<title>Flow health</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ background:#131217; color:#f1eff4; font:15px -apple-system,sans-serif;
         margin:0; padding:20px 16px 40px; }}
  h1 {{ font-size:17px; margin:0 0 4px; }}
  .sub {{ color:#928d9b; font-size:12px; margin-bottom:16px; }}
  .banner {{ padding:10px 14px; border-radius:8px; font-weight:600; margin-bottom:16px; }}
  .banner.ok {{ background:#14291f; color:#6cc79b; }}
  .banner.warn {{ background:#2f1817; color:#f08a83; }}
  .card {{ background:#1b1a20; border:1px solid #2c2a33; border-radius:10px;
          padding:12px 14px; margin-bottom:10px; }}
  .card h2 {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em;
             color:#928d9b; margin:0 0 8px; }}
  .r {{ display:flex; justify-content:space-between; padding:3px 0; font-size:13.5px; }}
  .k {{ color:#a39eae; }}
  .v {{ font-variant-numeric:tabular-nums; }}
  a {{ color:#ff7096; }}
</style>
<h1>Flow health</h1>
<div class="sub">generated {data['generated']} · <a href="/">back to the queue</a></div>
<div class="banner {banner_class}">{banner}</div>
<div class="card"><h2>Scheduled jobs</h2>
  {row("board tick", fmt_age(data.get('board_tick_minutes_ago')))}
  {row("corpus refresh", fmt_age(data.get('corpus_refresh_minutes_ago')))}
</div>
{f'<div class="card"><h2>Active alerts</h2>{alerts_html}</div>' if data.get("active_alerts") else ""}
<div class="card"><h2>Approved, not yet executed</h2>
  {row("total", data.get('approved_not_executed'))}
  {aging_html or '<div class="r"><span class="k">none waiting long</span></div>'}
</div>
<div class="card"><h2>Learning loop</h2>
  {row("corrections since last rule review", data.get('corrections_since_last_rule_review'))}
  {row("last review", data.get('last_rule_review') or "never")}
  {row("approved rules", data.get('approved_rules'))}
  {f'<div class="r"><span class="k">⚠ approved rules are not being applied — the active analyst is heuristic, not Claude</span></div>' if data.get("rules_active_but_unused") else ""}
</div>
<div class="card"><h2>Errors / warnings (24h)</h2>
  {row("error log entries", data.get('errors_24h'))}
  {warn_html or '<div class="r"><span class="k">no warnings</span></div>'}
</div>
"""


def _decide(path: str, fields: dict) -> dict:
    """Route one edit from the page to the store it belongs to."""
    collection, _, doc_id = path.partition("/")
    decision = {"proposal_id": doc_id,
                "decision": fields.get("decision", "pending"),
                "note": fields.get("note", ""),
                "edited_reply": fields.get("edited_reply", ""),
                "assignee_override": fields.get("assignee_override")}

    if collection == "proposals":
        from agents.jira_leader import mobile as jira_mobile
        return jira_mobile.apply([decision])
    if collection == "slack_proposals":
        from agents.slack_leader import mobile as slack_mobile
        return slack_mobile.apply([decision])
    if collection == "recommendations":
        from core import recommendations
        state = fields.get("state", "open")
        recommendations.resolve(doc_id, state)
        return {"recommendation": doc_id, "state": state}
    raise ValueError(f"nothing writes to {collection!r}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):     # quiet; we have our own logger
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            html = PAGE.read_text(encoding="utf-8")
            self._send(200, (MOBILE_HEAD + SWIPE + SHIM + html).encode("utf-8"),
                       "text/html; charset=utf-8")
            return
        if self.path == "/manifest.webmanifest":
            self._send(200, json.dumps(MANIFEST).encode("utf-8"),
                       "application/manifest+json")
            return
        if self.path.startswith("/icon-"):
            try:
                size = int(self.path.split("-")[1].split(".")[0])
            except (IndexError, ValueError):
                size = 180
            self._send(200, icon_png(size), "image/png")
            return
        if self.path == "/api/collections":
            self._send(200, json.dumps(_documents()).encode("utf-8"),
                       "application/json")
            return
        if self.path == "/health":
            self._send(200, _health_html(_health()).encode("utf-8"),
                       "text/html; charset=utf-8")
            return
        if self.path == "/api/health":
            self._send(200, json.dumps(_health()).encode("utf-8"),
                       "application/json")
            return
        self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        if self.path != "/api/doc":
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            result = _decide(payload["path"], payload.get("fields") or {})
        except Exception as exc:
            LOG.warn("panel.write_failed", error=str(exc)[:160])
            self._send(400, json.dumps({"error": str(exc)}).encode(),
                       "application/json")
            return
        LOG.info("panel.decided", path=payload["path"])
        self._send(200, json.dumps(result).encode("utf-8"), "application/json")


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    where = "localhost" if host == "127.0.0.1" else host
    print(f"review panel on http://{where}:{port}  (ctrl-c to stop)")
    if host != "127.0.0.1":
        print("  reachable from your network — there is no authentication")
    print(f"  decisions land in {config.REVIEW_DIR} and "
          f"{config.CORRECTIONS.name}; nothing is written to Jira from here")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m tools.panel")
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 to reach it from your phone on the same wifi")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
