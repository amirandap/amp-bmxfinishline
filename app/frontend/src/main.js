import './style.css';

// ── State ─────────────────────────────────────────────────────────
let currentRaceId = null;
let statusPoller = null;

// ── API helper ────────────────────────────────────────────────────
async function api(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    if (res.status === 204) return null;
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    return data;
}

// ── Toast ─────────────────────────────────────────────────────────
function toast(msg, isError = false) {
    const el = document.createElement('div');
    el.className = 'toast' + (isError ? ' toast-error' : '');
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3500);
}

// ── Race List ─────────────────────────────────────────────────────
async function loadRaces() {
    let races;
    try { races = await api('GET', '/races'); }
    catch (e) { toast('Failed to load races: ' + e.message, true); return; }

    const ul = document.getElementById('race-list');
    ul.innerHTML = '';
    races.forEach(r => {
        const li = document.createElement('li');
        li.dataset.raceId = r.id;
        li.className = (r.id === currentRaceId) ? 'active' : '';
        li.innerHTML = `<span class="race-name">${escHtml(r.name)}</span>
                        <small class="race-id">${r.id}</small>`;
        li.addEventListener('click', () => selectRace(r.id, r.name));
        ul.appendChild(li);
    });
}

async function createRace() {
    const name = document.getElementById('new-race-name').value.trim();
    if (!name) { toast('Enter a race name', true); return; }
    try {
        const r = await api('POST', '/races', { name });
        document.getElementById('new-race-name').value = '';
        await loadRaces();
        selectRace(r.id, r.name);
        toast('Race created');
    } catch (e) { toast('Error: ' + e.message, true); }
}

async function deleteRace() {
    if (!currentRaceId) return;
    if (!confirm('Delete this race and all its arrivals?')) return;
    try {
        await api('DELETE', `/races/${currentRaceId}`);
        currentRaceId = null;
        document.getElementById('detail-panel').style.display = 'none';
        stopPoller();
        await loadRaces();
        toast('Race deleted');
    } catch (e) { toast('Error: ' + e.message, true); }
}

function selectRace(id, name) {
    currentRaceId = id;
    document.getElementById('detail-race-name').textContent = name;
    document.getElementById('detail-panel').style.display = '';
    document.getElementById('camera-warn').style.display = 'none';
    document.querySelectorAll('#race-list li').forEach(li => {
        li.classList.toggle('active', li.dataset.raceId === id);
    });
    loadCalibration();
    loadArrivals();
    pollStatus();
    // Eagerly init the calibration canvas so the video starts loading in the
    // background even before the user opens the Calibration <details>.
    calibInitCanvas();
}

// ── Calibration Editor ────────────────────────────────────────────
// Borrowing canvas drawing mechanics from demo.html:
//   crosshair cursor, glow finish line, polygon click-to-close,
//   canvasPt() for CSS↔pixel mapping, redrawCalib() for overlay.

const CALIB_VIDEO_SRC = '/videos/IMG_0109.MOV'; // hardcoded test video

const calibDraw = {
    canvas: null,
    ctx: null,
    video: null,           // hidden video for first-frame extraction
    finishLine: null,      // {x1,y1,x2,y2} in canvas px
    roiPolygon: [],        // [{x,y}, ...]  complete polygon
    readingZone: [],       // [{x,y}, ...]  complete polygon (nullable)
    mode: 'none',          // 'none' | 'finish_line' | 'roi_polygon' | 'reading_zone'
    dragStart: null,       // for finish_line drag
    tempPoly: [],          // in-progress polygon vertices
    mousePos: null,        // current cursor position (for live preview)
    frameLoaded: false,
};

function calibCanvasPt(e) {
    const r = calibDraw.canvas.getBoundingClientRect();
    return {
        x: (e.clientX - r.left) * (calibDraw.canvas.width  / r.width),
        y: (e.clientY - r.top)  * (calibDraw.canvas.height / r.height),
    };
}

function calibDrawLine(x1, y1, x2, y2, color, width = 2, dash = []) {
    const ctx = calibDraw.ctx;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.setLineDash(dash);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
    ctx.restore();
}

function calibDrawPolygon(pts, strokeColor, fillColor, dotColor) {
    if (pts.length < 2) return;
    const ctx = calibDraw.ctx;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
    ctx.closePath();
    if (fillColor) { ctx.fillStyle = fillColor; ctx.fill(); }
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 2;
    ctx.setLineDash([]);
    ctx.stroke();
    ctx.restore();
    // vertex dots
    pts.forEach(p => {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
        ctx.fillStyle = dotColor || strokeColor;
        ctx.fill();
    });
}

function redrawCalib() {
    const { canvas, ctx, video, frameLoaded, finishLine, roiPolygon, readingZone,
            mode, dragStart, tempPoly, mousePos } = calibDraw;
    if (!canvas) return;

    // Background: video first frame or black
    if (frameLoaded && video) {
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    } else {
        ctx.fillStyle = '#1a1a2e';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
    }

    // ── ROI polygon overlay (green, semi-transparent) ──────────
    if (roiPolygon.length >= 3) {
        calibDrawPolygon(roiPolygon, '#2ecc71', 'rgba(46,204,113,0.15)', '#27ae60');
    } else if (roiPolygon.length >= 2) {
        calibDrawPolygon(roiPolygon, '#2ecc71', null, '#27ae60');
    }

    // ── Reading zone overlay (blue) ────────────────────────────
    if (readingZone.length >= 3) {
        calibDrawPolygon(readingZone, '#3498db', 'rgba(52,152,219,0.15)', '#2980b9');
    } else if (readingZone.length >= 2) {
        calibDrawPolygon(readingZone, '#3498db', null, '#2980b9');
    }

    // ── Finish line (yellow glow, like demo.html) ──────────────
    if (finishLine) {
        const { x1, y1, x2, y2 } = finishLine;
        ctx.save();
        ctx.shadowColor = 'rgba(240,192,74,.7)';
        ctx.shadowBlur = 12;
        calibDrawLine(x1, y1, x2, y2, '#f0c04a', 2.5);
        ctx.restore();
        // endpoint circles
        ctx.fillStyle = '#f0c04a';
        [[x1,y1],[x2,y2]].forEach(([px,py]) => {
            ctx.beginPath(); ctx.arc(px, py, 5, 0, Math.PI*2); ctx.fill();
        });
        // "FINISH" label
        const mx = (x1+x2)/2, my = (y1+y2)/2;
        ctx.font = 'bold 13px sans-serif';
        ctx.fillStyle = 'rgba(0,0,0,.6)'; ctx.fillText('FINISH', mx+1, my-9);
        ctx.fillStyle = '#f0c04a';        ctx.fillText('FINISH', mx,   my-10);
    }

    // ── Live preview while drawing ─────────────────────────────
    if (mode === 'finish_line' && dragStart && mousePos) {
        calibDrawLine(dragStart.x, dragStart.y, mousePos.x, mousePos.y, '#f0c04a', 2, [8,4]);
    }
    const activePoly = mode === 'roi_polygon' ? '#2ecc71' : mode === 'reading_zone' ? '#3498db' : null;
    if (activePoly && tempPoly.length > 0 && mousePos) {
        // draw completed edges
        for (let i = 1; i < tempPoly.length; i++) {
            calibDrawLine(tempPoly[i-1].x, tempPoly[i-1].y, tempPoly[i].x, tempPoly[i].y, activePoly, 1.5, [6,3]);
        }
        // edge from last vertex to cursor
        const last = tempPoly[tempPoly.length-1];
        calibDrawLine(last.x, last.y, mousePos.x, mousePos.y, activePoly, 1.5, [6,3]);
        // vertex dots
        tempPoly.forEach(p => {
            ctx.beginPath(); ctx.arc(p.x, p.y, 4, 0, Math.PI*2);
            ctx.fillStyle = activePoly; ctx.fill();
        });
    }
}

function calibSetMode(m) {
    calibDraw.mode = m;
    calibDraw.dragStart = null;
    calibDraw.tempPoly = [];
    const c = calibDraw.canvas;
    if (!c) return;
    c.style.cursor = (m === 'none') ? 'default' : 'crosshair';
    // Toggle active state on toolbar buttons
    ['calib-btn-line','calib-btn-roi','calib-btn-reading'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.classList.remove('active');
    });
    const map = { finish_line: 'calib-btn-line', roi_polygon: 'calib-btn-roi', reading_zone: 'calib-btn-reading' };
    if (map[m]) document.getElementById(map[m])?.classList.add('active');
    const hints = {
        finish_line:  'Drag to draw the finish line',
        roi_polygon:  'Click to add ROI vertices — double-click to close',
        reading_zone: 'Click to add reading-zone vertices — double-click to close',
        none:         'Choose a tool and draw on the frame',
    };
    const hintEl = document.getElementById('calib-hint');
    if (hintEl) hintEl.textContent = hints[m] || '';
}

function calibSyncToJSON() {
    const fl = calibDraw.finishLine;
    const roi = calibDraw.roiPolygon;
    const rz = calibDraw.readingZone;
    const obj = {
        finish_line: fl ? [[fl.x1, fl.y1],[fl.x2, fl.y2]] : null,
        roi_polygon: roi.length >= 3 ? roi.map(p => [p.x, p.y]) : null,
        reading_zone: rz.length >= 3 ? rz.map(p => [p.x, p.y]) : null,
    };
    const ta = document.getElementById('calibration-json');
    if (ta) ta.value = JSON.stringify(obj, null, 2);
}

function calibLoadFromJSON() {
    const ta = document.getElementById('calibration-json');
    if (!ta) return;
    let obj;
    try { obj = JSON.parse(ta.value); } catch { toast('Invalid JSON', true); return; }
    calibDraw.finishLine = null;
    calibDraw.roiPolygon = [];
    calibDraw.readingZone = [];
    if (Array.isArray(obj.finish_line) && obj.finish_line.length === 2) {
        const [[x1,y1],[x2,y2]] = obj.finish_line;
        calibDraw.finishLine = {x1, y1, x2, y2};
    }
    if (Array.isArray(obj.roi_polygon)) {
        calibDraw.roiPolygon = obj.roi_polygon.map(([x,y]) => ({x,y}));
    }
    if (Array.isArray(obj.reading_zone)) {
        calibDraw.readingZone = obj.reading_zone.map(([x,y]) => ({x,y}));
    }
    redrawCalib();
    toast('Loaded from JSON');
}

function calibInitCanvas() {
    const canvas = document.getElementById('calib-canvas');
    if (!canvas || calibDraw.canvas === canvas) return;
    calibDraw.canvas = canvas;
    calibDraw.ctx = canvas.getContext('2d');
    const placeholder = document.getElementById('calib-placeholder');

    // Hidden video element for first-frame extraction
    const vid = document.createElement('video');
    vid.preload = 'auto';
    vid.muted = true;
    vid.style.display = 'none';
    vid.crossOrigin = 'anonymous';
    calibDraw.video = vid;
    document.body.appendChild(vid);

    const drawFirstFrame = () => {
        const MAX_W = 1280;
        const scale = Math.min(1, MAX_W / (vid.videoWidth || MAX_W));
        canvas.width  = Math.round((vid.videoWidth  || 1280) * scale);
        canvas.height = Math.round((vid.videoHeight || 720)  * scale);
        calibDraw.ctx.drawImage(vid, 0, 0, canvas.width, canvas.height);
        calibDraw.frameLoaded = true;
        if (placeholder) placeholder.style.display = 'none';
        canvas.style.display = 'block';
        redrawCalib();  // render any previously loaded calibration on top
    };
    // Chore: auto-detect finish line once the first frame is ready
    let _autoDetectTriggered = false;
    const onFirstFrame = () => {
        drawFirstFrame();
        if (!_autoDetectTriggered) {
            _autoDetectTriggered = true;
            autoDetectCalibration();
        }
    };
    vid.addEventListener('loadeddata', onFirstFrame, { once: true });
    vid.addEventListener('seeked',     onFirstFrame, { once: true });
    vid.addEventListener('error', () => {
        if (placeholder) placeholder.textContent = '⚠ Could not load video preview';
        canvas.width = 1280; canvas.height = 720;
        redrawCalib();
        if (placeholder) placeholder.style.display = 'none';
        canvas.style.display = 'block';
        calibDraw.frameLoaded = false;
    });
    vid.src = CALIB_VIDEO_SRC;
    vid.currentTime = 0;
    vid.load();

    // ── Mouse events (same geometry as demo.html canvasPt) ────
    canvas.addEventListener('mousedown', e => {
        const pt = calibCanvasPt(e);
        if (calibDraw.mode === 'finish_line') {
            calibDraw.dragStart = pt;
        }
    });

    canvas.addEventListener('mousemove', e => {
        calibDraw.mousePos = calibCanvasPt(e);
        redrawCalib();
    });

    canvas.addEventListener('mouseup', e => {
        const pt = calibCanvasPt(e);
        if (calibDraw.mode === 'finish_line' && calibDraw.dragStart) {
            const dx = pt.x - calibDraw.dragStart.x, dy = pt.y - calibDraw.dragStart.y;
            if (Math.sqrt(dx*dx+dy*dy) > 8) {
                calibDraw.finishLine = { x1: calibDraw.dragStart.x, y1: calibDraw.dragStart.y, x2: pt.x, y2: pt.y };
                calibSyncToJSON();
                calibSetMode('none');
            }
            calibDraw.dragStart = null;
            redrawCalib();
        }
    });

    canvas.addEventListener('click', e => {
        if (calibDraw.mode !== 'roi_polygon' && calibDraw.mode !== 'reading_zone') return;
        const pt = calibCanvasPt(e);
        calibDraw.tempPoly.push(pt);
        redrawCalib();
    });

    canvas.addEventListener('dblclick', e => {
        if (calibDraw.mode !== 'roi_polygon' && calibDraw.mode !== 'reading_zone') return;
        const poly = [...calibDraw.tempPoly];
        if (poly.length >= 3) {
            if (calibDraw.mode === 'roi_polygon')   calibDraw.roiPolygon  = poly;
            if (calibDraw.mode === 'reading_zone')  calibDraw.readingZone = poly;
            calibSyncToJSON();
        } else {
            toast('Need at least 3 points for a polygon', true);
        }
        calibSetMode('none');
        redrawCalib();
    });
}

/**
 * Auto-detect: send the current video path to the backend for LSD-based
 * finish-line detection.  Updates the canvas state with the result.
 * Silently no-ops when detection fails (user can draw manually).
 */
async function autoDetectCalibration() {
    if (!currentRaceId) return;
    const hint = document.getElementById('calib-hint');
    const origHint = hint?.textContent;
    try {
        if (hint) hint.textContent = '\u26a1 Auto-detecting finish line\u2026';
        const c = await api('POST', `/races/${currentRaceId}/calibration/auto-detect`, {
            // Strip leading '/' so the server sees a relative path from cwd
            video_path: CALIB_VIDEO_SRC.replace(/^\//, ''),
            frame_no: 0,
            orientation: 'horizontal',
            angle_thresh_deg: 15.0,
            save: true,
        });
        if (c.finish_line && c.finish_line.length === 2) {
            const [[x1, y1], [x2, y2]] = c.finish_line;
            calibDraw.finishLine = { x1, y1, x2, y2 };
            calibSyncToJSON();
            redrawCalib();
            toast('\u26a1 Finish line auto-detected');
            if (hint) hint.textContent = '\u26a1 Auto-detected \u2014 refine by dragging the finish line';
        } else {
            if (hint) hint.textContent = origHint;
        }
    } catch (e) {
        // Silent fail — user can always draw manually
        if (hint) hint.textContent = origHint;
        console.warn('Auto-detect skipped:', e.message);
    }
}

async function loadCalibration() {
    try {
        const c = await api('GET', `/races/${currentRaceId}/calibration`);
        const ta = document.getElementById('calibration-json');
        if (ta) ta.value = JSON.stringify(c, null, 2);
        // Populate canvas state from backend data
        calibDraw.finishLine = null;
        calibDraw.roiPolygon = [];
        calibDraw.readingZone = [];
        if (Array.isArray(c.finish_line) && c.finish_line.length === 2) {
            const [[x1,y1],[x2,y2]] = c.finish_line;
            calibDraw.finishLine = {x1, y1, x2, y2};
        }
        if (Array.isArray(c.roi_polygon)) calibDraw.roiPolygon = c.roi_polygon.map(([x,y]) => ({x,y}));
        if (Array.isArray(c.reading_zone)) calibDraw.readingZone = c.reading_zone.map(([x,y]) => ({x,y}));
        redrawCalib();
    } catch (e) { /* ignore */ }
}

async function saveCalibration() {
    // Use canvas state if available, otherwise fall back to raw textarea
    let data;
    const fl = calibDraw.finishLine;
    const roi = calibDraw.roiPolygon;
    const rz = calibDraw.readingZone;
    if (fl || roi.length >= 3) {
        data = {
            finish_line: fl ? [[fl.x1, fl.y1],[fl.x2, fl.y2]] : null,
            roi_polygon: roi.length >= 3 ? roi.map(p => [p.x, p.y]) : null,
            reading_zone: rz.length >= 3 ? rz.map(p => [p.x, p.y]) : null,
        };
    } else {
        const ta = document.getElementById('calibration-json');
        try { data = JSON.parse(ta?.value || '{}'); }
        catch { toast('Invalid JSON', true); return; }
    }
    try {
        await api('POST', `/races/${currentRaceId}/calibration`, data);
        toast('Calibration saved');
    } catch (e) { toast('Error: ' + e.message, true); }
}

async function loadCalibDefaults() {
    if (!currentRaceId) { toast('Select a race first', true); return; }
    const res = document.getElementById('calib-resolution').value;
    const [w, h] = res.split('x').map(Number);
    try {
        const c = await api('POST', `/races/${currentRaceId}/calibration/reset?width=${w}&height=${h}`);
        const ta = document.getElementById('calibration-json');
        if (ta) ta.value = JSON.stringify(c, null, 2);
        calibDraw.finishLine = null;
        calibDraw.roiPolygon = [];
        calibDraw.readingZone = [];
        if (Array.isArray(c.finish_line) && c.finish_line.length === 2) {
            const [[x1,y1],[x2,y2]] = c.finish_line;
            calibDraw.finishLine = {x1, y1, x2, y2};
        }
        if (Array.isArray(c.roi_polygon)) calibDraw.roiPolygon = c.roi_polygon.map(([x,y]) => ({x,y}));
        redrawCalib();
        toast(`Defaults loaded for ${w}\u00d7${h}`);
    } catch (e) { toast('Error: ' + e.message, true); }
}

// ── Processing ────────────────────────────────────────────────────
async function startProcess() {
    const input_type = document.getElementById('input-type').value;
    const input = document.getElementById('input-path').value.trim();
    if (!input) { toast('Enter a file path or RTMP URL', true); return; }
    try {
        await api('POST', `/races/${currentRaceId}/process/start`, { input_type, input });
        toast('Processing started');
        startPoller();
    } catch (e) { toast('Error: ' + e.message, true); }
}

async function stopProcess() {
    try {
        await api('POST', `/races/${currentRaceId}/process/stop`);
        toast('Processing stopped');
    } catch (e) { toast('Error: ' + e.message, true); }
}

async function updateStatus() {
    if (!currentRaceId) return;
    try {
        const s = await api('GET', `/races/${currentRaceId}/process/status`);
        document.getElementById('process-status').textContent = s.state;
        document.getElementById('frames-processed').textContent = s.frames_processed;
        document.getElementById('arrivals-detected').textContent = s.arrivals_detected;
        if (s.camera_moved) document.getElementById('camera-warn').style.display = '';
        if (s.state === 'finished' || s.state === 'error' || s.state === 'idle') {
            stopPoller();
            if (s.state === 'finished') { toast('Processing complete'); loadArrivals(); }
            if (s.state === 'error') toast('Pipeline error: ' + (s.error || 'unknown'), true);
        }
    } catch (e) { /* ignore transient */ }
}

function startPoller() { stopPoller(); statusPoller = setInterval(updateStatus, 1500); }
function stopPoller() { if (statusPoller) { clearInterval(statusPoller); statusPoller = null; } }
function pollStatus() { updateStatus(); startPoller(); }

// ── Upload ────────────────────────────────────────────────────────
// TODO [CHORE]: Evaluate WebSocket (FastAPI native via `websockets`) or Redis
//   Pub/Sub (with fastapi-socketio / sse-starlette) to replace the current
//   1500 ms status polling and push real-time pipeline events + arrival
//   notifications to the browser.  Consider Redis if horizontal scaling is
//   needed later; native WebSocket is sufficient for a single-server deployment.

let _pendingUploadFile = null;

function onVideoFileChange(e) {
    const file = e.target.files[0];
    if (!file) return;
    _pendingUploadFile = file;
    document.getElementById('upload-filename').textContent = file.name;
    document.getElementById('upload-btn').style.display = '';
    initPreview(file);
}

async function uploadFile() {
    if (!currentRaceId) { toast('Select a race first', true); return; }
    if (!_pendingUploadFile) { toast('Choose a video file first', true); return; }

    const progressEl = document.getElementById('upload-progress');
    const btnEl = document.getElementById('upload-btn');
    progressEl.textContent = 'Uploading…';
    progressEl.style.display = '';
    btnEl.disabled = true;

    const form = new FormData();
    form.append('file', _pendingUploadFile);

    try {
        const res = await fetch(`/races/${currentRaceId}/upload`, { method: 'POST', body: form });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.detail || `HTTP ${res.status}`);
        }
        const data = await res.json();
        document.getElementById('input-path').value = data.path;
        document.getElementById('input-type').value = 'file';
        progressEl.textContent = `✓ Saved as ${data.filename}`;
        toast('Video uploaded — ready to process');
    } catch (e) {
        toast('Upload failed: ' + e.message, true);
        progressEl.textContent = '';
        progressEl.style.display = 'none';
    } finally {
        btnEl.disabled = false;
    }
}

// ── Video Preview (first-frame canvas render) ─────────────────────
function initPreview(file) {
    const video = document.getElementById('hidden-video');
    const canvas = document.getElementById('preview-canvas');
    const panel = document.getElementById('preview-panel');
    const label = document.getElementById('preview-label');
    const ctx = canvas.getContext('2d');

    const url = URL.createObjectURL(file);
    video.src = url;
    video.currentTime = 0;

    const draw = () => {
        canvas.width = video.videoWidth || 640;
        canvas.height = video.videoHeight || 360;
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        label.textContent = `${file.name}  ·  ${canvas.width}×${canvas.height}`;
        panel.style.display = '';
        URL.revokeObjectURL(url);
    };

    video.addEventListener('loadeddata', draw, { once: true });
    // Fallback: seeked fires reliably in some browsers after setting currentTime
    video.addEventListener('seeked', draw, { once: true });
}

// ── Toggle upload row based on input type ─────────────────────────
function onInputTypeChange() {
    const isFile = document.getElementById('input-type').value === 'file';
    document.getElementById('upload-row').style.display = isFile ? '' : 'none';
}

// ── Arrivals ──────────────────────────────────────────────────────
async function loadArrivals() {
    if (!currentRaceId) return;
    let arrivals;
    try { arrivals = await api('GET', `/races/${currentRaceId}/arrivals`); }
    catch (e) { toast('Error loading arrivals: ' + e.message, true); return; }

    const tbody = document.querySelector('#arrivals-table tbody');
    tbody.innerHTML = '';
    arrivals.forEach(a => tbody.appendChild(buildArrivalRow(a)));

    buildThumbLog(arrivals);
}

function buildArrivalRow(a) {
    const tr = document.createElement('tr');
    tr.dataset.arrivalId = a.id;
    const statusClass = a.status === 'AUTO_OK' ? 'ok'
        : a.status === 'MANUAL_OK' ? 'manual' : 'review';
    const confPct = (a.confidence * 100).toFixed(1);
    tr.innerHTML = `
        <td>${a.effective_position ?? ''}</td>
        <td class="bib-cell">${escHtml(a.bib || '\u2014')}</td>
        <td>${a.timestamp_ms.toFixed(1)}</td>
        <td>${confPct}%</td>
        <td><span class="badge ${statusClass}">${a.status}</span></td>
        <td class="img-cell">${a.crossing_frame_path
            ? `<img src="/races/${a.race_id}/arrivals/${a.id}/image"
                    alt="frame" class="thumb"
                    onerror="this.style.display='none'"
                    onclick="showLightbox(this.src)">`
            : '\u2014'}</td>
        <td>
            <button class="btn-sm" onclick="openBibEdit('${a.id}','${escHtml(a.bib||'')}')">&#9998; Bib</button>
            <button class="btn-sm btn-danger" onclick="deleteArrival('${a.id}')">&#128465;</button>
        </td>`;
    return tr;
}

// ── Thumbnail logger ──────────────────────────────────────────────
function buildThumbLog(arrivals) {
    const container = document.getElementById('thumb-log-list');
    const emptyMsg = document.getElementById('thumb-empty-msg');
    const countEl = document.getElementById('thumb-count');

    const withImages = arrivals.filter(a => a.crossing_frame_path);
    countEl.textContent = withImages.length
        ? `${withImages.length} crossing${withImages.length !== 1 ? 's' : ''}`
        : '';

    // Only rebuild when content has changed
    const newIds = withImages.map(a => a.id).join(',');
    if (container.dataset.renderedIds === newIds) return;
    container.dataset.renderedIds = newIds;

    container.innerHTML = '';
    if (withImages.length === 0) {
        container.appendChild(emptyMsg);
        return;
    }

    withImages.forEach(a => {
        const item = document.createElement('div');
        item.className = 'log-thumb-item';
        const posLabel = a.effective_position != null ? `#${a.effective_position}` : '—';
        const bibLabel = a.bib ? escHtml(a.bib) : '';
        item.innerHTML = `
            <img src="/races/${a.race_id}/arrivals/${a.id}/image"
                 alt="crossing ${posLabel}"
                 onerror="this.closest('.log-thumb-item').style.display='none'"
                 onclick="showLightbox(this.src)">
            <div class="log-thumb-meta">
                <strong>${posLabel}</strong>
                ${bibLabel ? `<span>${bibLabel}</span>` : ''}
                <span class="muted-label">${a.timestamp_ms.toFixed(0)} ms</span>
            </div>`;
        container.appendChild(item);
    });
}

// ── Lightbox ──────────────────────────────────────────────────────
function showLightbox(src) {
    const dialog = document.getElementById('lightbox');
    const img = document.getElementById('lightbox-img');
    img.src = src;
    dialog.showModal();
}

function closeLightbox() {
    document.getElementById('lightbox').close();
}

// ── Bib edit ──────────────────────────────────────────────────────
let _editArrivalId = null;

function openBibEdit(arrivalId, currentBib) {
    _editArrivalId = arrivalId;
    document.getElementById('bib-input').value = currentBib;
    document.getElementById('bib-modal').showModal();
}

async function saveBib() {
    const bib = document.getElementById('bib-input').value.trim();
    try {
        await api('PATCH', `/races/${currentRaceId}/arrivals/${_editArrivalId}`,
            { bib, status: 'MANUAL_OK' });
        document.getElementById('bib-modal').close();
        toast('Bib updated');
        loadArrivals();
    } catch (e) { toast('Error: ' + e.message, true); }
}

async function deleteArrival(arrivalId) {
    if (!confirm('Delete this arrival?')) return;
    try {
        await api('DELETE', `/races/${currentRaceId}/arrivals/${arrivalId}`);
        toast('Arrival deleted');
        loadArrivals();
    } catch (e) { toast('Error: ' + e.message, true); }
}

// ── Utilities ─────────────────────────────────────────────────────
function escHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Expose functions needed by inline onclick attributes
window.openBibEdit = openBibEdit;
window.deleteArrival = deleteArrival;
window.showLightbox = showLightbox;

// ── Bootstrap ─────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
    loadRaces();
    document.getElementById('create-race').addEventListener('click', createRace);
    document.getElementById('new-race-name').addEventListener('keydown', e => {
        if (e.key === 'Enter') createRace();
    });
    document.getElementById('delete-race').addEventListener('click', deleteRace);
    document.getElementById('save-calibration').addEventListener('click', saveCalibration);
    document.getElementById('load-calib-defaults').addEventListener('click', loadCalibDefaults);
    document.getElementById('calib-from-json').addEventListener('click', calibLoadFromJSON);
    document.getElementById('calib-btn-line').addEventListener('click', () =>
        calibSetMode(calibDraw.mode === 'finish_line' ? 'none' : 'finish_line'));
    document.getElementById('calib-btn-roi').addEventListener('click', () =>
        calibSetMode(calibDraw.mode === 'roi_polygon' ? 'none' : 'roi_polygon'));
    document.getElementById('calib-btn-reading').addEventListener('click', () =>
        calibSetMode(calibDraw.mode === 'reading_zone' ? 'none' : 'reading_zone'));
    document.getElementById('calib-btn-clear').addEventListener('click', () => {
        calibDraw.finishLine = null; calibDraw.roiPolygon = []; calibDraw.readingZone = [];
        calibDraw.tempPoly = []; calibSetMode('none'); calibSyncToJSON(); redrawCalib();
    });
    document.getElementById('calib-btn-autodetect').addEventListener('click', autoDetectCalibration);
    // Init canvas the first time the calibration <details> is opened
    document.getElementById('calibration-section').addEventListener('toggle', e => {
        if (e.target.open) calibInitCanvas();
    });
    document.getElementById('start-process').addEventListener('click', startProcess);
    document.getElementById('stop-process').addEventListener('click', stopProcess);
    document.getElementById('refresh-arrivals').addEventListener('click', loadArrivals);
    document.getElementById('bib-save').addEventListener('click', saveBib);

    // Upload
    document.getElementById('video-file-input').addEventListener('change', onVideoFileChange);
    document.getElementById('upload-btn').addEventListener('click', uploadFile);
    document.getElementById('input-type').addEventListener('change', onInputTypeChange);

    // Lightbox close
    document.getElementById('lightbox-close').addEventListener('click', closeLightbox);
    document.getElementById('lightbox').addEventListener('click', e => {
        if (e.target === e.currentTarget) closeLightbox();
    });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') closeLightbox();
    });
});
