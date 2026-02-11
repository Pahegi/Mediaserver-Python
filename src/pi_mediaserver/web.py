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
import logging
import os
import shutil
import socket
import subprocess
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

if TYPE_CHECKING:
    from pi_mediaserver.main import Server

log = logging.getLogger(__name__)


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

  /* Dashboard layout */
  .dashboard {{ display: grid; grid-template-columns: 1fr; gap: 1rem; margin-bottom: 1rem; }}
  @media (min-width: 768px) {{ .dashboard {{ grid-template-columns: 1fr 1fr; }} }}
  .stat-card {{ background: var(--pico-card-background-color);
               border: 1px solid var(--pico-muted-border-color);
               border-radius: .5rem; padding: 1rem; }}
  .stat-card.full {{ grid-column: 1 / -1; }}
  .stat-card h4 {{ margin: 0 0 .6rem; font-size: .85rem; text-transform: uppercase;
                   letter-spacing: .06em; font-weight: 700;
                   color: var(--accent); }}
  .now-playing {{ display: flex; align-items: center; gap: .5rem; margin-bottom: .5rem;
                  font-size: .95rem; font-weight: 600; overflow: hidden; }}
  .now-playing .np-file {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .stat-row {{ display: flex; justify-content: space-between; align-items: center;
               padding: .2rem 0; font-size: .85rem; }}
  .stat-row .s-label {{ opacity: .45; min-width: 5.5rem; flex-shrink: 0; }}
  .stat-row .s-value {{ text-align: right; display: flex; align-items: center; gap: .4rem;
                        justify-content: flex-end; font-variant-numeric: tabular-nums; }}
  .s-pct {{ display: inline-block; min-width: 3.5rem; text-align: right;
            font-variant-numeric: tabular-nums; font-family: monospace; }}
  .s-raw {{ font-size: .72rem; opacity: .35; white-space: nowrap; }}
  .section-sep {{ border: none; border-top: 1px solid var(--pico-muted-border-color);
                  margin: .35rem 0; opacity: .15; }}

  /* DMX control two-column grid */
  .dmx-ctrl {{ display: grid; grid-template-columns: 1fr; gap: 0 2rem; }}
  @media (min-width: 768px) {{ .dmx-ctrl {{ grid-template-columns: 1fr 1fr; }} }}
  .dmx-ctrl .stat-row {{ padding: .18rem 0; }}

  /* Mini progress bars */
  .mini-bar {{ width: 48px; height: 4px; background: var(--pico-muted-border-color);
               border-radius: 2px; overflow: hidden; display: inline-block; vertical-align: middle;
               flex-shrink: 0; }}
  .mini-bar-fill {{ height: 100%; border-radius: 2px; transition: width .4s; }}
  .fill-ok {{ background: var(--ok); }}
  .fill-warn {{ background: var(--warn); }}
  .fill-err {{ background: var(--err); }}
  .fill-accent {{ background: var(--accent); }}

  /* Signal indicator dot */
  .sig {{ display: inline-block; width: .45rem; height: .45rem; border-radius: 50%;
          vertical-align: middle; margin-left: .4rem; }}
  .sig.active {{ background: var(--ok); box-shadow: 0 0 4px var(--ok); }}
  .sig.inactive {{ background: #555; }}
  /* Throttle warning (hidden when ok) */
  .throttle-row {{ transition: opacity .3s; }}
  .throttle-row.hidden {{ display: none; }}

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

  /* Drop zone — combined drag & click upload */
  .dropzone {{ border: 2px dashed var(--pico-muted-border-color); border-radius: 8px;
               padding: 1.2rem .8rem; text-align: center; font-size: .85rem; opacity: .6;
               transition: all .2s; margin-top: .6rem; cursor: pointer; position: relative; }}
  .dropzone:hover {{ border-color: var(--accent); opacity: .85; }}
  .dropzone.active {{ border-color: var(--accent); opacity: 1; background: var(--accent-dim); }}
  .dropzone input[type=file] {{ position: absolute; inset: 0; opacity: 0; cursor: pointer; }}
  .dropzone .dz-icon {{ font-size: 1.6rem; display: block; margin-bottom: .3rem; opacity: .5; }}
  .dropzone .dz-hint {{ font-size: .78rem; opacity: .45; margin-top: .2rem; }}

  /* Pending upload list */
  .pending-files {{ margin-top: .5rem; font-size: .82rem; }}
  .pending-files .pf-item {{ display: flex; justify-content: space-between; align-items: center;
                             padding: .2rem .5rem; border-bottom: 1px solid var(--pico-muted-border-color); }}
  .pending-files .pf-name {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .pending-files .pf-size {{ opacity: .45; font-size: .75rem; white-space: nowrap; margin-left: .5rem; }}
  .pending-files .pf-remove {{ cursor: pointer; color: var(--err); font-weight: 700;
                               border: none; background: none; padding: 0 .3rem; font-size: .9rem; }}
  .upload-actions {{ display: flex; gap: .5rem; margin-top: .5rem; align-items: center; }}
  .upload-progress {{ flex: 1; height: 4px; background: var(--pico-muted-border-color);
                      border-radius: 2px; overflow: hidden; display: none; }}
  .upload-progress .bar {{ height: 100%; background: var(--accent); width: 0%;
                           transition: width .2s; }}

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
  .modal-overlay.open {{ display: flex !important; position: fixed !important; inset: 0 !important;
                         z-index: 9999 !important; }}
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
  .config-grid input, .config-grid select {{ padding: .35rem .5rem; margin-bottom: 0; }}

  /* Collapse sections */
  details {{ margin-bottom: 1rem; }}
  details summary {{ cursor: pointer; font-weight: 600; }}

  /* Connection indicator */
  .conn {{ display: inline-block; width: .6rem; height: .6rem; border-radius: 50%;
           margin-right: .4rem; vertical-align: middle; }}
  .conn.ok {{ background: var(--ok); box-shadow: 0 0 4px var(--ok); }}
  .conn.lost {{ background: var(--err); box-shadow: 0 0 4px var(--err); animation: blink 1s infinite; }}
  @keyframes blink {{ 50% {{ opacity: .3; }} }}
</style>
</head>
<body>
<div class="page-header">
  <h1><span id="conn" class="conn ok"></span>&#9654; Pi Mediaserver</h1>
  <p>DMX/sACN-controlled media server &bull; v3.5</p>
</div>

{message}

<div class="dashboard">
  <!-- Now Playing -->
  <div class="stat-card">
    <h4>Now Playing</h4>
    <div class="now-playing">
      <span id="st-badge" class="badge {playing_class}">{playing_label}</span>
      <span class="np-file" id="st-file" title="{current_file}">{current_file}</span>
    </div>
    <div class="stat-row"><span class="s-label">Resolution</span><span class="s-value" id="st-res">{resolution}</span></div>
    <div class="stat-row"><span class="s-label">FPS</span><span class="s-value" id="st-fps">{fps}</span></div>
    <div class="stat-row"><span class="s-label">Dropped</span><span class="s-value" id="st-drop">{dropped_frames}</span></div>
  </div>
  <!-- System -->
  <div class="stat-card">
    <h4>System</h4>
    <div class="stat-row"><span class="s-label">CPU</span><span class="s-value" id="st-cpu"><span class="s-pct">{cpu_percent}%</span> <span class="mini-bar"><span class="mini-bar-fill {cpu_bar_class}" style="width:{cpu_percent}%"></span></span></span></div>
    <div class="stat-row"><span class="s-label">RAM</span><span class="s-value" id="st-ram"><span class="s-pct">{ram_percent}%</span> <span class="mini-bar"><span class="mini-bar-fill {ram_bar_class}" style="width:{ram_percent}%"></span></span> <small style="opacity:.35">{ram_used}/{ram_total} MB</small></span></div>
    <hr class="section-sep">
    <div class="stat-row"><span class="s-label">CPU Temp</span><span class="s-value {cpu_temp_class}" id="st-ctemp">{cpu_temp}</span></div>
    <div class="stat-row"><span class="s-label">GPU Temp</span><span class="s-value {gpu_temp_class}" id="st-gtemp">{gpu_temp}</span></div>
    <div class="stat-row throttle-row {throttle_hidden}" id="st-throttle-row"><span class="s-label" style="color:var(--warn)">Throttle</span><span class="s-value" id="st-throttle"><span class="badge off">{throttle}</span></span></div>
  </div>
  <!-- DMX Control (full-width) -->
  <div class="stat-card full">
    <h4>DMX Control <small style="opacity:.35;font-weight:400;text-transform:none;letter-spacing:0">&mdash; {address}.{universe}</small> <span class="sig {dmx_sig_class}" id="st-dmx-sig" title="sACN signal"></span></h4>
    <div class="dmx-ctrl">
      <div>
        <div class="stat-row"><span class="s-label">Mode</span><span class="s-value" id="st-mode">{play_mode} <span class="s-raw" id="st-mode-raw">DMX {dmx_playmode_raw}</span></span></div>
        <div class="stat-row"><span class="s-label">Volume</span><span class="s-value" id="st-vol">{volume_percent}% <span class="mini-bar"><span class="mini-bar-fill fill-accent" style="width:{volume_percent}%"></span></span> <span class="s-raw">DMX {volume_raw}</span></span></div>
        <div class="stat-row"><span class="s-label">Brightness</span><span class="s-value" id="st-bri">{brightness_percent}% <span class="mini-bar"><span class="mini-bar-fill fill-accent" style="width:{brightness_percent}%"></span></span> <span class="s-raw">DMX {brightness_raw}</span></span></div>
        <div class="stat-row"><span class="s-label">Speed</span><span class="s-value" id="vp-speed">{vp_speed}x <span class="s-raw" id="vp-speed-raw">DMX {dmx_speed_raw}</span></span></div>
        <div class="stat-row"><span class="s-label">Rotation</span><span class="s-value" id="vp-rotation">{vp_rotation}&deg; <span class="s-raw" id="vp-rotation-raw">DMX {dmx_rotation_raw}</span></span></div>
      </div>
      <div>
        <div class="stat-row"><span class="s-label">Contrast</span><span class="s-value" id="vp-contrast">{vp_contrast} <span class="s-raw" id="vp-contrast-raw">DMX {dmx_contrast_raw}</span></span></div>
        <div class="stat-row"><span class="s-label">Saturation</span><span class="s-value" id="vp-saturation">{vp_saturation} <span class="s-raw" id="vp-saturation-raw">DMX {dmx_saturation_raw}</span></span></div>
        <div class="stat-row"><span class="s-label">Gamma</span><span class="s-value" id="vp-gamma">{vp_gamma} <span class="s-raw" id="vp-gamma-raw">DMX {dmx_gamma_raw}</span></span></div>
        <div class="stat-row"><span class="s-label">Zoom</span><span class="s-value" id="vp-zoom">{vp_zoom} <span class="s-raw" id="vp-zoom-raw">DMX {dmx_zoom_raw}</span></span></div>
        <div class="stat-row"><span class="s-label">Pan X</span><span class="s-value" id="vp-pan_x">{vp_pan_x} <span class="s-raw" id="vp-pan_x-raw">DMX {dmx_pan_x_raw}</span></span></div>
        <div class="stat-row"><span class="s-label">Pan Y</span><span class="s-value" id="vp-pan_y">{vp_pan_y} <span class="s-raw" id="vp-pan_y-raw">DMX {dmx_pan_y_raw}</span></span></div>
      </div>
    </div>
  </div>
</div>

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
    <span class="ch">CH11</span><span>Zoom &mdash; 0=0.1x, 128=1.0x, 255=2.0x</span>
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
      <label for="failmode">Fail Behavior</label>
      <select id="failmode" name="failmode" style="padding:.35rem .5rem;margin-bottom:0">
        <option value="hold" {fail_hold_sel}>Hold &mdash; keep last state</option>
        <option value="blackout" {fail_blackout_sel}>Blackout &mdash; stop playback</option>
      </select>
      <label for="failosd">Signal Loss Message</label>
      <select id="failosd" name="failosd" style="padding:.35rem .5rem;margin-bottom:0">
        <option value="on" {fail_osd_on_sel}>On &mdash; show message on screen</option>
        <option value="off" {fail_osd_off_sel}>Off &mdash; no message</option>
      </select>
    </div>
    <button type="submit" class="btn btn-primary" style="margin-top:.6rem;width:100%">
      Save &amp; Restart Receiver</button>
  </form>
</details>

<!-- WiFi Settings -->
<details id="wifi-settings">
  <summary>WiFi Settings</summary>
  <div style="margin-top:.6rem">
    <div class="stat-row" style="margin-bottom:.5rem">
      <span class="s-label">Status</span>
      <span class="s-value" id="wifi-status-text">&mdash;</span>
    </div>
    <div class="stat-row" style="margin-bottom:.5rem">
      <span class="s-label">Network</span>
      <span class="s-value" id="wifi-ssid">—</span>
    </div>
    <div class="stat-row" style="margin-bottom:.5rem">
      <span class="s-label">Signal</span>
      <span class="s-value" id="wifi-signal">—</span>
    </div>
    <div class="stat-row" style="margin-bottom:.8rem">
      <span class="s-label">IP Address</span>
      <span class="s-value" id="wifi-ip">—</span>
    </div>
    <hr class="section-sep">
    <div style="margin-top:.8rem">
      <label for="wifi-network-select" style="font-size:.85rem;opacity:.7">Connect to network:</label>
      <div style="display:flex;gap:.5rem;margin-top:.3rem">
        <select id="wifi-network-select" style="flex:1;padding:.35rem .5rem;margin-bottom:0">
          <option value="">Scanning...</option>
        </select>
        <button class="btn btn-outline btn-sm" onclick="wifiScan()" title="Rescan">\u21BB</button>
      </div>
      <input type="password" id="wifi-password" placeholder="Password (if required)"
             style="margin-top:.5rem;margin-bottom:0">
      <div style="display:flex;gap:.5rem;margin-top:.6rem">
        <button class="btn btn-primary btn-sm" onclick="wifiConnect()" style="flex:1">Connect</button>
        <button class="btn btn-outline btn-sm" onclick="wifiDisconnect()">Disconnect</button>
      </div>
    </div>
    <hr class="section-sep" style="margin-top:.8rem">
    <div style="margin-top:.8rem;display:flex;gap:.5rem">
      <button class="btn btn-outline btn-sm" onclick="wifiEnable()" id="wifi-enable-btn">Enable WiFi</button>
      <button class="btn btn-danger btn-sm" onclick="wifiDisable()" id="wifi-disable-btn">Disable WiFi</button>
    </div>
  </div>
</details>

<!-- NDI Settings -->
<details id="ndi-settings">
  <summary>NDI Sources</summary>
  <div style="margin-top:.6rem">
    <div class="stat-row" style="margin-bottom:.5rem">
      <span class="s-label">SDK</span>
      <span class="s-value" id="ndi-status-text">&mdash;</span>
    </div>
    <div class="stat-row" style="margin-bottom:.5rem">
      <span class="s-label">Playing</span>
      <span class="s-value" id="ndi-source-text">\u2014</span>
    </div>
    <div class="stat-row" style="margin-bottom:.5rem">
      <span class="s-label">Bandwidth</span>
      <span class="s-value">
        <button class="btn btn-sm" id="ndi-bw-lowest" onclick="setNdiBandwidth('lowest')" style="padding:.15rem .4rem;font-size:.7rem">Lowest</button>
        <button class="btn btn-sm" id="ndi-bw-highest" onclick="setNdiBandwidth('highest')" style="padding:.15rem .4rem;font-size:.7rem">Highest</button>
      </span>
    </div>
    <div id="ndi-sources-list" style="margin-top:.8rem"></div>
    <div style="margin-top:.6rem">
      <button class="btn btn-outline btn-sm" onclick="ndiRefreshSources()">Refresh Sources</button>
    </div>
    <p style="font-size:.75rem;opacity:.5;margin:.8rem 0 0">
      Add discovered sources to folders as <code>.ndi</code> files for DMX control.
    </p>
  </div>
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

<!-- Add Source to Folder Modal -->
<div class="modal-overlay" id="add-source-modal">
  <div class="modal">
    <h3 id="add-source-title">Add Source to Folder</h3>
    <div style="margin-bottom:.6rem">
      <label style="font-size:.8rem;opacity:.6">Content</label>
      <input type="text" id="add-source-content" readonly style="width:100%;padding:.4rem;margin-top:.2rem;background:var(--card);color:var(--fg);border:1px solid #444;border-radius:4px">
    </div>
    <div style="margin-bottom:.6rem">
      <label style="font-size:.8rem;opacity:.6">Folder</label>
      <select id="add-source-folder" style="width:100%;padding:.4rem;margin-top:.2rem;background:var(--card);color:var(--fg);border:1px solid #444;border-radius:4px">
        {folder_options}
      </select>
    </div>
    <div style="margin-bottom:.6rem">
      <label style="font-size:.8rem;opacity:.6">Filename</label>
      <input type="text" id="add-source-filename" placeholder="source.ndi" style="width:100%;padding:.4rem;margin-top:.2rem;background:var(--card);color:var(--fg);border:1px solid #444;border-radius:4px">
    </div>
    <div class="modal-buttons">
      <button class="btn btn-outline" onclick="closeAddSourceModal()">Cancel</button>
      <button class="btn btn-primary" onclick="submitAddSource()">Add</button>
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

  // External file drop (from desktop) — add to pending list
  if (e.dataTransfer.files && e.dataTransfer.files.length > 0 && !dragData) {{
    // Find the folder index from the dropzone id
    const dz = e.target.closest('.dropzone');
    const fidx = dz ? dz.id.replace('dz-', '') : null;
    if (fidx !== null) {{
      addToPending(parseInt(fidx), targetFolder, e.dataTransfer.files);
    }}
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
  const cell = document.getElementById('name-' + folder + '-' + oldName);
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
// TXT/NDI Source File Editor
// ---------------------------------------------------------------------------
let txtEditCtx = null;

function openTxtEditor(folder, file) {{
  txtEditCtx = {{ folder: folder, file: file, isNew: false }};
  document.getElementById('txt-modal-title').textContent = 'Edit: ' + file;
  var editor = document.getElementById('txt-editor');
  editor.value = 'Loading...';
  document.getElementById('txt-modal').classList.add('open');
  var url = '/api/file/content?folder=' + encodeURIComponent(folder) + '&file=' + encodeURIComponent(file);
  fetch(url).then(function(r) {{ return r.json(); }}).then(function(d) {{
    editor.value = d.content || '';
  }}).catch(function(e) {{ editor.value = 'Error: ' + e.message; }});
}}

function createSourceFile(folder) {{
  var name = prompt('Enter filename (e.g. stream.txt or source.ndi):');
  if (!name) return;
  if (!name.endsWith('.txt') && !name.endsWith('.ndi')) {{
    toast('Filename must end with .txt or .ndi', 'err');
    return;
  }}
  txtEditCtx = {{ folder: folder, file: name, isNew: true }};
  document.getElementById('txt-modal-title').textContent = 'Create: ' + name;
  document.getElementById('txt-editor').value = name.endsWith('.ndi')
    ? 'ndi://HOSTNAME (Source Name)'
    : 'https://example.com/stream.m3u8';
  document.getElementById('txt-modal').classList.add('open');
}}

function closeTxtModal() {{
  document.getElementById('txt-modal').classList.remove('open');
  txtEditCtx = null;
}}

function saveTxtFile() {{
  if (!txtEditCtx) return;
  var content = document.getElementById('txt-editor').value;
  var endpoint = txtEditCtx.isNew ? '/api/file/create' : '/api/file/content';
  api(endpoint, {{ folder: txtEditCtx.folder, file: txtEditCtx.file, content: content }})
    .then(function(d) {{
      if (d.ok) {{
        toast(txtEditCtx.isNew ? 'Created' : 'Saved');
        var wasNew = txtEditCtx.isNew;
        closeTxtModal();
        if (wasNew) location.reload();
      }}
      else toast(d.error||'Save failed', 'err');
    }});
}}

// Close modal on overlay click
document.getElementById('txt-modal').addEventListener('click', function(e) {{
  if (e.target === this) closeTxtModal();
}});

// ---------------------------------------------------------------------------
// Add Source to Folder Modal
// ---------------------------------------------------------------------------
function openAddSourceModal(content, suggestedFilename) {{
  document.getElementById('add-source-content').value = content;
  document.getElementById('add-source-filename').value = suggestedFilename || '';
  var ext = suggestedFilename && suggestedFilename.endsWith('.ndi') ? '.ndi' : '.txt';
  document.getElementById('add-source-title').textContent = 'Add ' + ext.toUpperCase().slice(1) + ' Source to Folder';
  document.getElementById('add-source-modal').classList.add('open');
}}

function closeAddSourceModal() {{
  document.getElementById('add-source-modal').classList.remove('open');
}}

function submitAddSource() {{
  var folder = document.getElementById('add-source-folder').value;
  var filename = document.getElementById('add-source-filename').value.trim();
  var content = document.getElementById('add-source-content').value;
  if (!folder) {{ toast('Select a folder', 'err'); return; }}
  if (!filename) {{ toast('Enter a filename', 'err'); return; }}
  if (!filename.endsWith('.txt') && !filename.endsWith('.ndi')) {{
    toast('Filename must end with .txt or .ndi', 'err'); return;
  }}
  api('/api/file/create', {{ folder: folder, file: filename, content: content }})
    .then(function(d) {{
      if (d.ok) {{ toast('Source added'); closeAddSourceModal(); location.reload(); }}
      else toast(d.error||'Failed', 'err');
    }});
}}

document.getElementById('add-source-modal').addEventListener('click', function(e) {{
  if (e.target === this) closeAddSourceModal();
}});

// ---------------------------------------------------------------------------
// Upload: File selection, preview, and upload
// ---------------------------------------------------------------------------
const pendingFiles = {{}};  // fidx -> {{ folder, files: File[] }}

function formatSize(bytes) {{
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
  return (bytes / 1073741824).toFixed(2) + ' GB';
}}

function onFilesSelected(input, folder, fidx) {{
  if (input.files.length > 0) {{
    addToPending(fidx, folder, input.files);
    input.value = '';
  }}
}}

function addToPending(fidx, folder, fileList) {{
  if (!pendingFiles[fidx]) pendingFiles[fidx] = {{ folder, files: [] }};
  for (const f of fileList) {{
    // Avoid duplicates by name
    if (!pendingFiles[fidx].files.some(e => e.name === f.name)) {{
      pendingFiles[fidx].files.push(f);
    }}
  }}
  renderPending(fidx);
}}

function removePending(fidx, idx) {{
  if (pendingFiles[fidx]) {{
    pendingFiles[fidx].files.splice(idx, 1);
    renderPending(fidx);
  }}
}}

function clearPending(fidx) {{
  delete pendingFiles[fidx];
  renderPending(fidx);
}}

function renderPending(fidx) {{
  const container = document.getElementById('pf-' + fidx);
  const actions = document.getElementById('ua-' + fidx);
  const count = document.getElementById('uc-' + fidx);
  if (!container) return;

  const data = pendingFiles[fidx];
  if (!data || data.files.length === 0) {{
    container.innerHTML = '';
    actions.style.display = 'none';
    delete pendingFiles[fidx];
    return;
  }}

  let html = '';
  let totalSize = 0;
  data.files.forEach((f, i) => {{
    totalSize += f.size;
    html += '<div class="pf-item">'
          + '<span class="pf-name">' + f.name + '</span>'
          + '<span class="pf-size">' + formatSize(f.size) + '</span>'
          + '<button class="pf-remove" onclick="removePending(' + fidx + ',' + i + ')">&times;</button>'
          + '</div>';
  }});
  container.innerHTML = html;
  count.textContent = data.files.length + ' file(s), ' + formatSize(totalSize);
  actions.style.display = 'flex';
  document.getElementById('up-' + fidx).style.display = 'none';
  document.getElementById('upb-' + fidx).style.width = '0%';
}}

function uploadPending(folder, fidx) {{
  const data = pendingFiles[fidx];
  if (!data || data.files.length === 0) return;

  const fd = new FormData();
  fd.append('folder', folder);
  for (const f of data.files) fd.append('file', f);

  const progressBar = document.getElementById('up-' + fidx);
  const progressFill = document.getElementById('upb-' + fidx);
  progressBar.style.display = 'block';
  progressFill.style.width = '0%';

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');
  xhr.upload.onprogress = (e) => {{
    if (e.lengthComputable) {{
      progressFill.style.width = Math.round(e.loaded / e.total * 100) + '%';
    }}
  }};
  xhr.onload = () => {{
    if (xhr.status >= 200 && xhr.status < 300) {{
      toast('Uploaded ' + data.files.length + ' file(s)');
      clearPending(fidx);
      location.reload();
    }} else {{
      toast('Upload failed', 'err');
    }}
  }};
  xhr.onerror = () => toast('Upload failed', 'err');
  xhr.send(fd);
}}

// Live status polling (no page reload)
let _failCount = 0;
function refreshStatus() {{
  const ctrl = new AbortController();
  const tid = setTimeout(() => ctrl.abort(), 3000);
  fetch('/api/status', {{signal: ctrl.signal}}).then(r => {{
    clearTimeout(tid);
    if (!r.ok) throw new Error(r.status);
    return r.json();
  }}).then(d => {{
    _failCount = 0;
    document.getElementById('conn').className = 'conn ok';

    // Helpers
    function _barCls(pct) {{ return pct > 85 ? 'fill-err' : pct > 65 ? 'fill-warn' : 'fill-ok'; }}
    function _tempCls(s) {{ var n=parseFloat(s); return n>80?'fill-err':n>70?'fill-warn':''; }}
    function _bar(pct, cls) {{ return '<span class="mini-bar"><span class="mini-bar-fill '+cls+'" style="width:'+pct+'%"></span></span>'; }}

    // Now Playing
    const badge = document.getElementById('st-badge');
    const isNdi = d.ndi && d.ndi.playing;
    if (isNdi) {{
      badge.textContent = 'NDI';
      badge.className = 'badge on';
    }} else {{
      badge.textContent = d.paused ? 'Paused' : (d.playing ? 'Playing' : 'Stopped');
      badge.className = 'badge ' + (d.paused ? 'paused' : (d.playing ? 'on' : 'off'));
    }}
    const fEl = document.getElementById('st-file');
    var fname;
    if (isNdi && d.ndi.source) {{
      fname = d.ndi.source;
    }} else {{
      fname = d.current_file ? d.current_file.split('/').pop() : '\u2014';
    }}
    fEl.textContent = fname; fEl.title = isNdi ? ('NDI: ' + (d.ndi.source || '')) : (d.current_file || '');
    document.getElementById('st-res').textContent = d.resolution || '\u2014';
    document.getElementById('st-fps').textContent = d.fps ? d.fps + ' fps' : '\u2014';
    var drops = d.dropped_frames != null ? d.dropped_frames : 0;
    var dropCls = drops > 1000 ? 'color:var(--err)' : drops > 100 ? 'color:var(--warn)' : '';
    document.getElementById('st-drop').innerHTML = '<span style="'+dropCls+'">' + drops + '</span>';

    // System
    var cpuPct = parseFloat(d.cpu_percent) || 0;
    var ramPct = parseFloat(d.ram_percent) || 0;
    document.getElementById('st-cpu').innerHTML = '<span class="s-pct">' + d.cpu_percent + '%</span> ' + _bar(cpuPct, _barCls(cpuPct));
    document.getElementById('st-ram').innerHTML = '<span class="s-pct">' + d.ram_percent + '%</span> ' + _bar(ramPct, _barCls(ramPct)) + ' <small style="opacity:.35">' + d.ram_used + '/' + d.ram_total + ' MB</small>';
    var ctCls = _tempCls(d.cpu_temp); var gtCls = _tempCls(d.gpu_temp);
    document.getElementById('st-ctemp').style.color = ctCls ? (ctCls==='fill-err'?'var(--err)':'var(--warn)') : '';
    document.getElementById('st-ctemp').textContent = d.cpu_temp;
    document.getElementById('st-gtemp').style.color = gtCls ? (gtCls==='fill-err'?'var(--err)':'var(--warn)') : '';
    document.getElementById('st-gtemp').textContent = d.gpu_temp;
    // Throttle (only show when active)
    var tRow = document.getElementById('st-throttle-row');
    if (d.throttle_active) {{
      tRow.classList.remove('hidden');
      document.getElementById('st-throttle').innerHTML = '<span class="badge off">' + d.throttle + '</span>';
    }} else {{
      tRow.classList.add('hidden');
    }}
    // DMX signal dot
    var dmxSig = document.getElementById('st-dmx-sig');
    dmxSig.className = 'sig ' + (d.dmx_active ? 'active' : 'inactive');

    // DMX Control
    document.getElementById('st-mode').innerHTML = d.play_mode + ' <span class="s-raw" id="st-mode-raw">DMX ' + (d.dmx_raw ? d.dmx_raw.playmode : '') + '</span>';
    document.getElementById('st-vol').innerHTML = d.volume_percent + '% ' + _bar(d.volume_percent,'fill-accent') + ' <span class="s-raw">DMX ' + d.volume + '</span>';
    document.getElementById('st-bri').innerHTML = d.brightness_percent + '% ' + _bar(d.brightness_percent,'fill-accent') + ' <span class="s-raw">DMX ' + d.brightness + '</span>';
    if (d.video_params) {{
      var vp = d.video_params;
      var dr = d.dmx_raw || {{}};
      var ve = function(id, v, rawId, rawVal) {{
        var el = document.getElementById(id);
        if(el) el.innerHTML = v + ' <span class="s-raw" id="' + rawId + '">DMX ' + (rawVal != null ? rawVal : '') + '</span>';
      }};
      ve('vp-contrast', vp.contrast, 'vp-contrast-raw', dr.contrast);
      ve('vp-saturation', vp.saturation, 'vp-saturation-raw', dr.saturation);
      ve('vp-gamma', vp.gamma, 'vp-gamma-raw', dr.gamma);
      ve('vp-speed', vp.speed + 'x', 'vp-speed-raw', dr.speed);
      ve('vp-rotation', vp.rotation + '\u00B0', 'vp-rotation-raw', dr.rotation);
      ve('vp-zoom', vp.zoom, 'vp-zoom-raw', dr.zoom);
      ve('vp-pan_x', vp.pan_x, 'vp-pan_x-raw', dr.pan_x);
      ve('vp-pan_y', vp.pan_y, 'vp-pan_y-raw', dr.pan_y);
    }}
  }}).catch(() => {{
    clearTimeout(tid);
    _failCount++;
    if (_failCount >= 2) {{
      document.getElementById('conn').className = 'conn lost';
    }}
  }});
}}
setInterval(refreshStatus, 2000);

// ---------------------------------------------------------------------------
// WiFi Management
// ---------------------------------------------------------------------------
function wifiRefreshStatus() {{
  fetch('/api/wifi/status').then(r => r.json()).then(d => {{
    var statusEl = document.getElementById('wifi-status-text');
    if (!d.ok) {{
      statusEl.innerHTML = '<span class="badge off">Unavailable</span>';
      return;
    }}
    var ssidEl = document.getElementById('wifi-ssid');
    var sigEl = document.getElementById('wifi-signal');
    var ipEl = document.getElementById('wifi-ip');
    var enableBtn = document.getElementById('wifi-enable-btn');
    var disableBtn = document.getElementById('wifi-disable-btn');

    if (!d.enabled) {{
      statusEl.innerHTML = '<span class="badge off">Disabled</span>';
      ssidEl.textContent = '—';
      sigEl.textContent = '—';
      ipEl.textContent = '—';
      enableBtn.style.display = '';
      disableBtn.style.display = 'none';
    }} else if (d.connected) {{
      statusEl.innerHTML = '<span class="badge on">Connected</span>';
      ssidEl.textContent = d.ssid || '—';
      sigEl.textContent = d.signal != null ? d.signal + '%' : '—';
      ipEl.textContent = d.ip || '—';
      enableBtn.style.display = 'none';
      disableBtn.style.display = '';
    }} else {{
      statusEl.innerHTML = '<span class="badge paused">Disconnected</span>';
      ssidEl.textContent = '—';
      sigEl.textContent = '—';
      ipEl.textContent = '—';
      enableBtn.style.display = 'none';
      disableBtn.style.display = '';
    }}
  }}).catch(function() {{
    document.getElementById('wifi-status-text').innerHTML = '<span class="badge off">Unavailable</span>';
  }});
}}

function wifiScan() {{
  var sel = document.getElementById('wifi-network-select');
  sel.innerHTML = '<option value="">Scanning...</option>';
  fetch('/api/wifi/networks').then(r => r.json()).then(d => {{
    if (!d.ok) {{
      sel.innerHTML = '<option value="">Scan failed</option>';
      return;
    }}
    if (d.networks.length === 0) {{
      sel.innerHTML = '<option value="">No networks found</option>';
      return;
    }}
    sel.innerHTML = '<option value="">Select a network...</option>';
    d.networks.forEach(n => {{
      var opt = document.createElement('option');
      opt.value = n.ssid;
      opt.textContent = n.ssid + ' (' + n.signal + '%, ' + n.security + ')';
      sel.appendChild(opt);
    }});
  }}).catch(() => {{
    sel.innerHTML = '<option value="">Scan failed</option>';
  }});
}}

function wifiConnect() {{
  var ssid = document.getElementById('wifi-network-select').value;
  var password = document.getElementById('wifi-password').value;
  if (!ssid) {{
    toast('Select a network first', 'err');
    return;
  }}
  toast('Connecting to ' + ssid + '...', 'ok');
  api('/api/wifi/connect', {{ ssid: ssid, password: password }}).then(d => {{
    if (d.ok) {{
      toast('Connected to ' + ssid, 'ok');
      document.getElementById('wifi-password').value = '';
      setTimeout(wifiRefreshStatus, 1000);
    }} else {{
      toast('Failed: ' + (d.error || 'Unknown error'), 'err');
    }}
  }}).catch(() => toast('Connection error', 'err'));
}}

function wifiDisconnect() {{
  api('/api/wifi/disconnect', {{}}).then(d => {{
    if (d.ok) {{
      toast('Disconnected', 'ok');
      setTimeout(wifiRefreshStatus, 500);
    }} else {{
      toast('Failed: ' + (d.error || 'Unknown error'), 'err');
    }}
  }}).catch(() => toast('Error', 'err'));
}}

function wifiEnable() {{
  api('/api/wifi/enable', {{}}).then(d => {{
    if (d.ok) {{
      toast('WiFi enabled', 'ok');
      setTimeout(() => {{ wifiRefreshStatus(); wifiScan(); }}, 1000);
    }} else {{
      toast('Failed: ' + (d.error || 'Unknown error'), 'err');
    }}
  }}).catch(() => toast('Error', 'err'));
}}

function wifiDisable() {{
  if (!confirm('Disable WiFi? You will lose WiFi connectivity.')) return;
  api('/api/wifi/disable', {{}}).then(d => {{
    if (d.ok) {{
      toast('WiFi disabled', 'ok');
      setTimeout(wifiRefreshStatus, 500);
    }} else {{
      toast('Failed: ' + (d.error || 'Unknown error'), 'err');
    }}
  }}).catch(() => toast('Error', 'err'));
}}

// Load WiFi status and networks when the section is opened
document.getElementById('wifi-settings').addEventListener('toggle', function(e) {{
  if (e.target.open) {{
    wifiRefreshStatus();
    wifiScan();
  }}
}});

// ---------------------------------------------------------------------------
// NDI Management
// ---------------------------------------------------------------------------
function ndiRefreshStatus() {{
  fetch('/api/ndi/status').then(function(r) {{ return r.json(); }}).then(function(d) {{
    var statusEl = document.getElementById('ndi-status-text');
    var sourceEl = document.getElementById('ndi-source-text');
    if (!d.available) {{
      statusEl.innerHTML = '<span class="badge off">Not Installed</span>';
      sourceEl.textContent = '\u2014';
    }} else if (d.active) {{
      statusEl.innerHTML = '<span class="badge on">Playing</span>';
      sourceEl.textContent = d.current_source || '\u2014';
    }} else {{
      statusEl.innerHTML = '<span class="badge paused">Available</span>';
      sourceEl.textContent = '\u2014';
    }}
    if (d.available) {{
      ndiRefreshSources();
      ndiRefreshBandwidth();
    }}
  }}).catch(function() {{
    document.getElementById('ndi-status-text').innerHTML = '<span class="badge off">Error</span>';
  }});
}}

function ndiRefreshBandwidth() {{
  fetch('/api/ndi/bandwidth').then(function(r) {{ return r.json(); }}).then(function(d) {{
    if (!d.ok) return;
    var lowestBtn = document.getElementById('ndi-bw-lowest');
    var highestBtn = document.getElementById('ndi-bw-highest');
    if (d.bandwidth === 'highest') {{
      lowestBtn.classList.remove('btn-primary');
      lowestBtn.classList.add('btn-outline');
      highestBtn.classList.remove('btn-outline');
      highestBtn.classList.add('btn-primary');
    }} else {{
      highestBtn.classList.remove('btn-primary');
      highestBtn.classList.add('btn-outline');
      lowestBtn.classList.remove('btn-outline');
      lowestBtn.classList.add('btn-primary');
    }}
  }}).catch(function() {{}});
}}

function setNdiBandwidth(bw) {{
  fetch('/api/ndi/bandwidth', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{bandwidth: bw}})
  }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
    if (d.ok) {{
      ndiRefreshBandwidth();
      toast('NDI bandwidth set to ' + bw);
    }} else {{
      toast(d.error || 'Failed to set bandwidth', 'error');
    }}
  }}).catch(function() {{
    toast('Network error', 'error');
  }});
}}

function ndiRefreshSources() {{
  fetch('/api/ndi/sources').then(function(r) {{ return r.json(); }}).then(function(d) {{
    var list = document.getElementById('ndi-sources-list');
    if (!d.ok || !d.sources || d.sources.length === 0) {{
      list.innerHTML = '<p style="font-size:.8rem;opacity:.5">No NDI sources discovered</p>';
      return;
    }}
    var html = '<table class="file-table" style="margin-top:.3rem"><tr><th>Source</th><th>Resolution</th><th style="text-align:right">Action</th></tr>';
    d.sources.forEach(function(s) {{
      var res = (s.probed && s.width > 0) ? s.width + 'x' + s.height : '<span style="opacity:.4">probing...</span>';
      var esc = s.name.replace(/'/g, "\\'").replace(/"/g, '&quot;');
      var safeName = s.name.replace(/[^a-zA-Z0-9_(). -]/g, '_').replace(/ +/g, '-').toLowerCase();
      var onclick = 'openAddSourceModal(&apos;ndi://' + esc + '&apos;, &apos;' + safeName + '.ndi&apos;)';
      html += '<tr><td style="font-size:.8rem">' + s.name + '</td>'
            + '<td style="font-size:.8rem">' + res + '</td>'
            + '<td style="text-align:right"><button class="btn btn-primary btn-sm" onclick="' + onclick + '">Add to Folder</button></td></tr>';
    }});
    html += '</table>';
    list.innerHTML = html;
  }}).catch(function() {{
    document.getElementById('ndi-sources-list').innerHTML = '<p style="font-size:.8rem;color:var(--err)">Error loading sources</p>';
  }});
}}

// Load NDI status and sources when the section is opened
var ndiSection = document.getElementById('ndi-settings');
if (ndiSection) {{
  ndiSection.addEventListener('toggle', function(e) {{
    if (e.target.open) {{
      ndiRefreshStatus();
    }}
  }});
}}

// Initial status fetch on page load
wifiRefreshStatus();
ndiRefreshStatus();
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
        try:
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
            elif self.path == "/api/health":
                self._serve_json({"ok": True, "status": "running"})
            elif self.path == "/api/wifi/status":
                self._handle_wifi_status()
            elif self.path == "/api/wifi/networks":
                self._handle_wifi_scan()
            elif self.path == "/api/ndi/status":
                self._handle_ndi_status()
            elif self.path == "/api/ndi/sources":
                self._handle_ndi_sources()
            elif self.path == "/api/ndi/bandwidth":
                self._handle_ndi_get_bandwidth()
            else:
                self.send_error(404)
        except Exception as exc:
            self._try_error_response(exc)

    # ----- POST -----

    def do_POST(self) -> None:  # noqa: N802
        try:
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
            elif self.path == "/api/file/create":
                self._handle_create_file()
            elif self.path == "/api/video-params":
                self._handle_video_params()
            elif self.path == "/api/wifi/connect":
                self._handle_wifi_connect()
            elif self.path == "/api/wifi/disconnect":
                self._handle_wifi_disconnect()
            elif self.path == "/api/wifi/enable":
                self._handle_wifi_enable()
            elif self.path == "/api/wifi/disable":
                self._handle_wifi_disable()
            elif self.path == "/api/ndi/refresh":
                self._handle_ndi_refresh()
            elif self.path == "/api/ndi/play":
                self._handle_ndi_play()
            elif self.path == "/api/ndi/bandwidth":
                self._handle_ndi_set_bandwidth()
            else:
                self.send_error(404)
        except Exception as exc:
            self._try_error_response(exc)

    def _try_error_response(self, exc: Exception) -> None:
        """Try to send a 500 JSON error. Swallow if the connection is broken."""
        log.error("Request error on %s: %s", self.path, exc)
        try:
            self._serve_json({"ok": False, "error": "Internal server error"}, code=500)
        except Exception:
            pass

    # =====================================================================
    # Status helpers
    # =====================================================================

    @staticmethod
    def _get_system_stats() -> dict:
        """Collect CPU, RAM, and temperature stats."""
        # CPU load
        try:
            load = os.getloadavg()
            cpu_count = os.cpu_count() or 4
            cpu_percent = round(load[0] / cpu_count * 100, 1)
        except OSError:
            cpu_percent = 0.0

        # RAM from /proc/meminfo
        ram_total = ram_used = ram_percent = 0
        try:
            with open("/proc/meminfo") as f:
                info = {}
                for line in f:
                    parts = line.split()
                    if parts[0] in ("MemTotal:", "MemAvailable:"):
                        info[parts[0]] = int(parts[1])
                total_kb = info.get("MemTotal:", 0)
                avail_kb = info.get("MemAvailable:", 0)
                ram_total = round(total_kb / 1024)
                ram_used = round((total_kb - avail_kb) / 1024)
                ram_percent = round((total_kb - avail_kb) / total_kb * 100, 1) if total_kb else 0
        except OSError:
            pass

        # CPU temp
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                cpu_temp = f"{round(int(f.read().strip()) / 1000, 1)}\u00b0C"
        except OSError:
            cpu_temp = "\u2014"

        # GPU temp via vcgencmd
        try:
            out = subprocess.check_output(
                ["vcgencmd", "measure_temp"], timeout=2, text=True
            )
            gpu_temp = out.strip().replace("temp=", "").replace("'C", "\u00b0C")
        except Exception:
            gpu_temp = "\u2014"

        # Throttling status via vcgencmd
        throttle_flags = []
        try:
            out = subprocess.check_output(
                ["vcgencmd", "get_throttled"], timeout=2, text=True
            )
            val = int(out.strip().split("=")[1], 16)
            if val & 0x1:
                throttle_flags.append("Under-voltage!")
            if val & 0x2:
                throttle_flags.append("Freq capped")
            if val & 0x4:
                throttle_flags.append("Throttled")
            if val & 0x8:
                throttle_flags.append("Soft temp limit")
            if val & 0x10000:
                throttle_flags.append("Under-voltage occurred")
            if val & 0x20000:
                throttle_flags.append("Freq cap occurred")
            if val & 0x40000:
                throttle_flags.append("Throttling occurred")
            if val & 0x80000:
                throttle_flags.append("Soft temp limit occurred")
        except Exception:
            pass

        throttle_active = bool(throttle_flags and any(
            f in throttle_flags for f in ["Under-voltage!", "Freq capped", "Throttled", "Soft temp limit"]
        ))

        return {
            "cpu_percent": cpu_percent,
            "ram_total": ram_total,
            "ram_used": ram_used,
            "ram_percent": ram_percent,
            "cpu_temp": cpu_temp,
            "gpu_temp": gpu_temp,
            "throttle": ", ".join(throttle_flags) if throttle_flags else "None",
            "throttle_active": throttle_active,
        }

    def _build_status(self) -> dict:
        srv = self.server_ref
        try:
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
                "fps": srv.player.fps,
                "dropped_frames": srv.player.dropped_frames,
                "resolution": srv.player.resolution,
                "dmx": {"address": srv.config.address, "universe": srv.config.universe},
                "dmx_active": srv.receiver.is_receiving,
                "dmx_receiving": srv.receiver.is_receiving,
                "dmx_values_changed": srv.receiver.is_active,
                "dmx_fail_mode": srv.config.dmx_fail_mode,
                "dmx_fail_osd": srv.config.dmx_fail_osd,
                "dmx_raw": {
                    "file": srv.receiver.channellist.get(0),
                    "folder": srv.receiver.channellist.get(1),
                    "playmode": srv.receiver.channellist.get(2),
                    "volume": srv.receiver.channellist.get(3),
                    "brightness": srv.receiver.channellist.get(4),
                    "contrast": srv.receiver.channellist.get(5),
                    "saturation": srv.receiver.channellist.get(6),
                    "gamma": srv.receiver.channellist.get(7),
                    "speed": srv.receiver.channellist.get(8),
                    "rotation": srv.receiver.channellist.get(9),
                    "zoom": srv.receiver.channellist.get(10),
                    "pan_x": srv.receiver.channellist.get(11),
                    "pan_y": srv.receiver.channellist.get(12),
                },
                "mediapath": srv.config.mediapath,
                "video_params": srv.player.video_params,
                "ndi": {
                    "available": srv.player.ndi_available,
                    "playing": srv.player.is_playing_ndi,
                    "source": srv.player.ndi_source,
                },
                **self._get_system_stats(),
            }
        except Exception as exc:
            log.error("_build_status error: %s", exc)
            return {"playing": False, "error": str(exc)}

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
                    f"<button class=\"btn btn-primary btn-sm\" onclick=\"createSourceFile('{esc}')\""
                    f">+ New Source</button>"
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
                        f_lower = f.lower()
                        is_source = f_lower.endswith(".txt") or f_lower.endswith(".ndi")
                        css_cls = ' class="url-file"' if is_source else ""
                        esc_f = f.replace("'", "\\'").replace('"', "&quot;")

                        edit_btn = (
                            f"<button class=\"btn btn-warn btn-sm\" "
                            f"onclick=\"openTxtEditor('{esc}','{esc_f}')\">Edit</button> "
                            if is_source
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

                # Combined drop/upload zone with file preview
                parts.append(
                    f'<div class="dropzone" id="dz-{fidx}" '
                    f'ondragover="onDragOver(event)" '
                    f'ondragleave="onDragLeave(event)" '
                    f"ondrop=\"onDropToFolder(event,'{esc}')\">"
                    f'<span class="dz-icon">\U0001F4C1</span>'
                    f"Drop files here or click to browse"
                    f'<div class="dz-hint">Upload to <b>{fname}</b></div>'
                    f'<input type="file" multiple '
                    f"onchange=\"onFilesSelected(this, '{esc}', {fidx})\">"
                    f"</div>"
                    f'<div class="pending-files" id="pf-{fidx}"></div>'
                    f'<div class="upload-actions" id="ua-{fidx}" style="display:none">'
                    f'<button class="btn btn-primary btn-sm" '
                    f"onclick=\"uploadPending('{esc}', {fidx})\">Upload</button>"
                    f'<button class="btn btn-outline btn-sm" '
                    f"onclick=\"clearPending({fidx})\">Clear</button>"
                    f'<span class="upload-count" id="uc-{fidx}"></span>'
                    f'<div class="upload-progress" id="up-{fidx}">'
                    f'<div class="bar" id="upb-{fidx}"></div></div>'
                    f"</div>"
                )
                parts.append("</div>")
            folders_html = "\n".join(parts)
        else:
            folders_html = '<p style="opacity:.4">No media folders found. Create one above.</p>'

        # Build folder <option> tags for the add-source modal
        folder_options_parts: list[str] = []
        if folders_data["folders"]:
            for folder in folders_data["folders"]:
                esc_name = folder["name"].replace('"', "&quot;")
                folder_options_parts.append(
                    f'<option value="{esc_name}">{folder["name"]}</option>'
                )
        folder_options = "\n        ".join(folder_options_parts)

        # Playing status
        if status["paused"]:
            playing_class, playing_label = "paused", "Paused"
        elif status["playing"]:
            playing_class, playing_label = "on", "Playing"
        else:
            playing_class, playing_label = "off", "Stopped"

        play_mode = "Pause" if status["paused"] else ("Loop" if status["loop"] else "Play once")

        # Color-code helpers for initial render
        cpu_pct = float(status["cpu_percent"])
        ram_pct = float(status["ram_percent"])
        cpu_bar_class = "fill-err" if cpu_pct > 85 else "fill-warn" if cpu_pct > 65 else "fill-ok"
        ram_bar_class = "fill-err" if ram_pct > 85 else "fill-warn" if ram_pct > 65 else "fill-ok"

        def _temp_class(temp_str: str) -> str:
            try:
                n = float(temp_str.replace("°C", ""))
            except (ValueError, AttributeError):
                return ""
            if n > 80:
                return "fill-err"
            if n > 70:
                return "fill-warn"
            return ""

        cpu_temp_class = _temp_class(status["cpu_temp"])
        gpu_temp_class = _temp_class(status["gpu_temp"])
        # Map to inline style for initial render
        cpu_temp_style = (
            'style="color:var(--err)"' if cpu_temp_class == "fill-err"
            else 'style="color:var(--warn)"' if cpu_temp_class == "fill-warn"
            else ""
        )
        gpu_temp_style = (
            'style="color:var(--err)"' if gpu_temp_class == "fill-err"
            else 'style="color:var(--warn)"' if gpu_temp_class == "fill-warn"
            else ""
        )

        vp = srv.player.video_params

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
            resolution=status["resolution"] or "\u2014",
            fps=f'{status["fps"]} fps' if status["fps"] else "\u2014",
            dropped_frames=status["dropped_frames"],
            cpu_percent=status["cpu_percent"],
            ram_total=status["ram_total"],
            ram_used=status["ram_used"],
            ram_percent=status["ram_percent"],
            cpu_temp=status["cpu_temp"],
            gpu_temp=status["gpu_temp"],
            throttle=status["throttle"],
            throttle_hidden="" if status["throttle_active"] else "hidden",
            dmx_sig_class="active" if status["dmx_active"] else "inactive",
            cpu_bar_class=cpu_bar_class,
            ram_bar_class=ram_bar_class,
            cpu_temp_class=cpu_temp_style,
            gpu_temp_class=gpu_temp_style,
            address=srv.config.address,
            universe=srv.config.universe,
            mediapath=srv.config.mediapath,
            folders_html=folders_html,
            folder_options=folder_options,
            vp_contrast=vp["contrast"],
            vp_saturation=vp["saturation"],
            vp_gamma=vp["gamma"],
            vp_speed=vp["speed"],
            vp_rotation=vp["rotation"],
            vp_zoom=vp["zoom"],
            vp_pan_x=vp["pan_x"],
            vp_pan_y=vp["pan_y"],
            # Raw DMX values
            dmx_playmode_raw=status["dmx_raw"]["playmode"],
            dmx_speed_raw=status["dmx_raw"]["speed"],
            dmx_rotation_raw=status["dmx_raw"]["rotation"],
            dmx_contrast_raw=status["dmx_raw"]["contrast"],
            dmx_saturation_raw=status["dmx_raw"]["saturation"],
            dmx_gamma_raw=status["dmx_raw"]["gamma"],
            dmx_zoom_raw=status["dmx_raw"]["zoom"],
            dmx_pan_x_raw=status["dmx_raw"]["pan_x"],
            dmx_pan_y_raw=status["dmx_raw"]["pan_y"],
            # Fail mode select
            fail_hold_sel='selected' if srv.config.dmx_fail_mode == 'hold' else '',
            fail_blackout_sel='selected' if srv.config.dmx_fail_mode == 'blackout' else '',
            fail_osd_on_sel='selected' if srv.config.dmx_fail_osd else '',
            fail_osd_off_sel='' if srv.config.dmx_fail_osd else 'selected',
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
        try:
            new_address = int(params.get("address", [str(srv.config.address)])[0])
            new_universe = int(params.get("universe", [str(srv.config.universe)])[0])
        except (ValueError, IndexError):
            msg = '<div class="msg err">Invalid address or universe value.</div>'
            self._serve_index(message=msg)
            return

        new_mediapath = params.get("mediapath", [srv.config.mediapath])[0].strip()
        if not new_mediapath.endswith("/"):
            new_mediapath += "/"

        new_fail_mode = params.get("failmode", [srv.config.dmx_fail_mode])[0].strip()
        if new_fail_mode not in ("hold", "blackout"):
            new_fail_mode = "hold"
        new_fail_osd = params.get("failosd", ["on" if srv.config.dmx_fail_osd else "off"])[0].strip() == "on"

        universe_changed = new_universe != srv.config.universe

        srv.config.address = new_address
        srv.config.universe = new_universe
        srv.config.mediapath = new_mediapath
        srv.config.dmx_fail_mode = new_fail_mode
        srv.config.dmx_fail_osd = new_fail_osd
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
                log.info("Uploaded '%s' to '%s'", safe_name, folder_name)

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
        if not self._require_keys(data, "folder", "old_name", "new_name"):
            return

        folder = data["folder"]
        old_name = data["old_name"]
        new_name = data["new_name"]
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
        log.info("Renamed file: %s -> %s in %s", old_name, new_name, folder)
        self._serve_json({"ok": True})

    # =====================================================================
    # File move
    # =====================================================================

    def _handle_move(self) -> None:
        data = self._read_json_body()
        if data is None:
            return

        if not self._require_keys(data, "file", "from_folder", "to_folder"):
            return

        file_name = data["file"]
        from_folder = data["from_folder"]
        to_folder = data["to_folder"]

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
        log.info("Moved '%s' from '%s' to '%s'", file_name, from_folder, to_folder)
        self._serve_json({"ok": True})

    # =====================================================================
    # File delete
    # =====================================================================

    def _handle_delete(self) -> None:
        data = self._read_json_body()
        if data is None:
            return

        if not self._require_keys(data, "folder", "file"):
            return

        folder = data["folder"]
        file_name = data["file"]
        mediapath = self.server_ref.config.mediapath
        path = os.path.join(mediapath, folder, file_name)

        if not os.path.exists(path):
            self._serve_json({"ok": False, "error": "File not found"}, code=404)
            return

        os.remove(path)
        log.info("Deleted '%s' from '%s'", file_name, folder)
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
        log.info("Created folder '%s'", name)
        self._serve_json({"ok": True})

    def _handle_folder_rename(self) -> None:
        data = self._read_json_body()
        if data is None:
            return
        if not self._require_keys(data, "old_name", "new_name"):
            return

        old_name = data["old_name"].strip()
        new_name = data["new_name"].strip()
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
        log.info("Renamed folder: %s -> %s", old_name, new_name)
        self._serve_json({"ok": True})

    def _handle_folder_delete(self) -> None:
        data = self._read_json_body()
        if data is None:
            return
        if not self._require_keys(data, "name"):
            return

        name = data["name"].strip()

        mediapath = self.server_ref.config.mediapath
        folder_path = os.path.join(mediapath, name)

        if not os.path.isdir(folder_path):
            self._serve_json({"ok": False, "error": "Folder not found"}, code=404)
            return

        shutil.rmtree(folder_path)
        log.info("Deleted folder '%s' and all contents", name)
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
            log.info("Updated content of '%s' in '%s'", file_name, folder)
            self._serve_json({"ok": True})
        except OSError as e:
            self._serve_json({"ok": False, "error": str(e)}, code=500)

    def _handle_create_file(self) -> None:
        """POST /api/file/create — create a new text-based source file."""
        data = self._read_json_body()
        if data is None:
            return

        folder = data.get("folder", "")
        file_name = data.get("file", "")
        content = data.get("content", "")

        if not all([folder, file_name]):
            self._serve_json({"ok": False, "error": "Missing fields"}, code=400)
            return

        # Validate extension
        if not (file_name.endswith(".txt") or file_name.endswith(".ndi")):
            self._serve_json(
                {"ok": False, "error": "Only .txt and .ndi files allowed"},
                code=400,
            )
            return

        mediapath = self.server_ref.config.mediapath
        folder_path = os.path.join(mediapath, folder)
        path = os.path.join(folder_path, file_name)

        if not os.path.isdir(folder_path):
            self._serve_json({"ok": False, "error": "Folder not found"}, code=404)
            return

        if os.path.exists(path):
            self._serve_json({"ok": False, "error": "File already exists"}, code=409)
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            log.info("Created source file '%s' in '%s'", file_name, folder)
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
        try:
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
        except (ValueError, TypeError) as exc:
            self._serve_json({"ok": False, "error": f"Invalid value: {exc}"}, code=400)
            return

        self._serve_json({"ok": True, **player.video_params})

    # =====================================================================
    # WiFi management
    # =====================================================================

    def _handle_wifi_status(self) -> None:
        """GET /api/wifi/status — current WiFi connection status."""
        try:
            # Check if WiFi radio is enabled
            radio_out = subprocess.run(
                ["nmcli", "radio", "wifi"],
                capture_output=True, text=True, timeout=5
            )
            wifi_enabled = radio_out.stdout.strip() == "enabled"

            if not wifi_enabled:
                self._serve_json({
                    "ok": True,
                    "enabled": False,
                    "connected": False,
                    "ssid": None,
                    "signal": None,
                    "ip": None,
                })
                return

            # Get current connection
            conn_out = subprocess.run(
                ["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"],
                capture_output=True, text=True, timeout=5
            )
            ssid = None
            signal = None
            for line in conn_out.stdout.strip().split("\n"):
                parts = line.split(":")
                if len(parts) >= 3 and parts[0] == "yes":
                    ssid = parts[1]
                    signal = int(parts[2]) if parts[2].isdigit() else None
                    break

            # Get IP address
            ip = None
            if ssid:
                ip_out = subprocess.run(
                    ["nmcli", "-t", "-f", "IP4.ADDRESS", "dev", "show", "wlan0"],
                    capture_output=True, text=True, timeout=5
                )
                for line in ip_out.stdout.strip().split("\n"):
                    if line.startswith("IP4.ADDRESS"):
                        ip = line.split(":")[1].split("/")[0] if ":" in line else None
                        break

            self._serve_json({
                "ok": True,
                "enabled": True,
                "connected": ssid is not None,
                "ssid": ssid,
                "signal": signal,
                "ip": ip,
            })
        except Exception as exc:
            self._serve_json({"ok": False, "error": str(exc)}, code=500)

    def _handle_wifi_scan(self) -> None:
        """GET /api/wifi/networks — scan for available WiFi networks."""
        try:
            # Trigger a rescan
            subprocess.run(
                ["nmcli", "dev", "wifi", "rescan"],
                capture_output=True, timeout=10
            )
            # List networks
            out = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
                capture_output=True, text=True, timeout=10
            )
            networks = []
            seen = set()
            for line in out.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split(":")
                if len(parts) >= 3:
                    ssid = parts[0]
                    if not ssid or ssid in seen:
                        continue
                    seen.add(ssid)
                    networks.append({
                        "ssid": ssid,
                        "signal": int(parts[1]) if parts[1].isdigit() else 0,
                        "security": parts[2] if parts[2] else "Open",
                    })
            # Sort by signal strength
            networks.sort(key=lambda x: x["signal"], reverse=True)
            self._serve_json({"ok": True, "networks": networks})
        except Exception as exc:
            self._serve_json({"ok": False, "error": str(exc)}, code=500)

    def _handle_wifi_connect(self) -> None:
        """POST /api/wifi/connect — connect to a WiFi network."""
        data = self._read_json_body()
        if data is None:
            return

        ssid = data.get("ssid", "").strip()
        password = data.get("password", "")

        if not ssid:
            self._serve_json({"ok": False, "error": "Missing SSID"}, code=400)
            return

        try:
            # Delete any existing connection profiles containing this SSID (by UUID)
            # This avoids issues with special characters in connection names
            list_result = subprocess.run(
                ["nmcli", "-t", "-f", "NAME,UUID,TYPE", "connection", "show"],
                capture_output=True, text=True, timeout=10
            )
            for line in list_result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split(":")
                if len(parts) >= 3 and "wireless" in parts[2]:
                    name, uuid = parts[0], parts[1]
                    if ssid in name:
                        subprocess.run(
                            ["nmcli", "connection", "delete", uuid],
                            capture_output=True, text=True, timeout=10
                        )

            # Connect (creates fresh profile)
            cmd = ["nmcli", "dev", "wifi", "connect", ssid]
            if password:
                cmd.extend(["password", password])

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )

            if result.returncode == 0:
                log.info("WiFi connected to '%s'", ssid)
                self._serve_json({"ok": True})
            else:
                error = result.stderr.strip() or result.stdout.strip()
                log.warning("WiFi failed to connect to '%s': %s", ssid, error)
                self._serve_json({"ok": False, "error": error}, code=400)
        except subprocess.TimeoutExpired:
            self._serve_json({"ok": False, "error": "Connection timeout"}, code=500)
        except Exception as exc:
            self._serve_json({"ok": False, "error": str(exc)}, code=500)

    def _handle_wifi_disconnect(self) -> None:
        """POST /api/wifi/disconnect — disconnect from current WiFi."""
        try:
            result = subprocess.run(
                ["nmcli", "dev", "disconnect", "wlan0"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                log.info("WiFi disconnected")
                self._serve_json({"ok": True})
            else:
                self._serve_json({"ok": False, "error": result.stderr.strip()}, code=400)
        except Exception as exc:
            self._serve_json({"ok": False, "error": str(exc)}, code=500)

    def _handle_wifi_enable(self) -> None:
        """POST /api/wifi/enable — enable WiFi radio."""
        try:
            result = subprocess.run(
                ["nmcli", "radio", "wifi", "on"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                log.info("WiFi radio enabled")
                self._serve_json({"ok": True})
            else:
                self._serve_json({"ok": False, "error": result.stderr.strip()}, code=400)
        except Exception as exc:
            self._serve_json({"ok": False, "error": str(exc)}, code=500)

    def _handle_wifi_disable(self) -> None:
        """POST /api/wifi/disable — disable WiFi radio."""
        try:
            result = subprocess.run(
                ["nmcli", "radio", "wifi", "off"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                log.info("WiFi radio disabled")
                self._serve_json({"ok": True})
            else:
                self._serve_json({"ok": False, "error": result.stderr.strip()}, code=400)
        except Exception as exc:
            self._serve_json({"ok": False, "error": str(exc)}, code=500)

    # =====================================================================
    # NDI handlers
    # =====================================================================

    def _handle_ndi_status(self) -> None:
        """GET /api/ndi/status — get NDI availability and current state."""
        player = self.server_ref.player
        self._serve_json({
            "ok": True,
            "available": player.ndi_available,
            "active": player.is_playing_ndi,
            "current_source": player.ndi_source,
        })

    def _handle_ndi_sources(self) -> None:
        """GET /api/ndi/sources — list discovered NDI sources with resolution."""
        player = self.server_ref.player
        if not player.ndi_available:
            self._serve_json({
                "ok": False,
                "error": "NDI not available (SDK not installed)",
            }, code=400)
            return
        sources = player.get_ndi_sources()
        self._serve_json({
            "ok": True,
            "available": True,
            "sources": [s.to_dict() if hasattr(s, 'to_dict') else {"name": s} for s in sources],
        })

    def _handle_ndi_refresh(self) -> None:
        """POST /api/ndi/refresh — restart NDI discovery."""
        player = self.server_ref.player
        if not player.ndi_available:
            self._serve_json({
                "ok": False,
                "error": "NDI not available (SDK not installed)",
            }, code=400)
            return
        player.stop_ndi_discovery()
        player.start_ndi_discovery()
        self._serve_json({"ok": True, "message": "Discovery restarted"})

    def _handle_ndi_play(self) -> None:
        """POST /api/ndi/play — play an NDI source directly."""
        player = self.server_ref.player
        if not player.ndi_available:
            self._serve_json({
                "ok": False,
                "error": "NDI not available (SDK not installed)",
            }, code=400)
            return

        data = self._read_json_body()
        if data is None:
            return

        source_name = data.get("source", "").strip()
        if not source_name:
            self._serve_json({"ok": False, "error": "Missing source name"}, code=400)
            return

        if player.play_ndi(source_name):
            self._serve_json({"ok": True})
        else:
            self._serve_json({"ok": False, "error": "Failed to play NDI source"}, code=500)

    def _handle_ndi_get_bandwidth(self) -> None:
        """GET /api/ndi/bandwidth — get current NDI bandwidth setting."""
        player = self.server_ref.player
        if not player.ndi_available:
            self._serve_json({
                "ok": False,
                "error": "NDI not available (SDK not installed)",
            }, code=400)
            return
        bandwidth = player.get_ndi_bandwidth()
        self._serve_json({"ok": True, "bandwidth": bandwidth})

    def _handle_ndi_set_bandwidth(self) -> None:
        """POST /api/ndi/bandwidth — set NDI bandwidth mode (lowest/highest)."""
        player = self.server_ref.player
        if not player.ndi_available:
            self._serve_json({
                "ok": False,
                "error": "NDI not available (SDK not installed)",
            }, code=400)
            return

        data = self._read_json_body()
        if data is None:
            return

        bandwidth = data.get("bandwidth", "").strip().lower()
        if bandwidth not in ("lowest", "highest"):
            self._serve_json({"ok": False, "error": "Invalid bandwidth (use 'lowest' or 'highest')"}, code=400)
            return

        player.set_ndi_bandwidth(bandwidth)
        # Also save to config
        srv = self.server_ref
        srv.config.ndi_bandwidth = bandwidth
        self._save_config(srv.config)
        self._serve_json({"ok": True, "bandwidth": bandwidth})

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

    def _require_keys(self, data: dict, *keys: str) -> bool:
        """Validate that all required keys are present and non-empty.

        Sends a 400 response with missing key details if validation fails.

        Returns:
            True if all keys are present and have truthy values.
        """
        missing = [k for k in keys if not data.get(k)]
        if missing:
            self._serve_json(
                {"ok": False, "error": f"Missing required fields: {', '.join(missing)}"},
                code=400,
            )
            return False
        return True

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
            "FailMode": config.dmx_fail_mode,
            "FailOSD": str(config.dmx_fail_osd),
        }
        parser["Web"] = {"Port": str(config.web_port)}
        parser["NDI"] = {"Bandwidth": config.ndi_bandwidth}
        try:
            with open(path, "w") as f:
                parser.write(f)
        except OSError as e:
            log.error("Failed to save config: %s", e)

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
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class _DualStackHTTPServer(ThreadingHTTPServer):
    """HTTP server that accepts both IPv4 and IPv6 connections."""

    address_family = socket.AF_INET6
    allow_reuse_address = True

    def server_bind(self) -> None:
        # Allow dual-stack: accept IPv4 connections on the IPv6 socket
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def start_web_server(server: Server, port: int = 8080) -> None:
    """Start the web interface on a daemon thread.

    Args:
        server: The running Server instance to expose via the web UI.
        port: TCP port to listen on.
    """
    handler = partial(_WebHandler, server)
    try:
        httpd = _DualStackHTTPServer(("::", port), handler)
    except OSError:
        # Fallback to IPv4-only if IPv6 is not available
        httpd = ThreadingHTTPServer(("", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    log.info("Web interface running at http://0.0.0.0:%d/", port)
