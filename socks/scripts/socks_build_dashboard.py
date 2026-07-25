#!/usr/bin/env python3
"""
socks_build_dashboard.py -- render a `socks build` ledger as one page.

Phase A of the build dashboard (design-build-dashboard-spec.md section 7):
a snapshot generator. Point it at a build-ledger.jsonl and it writes a
single self-contained, theme-aware HTML page -- stage timeline, gate
verdicts, artifact hashes, live log tail, and the baseline-comparability
verdict strip.

    python3 socks_build_dashboard.py <ledger.jsonl> [-o page.html]

It works MID-BUILD: the emitter flushes every line, so re-running the
generator at any moment renders "now" (stages still running show as
active). It is equally the post-hoc audit view -- live-follow and audit
are one code path because they read the same artifact.

Never hand-edit the output: the ledger is the product, the page is a view.
Origin thread: cross-cutting/20260703-socks-canonical-build-driver.
"""

import argparse
import html
import json
import os
import sys

STAGE_ORDER = ["apply", "hdl_make", "kernel", "dt", "boot_assembly", "gates"]
STAGE_LABEL = {"apply": "apply (materialize + patch)", "hdl_make": "hdl_make (Stage-14 ADI make)",
               "kernel": "kernel", "dt": "device tree", "boot_assembly": "boot assembly",
               "gates": "gates"}


def read_ledger(path):
    events, bad = [], 0
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                bad += 1          # a torn last line mid-flush is expected, not fatal
    return events, bad


def digest(events):
    """Fold the event stream into the page model. Pure function of the
    ledger -- nothing here reads the filesystem."""
    m = {"run": None, "done": None, "stages": {}, "gates": [], "artifacts": [],
         "baselines": [], "tail": []}
    for e in events:
        ev = e.get("event")
        if ev == "run_start":
            m["run"] = e
        elif ev == "run_done":
            m["done"] = e
        elif ev in ("stage_start", "stage_done", "step", "progress"):
            st = m["stages"].setdefault(e["stage"], {"state": "pending", "steps": [],
                                                     "last": None, "elapsed_s": None,
                                                     "status": None})
            if ev == "stage_start":
                st["state"] = "running"
            elif ev == "stage_done":
                st["state"] = "done"
                st["status"] = e.get("status")
                st["elapsed_s"] = e.get("elapsed_s")
            elif ev == "step":
                st["steps"].append(e)
                st["last"] = e.get("name")
                m["tail"].append(f"[{e['t']}] {e['stage']} step {e.get('index','')}"
                                 f"{'/' + str(e['total']) if e.get('total') else ''} {e['name']}")
            else:
                st["last"] = e.get("detail")
                m["tail"].append(f"[{e['t']}] {e['stage']} {e.get('detail','')}")
        elif ev == "gate":
            m["gates"].append(e)
        elif ev == "artifact":
            m["artifacts"].append(e)
        elif ev == "baseline":
            m["baselines"].append(e)
    return m


def esc(x):
    return html.escape(str(x if x is not None else ""))


def verdict_strip(m):
    if not m["run"]:
        return "no run_start in ledger", "warn"
    if not m["done"]:
        running = [s for s, v in m["stages"].items() if v["state"] == "running"]
        where = running[0] if running else "starting"
        return f"RUNNING — in {esc(where)}", "run"
    d = m["done"]
    s = d.get("summary", {})
    base = ("reproduces baseline" if s.get("baseline_reproduces") and not s.get("baseline_diverges")
            and not s.get("baseline_absent")
            else "no baseline (first build)" if s.get("baseline_absent") and not s.get("baseline_diverges")
            else f"diverges ({s.get('baseline_diverges', 0)})" if s.get("baseline_diverges")
            else "no artifacts compared")
    if d.get("status") == "ok":
        return (f"PASS — {s.get('gates_passed', 0)} gates, "
                f"{s.get('artifacts', 0)} artifacts — {base}"), "pass"
    return (f"FAIL — {s.get('gates_failed', 0)} gate(s) failed"
            f"{' — ' + esc(d['detail']) if d.get('detail') else ''}"), "fail"


CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1b;--muted:#6b6b66;--line:#e0e0dc;--card:#fff;
--pass:#1a7f4b;--fail:#b3261e;--run:#8a5a00;--pend:#9a9a94;--accent:#2f5fa8}
@media (prefers-color-scheme:dark){:root{--bg:#16171a;--fg:#e8e8e4;--muted:#9b9b95;
--line:#2c2e33;--card:#1e2024;--pass:#4ec27f;--fail:#ef7a72;--run:#e0a33c;
--pend:#63656b;--accent:#7aa6e8}}
:root[data-theme=light]{--bg:#fbfbfa;--fg:#1d1d1b;--muted:#6b6b66;--line:#e0e0dc;
--card:#fff;--pass:#1a7f4b;--fail:#b3261e;--run:#8a5a00;--pend:#9a9a94;--accent:#2f5fa8}
:root[data-theme=dark]{--bg:#16171a;--fg:#e8e8e4;--muted:#9b9b95;--line:#2c2e33;
--card:#1e2024;--pass:#4ec27f;--fail:#ef7a72;--run:#e0a33c;--pend:#63656b;--accent:#7aa6e8}
body{background:var(--bg);color:var(--fg);font:14px/1.5 ui-sans-serif,system-ui,sans-serif;
margin:0;padding:1.25rem}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:1.15rem;margin:0 0 .2rem}
.sub{color:var(--muted);font-size:.85rem;margin-bottom:1rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:.85rem 1rem;margin-bottom:1rem}
.grid{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:1rem}
@media(max-width:820px){.grid{grid-template-columns:1fr}}
h2{font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
margin:0 0 .6rem;font-weight:600}
table{border-collapse:collapse;width:100%;font-size:.82rem}
th,td{text-align:left;padding:.3rem .5rem;border-bottom:1px solid var(--line);
vertical-align:top}
th{color:var(--muted);font-weight:600}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.78rem;
word-break:break-all}
.scroll{overflow-x:auto}
.dot{display:inline-block;width:.7rem}
.pass{color:var(--pass)}.fail{color:var(--fail)}.run{color:var(--run)}
.pend{color:var(--pend)}.warn{color:var(--run)}
.stage{display:flex;gap:.55rem;padding:.3rem 0;align-items:baseline}
.stage .nm{font-weight:600}
.stage .dt{color:var(--muted);font-size:.8rem}
.kv{display:grid;grid-template-columns:auto 1fr;gap:.15rem .8rem;font-size:.83rem}
.kv dt{color:var(--muted)}
.kv dd{margin:0}
.strip{border-radius:8px;padding:.7rem 1rem;font-weight:600;border:1px solid var(--line)}
.strip.pass{background:color-mix(in srgb,var(--pass) 12%,transparent)}
.strip.fail{background:color-mix(in srgb,var(--fail) 12%,transparent)}
.strip.run{background:color-mix(in srgb,var(--run) 12%,transparent)}
pre{margin:0;max-height:22rem;overflow:auto;font-size:.75rem;
font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
details summary{cursor:pointer;color:var(--muted);font-size:.8rem}
"""


def render(m, ledger_path, bad_lines):
    run = m["run"] or {}
    tc = run.get("toolchain", {})
    pin = run.get("upstream_pin", {})
    text, cls = verdict_strip(m)

    out = [f"<title>socks build — {esc(run.get('recipe_name', 'ledger'))}</title>",
           f"<style>{CSS}</style>", "<div class=wrap>"]
    out.append(f"<h1>socks build — {esc(run.get('recipe_name', '(unknown recipe)'))}</h1>")
    out.append(f"<div class=sub>ledger <span class=mono>{esc(os.path.basename(ledger_path))}</span>"
               f" · {len(m['artifacts'])} artifacts · {len(m['gates'])} gates"
               + (f" · <span class=warn>{bad_lines} unparsable line(s)</span>" if bad_lines else "")
               + "</div>")

    out.append(f"<div class='strip {cls}'>{text}</div><div style=height:1rem></div>")

    # identity
    out.append("<div class=card><h2>Recipe identity</h2><dl class=kv>")
    for k, v in [("recipe", run.get("recipe_path")),
                 ("recipe sha256", run.get("recipe_sha256")),
                 ("toolchain", f"vivado {tc.get('vivado')} · vitis {tc.get('vitis')} · "
                               f"bootgen {tc.get('bootgen')} · xsct {tc.get('xsct')}"
                               if tc else None),
                 ("hdl pin", f"{pin.get('hdl_repo')} @ {pin.get('hdl_sha')}" if pin else None),
                 ("no-OS pin", f"{pin.get('no_os_repo')} @ {pin.get('no_os_sha')}" if pin else None),
                 ("worktree", f"{run.get('worktree_sha')}"
                              f"{' (dirty)' if run.get('worktree_dirty') else ''}"),
                 ("run label", run.get("run_label")),
                 ("started", run.get("t"))]:
        if v:
            out.append(f"<dt>{esc(k)}</dt><dd class=mono>{esc(v)}</dd>")
    out.append("</dl></div>")

    out.append("<div class=grid>")

    # stage timeline
    out.append("<div class=card><h2>Stage timeline</h2>")
    planned = run.get("stage_plan") or [s for s in STAGE_ORDER if s in m["stages"]]
    for s in planned:
        st = m["stages"].get(s, {"state": "pending", "steps": [], "last": None,
                                 "elapsed_s": None, "status": None})
        if st["state"] == "done":
            mark, klass = ("✓", "pass") if st.get("status") == "ok" else ("✗", "fail")
        elif st["state"] == "running":
            mark, klass = "▶", "run"
        else:
            mark, klass = "○", "pend"
        n = len(st["steps"])
        tot = st["steps"][-1].get("total") if n and st["steps"][-1].get("total") else None
        count = f"{n}/{tot}" if tot else (str(n) if n else "")
        secs = f"{st['elapsed_s']:.0f}s" if st.get("elapsed_s") is not None else ""
        out.append(f"<div class=stage><span class='dot {klass}'>{mark}</span>"
                   f"<span class=nm>{esc(STAGE_LABEL.get(s, s))}</span>"
                   f"<span class=dt>{esc(count)} {esc(secs)}</span></div>")
        if st.get("last"):
            out.append(f"<div class=dt style='margin:-.2rem 0 .3rem 1.25rem'>"
                       f"{esc(str(st['last'])[:120])}</div>")
    out.append("</div>")

    # gates
    out.append("<div class=card><h2>Gates</h2><div class=scroll><table>"
               "<tr><th>gate</th><th>verdict</th><th>evidence</th></tr>")
    for g in m["gates"]:
        ev = g.get("evidence", "")
        if g.get("wns_ns") is not None:
            ev = f"WNS {g['wns_ns']:+.3f} ns · WHS {g.get('whs_ns', 0):+.3f} ns · {ev}"
        out.append(f"<tr><td class=mono>{esc(g['name'])}</td>"
                   f"<td class={esc(g['verdict'])}>{esc(g['verdict'].upper())}</td>"
                   f"<td class=mono>{esc(ev)}</td></tr>")
    if not m["gates"]:
        out.append("<tr><td colspan=3 class=pend>none yet</td></tr>")
    out.append("</table></div></div></div>")

    # artifacts + baselines
    out.append("<div class=card><h2>Artifacts &amp; baseline comparability</h2><div class=scroll><table>"
               "<tr><th>artifact</th><th>kind</th><th>sha256</th><th>md5</th><th>baseline</th></tr>")
    base_by_file = {os.path.basename(b["artifact"]): b for b in m["baselines"]}
    for a in m["artifacts"]:
        name = os.path.basename(a["path"])
        b = base_by_file.get(name)
        bv = b["verdict"] if b else "—"
        bclass = {"reproduces": "pass", "diverges": "run", "no_baseline": "pend"}.get(bv, "pend")
        out.append(f"<tr><td class=mono>{esc(a['path'])}</td><td>{esc(a['kind'])}</td>"
                   f"<td class=mono>{esc(a['sha256'])}</td>"
                   f"<td class=mono>{esc(a.get('md5', ''))}</td>"
                   f"<td class={bclass}>{esc(bv)}</td></tr>")
    if not m["artifacts"]:
        out.append("<tr><td colspan=5 class=pend>none yet</td></tr>")
    out.append("</table></div></div>")

    # log tail
    out.append("<div class=card><details open><summary>Live log tail "
               f"(last {min(len(m['tail']), 200)} of {len(m['tail'])} milestones)</summary>"
               f"<pre>{esc(chr(10).join(m['tail'][-200:]))}</pre></details></div>")

    out.append("</div>")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Render a socks build ledger as one HTML page")
    ap.add_argument("ledger")
    ap.add_argument("-o", "--out", default=None,
                    help="Output HTML path (default: <ledger dir>/build-dashboard.html)")
    args = ap.parse_args()

    if not os.path.isfile(args.ledger):
        sys.exit(f"ERROR: ledger not found: {args.ledger}")
    events, bad = read_ledger(args.ledger)
    if not events:
        sys.exit(f"ERROR: ledger is empty: {args.ledger}")
    model = digest(events)
    out = args.out or os.path.join(os.path.dirname(os.path.abspath(args.ledger)),
                                   "build-dashboard.html")
    body = render(model, args.ledger, bad)
    with open(out, "w") as fh:
        fh.write("<!doctype html><meta charset=utf-8>"
                 "<meta name=viewport content='width=device-width,initial-scale=1'>\n")
        fh.write(body + "\n")
    text, _ = verdict_strip(model)
    print(f"{out}  ({len(events)} events, {len(model['artifacts'])} artifacts)  {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
