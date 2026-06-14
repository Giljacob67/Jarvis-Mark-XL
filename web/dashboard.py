"""
MARK XL — Web Dashboard.

Lightweight Flask server for monitoring and configuration.
Accessible at http://localhost:5050 when started.

Routes:
    /                  — Dashboard overview
    /config            — Configuration viewer/editor
    /conversations     — Conversation history browser
    /conversations/<id>— Single conversation detail
    /memory            — Long-term memory viewer
    /tools             — Tool call statistics
    /api/config        — JSON config API
    /api/stats         — JSON stats API
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request, redirect, url_for

from core.paths import BASE_DIR, API_CONFIG_PATH
from core.logger import get_logger

log = get_logger("web")

app = Flask(__name__)
app.secret_key = "jarvis-mark-xl"

# ---------------------------------------------------------------------------
# HTML Templates (inline for single-file simplicity)
# ---------------------------------------------------------------------------

def _LAYOUT(title: str, content: str) -> str:
    return _LAYOUT_TEMPLATE.replace("{{ title }}", title).replace("{{ content }}", content)


_LAYOUT_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JARVIS — {{ title }}</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Courier New', monospace; background: #00060a; color: #8ffcff; }
  a { color: #00d4ff; text-decoration: none; }
  a:hover { color: #ffcc00; }
  .nav { background: #010d14; border-bottom: 1px solid #0d3347; padding: 12px 24px; display: flex; gap: 20px; align-items: center; }
  .nav .brand { font-size: 14px; font-weight: bold; color: #00d4ff; }
  .nav a { font-size: 12px; }
  .container { max-width: 1000px; margin: 24px auto; padding: 0 20px; }
  h1 { font-size: 18px; color: #00d4ff; margin-bottom: 16px; }
  h2 { font-size: 14px; color: #ffcc00; margin: 16px 0 8px; }
  .card { background: #010d14; border: 1px solid #0d3347; border-radius: 6px; padding: 16px; margin-bottom: 12px; }
  .stat { display: inline-block; margin-right: 24px; }
  .stat .val { font-size: 24px; color: #00d4ff; }
  .stat .lbl { font-size: 10px; color: #3a8a9a; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th { text-align: left; color: #ffcc00; padding: 8px; border-bottom: 1px solid #0d3347; }
  td { padding: 8px; border-bottom: 1px solid #011520; }
  tr:hover { background: #011520; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 3px; font-size: 10px; }
  .badge-ok { background: #003322; color: #00ff88; }
  .badge-err { background: #330015; color: #ff3355; }
  .badge-info { background: #001f2e; color: #00d4ff; }
  pre { background: #000d12; border: 1px solid #0d3347; border-radius: 4px; padding: 12px; font-size: 11px; overflow-x: auto; white-space: pre-wrap; }
  .msg-user { color: #d8f8ff; }
  .msg-assistant { color: #00d4ff; }
  .msg-tool { color: #ffcc00; font-size: 11px; }
  .empty { color: #3a8a9a; font-style: italic; }
</style>
</head>
<body>
  <div class="nav">
    <span class="brand">J.A.R.V.I.S</span>
    <a href="/">Dashboard</a>
    <a href="/config">Config</a>
    <a href="/conversations">History</a>
    <a href="/memory">Memory</a>
    <a href="/tools">Tools</a>
  </div>
  <div class="container">
    <h1>{{ title }}</h1>
    {{ content }}
  </div>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    from memory.conversation_db import get_stats
    from memory.memory_manager import load_memory

    db_stats = get_stats()
    mem = load_memory()
    mem_count = sum(len(v) for v in mem.values() if isinstance(v, dict))

    content = f"""
    <div class="card">
      <div class="stat"><div class="val">{db_stats['conversations']}</div><div class="lbl">Conversations</div></div>
      <div class="stat"><div class="val">{db_stats['messages']}</div><div class="lbl">Messages</div></div>
      <div class="stat"><div class="val">{db_stats['tool_calls']}</div><div class="lbl">Tool Calls</div></div>
      <div class="stat"><div class="val">{mem_count}</div><div class="lbl">Memory Facts</div></div>
    </div>
    <div class="card">
      <h2>Quick Links</h2>
      <p><a href="/config">View/Edit Configuration</a></p>
      <p><a href="/conversations">Browse Conversation History</a></p>
      <p><a href="/memory">View Long-term Memory</a></p>
      <p><a href="/tools">Tool Call Statistics</a></p>
    </div>
    """
    return render_template_string(_LAYOUT("Dashboard", content))


@app.route("/config", methods=["GET", "POST"])
def config_page():
    if request.method == "POST":
        new_cfg = {}
        for key in request.form:
            val = request.form[key].strip()
            if key in ("llm_model", "stt_model"):
                new_cfg[key] = val
            elif key in ("allow_code_execution",):
                new_cfg[key] = val.lower() in ("true", "1", "yes")
            else:
                new_cfg[key] = val
        existing = _load_cfg()
        existing.update(new_cfg)
        API_CONFIG_PATH.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        return redirect(url_for("config_page"))

    cfg = _load_cfg()
    fields = [
        ("stt_engine", "STT Engine", "whisper"),
        ("stt_model", "STT Model", "base"),
        ("stt_language", "STT Language", "auto"),
        ("llm_provider", "LLM Provider", "ollama"),
        ("llm_url", "LLM URL", "http://localhost:11434"),
        ("llm_model", "LLM Model", "qwen2.5:7b"),
        ("tts_engine", "TTS Engine", "edgetts"),
        ("tts_voice", "TTS Voice", "en-US-GuyNeural"),
    ]
    rows = ""
    for key, label, default in fields:
        val = cfg.get(key, default)
        rows += f"""
        <tr>
          <td>{label}</td>
          <td><input name="{key}" value="{val}" style="background:#000d12;color:#8ffcff;border:1px solid #0d3347;padding:6px 10px;width:100%;font-family:inherit;border-radius:3px;"></td>
          <td style="color:#3a8a9a;font-size:10px;">{key}</td>
        </tr>"""

    content = f"""
    <div class="card">
      <form method="POST">
        <table>
          <tr><th>Setting</th><th>Value</th><th>Key</th></tr>
          {rows}
        </table>
        <br>
        <button type="submit" style="background:#00d4ff;color:#001a22;border:none;padding:8px 20px;cursor:pointer;font-family:inherit;font-weight:bold;border-radius:3px;">Save Configuration</button>
      </form>
    </div>
    """
    return render_template_string(_LAYOUT("Configuration", content))


@app.route("/conversations")
def conversations_list():
    from memory.conversation_db import list_conversations
    convs = list_conversations()
    if not convs:
        content = '<div class="card"><p class="empty">No conversations yet.</p></div>'
    else:
        rows = ""
        for c in convs:
            rows += f"""
            <tr>
              <td><a href="/conversations/{c['id']}">#{c['id']}</a></td>
              <td>{c['title'] or 'Untitled'}</td>
              <td>{c['message_count']}</td>
              <td>{c['created_at']}</td>
            </tr>"""
        content = f"""
        <div class="card">
          <table>
            <tr><th>ID</th><th>Title</th><th>Messages</th><th>Created</th></tr>
            {rows}
          </table>
        </div>"""
    return render_template_string(_LAYOUT("Conversations", content))


@app.route("/conversations/<int:conv_id>")
def conversation_detail(conv_id):
    from memory.conversation_db import get_messages, get_tool_calls
    msgs = get_messages(conv_id, limit=200)
    if not msgs:
        content = '<div class="card"><p class="empty">Conversation not found.</p></div>'
    else:
        html_msgs = ""
        for m in msgs:
            cls = f"msg-{m['role']}"
            prefix = {"user": "You:", "assistant": "Jarvis:", "tool": "Tool:", "system": "Sys:"}.get(m["role"], "")
            content_text = m["content"].replace("<", "&lt;").replace(">", "&gt;")
            html_msgs += f'<p class="{cls}"><strong>{prefix}</strong> {content_text}</p>\n'
        content = f"""
        <div class="card">
          <h2>Conversation #{conv_id}</h2>
          {html_msgs}
        </div>"""
    return render_template_string(_LAYOUT(f"Conversation #{conv_id}", content))


@app.route("/memory")
def memory_page():
    from memory.memory_manager import load_memory
    mem = load_memory()
    sections = ""
    for cat, entries in mem.items():
        if not entries:
            continue
        rows = ""
        for key, entry in entries.items():
            val = entry.get("value", str(entry)) if isinstance(entry, dict) else str(entry)
            updated = entry.get("updated", "") if isinstance(entry, dict) else ""
            rows += f"<tr><td>{key}</td><td>{val[:80]}</td><td style='color:#3a8a9a'>{updated}</td></tr>"
        sections += f"""
        <div class="card">
          <h2>{cat.upper()}</h2>
          <table>
            <tr><th>Key</th><th>Value</th><th>Updated</th></tr>
            {rows}
          </table>
        </div>"""
    if not sections:
        sections = '<div class="card"><p class="empty">No memory entries yet.</p></div>'
    return render_template_string(_LAYOUT("Memory", sections))


@app.route("/tools")
def tools_page():
    from memory.conversation_db import _get_conn
    conn = _get_conn()
    try:
        rows = conn.execute(
            """
            SELECT tool_name, COUNT(*) as count,
                   AVG(duration_ms) as avg_ms,
                   MAX(duration_ms) as max_ms
            FROM tool_calls
            GROUP BY tool_name
            ORDER BY count DESC
            """
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        content = '<div class="card"><p class="empty">No tool calls recorded yet.</p></div>'
    else:
        trows = ""
        for r in rows:
            avg = f"{r['avg_ms']:.0f}" if r["avg_ms"] else "—"
            mx = f"{r['max_ms']}" if r["max_ms"] else "—"
            trows += f"""
            <tr>
              <td>{r['tool_name']}</td>
              <td>{r['count']}</td>
              <td>{avg} ms</td>
              <td>{mx} ms</td>
            </tr>"""
        content = f"""
        <div class="card">
          <table>
            <tr><th>Tool</th><th>Calls</th><th>Avg Time</th><th>Max Time</th></tr>
            {trows}
          </table>
        </div>"""
    return render_template_string(_LAYOUT("Tool Statistics", content))


# ---------------------------------------------------------------------------
# JSON APIs
# ---------------------------------------------------------------------------

@app.route("/api/config")
def api_config():
    return jsonify(_load_cfg())


@app.route("/api/stats")
def api_stats():
    from memory.conversation_db import get_stats
    return jsonify(get_stats())


# ---------------------------------------------------------------------------
# Audio Test
# ---------------------------------------------------------------------------

@app.route("/audio_test")
def audio_test_page():
    cfg = _load_cfg()
    engine = cfg.get("tts_engine", "edgetts")
    voice = cfg.get("tts_voice", "pt-BR-AntonioNeural")
    stt_model = cfg.get("stt_model", "medium")
    llm_model = cfg.get("llm_model", "qwen3.5:2b")

    content = f"""
    <div class="card">
      <h2>Audio Test</h2>
      <p>Test microphone, STT, TTS, and LLM from the browser.</p>

      <h2>Current Config</h2>
      <table>
        <tr><td>STT Model</td><td>{stt_model}</td></tr>
        <tr><td>TTS Engine</td><td>{engine}</td></tr>
        <tr><td>TTS Voice</td><td>{voice}</td></tr>
        <tr><td>LLM Model</td><td>{llm_model}</td></tr>
      </table>

      <h2>Quick Test</h2>
      <p>Run from terminal:</p>
      <pre>cd ~/Jarvis-Mark-XL && .venv/bin/python scripts/test_audio.py</pre>

      <h2>Test Commands</h2>
      <table>
        <tr><th>Command</th><th>Purpose</th></tr>
        <tr><td><code>python scripts/test_audio.py</code></td><td>Full audio test (mic + STT + TTS + LLM)</td></tr>
        <tr><td><code>python scripts/test_stt.py</code></td><td>STT only (records 3s and transcribes)</td></tr>
      </table>
    </div>
    """
    return render_template_string(_LAYOUT("Audio Test", content))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_cfg() -> dict:
    try:
        return json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Server runner
# ---------------------------------------------------------------------------

def start_web_dashboard(port: int = 5050, debug: bool = False) -> None:
    """Start the web dashboard in a background thread."""
    def _run():
        log.info("Web dashboard starting on http://localhost:%d", port)
        app.run(host="127.0.0.1", port=port, debug=debug, use_reloader=False)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    log.info("Web dashboard running at http://localhost:%d", port)
