"""A third front end: a local web page, on the standard library alone.

It exists to prove the point the architecture keeps making -- the CLI, the
tkinter window and this browser page all call the same ``Router`` and share
the same ``InstructionGenerator``. Nothing about the engine changed to make a
web version possible.

    python3 web.py            then open http://127.0.0.1:8731

No frameworks, no build step, no internet: ``http.server`` serves one page and
one JSON endpoint.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from navigator import (
    InstructionGenerator,
    MetroError,
    MetroNetwork,
    Router,
)

HOST, PORT = "127.0.0.1", 8731

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shenzhen Metro Smart Navigator</title>
<style>
  :root { color-scheme: dark; --bg:#0c1016; --card:#141b24; --edge:#222d3a;
          --fg:#e8edf3; --muted:#8b9bb0; --accent:#00c364; }
  * { box-sizing: border-box; }
  body { margin:0; padding:40px 20px; background:var(--bg); color:var(--fg);
         font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
  main { max-width:880px; margin:0 auto; }
  h1 { font-size:30px; margin:0 0 4px; letter-spacing:-.4px; }
  .sub { color:var(--muted); font-size:13px; margin-bottom:28px; }
  .card { background:var(--card); border:1px solid var(--edge); border-radius:14px;
          padding:22px; margin-bottom:18px; }
  .row { display:grid; grid-template-columns:150px 1fr; gap:12px; margin-bottom:12px; }
  label.role { display:block; font-size:11px; letter-spacing:.9px; text-transform:uppercase;
               color:var(--muted); margin-bottom:7px; font-weight:700; }
  select { width:100%; padding:11px 12px; background:#0e141c; color:var(--fg);
           border:1px solid var(--edge); border-radius:9px; font-size:14px; }
  select:disabled { opacity:.45; }
  .controls { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-top:18px; }
  button { padding:12px 20px; border-radius:9px; border:1px solid var(--edge);
           background:#1b2430; color:var(--fg); font-size:14px; cursor:pointer; }
  button.go { background:var(--accent); border-color:var(--accent); color:#04160c; font-weight:700; }
  button:disabled { opacity:.4; cursor:not-allowed; }
  .opts { margin-left:auto; color:var(--muted); font-size:13px; }
  .opts select { width:auto; display:inline-block; padding:8px 10px; margin-left:6px; }
  ol { margin:0; padding-left:0; list-style:none; counter-reset:step; }
  li { counter-increment:step; padding:10px 0 10px 46px; position:relative;
       border-bottom:1px solid #1a222c; }
  li:last-child { border-bottom:0; }
  li::before { content:counter(step); position:absolute; left:0; top:10px; width:28px;
       height:28px; border-radius:50%; background:var(--swatch,#333); color:#06110a;
       font-weight:800; font-size:13px; display:grid; place-items:center; }
  .head { font-size:19px; font-weight:700; margin-bottom:14px; }
  .totals { margin-top:16px; padding-top:14px; border-top:1px solid var(--edge);
            color:var(--accent); font-weight:700; }
  .muted { color:var(--muted); font-weight:400; font-size:13px; margin-top:4px; }
  .pill { display:inline-block; padding:2px 9px; border-radius:20px; font-size:11px;
          font-weight:700; color:#06110a; margin-right:5px; }
</style></head><body><main>
  <h1>🚇 Shenzhen Metro</h1>
  <div class="sub" id="stats"></div>

  <div class="card">
    <div class="row"><div><label class="role">From 出发</label></div>
      <div style="display:grid;grid-template-columns:1fr 1.4fr;gap:10px">
        <select id="fromLine"></select><select id="fromStation" disabled></select></div></div>
    <div class="row"><div><label class="role">To 到达</label></div>
      <div style="display:grid;grid-template-columns:1fr 1.4fr;gap:10px">
        <select id="toLine"></select><select id="toStation" disabled></select></div></div>
    <div class="controls">
      <button class="go" id="go" disabled>Find route</button>
      <button id="swap">⇅ Swap</button>
      <span class="opts">optimise
        <select id="optimize"><option value="time">fastest</option>
          <option value="stops">fewest stops</option></select>
        language
        <select id="lang"><option value="both">EN + 中文</option>
          <option value="en">English</option><option value="zh">中文</option></select>
      </span>
    </div>
  </div>

  <div class="card" id="out"><span class="muted">Pick both ends of your trip.
    ⇄ marks a transfer station.</span></div>
</main><script>
let NET = null;

const el = id => document.getElementById(id);
const label = id => { const s = NET.stations[id];
  return s.en + " " + s.zh + (s.lines.length > 1 ? " ⇄" : ""); };

function fillLines(select) {
  select.innerHTML = '<option value="">— line —</option>' +
    Object.keys(NET.lines).map(n => `<option value="${n}">${n} · ${NET.lines[n].zh}</option>`).join("");
}
function fillStations(lineSelect, stationSelect) {
  const line = NET.lines[lineSelect.value];
  if (!line) { stationSelect.innerHTML = ""; stationSelect.disabled = true; return ready(); }
  stationSelect.innerHTML = '<option value="">— station —</option>' +
    line.stations.map(id => `<option value="${id}">${label(id)}</option>`).join("");
  stationSelect.disabled = false;
  ready();
}
const ready = () => el("go").disabled = !(el("fromStation").value && el("toStation").value);

async function findRoute() {
  const params = new URLSearchParams({ from: el("fromStation").value,
    to: el("toStation").value, optimize: el("optimize").value, lang: el("lang").value });
  const res = await fetch("/api/route?" + params);
  const data = await res.json();
  const out = el("out");
  if (data.error) { out.innerHTML = `<span class="muted">⚠ ${data.error}</span>`; return; }
  if (!data.steps.length) { out.innerHTML = `<div class="head">${data.sentences[0]}</div>`; return; }
  const colour = s => NET.lines[s.to_line || s.line]?.color || "#55606d";
  out.innerHTML = `<div class="head">${data.origin} → ${data.destination}</div><ol>` +
    data.sentences.map((t, i) =>
      `<li style="--swatch:${colour(data.steps[i])}">${t}</li>`).join("") +
    `</ol><div class="totals">${data.summary}</div><div class="muted">` +
    data.lines_used.map(n =>
      `<span class="pill" style="background:${NET.lines[n].color}">${n}</span>`).join("") +
    `</div>`;
}

(async () => {
  NET = await (await fetch("/api/network")).json();
  el("stats").textContent = `${Object.keys(NET.lines).length} lines · ` +
    `${Object.keys(NET.stations).length} stations · ${NET.transfers} transfer points · ` +
    `${NET.minutes_per_stop} min per stop · ${NET.minutes_per_transfer} min per transfer · offline`;
  [["fromLine","fromStation"],["toLine","toStation"]].forEach(([l, s]) => {
    fillLines(el(l));
    el(l).onchange = () => fillStations(el(l), el(s));
    el(s).onchange = ready;
  });
  el("go").onclick = findRoute;
  el("swap").onclick = () => {
    const a = [el("fromLine").value, el("fromStation").value];
    const b = [el("toLine").value, el("toStation").value];
    el("fromLine").value = b[0]; fillStations(el("fromLine"), el("fromStation"));
    el("fromStation").value = b[1];
    el("toLine").value = a[0]; fillStations(el("toLine"), el("toStation"));
    el("toStation").value = a[1];
    ready();
  };
})();
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    """Two endpoints and one page. The engine is shared with the CLI."""

    network: MetroNetwork
    router: Router

    def log_message(self, *_args: Any) -> None:  # quieter console
        pass

    # -- plumbing ----------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (http.server's naming)
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)

        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/network":
            self._json(self._network_payload())
        elif path == "/api/route":
            self._route(query)
        else:
            self._json({"error": "not found"}, status=404)

    def _network_payload(self) -> dict:
        net = self.network
        return {
            "lines": {
                name: {"zh": line.zh, "color": line.color,
                       "stations": list(line.stations)}
                for name, line in net.lines.items()
            },
            "stations": {
                s.id: {"en": s.en, "zh": s.zh, "lines": list(s.lines)}
                for s in net.stations.values()
            },
            "transfers": len(net.transfer_stations()),
            "minutes_per_stop": net.minutes_per_stop,
            "minutes_per_transfer": net.minutes_per_transfer,
        }

    def _route(self, query: dict) -> None:
        origin = (query.get("from") or [""])[0]
        destination = (query.get("to") or [""])[0]
        optimize = (query.get("optimize") or ["time"])[0]
        lang = (query.get("lang") or ["both"])[0]

        if not origin or not destination:
            self._json({"error": "both 'from' and 'to' are required"}, status=400)
            return
        try:
            route = self.router.find_route(origin, destination, optimize=optimize)
            voice = InstructionGenerator(self.network, lang)
        except (MetroError, ValueError) as exc:
            self._json({"error": str(exc)}, status=400)
            return

        payload = route.to_dict()
        payload.update(
            origin=self.network.station(route.start).name(lang),
            destination=self.network.station(route.end).name(lang),
            sentences=voice.steps(route),
            summary=voice.summary(route),
            lines_used=route.lines_used,
        )
        self._json(payload)


def serve(data: Path | str | None = None, host: str = HOST, port: int = PORT) -> int:
    network = MetroNetwork.load(data) if data else MetroNetwork.load()
    Handler.network = network
    Handler.router = Router(network)
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Shenzhen Metro Navigator — http://{host}:{port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
