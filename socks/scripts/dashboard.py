#!/usr/bin/env python3
"""
dashboard.py -- Live SSE dashboard for the SOCKS pipeline.

Serves an HTML dashboard that watches session.json and pushes updates
to the browser via Server-Sent Events.

Usage:
    python scripts/dashboard.py --project-dir .
    python scripts/dashboard.py --project-dir . --port 8099
    python scripts/dashboard.py --project-dir . --output build/logs/dashboard.html --no-serve
"""

import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler

# ---------------------------------------------------------------------------
# Stage name definitions (local copy to avoid importing from socks.py)
# ---------------------------------------------------------------------------

STAGE_NAMES = {
    0:  "Environment Setup",
    1:  "Architecture Analysis",
    2:  "Write/Modify RTL",
    3:  "VHDL Linter",
    4:  "Synthesis Audit",
    5:  "Python Testbench",
    6:  "Bare-Metal C Driver",
    7:  "SV/Xsim Testbench",
    8:  "VCD Verification",
    9:  "CSV Cross-Check",
    10: "Vivado Synthesis",
    11: "Bash Audit",
    12: "CLAUDE.md Documentation",
    13: "SOCKS Self-Audit",
}

# ---------------------------------------------------------------------------
# Session / state file helpers
# ---------------------------------------------------------------------------

def _state_path(project_dir):
    return os.path.join(project_dir, "build", "state", "project.json")


def session_path(project_dir):
    """Return the active data file: project.json if it exists, else session.json."""
    sp = _state_path(project_dir)
    if os.path.isfile(sp):
        return sp
    return os.path.join(project_dir, "build", "logs", "session.json")


def load_session(project_dir):
    path = session_path(project_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def session_mtime(project_dir):
    path = session_path(project_dir)
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0

# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOCKS Pipeline Dashboard</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
    --bg: #0f172a;
    --card-bg: #1e293b;
    --text: #e2e8f0;
    --text-muted: #94a3b8;
    --pass: #22c55e;
    --fail: #ef4444;
    --skip: #eab308;
    --not-run: #334155;
    --running: #3b82f6;
    --loop: #a78bfa;
    --border: #334155;
    --radius: 8px;
}

body {
    background: var(--bg);
    color: var(--text);
    font-family: ui-monospace, "Cascadia Code", "Fira Code", Menlo, Monaco, "Courier New", monospace;
    font-size: 14px;
    line-height: 1.5;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
}

/* Header */
.header {
    position: sticky;
    top: 0;
    z-index: 100;
    background: #1a2332;
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
}
.header-left {
    display: flex;
    align-items: center;
    gap: 12px;
}
.header-title {
    font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
    font-size: 18px;
    font-weight: 700;
    color: var(--text);
}
.header-session {
    color: var(--text-muted);
    font-size: 12px;
}
.header-center {
    font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
    font-size: 16px;
    font-weight: 600;
}
.header-right {
    display: flex;
    align-items: center;
    gap: 12px;
}
.badge {
    padding: 4px 12px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.badge-pass { background: var(--pass); color: #000; }
.badge-fail { background: var(--fail); color: #fff; }
.badge-progress { background: var(--running); color: #fff; }
.badge-none { background: var(--not-run); color: var(--text-muted); }

.live-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--not-run);
    transition: background 0.3s;
}
.live-dot.connected {
    background: var(--pass);
    animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(34,197,94,0.4); }
    50% { opacity: 0.7; box-shadow: 0 0 0 6px rgba(34,197,94,0); }
}

/* Stage Grid */
.grid-section {
    padding: 16px 24px;
}
.grid-section h2 {
    font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
    font-size: 14px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 12px;
}
.stage-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    gap: 10px;
}
.stage-card {
    background: var(--not-run);
    border-radius: var(--radius);
    padding: 12px;
    cursor: pointer;
    transition: background 0.3s, box-shadow 0.3s, transform 0.15s;
    position: relative;
    box-shadow: 0 1px 3px rgba(0,0,0,0.3);
}
.stage-card:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
}
.stage-card.pass { background: rgba(34,197,94,0.15); border: 1px solid rgba(34,197,94,0.3); }
.stage-card.fail { background: rgba(239,68,68,0.15); border: 1px solid rgba(239,68,68,0.3); }
.stage-card.skip { background: rgba(234,179,8,0.15); border: 1px solid rgba(234,179,8,0.3); }
.stage-card.not-run { border: 1px solid transparent; }
.stage-card.design-loop { border-left: 3px solid var(--loop); }
.stage-card.design-loop .stage-card-num { color: var(--loop); }
.stage-card-num {
    font-size: 11px;
    color: var(--text-muted);
    margin-bottom: 2px;
}
.stage-card-name {
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
}
.stage-card-status {
    font-size: 11px;
    margin-top: 6px;
    text-transform: uppercase;
    font-weight: 600;
}
.stage-card-status.pass { color: var(--pass); }
.stage-card-status.fail { color: var(--fail); }
.stage-card-status.skip { color: var(--skip); }
.stage-card-status.not-run { color: var(--text-muted); }
.iter-badge {
    position: absolute;
    top: 6px;
    right: 6px;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background: var(--running);
    color: #fff;
    font-size: 11px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
}

/* Timeline */
.timeline-section {
    flex: 1;
    padding: 0 24px 120px;
    overflow-y: auto;
}
.timeline-section h2 {
    font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
    font-size: 14px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 12px;
}
.timeline {
    display: flex;
    flex-direction: column;
    gap: 6px;
}
.timeline-entry {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 10px 14px;
    border-left: 3px solid var(--not-run);
    border-radius: 0 var(--radius) var(--radius) 0;
    background: var(--card-bg);
    animation: fadeIn 0.3s ease;
}
@keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
}
.timeline-entry.pass { border-left-color: var(--pass); }
.timeline-entry.fail { border-left-color: var(--fail); background: rgba(239,68,68,0.06); }
.timeline-entry.skip { border-left-color: var(--skip); }
.timeline-entry.design-loop { border-left-color: var(--loop); }
.timeline-entry.design-loop.fail { border-left-color: var(--fail); }
.timeline-time {
    color: var(--text-muted);
    font-size: 12px;
    min-width: 60px;
    flex-shrink: 0;
}
.timeline-stage {
    font-weight: 600;
    min-width: 200px;
    flex-shrink: 0;
}
.timeline-iter {
    display: inline-block;
    background: var(--running);
    color: #fff;
    font-size: 10px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 8px;
    margin-left: 6px;
}
.timeline-badge {
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    min-width: 48px;
    text-align: center;
    flex-shrink: 0;
}
.timeline-badge.pass { background: var(--pass); color: #000; }
.timeline-badge.fail { background: var(--fail); color: #fff; }
.timeline-badge.skip { background: var(--skip); color: #000; }
.timeline-note {
    color: var(--text-muted);
    font-size: 13px;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

/* Stats Bar */
.stats-bar {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: #1a2332;
    border-top: 1px solid var(--border);
    padding: 10px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 13px;
    z-index: 100;
}
.stats-bar .stat {
    display: flex;
    align-items: center;
    gap: 6px;
}
.stats-bar .stat-label {
    color: var(--text-muted);
}
.stats-bar .stat-value {
    font-weight: 600;
}
.stat-pass { color: var(--pass); }
.stat-fail { color: var(--fail); }
.stat-skip { color: var(--skip); }
.stats-bar .conn-status {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--text-muted);
    font-size: 12px;
}
.conn-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--not-run);
}
.conn-dot.ok { background: var(--pass); }

.empty-state {
    text-align: center;
    color: var(--text-muted);
    padding: 48px;
    font-size: 16px;
}
</style>
</head>
<body>

<div class="header">
    <div class="header-left">
        <span class="header-title">SOCKS Pipeline</span>
        <span class="header-session" id="session-id">--</span>
    </div>
    <div class="header-center" id="project-name">--</div>
    <div class="header-right">
        <span class="badge badge-none" id="overall-badge">NO DATA</span>
        <div class="live-dot" id="live-dot"></div>
    </div>
</div>

<div class="grid-section">
    <h2>Stages</h2>
    <div class="stage-grid" id="stage-grid"></div>
</div>

<div class="timeline-section" id="timeline-section">
    <h2>Timeline</h2>
    <div class="timeline" id="timeline">
        <div class="empty-state">Waiting for pipeline data...</div>
    </div>
</div>

<div class="stats-bar">
    <div class="stat">
        <span class="stat-label">Stages:</span>
        <span class="stat-value" id="stat-complete">0/14</span>
    </div>
    <div class="stat">
        <span class="stat-label">Pass:</span>
        <span class="stat-value stat-pass" id="stat-pass">0</span>
        <span class="stat-label">Fail:</span>
        <span class="stat-value stat-fail" id="stat-fail">0</span>
        <span class="stat-label">Skip:</span>
        <span class="stat-value stat-skip" id="stat-skip">0</span>
    </div>
    <div class="stat">
        <span class="stat-label">Iterations:</span>
        <span class="stat-value" id="stat-iter">0</span>
    </div>
    <div class="stat">
        <span class="stat-label">Duration:</span>
        <span class="stat-value" id="stat-duration">00:00:00</span>
    </div>
    <div class="conn-status">
        <div class="conn-dot" id="conn-dot"></div>
        <span id="conn-text">Disconnected</span>
    </div>
</div>

<script>
const STAGE_NAMES = {
    0: "Environment Setup",
    1: "Architecture Analysis",
    2: "Write/Modify RTL",
    3: "VHDL Linter",
    4: "Synthesis Audit",
    5: "Python Testbench",
    6: "Bare-Metal C Driver",
    7: "SV/Xsim Testbench",
    8: "VCD Verification",
    9: "CSV Cross-Check",
    10: "Vivado Synthesis",
    11: "Bash Audit",
    12: "CLAUDE.md Documentation",
    13: "SOCKS Self-Audit"
};

const DESIGN_LOOP = new Set([2, 3, 4, 5, 6, 7, 8, 9]);
let currentStagesCount = 0;
let firstTime = null;
let lastTime = null;
let userScrolled = false;
let durationInterval = null;

// Build stage grid
const grid = document.getElementById("stage-grid");
for (let i = 0; i <= 13; i++) {
    const card = document.createElement("div");
    card.className = "stage-card not-run" + (DESIGN_LOOP.has(i) ? " design-loop" : "");
    card.id = "card-" + i;
    card.innerHTML = '<div class="stage-card-num">Stage ' + i + '</div>' +
        '<div class="stage-card-name">' + STAGE_NAMES[i] + '</div>' +
        '<div class="stage-card-status not-run">--</div>';
    card.onclick = function() {
        const el = document.getElementById("tl-last-" + i);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    };
    grid.appendChild(card);
}

// Track user scroll
const tlSection = document.getElementById("timeline-section");
tlSection.addEventListener("scroll", function() {
    const atBottom = tlSection.scrollHeight - tlSection.scrollTop - tlSection.clientHeight < 40;
    userScrolled = !atBottom;
});

function parseTime(s) {
    if (!s) return null;
    const parts = s.split(":");
    if (parts.length !== 3) return null;
    return parseInt(parts[0]) * 3600 + parseInt(parts[1]) * 60 + parseInt(parts[2]);
}

function formatDuration(secs) {
    if (secs < 0) secs = 0;
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = Math.floor(secs % 60);
    return String(h).padStart(2, "0") + ":" +
           String(m).padStart(2, "0") + ":" +
           String(s).padStart(2, "0");
}

function updateDuration() {
    if (firstTime === null || lastTime === null) return;
    const dur = lastTime - firstTime;
    document.getElementById("stat-duration").textContent = formatDuration(dur);
}

function updateDashboard(data) {
    if (!data) return;

    // Header
    document.getElementById("session-id").textContent = data.session_id || "--";
    const proj = data.project || "";
    document.getElementById("project-name").textContent = proj.split("/").pop() || proj;

    const stages = data.stages || [];

    // Compute stats
    const stageLatest = {};  // stage_num -> latest entry
    let nPass = 0, nFail = 0, nSkip = 0;
    let maxIter = 0;

    for (const e of stages) {
        stageLatest[e.stage] = e;
        if (e.iteration > maxIter) maxIter = e.iteration;
    }

    // Count unique stages completed and their latest status
    for (const num in stageLatest) {
        const s = stageLatest[num].status;
        if (s === "pass") nPass++;
        else if (s === "fail") nFail++;
        else nSkip++;
    }

    const totalComplete = Object.keys(stageLatest).length;

    // Overall badge
    const badge = document.getElementById("overall-badge");
    if (stages.length === 0) {
        badge.className = "badge badge-none";
        badge.textContent = "NO DATA";
    } else if (nFail > 0) {
        badge.className = "badge badge-fail";
        badge.textContent = "FAIL";
    } else if (totalComplete >= 14) {
        badge.className = "badge badge-pass";
        badge.textContent = "PASS";
    } else {
        badge.className = "badge badge-progress";
        badge.textContent = "IN PROGRESS";
    }

    // Update grid cards
    // Reset all first
    for (let i = 0; i <= 13; i++) {
        const card = document.getElementById("card-" + i);
        const loopCls = DESIGN_LOOP.has(i) ? " design-loop" : "";
        card.className = "stage-card not-run" + loopCls;
        card.querySelector(".stage-card-status").textContent = "--";
        card.querySelector(".stage-card-status").className = "stage-card-status not-run";
        // Remove old iter badge
        const old = card.querySelector(".iter-badge");
        if (old) old.remove();
    }

    for (const num in stageLatest) {
        const e = stageLatest[num];
        const card = document.getElementById("card-" + num);
        if (!card) continue;
        card.className = "stage-card " + e.status + (DESIGN_LOOP.has(parseInt(num)) ? " design-loop" : "");
        const statusEl = card.querySelector(".stage-card-status");
        statusEl.textContent = e.status.toUpperCase();
        statusEl.className = "stage-card-status " + e.status;

        // Count iterations for this stage
        const iterCount = stages.filter(s => s.stage == num).length;
        if (iterCount > 1) {
            const ib = document.createElement("div");
            ib.className = "iter-badge";
            ib.textContent = iterCount;
            card.appendChild(ib);
        }
    }

    // Timeline - only add new entries
    const timeline = document.getElementById("timeline");
    if (stages.length > 0 && currentStagesCount === 0) {
        timeline.innerHTML = "";  // clear empty state
    }

    for (let i = currentStagesCount; i < stages.length; i++) {
        const e = stages[i];
        const entry = document.createElement("div");
        const loopTag = DESIGN_LOOP.has(e.stage) ? " design-loop" : "";
        entry.className = "timeline-entry " + e.status + loopTag;
        entry.id = "tl-last-" + e.stage;

        // Remove old "last" id for this stage
        const oldEl = document.querySelector("#tl-last-" + e.stage);
        if (oldEl && oldEl !== entry) {
            oldEl.id = "";
        }

        let iterHtml = "";
        if (e.iteration > 1) {
            iterHtml = '<span class="timeline-iter">' + e.iteration + '</span>';
        }

        entry.innerHTML =
            '<span class="timeline-time">' + (e.time || "") + '</span>' +
            '<span class="timeline-stage">Stage ' + e.stage + ': ' +
                (STAGE_NAMES[e.stage] || "?") + iterHtml + '</span>' +
            '<span class="timeline-badge ' + e.status + '">' +
                e.status.toUpperCase() + '</span>' +
            '<span class="timeline-note">' + (e.note || "") + '</span>';

        timeline.appendChild(entry);
    }

    currentStagesCount = stages.length;

    // Auto-scroll
    if (!userScrolled) {
        tlSection.scrollTop = tlSection.scrollHeight;
    }

    // Times
    if (stages.length > 0) {
        const ft = parseTime(stages[0].time);
        const lt = parseTime(stages[stages.length - 1].time);
        if (ft !== null) firstTime = ft;
        if (lt !== null) lastTime = lt;
    }

    // Stats
    document.getElementById("stat-complete").textContent = totalComplete + "/14";
    document.getElementById("stat-pass").textContent = nPass;
    document.getElementById("stat-fail").textContent = nFail;
    document.getElementById("stat-skip").textContent = nSkip;
    document.getElementById("stat-iter").textContent = maxIter > 1 ? maxIter : 0;
    updateDuration();
}

// SSE connection
function connectSSE() {
    const evtSrc = new EventSource("/events");

    evtSrc.onopen = function() {
        document.getElementById("live-dot").classList.add("connected");
        document.getElementById("conn-dot").classList.add("ok");
        document.getElementById("conn-text").textContent = "Connected";
    };

    evtSrc.onmessage = function(ev) {
        try {
            const data = JSON.parse(ev.data);
            updateDashboard(data);
        } catch(e) {}
    };

    evtSrc.onerror = function() {
        document.getElementById("live-dot").classList.remove("connected");
        document.getElementById("conn-dot").classList.remove("ok");
        document.getElementById("conn-text").textContent = "Reconnecting...";
    };
}

// Initial fetch
fetch("/api/session")
    .then(r => r.json())
    .then(data => updateDashboard(data))
    .catch(() => {});

// Start SSE
connectSSE();

// Duration ticker
durationInterval = setInterval(updateDuration, 1000);
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    project_dir = None  # set by factory

    def log_message(self, format, *args):
        # Suppress default request logging
        pass

    def do_GET(self):
        if self.path == "/":
            self._serve_html()
        elif self.path == "/events":
            self._serve_sse()
        elif self.path == "/api/session":
            self._serve_session_json()
        else:
            self.send_error(404)

    def _serve_html(self):
        body = HTML_TEMPLATE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_session_json(self):
        data = load_session(self.project_dir) or {
            "session_id": None, "project": self.project_dir, "stages": []
        }
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        last_mtime = 0
        try:
            while True:
                mt = session_mtime(self.project_dir)
                if mt != last_mtime:
                    last_mtime = mt
                    data = load_session(self.project_dir)
                    if data is not None:
                        payload = json.dumps(data)
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


def make_handler(project_dir):
    """Create a handler class with the project_dir baked in."""
    class Handler(DashboardHandler):
        pass
    Handler.project_dir = project_dir
    return Handler


# ---------------------------------------------------------------------------
# Static HTML output
# ---------------------------------------------------------------------------

def write_static_html(project_dir, output_path):
    """Write a static snapshot of the dashboard HTML."""
    # For static mode, we inject the current session data directly
    # into the HTML as a script variable, replacing the SSE/fetch logic
    data = load_session(project_dir) or {
        "session_id": None, "project": project_dir, "stages": []
    }

    # Replace the fetch + SSE block with direct data injection
    # This replaces everything from "// Initial fetch" to the closing </script>,
    # so we must NOT wrap in extra <script> tags (we're already inside one).
    static_js = (
        '// Static snapshot -- no SSE\n'
        'const STATIC_DATA = ' + json.dumps(data, indent=2) + ';\n'
        'updateDashboard(STATIC_DATA);\n'
        'document.getElementById("conn-text").textContent = "Static snapshot";\n'
    )

    html = HTML_TEMPLATE
    # Remove the fetch + SSE + ticker block, replace with static data injection.
    # Everything from "// Initial fetch" up to (but not including) the closing
    # </script> tag is replaced.
    marker_start = "// Initial fetch"
    marker_end = "</script>"
    idx_start = html.rfind(marker_start)
    idx_end = html.rfind(marker_end)
    if idx_start > 0 and idx_end > idx_start:
        html = html[:idx_start] + static_js + html[idx_end:]

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w") as f:
        f.write(html)
    print(f"  Static dashboard written to: {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="SOCKS Pipeline Live Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("--project-dir", type=str, required=True,
                        help="Project root directory")
    parser.add_argument("--port", type=int, default=8077,
                        help="HTTP server port (default: 8077)")
    parser.add_argument("--output", type=str, default=None,
                        help="Write static HTML to file instead of serving")
    parser.add_argument("--no-serve", action="store_true",
                        help="Just write HTML, don't start server")
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)

    # Static output mode
    if args.output:
        write_static_html(project_dir, args.output)
        if args.no_serve:
            return 0

    if args.no_serve and not args.output:
        print("  --no-serve requires --output")
        return 1

    if args.no_serve:
        return 0

    # Live server mode
    handler_cls = make_handler(project_dir)
    server = HTTPServer(("", args.port), handler_cls)

    url = f"http://localhost:{args.port}"
    print(f"  SOCKS Dashboard: {url}")
    print(f"  Project: {project_dir}")
    print(f"  Watching: {session_path(project_dir)}")
    print(f"  Press Ctrl-C to stop.\n")

    # Open browser in a thread to avoid blocking
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Dashboard stopped.")
        server.server_close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
