#!/usr/bin/env python3
"""
dashboard.py -- Live SSE dashboard for the SOCKS pipeline.

Serves an HTML dashboard that watches build/state/project.json and pushes
updates to the browser via Server-Sent Events.

Usage:
    python scripts/dashboard.py --project-dir .
    python scripts/dashboard.py --project-dir . --port 8099
    python scripts/dashboard.py --project-dir . --output build/dashboard.html --no-serve
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
# Stage name definitions
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
# State file helpers (project.json only -- no session.json fallback)
# ---------------------------------------------------------------------------

def state_path(project_dir):
    """Return path to build/state/project.json."""
    return os.path.join(project_dir, "build", "state", "project.json")


def load_state(project_dir):
    path = state_path(project_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def state_mtime(project_dir):
    try:
        return os.path.getmtime(state_path(project_dir))
    except OSError:
        return 0


def _empty_state(project_dir):
    """Minimal empty state for when project.json doesn't exist yet."""
    return {
        "version": 2,
        "project": {"name": os.path.basename(project_dir)},
        "stages": {},
        "inputs_hash": {},
        "next_action": None,
    }


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
    --action-bg: #78350f;
    --action-border: #f59e0b;
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
.header-center {
    font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
    font-size: 16px;
    font-weight: 600;
}
.header-right {
    display: flex;
    align-items: center;
    gap: 8px;
}
.badge {
    padding: 4px 12px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.badge-pass { background: var(--pass); color: #000; }
.badge-fail { background: var(--fail); color: #fff; }
.badge-progress { background: var(--running); color: #fff; }
.badge-none { background: var(--not-run); color: var(--text-muted); }
.badge-scope { background: #6366f1; color: #fff; }
.badge-workflow { background: #0d9488; color: #fff; }
.badge:empty { display: none; }

.live-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--not-run);
    transition: background 0.3s;
    margin-left: 4px;
}
.live-dot.connected {
    background: var(--pass);
    animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(34,197,94,0.4); }
    50% { opacity: 0.7; box-shadow: 0 0 0 6px rgba(34,197,94,0); }
}

/* Next Action Banner */
.next-action {
    margin: 12px 24px 0;
    padding: 12px 16px;
    background: var(--action-bg);
    border: 1px solid var(--action-border);
    border-radius: var(--radius);
    display: none;
}
.next-action.visible { display: block; }
.next-action.fail-action {
    background: rgba(239,68,68,0.12);
    border-color: var(--fail);
}
.next-action-label {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--action-border);
    margin-bottom: 4px;
}
.next-action.fail-action .next-action-label { color: var(--fail); }
.next-action-text {
    font-size: 14px;
    font-weight: 600;
    color: #fbbf24;
}
.next-action.fail-action .next-action-text { color: #fca5a5; }
.next-action-meta {
    font-size: 12px;
    color: var(--text-muted);
    margin-top: 4px;
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
    transition: background 0.3s, box-shadow 0.3s, transform 0.15s;
    position: relative;
    box-shadow: 0 1px 3px rgba(0,0,0,0.3);
    border: 1px solid transparent;
}
.stage-card:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
}
.stage-card.pass { background: rgba(34,197,94,0.15); border-color: rgba(34,197,94,0.3); }
.stage-card.fail { background: rgba(239,68,68,0.15); border-color: rgba(239,68,68,0.3); }
.stage-card.skip { background: rgba(234,179,8,0.15); border-color: rgba(234,179,8,0.3); }
.stage-card.blocked { opacity: 0.35; }
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
.stage-card-dur {
    font-size: 10px;
    color: var(--text-muted);
    margin-top: 2px;
}
.source-dot {
    position: absolute;
    top: 8px;
    right: 8px;
    font-size: 9px;
    padding: 1px 5px;
    border-radius: 4px;
    background: rgba(255,255,255,0.08);
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

/* Activity Log */
.activity-section {
    flex: 1;
    padding: 0 24px 120px;
    overflow-y: auto;
}
.activity-section h2 {
    font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
    font-size: 14px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 12px;
}
.activity {
    display: flex;
    flex-direction: column;
    gap: 6px;
}
.activity-entry {
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
.activity-entry.pass { border-left-color: var(--pass); }
.activity-entry.fail { border-left-color: var(--fail); background: rgba(239,68,68,0.06); }
.activity-entry.skip { border-left-color: var(--skip); }
.activity-entry.design-loop { border-left-color: var(--loop); }
.activity-entry.design-loop.fail { border-left-color: var(--fail); }
.activity-time {
    color: var(--text-muted);
    font-size: 12px;
    min-width: 48px;
    flex-shrink: 0;
}
.activity-stage {
    font-weight: 600;
    min-width: 200px;
    flex-shrink: 0;
}
.activity-badge {
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    min-width: 48px;
    text-align: center;
    flex-shrink: 0;
}
.activity-badge.pass { background: var(--pass); color: #000; }
.activity-badge.fail { background: var(--fail); color: #fff; }
.activity-badge.skip { background: var(--skip); color: #000; }
.activity-dur {
    color: var(--text-muted);
    font-size: 12px;
    min-width: 56px;
    text-align: right;
    flex-shrink: 0;
}
.activity-note {
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
.stats-bar .stat-label { color: var(--text-muted); }
.stats-bar .stat-value { font-weight: 600; }
.stat-pass { color: var(--pass); }
.stat-fail { color: var(--fail); }
.stat-skip { color: var(--skip); }

.hash-indicators {
    display: flex;
    align-items: center;
    gap: 8px;
}
.hash-dot {
    display: flex;
    align-items: center;
    gap: 3px;
    font-size: 11px;
    color: var(--text-muted);
}
.hash-dot .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--not-run);
}
.hash-dot .dot.tracked { background: var(--pass); }

.conn-status {
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
    </div>
    <div class="header-center" id="project-name">--</div>
    <div class="header-right">
        <span class="badge badge-scope" id="scope-badge"></span>
        <span class="badge badge-workflow" id="workflow-badge"></span>
        <span class="badge badge-none" id="overall-badge">NO DATA</span>
        <div class="live-dot" id="live-dot"></div>
    </div>
</div>

<div class="next-action" id="next-action">
    <div class="next-action-label">Next Action</div>
    <div class="next-action-text" id="next-action-text"></div>
    <div class="next-action-meta" id="next-action-meta"></div>
</div>

<div class="grid-section">
    <h2>Stages</h2>
    <div class="stage-grid" id="stage-grid"></div>
</div>

<div class="activity-section" id="activity-section">
    <h2>Activity</h2>
    <div class="activity" id="activity">
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
        <span class="stat-label">Duration:</span>
        <span class="stat-value" id="stat-duration">--</span>
    </div>
    <div class="hash-indicators" id="hash-indicators">
        <span class="stat-label">Inputs:</span>
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
const HASH_DIRS = ["docs", "src", "tb", "sw"];

// Build stage grid
const grid = document.getElementById("stage-grid");
for (let i = 0; i <= 13; i++) {
    const card = document.createElement("div");
    card.className = "stage-card" + (DESIGN_LOOP.has(i) ? " design-loop" : "");
    card.id = "card-" + i;
    card.innerHTML =
        '<div class="stage-card-num">Stage ' + i + '</div>' +
        '<div class="stage-card-name">' + STAGE_NAMES[i] + '</div>' +
        '<div class="stage-card-status not-run">--</div>' +
        '<div class="stage-card-dur"></div>';
    grid.appendChild(card);
}

// Build hash indicator dots
const hashEl = document.getElementById("hash-indicators");
for (const d of HASH_DIRS) {
    const span = document.createElement("span");
    span.className = "hash-dot";
    span.id = "hash-" + d;
    span.innerHTML = '<span class="dot"></span>' + d;
    hashEl.appendChild(span);
}

function fmtDur(secs) {
    if (secs == null || secs < 0) return "--";
    if (secs < 60) return secs.toFixed(1) + "s";
    const m = Math.floor(secs / 60);
    const s = Math.floor(secs % 60);
    if (m < 60) return m + "m " + s + "s";
    const h = Math.floor(m / 60);
    return h + "h " + (m % 60) + "m";
}

function extractTime(iso) {
    if (!iso) return "--";
    const t = iso.split("T")[1];
    if (!t) return "--";
    return t.substring(0, 5);
}

function updateDashboard(data) {
    if (!data) return;

    // -- Header --
    const proj = data.project || {};
    document.getElementById("project-name").textContent = proj.name || "--";
    document.getElementById("scope-badge").textContent = proj.scope || "";
    document.getElementById("workflow-badge").textContent =
        proj.last_workflow ? proj.last_workflow.replace(/^--/, "") : "";

    const stages = data.stages || {};
    const stageNums = Object.keys(stages);

    // -- Counts --
    let nPass = 0, nFail = 0, nSkip = 0, totalDur = 0;
    for (const num of stageNums) {
        const s = stages[num].status;
        if (s === "PASS") nPass++;
        else if (s === "FAIL") nFail++;
        else if (s === "SKIP") nSkip++;
        if (stages[num].duration_seconds)
            totalDur += stages[num].duration_seconds;
    }

    // -- Overall badge --
    const badge = document.getElementById("overall-badge");
    if (stageNums.length === 0) {
        badge.className = "badge badge-none";
        badge.textContent = "NO DATA";
    } else if (nFail > 0) {
        badge.className = "badge badge-fail";
        badge.textContent = "FAIL";
    } else if (nFail === 0 && nPass > 0 && nPass === stageNums.length - nSkip) {
        badge.className = "badge badge-pass";
        badge.textContent = "ALL PASS";
    } else {
        badge.className = "badge badge-progress";
        badge.textContent = "IN PROGRESS";
    }

    // -- Next Action banner --
    const actionEl = document.getElementById("next-action");
    const na = data.next_action;
    if (na && na.suggested) {
        actionEl.classList.add("visible");
        // Use red styling if suggestion contains FAIL
        if (na.suggested.indexOf("FAIL") !== -1) {
            actionEl.classList.add("fail-action");
        } else {
            actionEl.classList.remove("fail-action");
        }
        document.getElementById("next-action-text").textContent = na.suggested;
        let meta = "";
        if (na.can_retry_from != null)
            meta += "Retry from Stage " + na.can_retry_from;
        if (na.blocked_stages && na.blocked_stages.length > 0) {
            if (meta) meta += "  \u00b7  ";
            meta += "Blocked: " + na.blocked_stages.join(", ");
        }
        document.getElementById("next-action-meta").textContent = meta;
    } else {
        actionEl.classList.remove("visible");
        actionEl.classList.remove("fail-action");
    }

    // -- Blocked set --
    const blockedSet = new Set((na && na.blocked_stages) || []);

    // -- Update grid cards --
    for (let i = 0; i <= 13; i++) {
        const card = document.getElementById("card-" + i);
        const loopCls = DESIGN_LOOP.has(i) ? " design-loop" : "";
        const stage = stages[String(i)];

        // Remove old source dot
        const oldDot = card.querySelector(".source-dot");
        if (oldDot) oldDot.remove();

        if (stage) {
            const sl = stage.status.toLowerCase();
            card.className = "stage-card " + sl + loopCls;
            if (blockedSet.has(i)) card.classList.add("blocked");

            const statusEl = card.querySelector(".stage-card-status");
            statusEl.textContent = stage.status;
            statusEl.className = "stage-card-status " + sl;

            card.querySelector(".stage-card-dur").textContent =
                stage.duration_seconds ? fmtDur(stage.duration_seconds) : "";

            // Show source for non-script stages (guidance, manual)
            if (stage.source && stage.source !== "script") {
                const dot = document.createElement("span");
                dot.className = "source-dot";
                dot.textContent = stage.source;
                card.appendChild(dot);
            }
        } else {
            card.className = "stage-card" + loopCls;
            if (blockedSet.has(i)) card.classList.add("blocked");
            card.querySelector(".stage-card-status").textContent = "--";
            card.querySelector(".stage-card-status").className =
                "stage-card-status not-run";
            card.querySelector(".stage-card-dur").textContent = "";
        }
    }

    // -- Activity log (stages sorted by timestamp, newest first) --
    const activity = document.getElementById("activity");
    const sorted = stageNums
        .map(function(n) {
            return { num: parseInt(n), status: stages[n].status,
                     timestamp: stages[n].timestamp,
                     duration_seconds: stages[n].duration_seconds,
                     note: stages[n].note };
        })
        .sort(function(a, b) {
            return (b.timestamp || "").localeCompare(a.timestamp || "");
        });

    if (sorted.length === 0) {
        activity.innerHTML =
            '<div class="empty-state">Waiting for pipeline data...</div>';
    } else {
        activity.innerHTML = "";
        for (const e of sorted) {
            const sl = e.status.toLowerCase();
            const loopTag = DESIGN_LOOP.has(e.num) ? " design-loop" : "";
            const entry = document.createElement("div");
            entry.className = "activity-entry " + sl + loopTag;
            entry.innerHTML =
                '<span class="activity-time">' +
                    extractTime(e.timestamp) + '</span>' +
                '<span class="activity-stage">Stage ' + e.num + ': ' +
                    (STAGE_NAMES[e.num] || "?") + '</span>' +
                '<span class="activity-badge ' + sl + '">' +
                    e.status + '</span>' +
                '<span class="activity-dur">' +
                    (e.duration_seconds ? fmtDur(e.duration_seconds) : "") +
                    '</span>' +
                '<span class="activity-note">' +
                    (e.note || "") + '</span>';
            activity.appendChild(entry);
        }
    }

    // -- Hash indicators --
    const hashes = data.inputs_hash || {};
    for (const d of HASH_DIRS) {
        const dot = document.querySelector("#hash-" + d + " .dot");
        if (dot) dot.className = hashes[d] ? "dot tracked" : "dot";
    }

    // -- Stats bar --
    document.getElementById("stat-complete").textContent =
        stageNums.length + "/" + stageNums.length;
    document.getElementById("stat-pass").textContent = nPass;
    document.getElementById("stat-fail").textContent = nFail;
    document.getElementById("stat-skip").textContent = nSkip;
    document.getElementById("stat-duration").textContent = fmtDur(totalDur);
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
            updateDashboard(JSON.parse(ev.data));
        } catch(e) {}
    };

    evtSrc.onerror = function() {
        document.getElementById("live-dot").classList.remove("connected");
        document.getElementById("conn-dot").classList.remove("ok");
        document.getElementById("conn-text").textContent = "Reconnecting...";
    };
}

// Initial fetch + SSE
fetch("/api/state")
    .then(r => r.json())
    .then(data => updateDashboard(data))
    .catch(() => {});

connectSSE();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    project_dir = None  # set by factory

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == "/":
            self._serve_html()
        elif self.path == "/events":
            self._serve_sse()
        elif self.path == "/api/state":
            self._serve_state_json()
        else:
            self.send_error(404)

    def _serve_html(self):
        body = HTML_TEMPLATE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_state_json(self):
        data = load_state(self.project_dir) or \
            _empty_state(self.project_dir)
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
                mt = state_mtime(self.project_dir)
                if mt != last_mtime:
                    last_mtime = mt
                    data = load_state(self.project_dir)
                    if data is not None:
                        payload = json.dumps(data)
                        self.wfile.write(
                            f"data: {payload}\n\n".encode("utf-8"))
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
    data = load_state(project_dir) or _empty_state(project_dir)

    static_js = (
        '// Static snapshot -- no SSE\n'
        'const STATIC_DATA = ' + json.dumps(data, indent=2) + ';\n'
        'updateDashboard(STATIC_DATA);\n'
        'document.getElementById("conn-text").textContent = '
        '"Static snapshot";\n'
    )

    html = HTML_TEMPLATE
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
    print(f"  Watching: {state_path(project_dir)}")
    print(f"  Press Ctrl-C to stop.\n")

    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Dashboard stopped.")
        server.server_close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
