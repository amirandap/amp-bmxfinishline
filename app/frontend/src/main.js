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
}

// ── Calibration ───────────────────────────────────────────────────
async function loadCalibration() {
    try {
        const c = await api('GET', `/races/${currentRaceId}/calibration`);
        document.getElementById('calibration-json').value = JSON.stringify(c, null, 2);
    } catch (e) { /* ignore */ }
}

async function saveCalibration() {
    let data;
    try { data = JSON.parse(document.getElementById('calibration-json').value); }
    catch (e) { toast('Invalid JSON', true); return; }
    try {
        await api('POST', `/races/${currentRaceId}/calibration`, data);
        toast('Calibration saved');
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
