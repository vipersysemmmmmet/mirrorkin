"""Her trading, always on: the strategy lab (every 2 minutes), her hourly trading session, and a
live trading log page at http://127.0.0.1:8765, running in the background whether or not the
Mirrorkin app is open.

  python -m tiny.trading_service install    keep it running (starts now and at every login)
  python -m tiny.trading_service uninstall  stop it for good
  python -m tiny.trading_service run        run it in this terminal

Paper trading and the lab never touch money; real-money swaps only happen through
tiny/spending.py, inside the limits in profile.json.
"""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import life, memelab, spending, trading

PORT = 8765
URL = f"http://127.0.0.1:{PORT}/"
LABEL = "com.mirrorkin.trading"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
ROOT = Path(__file__).resolve().parent.parent
SESSION_CHECK_S = 300


def running() -> bool:
    """Is the always-on service (with its lab) up? The app then leaves trading to it."""
    try:
        with urllib.request.urlopen(URL + "ping", timeout=1) as r:
            return r.read() == b"service"
    except OSError:
        return False


# ---- what the page shows ----

def snapshot() -> dict:
    s = trading.state()
    lab = memelab.load() if memelab.STATE.exists() else {}
    real = spending.status()
    return {
        "now": datetime.now().isoformat(timespec="seconds"),
        "always_on": _is_service,
        "portfolio": {"scorecard": trading.scorecard(s), "cash": round(s["cash"], 2),
                      "holdings": {c: round(s.get("cost", {}).get(c, 0), 2) for c in s["positions"]},
                      "history": [{"date": h["date"], "value": h["value"], "btc_hold": h["btc_hold"]} for h in s["history"]],
                      "latest_view": s["history"][-1]["view"] if s["history"] else None,
                      "trades": list(reversed(s["trades"][-25:]))},
        "lab": {"generation": lab.get("generation"), "checks": lab.get("ticks"), "last_check": lab.get("last_tick"),
                "tokens_watched": lab.get("tokens_seen"), "evolved_at": lab.get("evolved_at"),
                "every_s": memelab.TICK_S, "evolve_every_h": memelab.EVOLVE_EVERY.total_seconds() / 3600,
                "open_positions": sum(len(x["pos"]) for x in lab.get("strategies", [])),
                "leaderboard": (memelab.leaderboard(10) or {}).get("top", []),
                "trades": memelab.recent_trades(120)},
        "lessons": memelab.lessons(),
        "watchlist": memelab.watchlist(10),
        "real_money": {"status": real, "ledger": list(reversed(spending._ledger()[-30:]))},
    }


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mirrorkin Trading Log</title>
<style>
:root { --bg:#f6f4ee; --card:#fffdf8; --ink:#1d1d1f; --muted:#6b6a63; --line:#e6e2d6; --up:#1f8a4c; --down:#c0392b; --accent:#b6e34b; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#141412; --card:#1d1c19; --ink:#f1efe8; --muted:#a19f96; --line:#2f2d28; --up:#5fd08a; --down:#ff7b6b; } }
* { box-sizing:border-box } body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 -apple-system, system-ui, sans-serif }
main { max-width:1180px; margin:0 auto; padding:24px 16px 60px } h1 { font-size:24px; margin:0 } h2 { font-size:15px; margin:0 0 10px }
.top { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:18px }
.pill { display:inline-flex; gap:6px; align-items:center; padding:5px 11px; border-radius:99px; background:var(--card); border:1px solid var(--line); font-size:12px; font-weight:600 }
.dot { width:8px; height:8px; border-radius:50%; background:var(--up) } .dot.stale { background:var(--down) }
.grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:12px; margin-bottom:12px }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; min-width:0 }
.big { font-size:26px; font-weight:700; font-variant-numeric:tabular-nums } .muted { color:var(--muted); font-size:12px }
.up { color:var(--up) } .down { color:var(--down) }
.two { display:grid; grid-template-columns:1.4fr 1fr; gap:12px; margin-bottom:12px } @media (max-width:860px) { .two { grid-template-columns:1fr } }
.scroll { max-height:460px; overflow:auto } table { width:100%; border-collapse:collapse; font-size:12.5px; font-variant-numeric:tabular-nums }
th { text-align:left; color:var(--muted); font-weight:600; position:sticky; top:0; background:var(--card); padding:6px 6px; border-bottom:1px solid var(--line) }
td { padding:6px; border-bottom:1px solid var(--line); vertical-align:top } a { color:inherit }
.tag { font-size:11px; font-weight:700; padding:2px 7px; border-radius:6px } .buy { background:#dff3e6; color:#14592f } .sell { background:#f8e1dc; color:#7a2318 }
@media (prefers-color-scheme: dark) { .buy { background:#1d3a28; color:#8fe0ad } .sell { background:#432019; color:#ffb3a7 } }
.view { font-size:13.5px } svg { width:100%; height:180px } .empty { color:var(--muted); padding:14px 0 }
</style></head><body><main>
<div class="top"><div><h1>Mirrorkin trading log</h1><div class="muted">Practice money unless real-money trading is switched on. Refreshes every 20 seconds.</div></div>
<span class="pill" id="status"><span class="dot"></span>loading…</span></div>
<div class="grid" id="cards"></div>
<div class="two"><div class="card"><h2>Hourly portfolio vs just holding Bitcoin</h2><div id="chart"></div><div class="view" id="view"></div></div>
<div class="card"><h2>Real money</h2><div id="real"></div></div></div>
<div class="two"><div class="card"><h2>Lab trades (live, every 2 minutes)</h2><div class="scroll" id="labtrades"></div></div>
<div class="card"><h2>Strategy leaderboard</h2><div class="scroll" id="board"></div></div></div>
<div class="two"><div class="card"><h2>Safe fresh coins (passed the rug check)</h2><div class="scroll" id="watch"></div></div>
<div class="card"><h2>Lessons from coins she caught early</h2><div id="lessons"></div></div></div>
<div class="card"><h2>Her hourly paper trades</h2><div class="scroll" id="ptrades"></div></div>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const pct = v => v == null ? "–" : `<span class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "+" : ""}${Number(v).toFixed(1)}%</span>`;
const ago = t => { if (!t) return "never"; const s = (Date.now() - new Date(t)) / 1000; return s < 90 ? `${Math.round(s)}s ago` : s < 5400 ? `${Math.round(s/60)} min ago` : `${Math.round(s/3600)}h ago`; };
const time = t => t ? new Date(t).toLocaleString([], {month:"short", day:"numeric", hour:"2-digit", minute:"2-digit"}) : "";
const link = tok => { const [c, a] = tok.split(":"); return `https://dexscreener.com/${c}/${a}`; };
const table = (head, rows) => rows.length ? `<table><tr>${head.map(h => `<th>${h}</th>`).join("")}</tr>${rows.join("")}</table>` : `<div class="empty">Nothing yet.</div>`;
function chart(h) {
  if (h.length < 2) return '<div class="empty">The chart starts after two sessions.</div>';
  const vals = h.flatMap(x => [x.value, x.btc_hold]), lo = Math.min(...vals), hi = Math.max(...vals), pad = (hi - lo) * .1 || 1;
  const X = i => i / (h.length - 1) * 600, Y = v => 170 - (v - lo + pad) / (hi - lo + 2 * pad) * 160;
  const line = k => h.map((x, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(x[k]).toFixed(1)}`).join("");
  return `<svg viewBox="0 0 600 180" preserveAspectRatio="none"><path d="${line("btc_hold")}" fill="none" stroke="currentColor" opacity=".35" stroke-width="2" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/><path d="${line("value")}" fill="none" stroke="var(--up)" stroke-width="2.5" vector-effect="non-scaling-stroke"/></svg><div class="muted">Green: her portfolio · dashed: holding Bitcoin · ${time(h[0].date)} → ${time(h[h.length-1].date)}</div>`;
}
async function load() {
  let d; try { d = await (await fetch("data", {cache: "no-store"})).json(); } catch (e) { $("status").innerHTML = '<span class="dot stale"></span>not reachable'; return; }
  const lab = d.lab, sc = d.portfolio.scorecard || {}, fresh = lab.last_check && (Date.now() - new Date(lab.last_check)) < 6 * 60 * 1000;
  $("status").innerHTML = `<span class="dot ${fresh ? "" : "stale"}"></span>${fresh ? "Trading now" : "Lab paused"} · last check ${ago(lab.last_check)}${d.always_on ? " · always on" : " · while the app is open"}`;
  const best = lab.leaderboard.find(x => x.trades >= 3);  // a "best" needs a few closed trades behind it
  $("cards").innerHTML = [
    ["Hourly portfolio", sc.return_pct != null ? pct(sc.return_pct) : "–", sc.sessions ? `${sc.sessions} sessions · Bitcoin ${sc.btc_return_pct >= 0 ? "+" : ""}${(sc.btc_return_pct || 0).toFixed(1)}% · ${Math.round(sc.cash_pct || 0)}% cash` : "No session yet"],
    ["Best lab strategy", best ? pct(best.return_pct) : "–", best ? `${best.trades} closed trades · win ${best.win_rate ?? "–"}%` : "Collecting: shown after 3 closed trades"],
    ["Strategy lab", `Gen ${lab.generation ?? "–"}`, `${lab.checks ?? 0} checks · ${lab.tokens_watched ?? 0} coins watched · ${lab.open_positions} open`],
    ["Coins followed", d.lessons.coins_followed ?? 0, d.lessons.all_coins ? `${d.lessons.all_coins.runner_pct}% ran · ${d.lessons.all_coins.rug_pct}% rugged` : "Lessons after 20 finished coins"],
  ].map(([t, b, m]) => `<div class="card"><div class="muted">${t}</div><div class="big">${b}</div><div class="muted">${m}</div></div>`).join("");
  $("chart").innerHTML = chart(d.portfolio.history);
  $("view").innerHTML = d.portfolio.latest_view ? `<p><b>Her latest read:</b> ${esc(d.portfolio.latest_view)}</p>` : "";
  const st = d.real_money.status;
  $("real").innerHTML = !st.enabled ? '<div class="empty">Off. Practice money only.</div>' :
    `<p><b>${st.dry_run ? "Dry run (checked and quoted, nothing sent)" : "LIVE"}</b> · from ${esc(st.trades_from || "")}<br><span class="muted">Auto up to $${st.auto_per_trade_usd} · max $${st.max_per_trade_usd}/trade · $${st.spent_today_usd} of $${st.daily_usd} today</span></p>` +
    table(["When", "", "Coin", "$", "Result"], d.real_money.ledger.map(e => `<tr><td>${time(e.time)}</td><td><span class="tag ${e.side}">${esc(e.side)}</span></td><td><a href="${link(e.chain + ":" + e.token)}" target="_blank">${esc(e.symbol)}</a></td><td>${e.usd ?? ""}</td><td>${e.ok ? (e.tx ? "sent" : "ok (dry run)") : `<span class="down">${esc(e.error)}</span>`}</td></tr>`));
  $("labtrades").innerHTML = table(["When", "", "Coin", "Strategy", "$ / result", "Why"], lab.trades.map(t => `<tr><td>${time(t.time)}</td><td><span class="tag ${t.side}">${t.side}</span></td><td><a href="${link(t.token)}" target="_blank">${esc(t.symbol)}</a></td><td>${t.strategy}</td><td>${t.side === "sell" ? pct(t.ret_pct) : "$" + (t.usd ?? "")}</td><td class="muted">${esc(t.reason)}${t.risk != null ? ` · risk ${t.risk}` : ""}</td></tr>`));
  $("board").innerHTML = table(["#", "Strategy", "Return", "Trades", "Win", "Worst dip"], lab.leaderboard.map((s, i) => `<tr><td>${i + 1}</td><td>${s.id}<div class="muted">${esc(s.origin)}</div></td><td>${pct(s.return_pct)}</td><td>${s.trades}</td><td>${s.win_rate ?? "–"}%</td><td>${pct(s.worst_dd_pct)}</td></tr>`));
  $("watch").innerHTML = table(["Coin", "Found", "Since", "Peak", "Risk", "Top wallets", "Buyers"], d.watchlist.map(w => `<tr><td><a href="${link(w.token)}" target="_blank">${esc(w.symbol)}</a></td><td>${time(w.found)}</td><td>${pct(w.since_found_pct)}</td><td>${pct(w.peak_pct)}</td><td>${w.risk}</td><td>${w.top10_pct}%</td><td>${Math.round(w.buy_ratio * 100)}%</td></tr>`));
  const L = d.lessons;
  $("lessons").innerHTML = L.signals ? `<p class="muted">${L.coins_followed} coins followed for 24h: ${L.all_coins.runner_pct}% ran +50%, ${L.all_coins.rug_pct}% rugged.</p>` + table(["Early sign", "Coins", "Ran", "Rugged"], L.signals.map(r => `<tr><td>${esc(r.signal)}</td><td>${r.coins}</td><td class="up">${r.runner_pct}%</td><td class="down">${r.rug_pct}%</td></tr>`)) : `<div class="empty">${esc(L.note || "")} (${L.coins_followed || 0} so far)</div>`;
  $("ptrades").innerHTML = table(["When", "", "Coin", "$", "Why"], d.portfolio.trades.map(t => `<tr><td>${time(t.time)}</td><td><span class="tag ${t.side}">${t.side}</span></td><td>${esc(t.coin)}</td><td>${t.usd}</td><td class="muted">${esc(t.why)}</td></tr>`));
}
load(); setInterval(load, 20000);
</script></main></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/ping":
            body, kind = (b"service" if _is_service else b"app"), "text/plain"
        elif path == "/data":
            body, kind = json.dumps(snapshot()).encode(), "application/json"
        elif path == "/":
            body, kind = PAGE.encode(), "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


_is_service = False
_page_started = False


def serve_page_in_background() -> None:
    """The log page from inside the app (when the always-on service isn't installed)."""
    global _page_started
    if _page_started:
        return
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
    except OSError:
        return  # something already serves it
    _page_started = True
    threading.Thread(target=server.serve_forever, daemon=True, name="trading-page").start()


def _sessions() -> None:
    while True:
        try:
            if life.profile().get("allow_trading") and trading.session_due():
                result = trading.daily()
                life.log("trading service session", "; ".join(result["results"]) or "no trades")
        except Exception as e:  # one failed session must never stop the service
            life.log("trading service session failed", str(e)[:200])
        time.sleep(SESSION_CHECK_S)


def run() -> None:
    global _is_service
    _is_service = True
    server = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
    memelab.start()
    threading.Thread(target=_sessions, daemon=True, name="sessions").start()
    print(f"Mirrorkin trading service: lab every {memelab.TICK_S}s, sessions hourly, log at {URL}", flush=True)
    server.serve_forever()


def install() -> None:
    life.DATA.mkdir(exist_ok=True)
    (life.DATA / "memelab").mkdir(exist_ok=True)
    log = life.DATA / "memelab" / "service.log"
    path = ":".join([str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"])
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>{sys.executable}</string><string>-m</string><string>tiny.trading_service</string><string>run</string></array>
  <key>WorkingDirectory</key><string>{ROOT}</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{path}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
""")
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], capture_output=True)
    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)], check=True)
    print(f"Installed. Trading runs in the background; log at {URL}")


def uninstall() -> None:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    PLIST.unlink(missing_ok=True)
    print("Stopped and removed. Trading now only runs while the app is open.")


if __name__ == "__main__":
    {"run": run, "install": install, "uninstall": uninstall}.get((sys.argv[1:] or ["run"])[0], run)()
