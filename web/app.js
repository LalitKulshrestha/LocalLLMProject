const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

let activeTab = 'face';
let activeLogJobId = null;
let logPollTimer = null;
let lastJobsSignature = '';

// Tab Switching
$$('.tabs button').forEach(button => {
  button.onclick = () => {
    $$('.tabs button').forEach(b => b.classList.remove('active'));
    button.classList.add('active');
    activeTab = button.dataset.tab;

    $('#face').classList.toggle('active', activeTab === 'face');
    $('#generate').classList.toggle('active', activeTab !== 'face');
    $('#generate [name=kind]').value = activeTab;

    const genImageLabel = $('#gen-image-label');
    const genImageTitle = $('#gen-image-title');
    const genImageHint = $('#gen-image-hint');
    $('#gen-image-section').classList.toggle('hidden', activeTab !== 'image_to_video');
    if (activeTab === 'text_to_image') {
      if (genImageLabel) genImageLabel.textContent = 'Reference Image (Optional)';
      if (genImageTitle) genImageTitle.textContent = 'Attach Reference Image';
      if (genImageHint) genImageHint.textContent = 'Optional: guides character identity, lighting, color, or composition';
    } else if (activeTab === 'text_to_video') {
      if (genImageLabel) genImageLabel.textContent = 'Reference / Starting Frame (Optional)';
      if (genImageTitle) genImageTitle.textContent = 'Attach Reference / First Frame';
      if (genImageHint) genImageHint.textContent = 'Optional: anchors scene composition, character, or starting motion';
    } else if (activeTab === 'image_to_video') {
      if (genImageLabel) genImageLabel.textContent = 'Source Image to Animate (Required)';
      if (genImageTitle) genImageTitle.textContent = 'Upload Source Image';
      if (genImageHint) genImageHint.textContent = 'Select high-resolution photo to bring to life with cinematic motion';
    }
    $('#video-format-field').classList.toggle('hidden', activeTab === 'text_to_image');
    $('#video-format-note').classList.toggle('hidden', activeTab === 'text_to_image');
  };
});

// Selected files can be replaced or removed before a job is submitted.
function setupFilePicker(zoneSelector) {
  const zone = $(zoneSelector);
  const input = zone.querySelector('input[type=file]');
  const selection = zone.querySelector('.file-selection');
  const preview = zone.querySelector('.file-name-preview');
  const clear = zone.querySelector('.file-clear');

  function sync() {
    const file = input.files?.[0];
    preview.textContent = file ? `✓ ${file.name} (${(file.size / (1024 * 1024)).toFixed(1)} MB)` : '';
    preview.title = file?.name || '';
    selection.classList.toggle('hidden', !file);
  }

  input.addEventListener('change', sync);
  clear.addEventListener('click', () => {
    input.value = '';
    sync();
  });
  sync();
  return sync;
}
const syncFaceFile = setupFilePicker('#face-dropzone');
const syncVideoFile = setupFilePicker('#video-dropzone');
const syncGenImageFile = setupFilePicker('#gen-image-dropzone');

// Quality Slider Sync
const qualityInput = $('#face [name=quality]');
const qualityBadge = $('#quality-val');
if (qualityInput && qualityBadge) {
  qualityInput.oninput = e => {
    qualityBadge.textContent = `${e.target.value}%`;
  };
}
// Profile Card Selection Logic
$$('.profile-card').forEach(card => {
  card.onclick = () => {
    $$('.profile-card').forEach(c => c.classList.remove('active-profile'));
    card.classList.add('active-profile');
    const radio = card.querySelector('input[type=radio]');
    if (radio) {
      radio.checked = true;
      const profile = radio.value;
      if (profile === 'standard') {
        qualityInput.value = 80;
        qualityBadge.textContent = '80%';
        $('#face [name=face_enhancer]').value = 'false';
        $('#face [name=face_swapper_model]').value = 'inswapper_128_fp16';
        $('#face [name=pixel_boost]').value = '256x256';
      } else if (profile === 'high') {
        qualityInput.value = 90;
        qualityBadge.textContent = '90%';
        $('#face [name=face_enhancer]').value = 'true';
        $('#face [name=face_swapper_model]').value = 'inswapper_128_fp16';
        $('#face [name=pixel_boost]').value = '512x512';
      } else if (profile === 'ultra') {
        qualityInput.value = 95;
        qualityBadge.textContent = '95%';
        $('#face [name=face_enhancer]').value = 'true';
        $('#face [name=face_swapper_model]').value = 'inswapper_128_fp16';
        $('#face [name=pixel_boost]').value = '1024x1024';
      }
    }
  };
});

// Form Submission
async function submitForm(form, url) {
  const msg = $('#message');
  msg.textContent = 'Uploading media and queuing job…';
  try {
    const res = await fetch(url, { method: 'POST', body: new FormData(form) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to submit job');
    form.reset();
    if (form.id === 'generate') {
      $('#generate [name=kind]').value = activeTab;
      syncGenImageFile();
    }
    if (form.id === 'face') {
      $('#face [name=face]').value = '';
      $('#face [name=video]').value = '';
      syncFaceFile();
      syncVideoFile();
    }
    msg.textContent = `✓ Successfully queued job #${data.id.slice(0, 10)}`;
    await loadJobs();
  } catch (err) {
    msg.textContent = `Error: ${err.message}`;
  }
}

$('#face').onsubmit = e => {
  e.preventDefault();
  submitForm(e.target, '/api/jobs/face-swap');
};

$('#generate').onsubmit = e => {
  e.preventDefault();
  submitForm(e.target, '/api/jobs/generate');
};

// Health Check
async function checkHealth() {
  try {
    const res = await fetch('/api/health');
    const h = await res.json();
    $('#health').innerHTML = Object.entries(h).filter(([k]) => k !== 'generation_modes').map(([k, v]) => `
      <span class="health ${v ? 'on' : ''}">
        ${k}: ${v ? 'ready' : (k === 'generation' ? 'not configured' : 'offline')}
      </span>
    `).join('');
    $$('.tabs button:not([data-tab=face])').forEach(button => {
      const ready = Boolean(h.generation_modes?.[button.dataset.tab]);
      button.disabled = !ready;
      button.title = ready ? '' : 'No compatible model is installed for this mode';
    });
    $('#generation-status-note').textContent = h.generation
      ? 'Text-to-image is available. HD video generation needs a different model and a tested workflow.'
      : 'Generation requires ComfyUI and a compatible model. Video generation is not configured.';
  } catch (err) {
    console.error('Health check error:', err);
  }
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

// Job Actions (Retry, Cancel, Delete)
window.retryJob = async function(id) {
  try {
    const res = await fetch(`/api/jobs/${id}/retry`, { method: 'POST' });
    if (!res.ok) throw new Error('Retry failed');
    await loadJobs();
  } catch (e) {
    alert(e.message);
  }
};

window.revealOutput = async function(id, index) {
  try {
    const res = await fetch(`/api/jobs/${id}/output/${index}/reveal`, { method: 'POST' });
    if (!res.ok) throw new Error((await res.json()).detail || 'Could not open Finder');
  } catch (e) {
    alert(e.message);
  }
};

window.cancelJob = async function(id) {
  if (!confirm('Cancel this running job?')) return;
  try {
    const res = await fetch(`/api/jobs/${id}/cancel`, { method: 'POST' });
    if (!res.ok) throw new Error('Cancel failed');
    await loadJobs();
  } catch (e) {
    alert(e.message);
  }
};

window.deleteJob = async function(id) {
  if (!confirm('Permanently delete this job and its local output file?')) return;
  try {
    const res = await fetch(`/api/jobs/${id}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Delete failed');
    await loadJobs();
  } catch (e) {
    alert(e.message);
  }
};

$('#open-output-folder').onclick = async () => {
  try {
    const res = await fetch('/api/outputs/reveal', { method: 'POST' });
    if (!res.ok) throw new Error((await res.json()).detail || 'Could not open Finder');
  } catch (e) {
    alert(e.message);
  }
};

$('#cleanup-failed').onclick = async () => {
  if (!confirm('Permanently delete all failed jobs and their local files?')) return;
  try {
    const res = await fetch('/api/jobs/cleanup-failed', { method: 'POST' });
    if (!res.ok) throw new Error('Cleanup failed');
    lastJobsSignature = '';
    await loadJobs();
    await loadStorage();
  } catch (e) {
    alert(e.message);
  }
};

$('#cleanup-old').onclick = async () => {
  if (!confirm('Permanently delete completed jobs and local outputs older than 30 days?')) return;
  try {
    const res = await fetch('/api/jobs/cleanup-old', { method: 'POST' });
    if (!res.ok) throw new Error('Cleanup failed');
    lastJobsSignature = '';
    await loadJobs();
    await loadStorage();
  } catch (e) {
    alert(e.message);
  }
};

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index++;
  }
  return `${value.toFixed(1)} ${units[index]}`;
}

async function loadStorage() {
  try {
    const res = await fetch('/api/storage');
    if (!res.ok) throw new Error('Storage status unavailable');
    const storage = await res.json();
    $('#output-path').textContent = storage.output_dir;
    $('#storage-usage').textContent = `Outputs ${formatBytes(storage.output_bytes)} · Upload copies ${formatBytes(storage.upload_bytes)} · Temporary ${formatBytes(storage.temp_bytes)} · Models ${formatBytes(storage.model_bytes)}`;
    $('#cleanup-old').classList.toggle('hidden', storage.old_completed_count === 0);
  } catch (e) {
    $('#storage-usage').textContent = e.message;
  }
}

// Log Modal Functions
window.openLogs = async function(id) {
  activeLogJobId = id;
  $('#modal-job-title').textContent = `Execution Log: ${id.slice(0, 10)}`;
  $('#log-modal').classList.remove('hidden');
  await refreshLogs();
  if (logPollTimer) clearInterval(logPollTimer);
  logPollTimer = setInterval(refreshLogs, 2000);
};

async function refreshLogs() {
  if (!activeLogJobId) return;
  try {
    const res = await fetch(`/api/jobs/${activeLogJobId}/logs`);
    const text = await res.text();
    const logBox = $('#log-content');
    const shouldScroll = logBox.scrollTop + logBox.clientHeight >= logBox.scrollHeight - 50;
    logBox.textContent = text || 'No logs yet.';
    if (shouldScroll) {
      logBox.scrollTop = logBox.scrollHeight;
    }
  } catch (e) {
    $('#log-content').textContent = `Failed to fetch logs: ${e.message}`;
  }
}

$('#modal-close-btn').onclick = () => {
  $('#log-modal').classList.add('hidden');
  activeLogJobId = null;
  if (logPollTimer) clearInterval(logPollTimer);
};

$('#modal-copy-btn').onclick = () => {
  navigator.clipboard.writeText($('#log-content').textContent);
  $('#modal-copy-btn').textContent = 'Copied!';
  setTimeout(() => { $('#modal-copy-btn').textContent = 'Copy'; }, 1500);
};

// Jobs List Rendering
async function loadJobs() {
  try {
    const res = await fetch('/api/jobs');
    const jobs = await res.json();
    $('#job-count').textContent = jobs.length;
    $('#cleanup-failed').classList.toggle('hidden', !jobs.some(j => j.status === 'failed'));
    const signature = JSON.stringify(jobs);
    if (signature === lastJobsSignature) return;
    lastJobsSignature = signature;

    if (!jobs.length) {
      $('#jobs').innerHTML = '<p class="job-meta">No jobs created yet. Submit a job above to begin.</p>';
      return;
    }

    $('#jobs').innerHTML = jobs.map(j => {
      const isRunning = j.status === 'running';
      const pct = Math.round((j.progress || 0) * 100);
      const isFaceSwap = j.kind === 'face_swap';
      const profile = j.params?.profile || 'high';

      return `
        <article class="job-card">
          <div class="job-header">
            <span class="job-kind">
              ${isFaceSwap ? 'Face Swap' : esc(j.kind.replace(/_/g, ' '))}
              ${isFaceSwap ? `<span class="tag-rec" style="margin-left:6px">${esc(profile)}</span>` : ''}
            </span>
            <span class="badge ${j.status}">${esc(j.status)}</span>
          </div>

          <div class="job-meta">
            ${new Date(j.created_at).toLocaleTimeString()} · ${j.id.slice(0, 10)}
            ${j.params?.quality ? `· Q:${j.params.quality}%` : ''}
          </div>

          ${isRunning ? `
            <div class="progress-container">
              <div class="progress-header">
                <span>${esc(j.stage || 'Preparing')}</span>
                <span>${isFaceSwap ? `${pct}%` : 'Working'}</span>
              </div>
              <div class="progress-track ${isFaceSwap ? '' : 'indeterminate'}">
                <div class="progress-fill" style="width: ${isFaceSwap ? pct : 35}%"></div>
              </div>
            </div>
          ` : ''}

          ${j.params?.prompt ? `<div style="font-size:12px;color:var(--text-muted)">${esc(j.params.prompt)}</div>` : ''}

          ${j.error ? `
            <div class="job-error-box">
              ${esc(j.error)}
            </div>
          ` : ''}

          ${j.outputs && j.outputs.length ? `
            <div class="output-preview-container">
              ${j.outputs.map((_, i) => `
                <video class="output-video-player" controls preload="metadata">
                  <source src="/api/jobs/${j.id}/output/${i}" type="video/mp4">
                  Your browser does not support the video tag.
                </video>
              `).join('')}
            </div>
          ` : ''}

          <div class="job-actions">
            ${j.outputs && j.outputs.length ? j.outputs.map((_, i) => `
              <button onclick="revealOutput('${j.id}', ${i})" class="action-btn">📁 Show in Finder</button>
            `).join('') : ''}

            ${j.status === 'failed' ? `
              <button onclick="retryJob('${j.id}')" class="action-btn">🔄 Retry</button>
            ` : ''}

            ${(j.status === 'running' || j.status === 'queued') ? `
              <button onclick="cancelJob('${j.id}')" class="action-btn danger">⏹ Cancel</button>
            ` : ''}

            <button onclick="openLogs('${j.id}')" class="action-btn">📜 View Logs</button>
            ${!['running', 'queued', 'staging'].includes(j.status) ? `<button onclick="deleteJob('${j.id}')" class="action-btn danger">🗑 Delete files</button>` : ''}
          </div>
        </article>
      `;
    }).join('');
  } catch (err) {
    console.error('Failed to load jobs:', err);
  }
}

// Initial Run and Live Polling
$('#refresh').onclick = () => { loadJobs(); loadStorage(); };
checkHealth();
loadJobs();
loadStorage();
setInterval(loadJobs, 4000);
setInterval(checkHealth, 15000);
setInterval(loadStorage, 15000);
