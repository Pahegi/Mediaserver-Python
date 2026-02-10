"""Web interface for Pi Mediaserver — status, configuration, and file management.

Provides an HTTP server with:
  - Live playback status dashboard
  - DMX configuration editor
  - Full media file manager (upload, rename, move, delete)
  - Folder management (create, rename, delete)
  - Inline .txt file editor (for URL stream sources)

Uses Pico CSS via CDN for polished styling. Runs in a daemon thread.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

if TYPE_CHECKING:
    from pi_mediaserver.main import Server


# ---------------------------------------------------------------------------
# HTML template — Pico CSS dark theme
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pi Mediaserver</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
<style>
  :root {{
    --pico-font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    --accent: #00d4ff;
    --accent-dim: rgba(0,212,255,.12);
    --ok: #4caf50;
    --warn: #ff9800;
    --err: #f44336;
  }}
  body {{ max-width: 1100px; margin: 0 auto; padding: 1.5rem; }}

  /* Header */
  .page-header {{ margin-bottom: 1.5rem; }}
  .page-header h1 {{ color: var(--accent); margin-bottom: .2rem; }}
  .page-header p {{ margin: 0; opacity: .6; font-size: .9rem; }}

  /* Status badges */
  .badge {{ display: inline-block; padding: .2rem .6rem; border-radius: 4px;
            font-size: .8rem; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; }}
  .badge.on {{ background: var(--ok); color: #fff; }}
  .badge.off {{ background: #334; color: #889; }}
  .badge.paused {{ background: var(--warn); color: #fff; }}

  /* Status grid */
  .status-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: .3rem 2rem; }}
  .status-grid .label {{ opacity: .55; font-size: .9rem; }}
  .status-grid .value {{ font-size: .9rem; }}

  /* Protocol reference */
  .protocol {{ display: grid; grid-template-columns: auto 1fr; gap: .15rem .8rem; font-size: .85rem; }}
  .protocol .ch {{ color: var(--accent); font-family: monospace; font-weight: 700; }}

  /* Folder cards */
  .folder-card {{ background: var(--pico-card-background-color);
                  border: 1px solid var(--pico-muted-border-color);
                  border-radius: .5rem; padding: 1rem; margin-bottom: 1rem; }}
  .folder-title {{ display: flex; justify-content: space-between; align-items: center;
                   margin-bottom: .6rem; gap: .5rem; flex-wrap: wrap; }}
  .folder-title h3 {{ margin: 0; font-size: 1rem; display: flex; align-items: center; gap: .4rem; }}
  .folder-title .idx {{ color: var(--accent); font-family: monospace; font-size: .85rem;
                        background: var(--accent-dim); padding: .1rem .4rem; border-radius: 3px; }}
  .folder-actions {{ display: flex; gap: .3rem; }}

  /* File table */
  .file-table {{ width: 100%; border-collapse: collapse; font-size: .88rem; margin-bottom: .5rem; }}
  .file-table th {{ text-align: left; font-size: .78rem; text-transform: uppercase;
                    letter-spacing: .04em; opacity: .5; padding: .3rem .5rem;
                    border-bottom: 1px solid var(--pico-muted-border-color); }}
  .file-table td {{ padding: .35rem .5rem; border-bottom: 1px solid var(--pico-muted-border-color); }}
  .file-table .col-dmx {{ width: 3.5rem; text-align: center; color: var(--accent);
                          font-family: monospace; font-weight: 700; }}
  .file-table .col-actions {{ width: 15rem; white-space: nowrap; }}
  .file-table tr[draggable] {{ cursor: grab; transition: background .15s; }}
  .file-table tr[draggable]:hover {{ background: var(--accent-dim); }}
  .file-table tr.dragging {{ opacity: .35; }}
  .url-file {{ color: var(--warn); }}

  /* Buttons */
  .btn {{ display: inline-block; padding: .28rem .6rem; border-radius: 4px; border: none;
          font-size: .78rem; font-weight: 600; cursor: pointer; transition: opacity .15s;
          text-decoration: none; line-height: 1.4; }}
  .btn:hover {{ opacity: .8; }}
  .btn-primary {{ background: var(--accent); color: #111; }}
  .btn-outline {{ background: transparent; border: 1px solid var(--pico-muted-border-color);
                  color: var(--pico-color); }}
  .btn-outline:hover {{ border-color: var(--accent); color: var(--accent); }}
  .btn-danger {{ background: var(--err); color: #fff; }}
  .btn-warn {{ background: var(--warn); color: #111; }}
  .btn-sm {{ padding: .2rem .45rem; font-size: .75rem; }}

  /* Drop zone */
  .dropzone {{ border: 2px dashed var(--pico-muted-border-color); border-radius: 6px;
               padding: .6rem; text-align: center; font-size: .82rem; opacity: .5;
               transition: all .2s; margin-top: .4rem; }}
  .dropzone.active {{ border-color: var(--accent); opacity: 1; background: var(--accent-dim); }}

  /* Upload bar */
  .upload-bar {{ display: flex; gap: .5rem; align-items: center; margin-top: .5rem;
                 font-size: .82rem; }}
  .upload-bar input[type=file] {{ font-size: .8rem; flex: 1; }}

  /* Inline rename input */
  .rename-input {{ background: var(--pico-background-color); color: var(--pico-color);
                   border: 1px solid var(--accent); border-radius: 3px;
                   padding: .2rem .4rem; font-size: .85rem; width: 70%; }}

  /* Toast notification */
  .toast {{ position: fixed; bottom: 1.5rem; right: 1.5rem; padding: .6rem 1.2rem;
            border-radius: 6px; font-size: .85rem; font-weight: 600; z-index: 999;
            animation: slideIn .3s ease; pointer-events: none; }}
  .toast.ok {{ background: var(--ok); color: #fff; }}
  .toast.err {{ background: var(--err); color: #fff; }}
  @keyframes slideIn {{ from {{ transform: translateY(1rem); opacity: 0; }}
                        to {{ transform: translateY(0); opacity: 1; }} }}

  /* Modal overlay for txt editor */
  .modal-overlay {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,.6);
                    z-index: 100; justify-content: center; align-items: center; }}
  .modal-overlay.open {{ display: flex; }}
  .modal {{ background: var(--pico-card-background-color);
            border: 1px solid var(--pico-muted-border-color);
            border-radius: .5rem; padding: 1.5rem; width: 90%; max-width: 600px;
            max-height: 80vh; display: flex; flex-direction: column; }}
  .modal h3 {{ margin: 0 0 .8rem; font-size: 1rem; color: var(--accent); }}
  .modal textarea {{ flex: 1; min-height: 200px; background: var(--pico-background-color);
                     color: var(--pico-color); border: 1px solid var(--pico-muted-border-color);
                     border-radius: 4px; padding: .5rem; font-family: monospace;
                     font-size: .88rem; resize: vertical; }}
  .modal .modal-buttons {{ display: flex; justify-content: flex-end; gap: .5rem;
                           margin-top: .8rem; }}

  /* Add folder bar */
  .add-folder-bar {{ display: flex; gap: .5rem; align-items: center; margin-bottom: 1rem; }}
  .add-folder-bar input {{ flex: 1; padding: .35rem .5rem; font-size: .88rem;
                           margin-bottom: 0; }}

  /* Message banner */
  .msg {{ padding: .6rem 1rem; border-radius: 6px; margin-bottom: 1rem; font-size: .85rem; }}
  .msg.ok {{ background: rgba(76,175,80,.15); color: var(--ok); border: 1px solid rgba(76,175,80,.3); }}
  .msg.err {{ background: rgba(244,67,54,.15); color: var(--err); border: 1px solid rgba(244,67,54,.3); }}

  /* Section spacing */
  article {{ margin-bottom: 1rem; }}

  /* Config form grid */
  .config-grid {{ display: grid; grid-template-columns: auto 1fr; gap: .5rem .8rem;
                  align-items: center; }}
  .config-grid label {{ font-size: .88rem; opacity: .6; margin-bottom: 0; }}
  .config-grid input {{ padding: .35rem .5rem; margin-bottom: 0; }}

  /* Collapse sections */
  details {{ margin-bottom: 1rem; }}
  details summary {{ cursor: pointer; font-weight: 600; }}

  /* Video params */
  .video-params {{ display: flex; flex-direction: column; gap: .4rem; }}
  .param-row {{ display: grid; grid-template-columns: 6rem 1fr 3.5rem; gap: .5rem;
                align-items: center; font-size: .88rem; }}
  .param-row label {{ opacity: .6; margin-bottom: 0; }}
  .param-row input[type=range] {{ margin-bottom: 0; }}
  .param-row select {{ margin-bottom: 0; padding: .3rem .4rem; font-size: .85rem; }}
  .param-val {{ font-family: monospace; font-size: .82rem; text-align: right;
                color: var(--accent); min-width: 3rem; }}
</style>
</head>
<body>
<div class="page-header">
  <h1>&#9654; Pi Mediaserver</h1>
  <p>DMX/sACN-controlled media server &bull; v3.5</p>
</div>

{message}

<!-- Playback Status -->
<article>
  <strong>Playback</strong>
  <div class="status-grid" style="margin-top:.5rem">
    <span class="label">Status</span>
    <span class="value"><span class="badge {playing_class}">{playing_label}</span></span>
    <span class="label">Current</span>
    <span class="value">{current_file}</span>
    <span class="label">Mode</span>
    <span class="value">{play_mode}</span>
    <span class="label">Volume</span>
    <span class="value">{volume_percent}% <small style="opacity:.45">DMX {volume_raw}</small></span>
    <span class="label">Brightness</span>
    <span class="value">{brightness_percent}% <small style="opacity:.45">DMX {brightness_raw}</small></span>
  </div>
</article>

<!-- DMX Protocol -->
<details>
  <summary>Video Effects</summary>
  <div style="margin-top:.6rem">
    <div class="video-params">
      <div class="param-row">
        <label>Contrast</label>
        <input type="range" id="vp-contrast" min="-100" max="100" value="{vp_contrast}">
        <span class="param-val" id="vp-contrast-val">{vp_contrast}</span>
      </div>
      <div class="param-row">
        <label>Saturation</label>
        <input type="range" id="vp-saturation" min="-100" max="100" value="{vp_saturation}">
        <span class="param-val" id="vp-saturation-val">{vp_saturation}</span>
      </div>
      <div class="param-row">
        <label>Gamma</label>
        <input type="range" id="vp-gamma" min="-100" max="100" value="{vp_gamma}">
        <span class="param-val" id="vp-gamma-val">{vp_gamma}</span>
      </div>
      <div class="param-row">
        <label>Speed</label>
        <input type="range" id="vp-speed" min="0.25" max="4" step="0.05" value="{vp_speed}">
        <span class="param-val" id="vp-speed-val">{vp_speed}x</span>
      </div>
      <div class="param-row">
        <label>Rotation</label>
        <select id="vp-rotation" onchange="setVideoParam('rotation', this.value)">
          <option value="0" {rot0_sel}>0&deg;</option>
          <option value="90" {rot90_sel}>90&deg;</option>
          <option value="180" {rot180_sel}>180&deg;</option>
          <option value="270" {rot270_sel}>270&deg;</option>
        </select>
      </div>
      <div class="param-row">
        <label>Zoom</label>
        <input type="range" id="vp-zoom" min="-2" max="2" step="0.05" value="{vp_zoom}">
        <span class="param-val" id="vp-zoom-val">{vp_zoom}</span>
      </div>
      <div class="param-row">
        <label>Pan X</label>
        <input type="range" id="vp-pan_x" min="-1" max="1" step="0.02" value="{vp_pan_x}">
        <span class="param-val" id="vp-pan_x-val">{vp_pan_x}</span>
      </div>
      <div class="param-row">
        <label>Pan Y</label>
        <input type="range" id="vp-pan_y" min="-1" max="1" step="0.02" value="{vp_pan_y}">
        <span class="param-val" id="vp-pan_y-val">{vp_pan_y}</span>
      </div>
    </div>
    <button class="btn btn-outline" style="margin-top:.5rem;width:100%" onclick="resetVideoParams()">
      Reset All Effects</button>
  </div>
</details>

<!-- DMX Protocol -->
<details>
  <summary>DMX Protocol Reference</summary>
  <div class="protocol" style="margin-top:.6rem">
    <span class="ch">CH1</span><span>File select &mdash; 0=stop, 1-255=file index</span>
    <span class="ch">CH2</span><span>Folder select &mdash; 0-255=folder index</span>
    <span class="ch">CH3</span><span>Playmode &mdash; 0-84=play, 85-169=pause, 170-255=loop</span>
    <span class="ch">CH4</span><span>Volume &mdash; 0=mute, 255=full</span>
    <span class="ch">CH5</span><span>Brightness &mdash; 0=black, 255=normal</span>
    <span class="ch">CH6</span><span>Contrast &mdash; 0=-100, 128=0, 255=+100</span>
    <span class="ch">CH7</span><span>Saturation &mdash; 0=-100, 128=0, 255=+100</span>
    <span class="ch">CH8</span><span>Gamma &mdash; 0=-100, 128=0, 255=+100</span>
    <span class="ch">CH9</span><span>Speed &mdash; 0=0.25x, 128=1.0x, 255=4.0x</span>
    <span class="ch">CH10</span><span>Rotation &mdash; 0-63=0&deg;, 64-127=90&deg;, 128-191=180&deg;, 192-255=270&deg;</span>
    <span class="ch">CH11</span><span>Zoom &mdash; 0=-2.0, 128=0, 255=+2.0</span>
    <span class="ch">CH12</span><span>Pan X &mdash; 0=-1.0, 128=0, 255=+1.0</span>
    <span class="ch">CH13</span><span>Pan Y &mdash; 0=-1.0, 128=0, 255=+1.0</span>
  </div>
</details>

<!-- DMX Configuration -->
<details>
  <summary>DMX Configuration</summary>
  <form method="post" action="/config" style="margin-top:.6rem">
    <div class="config-grid">
      <label for="address">Address</label>
      <input type="number" id="address" name="address" min="1" max="512" value="{address}">
      <label for="universe">Universe</label>
      <input type="number" id="universe" name="universe" min="1" max="63999" value="{universe}">
      <label for="mediapath">Media Path</label>
      <input type="text" id="mediapath" name="mediapath" value="{mediapath}">
    </div>
    <button type="submit" class="btn btn-primary" style="margin-top:.6rem;width:100%">
      Save &amp; Restart Receiver</button>
  </form>
</details>

<!-- Media Library -->
<article>
  <strong>Media Library</strong>
  <div class="add-folder-bar" style="margin-top:.5rem">
    <input type="text" id="new-folder-name" placeholder="New folder name&hellip;">
    <button class="btn btn-primary btn-sm" onclick="createFolder()">+ Add Folder</button>
  </div>
  {folders_html}
</article>

<!-- TXT Editor Modal -->
<div class="modal-overlay" id="txt-modal">
  <div class="modal">
    <h3 id="txt-modal-title">Edit File</h3>
    <textarea id="txt-editor"></textarea>
    <div class="modal-buttons">
      <button class="btn btn-outline" onclick="closeTxtModal()">Cancel</button>
      <button class="btn btn-primary" onclick="saveTxtFile()">Save</button>
    </div>
  </div>
</div>

<script>
// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------
function toast(msg, type) {{
  const el = document.createElement('div');
  el.className = 'toast ' + (type || 'ok');
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}}

function api(url, body) {{
  return fetch(url, {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify(body)
  }}).then(r => r.json());
}}

// ---------------------------------------------------------------------------
// Drag & Drop
// ---------------------------------------------------------------------------
let dragData = null;

function onDragStart(e) {{
  const tr = e.target.closest('tr[draggable]');
  if (!tr) return;
  dragData = {{ file: tr.dataset.file, folder: tr.dataset.folder }};
  tr.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', tr.dataset.file);
}}
function onDragEnd(e) {{
  document.querySelectorAll('.dragging').forEach(el => el.classList.remove('dragging'));
  document.querySelectorAll('.dropzone.active').forEach(el => el.classList.remove('active'));
}}
function onDragOver(e) {{
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const zone = e.target.closest('.dropzone');
  if (zone) zone.classList.add('active');
}}
function onDragLeave(e) {{
  const zone = e.target.closest('.dropzone');
  if (zone) zone.classList.remove('active');
}}
function onDropToFolder(e, targetFolder) {{
  e.preventDefault();
  document.querySelectorAll('.dropzone.active').forEach(el => el.classList.remove('active'));

  // External file drop (from desktop) — upload via FormData
  if (e.dataTransfer.files && e.dataTransfer.files.length > 0 && !dragData) {{
    const fd = new FormData();
    fd.append('folder', targetFolder);
    for (const f of e.dataTransfer.files) fd.append('file', f);
    fetch('/api/upload', {{ method: 'POST', body: fd }})
      .then(() => {{ toast('Uploaded ' + e.dataTransfer.files.length + ' file(s)'); location.reload(); }})
      .catch(() => toast('Upload failed', 'err'));
    return;
  }}

  // Internal drag-move between folders
  if (!dragData || dragData.folder === targetFolder) return;
  api('/api/move', {{ file: dragData.file, from_folder: dragData.folder, to_folder: targetFolder }})
    .then(d => {{ if (d.ok) location.reload(); else toast('Move failed: ' + (d.error||''), 'err'); }});
}}

// ---------------------------------------------------------------------------
// File: Rename
// ---------------------------------------------------------------------------
function startRename(folder, oldName) {{
  const cell = document.getElementById('name-' + CSS.escape(folder) + '-' + CSS.escape(oldName));
  if (!cell) return;
  const input = document.createElement('input');
  input.type = 'text'; input.value = oldName; input.className = 'rename-input';
  cell.innerHTML = ''; cell.appendChild(input);
  input.focus(); input.select();
  function doRename() {{
    const newName = input.value.trim();
    if (!newName || newName === oldName) {{ location.reload(); return; }}
    api('/api/rename', {{ folder, old_name: oldName, new_name: newName }})
      .then(d => {{ if (d.ok) location.reload(); else toast('Rename failed: '+(d.error||''), 'err'); }});
  }}
  input.addEventListener('keydown', ev => {{
    if (ev.key==='Enter') doRename();
    if (ev.key==='Escape') location.reload();
  }});
  input.addEventListener('blur', doRename);
}}

// ---------------------------------------------------------------------------
// File: Delete
// ---------------------------------------------------------------------------
function deleteFile(folder, file) {{
  if (!confirm('Delete "' + file + '"?')) return;
  api('/api/delete', {{ folder, file }})
    .then(d => {{ if (d.ok) location.reload(); else toast('Delete failed: '+(d.error||''), 'err'); }});
}}

// ---------------------------------------------------------------------------
// Folder: Create / Rename / Delete
// ---------------------------------------------------------------------------
function createFolder() {{
  const input = document.getElementById('new-folder-name');
  const name = input.value.trim();
  if (!name) {{ input.focus(); return; }}
  api('/api/folder/create', {{ name }})
    .then(d => {{ if (d.ok) location.reload(); else toast(d.error||'Failed', 'err'); }});
}}

function renameFolder(oldName) {{
  const newName = prompt('Rename folder "' + oldName + '" to:', oldName);
  if (!newName || newName === oldName) return;
  api('/api/folder/rename', {{ old_name: oldName, new_name: newName }})
    .then(d => {{ if (d.ok) location.reload(); else toast(d.error||'Failed', 'err'); }});
}}

function deleteFolder(name) {{
  if (!confirm('Delete folder "' + name + '" and ALL its files?')) return;
  api('/api/folder/delete', {{ name }})
    .then(d => {{ if (d.ok) location.reload(); else toast(d.error||'Failed', 'err'); }});
}}

// ---------------------------------------------------------------------------
// TXT File Editor
// ---------------------------------------------------------------------------
let txtEditCtx = null;

function openTxtEditor(folder, file) {{
  txtEditCtx = {{ folder, file }};
  document.getElementById('txt-modal-title').textContent = file;
  document.getElementById('txt-editor').value = 'Loading\\u2026';
  document.getElementById('txt-modal').classList.add('open');
  fetch('/api/file/content?folder=' + encodeURIComponent(folder) + '&file=' + encodeURIComponent(file))
    .then(r => r.json())
    .then(d => {{ document.getElementById('txt-editor').value = d.content || ''; }});
}}

function closeTxtModal() {{
  document.getElementById('txt-modal').classList.remove('open');
  txtEditCtx = null;
}}

function saveTxtFile() {{
  if (!txtEditCtx) return;
  const content = document.getElementById('txt-editor').value;
  api('/api/file/content', {{ folder: txtEditCtx.folder, file: txtEditCtx.file, content }})
    .then(d => {{
      if (d.ok) {{ toast('Saved'); closeTxtModal(); }}
      else toast(d.error||'Save failed', 'err');
    }});
}}

// ---------------------------------------------------------------------------
// Video Parameters
// ---------------------------------------------------------------------------
function setVideoParam(key, value) {{
  const body = {{}};
  body[key] = parseFloat(value);
  api('/api/video-params', body).then(d => {{
    if (!d.ok) {{ toast(d.error||'Failed', 'err'); return; }}
    // Update displayed values
    for (const [k, v] of Object.entries(d)) {{
      const valEl = document.getElementById('vp-' + k + '-val');
      if (valEl) valEl.textContent = k === 'speed' ? v + 'x' : v;
      const inp = document.getElementById('vp-' + k);
      if (inp && inp.type === 'range') inp.value = v;
    }}
  }});
}}

// Wire up all range sliders with debounced input
document.querySelectorAll('.param-row input[type=range]').forEach(inp => {{
  let timer;
  inp.addEventListener('input', () => {{
    const key = inp.id.replace('vp-', '');
    const valEl = document.getElementById(inp.id + '-val');
    if (valEl) valEl.textContent = key === 'speed' ? inp.value + 'x' : inp.value;
    clearTimeout(timer);
    timer = setTimeout(() => setVideoParam(key, inp.value), 120);
  }});
}});

function resetVideoParams() {{
  api('/api/video-params', {{ reset: true }}).then(d => {{
    if (d.ok) location.reload();
    else toast(d.error||'Failed', 'err');
  }});
}}

// Close modal on overlay click
document.getElementById('txt-modal').addEventListener('click', function(e) {{
  if (e.target === this) closeTxtModal();
}});

// Auto-refresh every 5 seconds
setTimeout(() => location.reload(), 5000);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class _WebHandler(BaseHTTPRequestHandler):
    """HTTP handler with reference to the running Server instance."""

    server_ref: Server

    def __init__(self, server_ref: Server, *args, **kwargs):
        self.server_ref = server_ref
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):  # noqa: A002
        pass

    # ----- GET -----

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._serve_index()
        elif self.path == "/api/status":
            self._serve_json(self._build_status())
        elif self.path == "/api/folders":
            self._serve_json(self._build_folders())
        elif self.path.startswith("/api/file/content"):
            self._handle_read_file()
        elif self.path == "/api/video-params":
            self._serve_json({"ok": True, **self.server_ref.player.video_params})
        else:
            self.send_error(404)

    # ----- POST -----

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/config":
            self._handle_config_update()
        elif self.path == "/api/upload":
            self._handle_upload()
        elif self.path == "/api/rename":
            self._handle_rename()
        elif self.path == "/api/move":
            self._handle_move()
        elif self.path == "/api/delete":
            self._handle_delete()
        elif self.path == "/api/folder/create":
            self._handle_folder_create()
        elif self.path == "/api/folder/rename":
            self._handle_folder_rename()
        elif self.path == "/api/folder/delete":
            self._handle_folder_delete()
        elif self.path == "/api/file/content":
            self._handle_write_file()
        elif self.path == "/api/video-params":
            self._handle_video_params()
        else:
            self.send_error(404)

    # =====================================================================
    # Status helpers
    # =====================================================================

    def _build_status(self) -> dict:
        srv = self.server_ref
        return {
            "playing": srv.player.is_playing,
            "paused": srv.player.paused,
            "current_file": srv.player.current_path,
            "loop": srv.player.loop,
            "play_mode": "paused" if srv.player.paused else ("loop" if srv.player.loop else "play"),
            "volume": srv.player.volume,
            "volume_percent": srv.player.volume_percent,
            "brightness": srv.player.brightness,
            "brightness_percent": srv.player.brightness_percent,
            "dmx": {"address": srv.config.address, "universe": srv.config.universe},
            "mediapath": srv.config.mediapath,
        }

    def _build_folders(self) -> dict:
        mediapath = self.server_ref.config.mediapath
        folders: list[dict] = []
        try:
            for name in sorted(os.listdir(mediapath)):
                folder_path = os.path.join(mediapath, name)
                if os.path.isdir(folder_path):
                    files = sorted(os.listdir(folder_path))
                    folders.append({"name": name, "index": len(folders), "files": files})
        except OSError:
            pass
        return {"mediapath": mediapath, "folders": folders}

    # =====================================================================
    # Index page
    # =====================================================================

    def _serve_index(self, message: str = "") -> None:
        srv = self.server_ref
        status = self._build_status()
        folders_data = self._build_folders()

        # Build folders HTML
        if folders_data["folders"]:
            parts: list[str] = []
            for folder in folders_data["folders"]:
                fname = folder["name"]
                fidx = folder["index"]
                esc = fname.replace("'", "\\'").replace('"', "&quot;")

                parts.append('<div class="folder-card">')
                parts.append(
                    f'<div class="folder-title">'
                    f'<h3><span class="idx">Folder {fidx}</span> {fname}</h3>'
                    f'<div class="folder-actions">'
                    f"<button class=\"btn btn-outline btn-sm\" onclick=\"renameFolder('{esc}')\""
                    f">Rename</button>"
                    f"<button class=\"btn btn-danger btn-sm\" onclick=\"deleteFolder('{esc}')\""
                    f">Delete</button>"
                    f"</div></div>"
                )

                if folder["files"]:
                    parts.append(
                        '<table class="file-table"><tr>'
                        '<th class="col-dmx">DMX</th><th>File</th>'
                        '<th class="col-actions">Actions</th></tr>'
                    )
                    for i, f in enumerate(folder["files"], 1):
                        is_txt = f.lower().endswith(".txt")
                        css_cls = ' class="url-file"' if is_txt else ""
                        esc_f = f.replace("'", "\\'").replace('"', "&quot;")

                        edit_btn = (
                            f"<button class=\"btn btn-warn btn-sm\" "
                            f"onclick=\"openTxtEditor('{esc}','{esc_f}')\">Edit</button> "
                            if is_txt
                            else ""
                        )

                        parts.append(
                            f'<tr draggable="true" data-file="{f}" data-folder="{fname}" '
                            f'ondragstart="onDragStart(event)" ondragend="onDragEnd(event)">'
                            f'<td class="col-dmx">{i}</td>'
                            f'<td{css_cls} id="name-{fname}-{f}">{f}</td>'
                            f'<td class="col-actions">'
                            f"{edit_btn}"
                            f"<button class=\"btn btn-outline btn-sm\" "
                            f"onclick=\"startRename('{esc}','{esc_f}')\">Rename</button> "
                            f"<button class=\"btn btn-danger btn-sm\" "
                            f"onclick=\"deleteFile('{esc}','{esc_f}')\">Delete</button>"
                            f"</td></tr>"
                        )
                    parts.append("</table>")
                else:
                    parts.append(
                        '<p style="opacity:.4;font-size:.85rem;margin:.3rem 0">Empty folder</p>'
                    )

                # Drop zone
                parts.append(
                    f'<div class="dropzone" ondragover="onDragOver(event)" '
                    f'ondragleave="onDragLeave(event)" '
                    f"ondrop=\"onDropToFolder(event,'{esc}')\">"
                    f"Drop files here to move to <b>{fname}</b></div>"
                )
                # Upload
                parts.append(
                    f'<form class="upload-bar" method="post" action="/api/upload" '
                    f'enctype="multipart/form-data">'
                    f'<input type="hidden" name="folder" value="{fname}">'
                    f'<input type="file" name="file" multiple>'
                    f'<button type="submit" class="btn btn-primary btn-sm">Upload</button>'
                    f"</form>"
                )
                parts.append("</div>")
            folders_html = "\n".join(parts)
        else:
            folders_html = '<p style="opacity:.4">No media folders found. Create one above.</p>'

        # Playing status
        if status["paused"]:
            playing_class, playing_label = "paused", "Paused"
        elif status["playing"]:
            playing_class, playing_label = "on", "Playing"
        else:
            playing_class, playing_label = "off", "Stopped"

        play_mode = "Pause" if status["paused"] else ("Loop" if status["loop"] else "Play once")

        vp = srv.player.video_params
        rotation_sel = {0: "", 90: "", 180: "", 270: ""}
        rotation_sel[vp["rotation"]] = 'selected="selected"'

        html = _HTML_TEMPLATE.format(
            message=message,
            playing_class=playing_class,
            playing_label=playing_label,
            current_file=os.path.basename(status["current_file"]) or "\u2014",
            play_mode=play_mode,
            volume_percent=status["volume_percent"],
            volume_raw=status["volume"],
            brightness_percent=status["brightness_percent"],
            brightness_raw=status["brightness"],
            address=srv.config.address,
            universe=srv.config.universe,
            mediapath=srv.config.mediapath,
            folders_html=folders_html,
            vp_contrast=vp["contrast"],
            vp_saturation=vp["saturation"],
            vp_gamma=vp["gamma"],
            vp_speed=vp["speed"],
            vp_rotation=vp["rotation"],
            vp_zoom=vp["zoom"],
            vp_pan_x=vp["pan_x"],
            vp_pan_y=vp["pan_y"],
            rot0_sel=rotation_sel[0],
            rot90_sel=rotation_sel[90],
            rot180_sel=rotation_sel[180],
            rot270_sel=rotation_sel[270],
        )
        self._send_html(html)

    # =====================================================================
    # Config update
    # =====================================================================

    def _handle_config_update(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        params = parse_qs(body)

        srv = self.server_ref
        new_address = int(params.get("address", [str(srv.config.address)])[0])
        new_universe = int(params.get("universe", [str(srv.config.universe)])[0])
        new_mediapath = params.get("mediapath", [srv.config.mediapath])[0].strip()
        if not new_mediapath.endswith("/"):
            new_mediapath += "/"

        universe_changed = new_universe != srv.config.universe

        srv.config.address = new_address
        srv.config.universe = new_universe
        srv.config.mediapath = new_mediapath
        srv.receiver.channellist.__init__(new_address)

        if universe_changed:
            srv.receiver.stop()
            srv.receiver.universe = new_universe
            srv.receiver.start()

        self._save_config(srv.config)
        msg = '<div class="msg ok">Configuration saved and applied.</div>'
        self._serve_index(message=msg)

    # =====================================================================
    # File upload (multipart)
    # =====================================================================

    def _handle_upload(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._serve_json({"ok": False, "error": "Expected multipart/form-data"}, code=400)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary="):].strip('"')
                break

        if not boundary:
            self._serve_json({"ok": False, "error": "Missing boundary"}, code=400)
            return

        parts = self._parse_multipart(body, boundary.encode())

        folder_name = ""
        uploaded: list[str] = []

        for name, filename, data in parts:
            if name == "folder" and not filename:
                folder_name = data.decode("utf-8").strip()

        if not folder_name:
            self._serve_json({"ok": False, "error": "Missing folder"}, code=400)
            return

        mediapath = self.server_ref.config.mediapath
        folder_path = os.path.join(mediapath, folder_name)
        if not os.path.isdir(folder_path):
            self._serve_json({"ok": False, "error": "Folder not found"}, code=404)
            return

        for name, filename, data in parts:
            if name in ("file", "files") and filename:
                safe_name = os.path.basename(filename)
                if not safe_name:
                    continue
                dest = os.path.join(folder_path, safe_name)
                with open(dest, "wb") as out:
                    out.write(data)
                uploaded.append(safe_name)
                print(f"Uploaded '{safe_name}' to '{folder_name}'")

        # Always redirect back — works for both form submit and JS fetch
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    @staticmethod
    def _parse_multipart(body: bytes, boundary: bytes) -> list[tuple[str, str, bytes]]:
        """Parse a multipart/form-data body into (name, filename, data) tuples."""
        parts: list[tuple[str, str, bytes]] = []
        delimiter = b"--" + boundary
        sections = body.split(delimiter)

        for section in sections:
            if section in (b"", b"--", b"--\r\n", b"\r\n"):
                continue
            section = section.strip(b"\r\n")
            if section == b"--":
                break

            header_end = section.find(b"\r\n\r\n")
            if header_end == -1:
                continue

            header_data = section[:header_end].decode("utf-8", errors="replace")
            file_data = section[header_end + 4:]
            if file_data.endswith(b"\r\n"):
                file_data = file_data[:-2]

            name = ""
            filename = ""
            for line in header_data.split("\r\n"):
                if line.lower().startswith("content-disposition:"):
                    for param in line.split(";"):
                        param = param.strip()
                        if param.startswith("name="):
                            name = param[5:].strip('"')
                        elif param.startswith("filename="):
                            filename = param[9:].strip('"')

            if name:
                parts.append((name, filename, file_data))

        return parts

    # =====================================================================
    # File rename
    # =====================================================================

    def _handle_rename(self) -> None:
        data = self._read_json_body()
        if data is None:
            return

        folder = data.get("folder", "")
        old_name = data.get("old_name", "")
        new_name = data.get("new_name", "")

        if not all([folder, old_name, new_name]):
            self._serve_json({"ok": False, "error": "Missing fields"}, code=400)
            return
        if "/" in new_name or "\\" in new_name:
            self._serve_json({"ok": False, "error": "Invalid filename"}, code=400)
            return

        mediapath = self.server_ref.config.mediapath
        old_path = os.path.join(mediapath, folder, old_name)
        new_path = os.path.join(mediapath, folder, new_name)

        if not os.path.exists(old_path):
            self._serve_json({"ok": False, "error": "File not found"}, code=404)
            return
        if os.path.exists(new_path):
            self._serve_json({"ok": False, "error": "Target name already exists"}, code=409)
            return

        os.rename(old_path, new_path)
        print(f"Renamed '{old_name}' \u2192 '{new_name}' in '{folder}'")
        self._serve_json({"ok": True})

    # =====================================================================
    # File move
    # =====================================================================

    def _handle_move(self) -> None:
        data = self._read_json_body()
        if data is None:
            return

        file_name = data.get("file", "")
        from_folder = data.get("from_folder", "")
        to_folder = data.get("to_folder", "")

        if not all([file_name, from_folder, to_folder]):
            self._serve_json({"ok": False, "error": "Missing fields"}, code=400)
            return

        mediapath = self.server_ref.config.mediapath
        src = os.path.join(mediapath, from_folder, file_name)
        dst_dir = os.path.join(mediapath, to_folder)
        dst = os.path.join(dst_dir, file_name)

        if not os.path.exists(src):
            self._serve_json({"ok": False, "error": "Source file not found"}, code=404)
            return
        if not os.path.isdir(dst_dir):
            self._serve_json({"ok": False, "error": "Target folder not found"}, code=404)
            return
        if os.path.exists(dst):
            self._serve_json({"ok": False, "error": "File already exists in target folder"}, code=409)
            return

        shutil.move(src, dst)
        print(f"Moved '{file_name}' from '{from_folder}' to '{to_folder}'")
        self._serve_json({"ok": True})

    # =====================================================================
    # File delete
    # =====================================================================

    def _handle_delete(self) -> None:
        data = self._read_json_body()
        if data is None:
            return

        folder = data.get("folder", "")
        file_name = data.get("file", "")

        if not all([folder, file_name]):
            self._serve_json({"ok": False, "error": "Missing fields"}, code=400)
            return

        mediapath = self.server_ref.config.mediapath
        path = os.path.join(mediapath, folder, file_name)

        if not os.path.exists(path):
            self._serve_json({"ok": False, "error": "File not found"}, code=404)
            return

        os.remove(path)
        print(f"Deleted '{file_name}' from '{folder}'")
        self._serve_json({"ok": True})

    # =====================================================================
    # Folder management
    # =====================================================================

    def _handle_folder_create(self) -> None:
        data = self._read_json_body()
        if data is None:
            return

        name = data.get("name", "").strip()
        if not name:
            self._serve_json({"ok": False, "error": "Missing folder name"}, code=400)
            return
        if "/" in name or "\\" in name:
            self._serve_json({"ok": False, "error": "Invalid folder name"}, code=400)
            return

        mediapath = self.server_ref.config.mediapath
        folder_path = os.path.join(mediapath, name)

        if os.path.exists(folder_path):
            self._serve_json({"ok": False, "error": "Folder already exists"}, code=409)
            return

        os.makedirs(folder_path, exist_ok=True)
        print(f"Created folder '{name}'")
        self._serve_json({"ok": True})

    def _handle_folder_rename(self) -> None:
        data = self._read_json_body()
        if data is None:
            return

        old_name = data.get("old_name", "").strip()
        new_name = data.get("new_name", "").strip()

        if not all([old_name, new_name]):
            self._serve_json({"ok": False, "error": "Missing fields"}, code=400)
            return
        if "/" in new_name or "\\" in new_name:
            self._serve_json({"ok": False, "error": "Invalid folder name"}, code=400)
            return

        mediapath = self.server_ref.config.mediapath
        old_path = os.path.join(mediapath, old_name)
        new_path = os.path.join(mediapath, new_name)

        if not os.path.isdir(old_path):
            self._serve_json({"ok": False, "error": "Folder not found"}, code=404)
            return
        if os.path.exists(new_path):
            self._serve_json({"ok": False, "error": "Target name already exists"}, code=409)
            return

        os.rename(old_path, new_path)
        print(f"Renamed folder '{old_name}' \u2192 '{new_name}'")
        self._serve_json({"ok": True})

    def _handle_folder_delete(self) -> None:
        data = self._read_json_body()
        if data is None:
            return

        name = data.get("name", "").strip()
        if not name:
            self._serve_json({"ok": False, "error": "Missing folder name"}, code=400)
            return

        mediapath = self.server_ref.config.mediapath
        folder_path = os.path.join(mediapath, name)

        if not os.path.isdir(folder_path):
            self._serve_json({"ok": False, "error": "Folder not found"}, code=404)
            return

        shutil.rmtree(folder_path)
        print(f"Deleted folder '{name}' and all contents")
        self._serve_json({"ok": True})

    # =====================================================================
    # TXT file read / write
    # =====================================================================

    def _handle_read_file(self) -> None:
        """GET /api/file/content?folder=X&file=Y — read text file contents."""
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = parse_qs(query)
        folder = params.get("folder", [""])[0]
        file_name = params.get("file", [""])[0]

        if not all([folder, file_name]):
            self._serve_json({"ok": False, "error": "Missing params"}, code=400)
            return

        mediapath = self.server_ref.config.mediapath
        path = os.path.join(mediapath, folder, file_name)

        if not os.path.isfile(path):
            self._serve_json({"ok": False, "error": "File not found"}, code=404)
            return

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            self._serve_json({"ok": True, "content": content})
        except OSError as e:
            self._serve_json({"ok": False, "error": str(e)}, code=500)

    def _handle_write_file(self) -> None:
        """POST /api/file/content — write text file contents."""
        data = self._read_json_body()
        if data is None:
            return

        folder = data.get("folder", "")
        file_name = data.get("file", "")
        content = data.get("content", "")

        if not all([folder, file_name]):
            self._serve_json({"ok": False, "error": "Missing fields"}, code=400)
            return

        mediapath = self.server_ref.config.mediapath
        path = os.path.join(mediapath, folder, file_name)

        if not os.path.isfile(path):
            self._serve_json({"ok": False, "error": "File not found"}, code=404)
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            print(f"Updated content of '{file_name}' in '{folder}'")
            self._serve_json({"ok": True})
        except OSError as e:
            self._serve_json({"ok": False, "error": str(e)}, code=500)

    # =====================================================================
    # Video parameters
    # =====================================================================

    def _handle_video_params(self) -> None:
        """POST /api/video-params — update video effect parameters."""
        data = self._read_json_body()
        if data is None:
            return

        player = self.server_ref.player

        # Check for reset request
        if data.get("reset"):
            player.reset_video_params()
            self._serve_json({"ok": True, **player.video_params})
            return

        # Apply individual parameters
        if "contrast" in data:
            player.contrast = int(data["contrast"])
        if "saturation" in data:
            player.saturation = int(data["saturation"])
        if "gamma" in data:
            player.gamma = int(data["gamma"])
        if "speed" in data:
            player.speed = float(data["speed"])
        if "rotation" in data:
            player.rotation = int(data["rotation"])
        if "zoom" in data:
            player.zoom = float(data["zoom"])
        if "pan_x" in data:
            player.pan_x = float(data["pan_x"])
        if "pan_y" in data:
            player.pan_y = float(data["pan_y"])

        self._serve_json({"ok": True, **player.video_params})

    # =====================================================================
    # Shared helpers
    # =====================================================================

    def _read_json_body(self) -> dict | None:
        """Read and parse JSON request body. Sends 400 on error."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            return json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._serve_json({"ok": False, "error": "Invalid JSON"}, code=400)
            return None

    @staticmethod
    def _save_config(config) -> None:
        """Write current config back to the INI file."""
        import configparser

        path = "/home/pi/config.txt"
        parser = configparser.ConfigParser()
        parser["DMX"] = {
            "Address": str(config.address),
            "Universe": str(config.universe),
            "MediaPath": config.mediapath,
        }
        parser["Web"] = {"Port": str(config.web_port)}
        try:
            with open(path, "w") as f:
                parser.write(f)
        except OSError as e:
            print(f"Failed to save config: {e}")

    def _serve_json(self, data: dict, code: int = 200) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def start_web_server(server: Server, port: int = 8080) -> None:
    """Start the web interface on a daemon thread.

    Args:
        server: The running Server instance to expose via the web UI.
        port: TCP port to listen on.
    """
    handler = partial(_WebHandler, server)
    httpd = HTTPServer(("", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    print(f"Web interface running at http://0.0.0.0:{port}/")
