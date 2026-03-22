/**
 * Customizer Admin Panel — JavaScript
 * =====================================
 * Standalone tool (open index.html in a browser — no server required).
 *
 * State is held in `state` and saved to localStorage on every change so work
 * is not lost on accidental page refresh.
 */

'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────────────────

const STORAGE_KEY = 'customizer-admin-state';

const defaultState = () => ({
  previewUrl: '',          // URL of the loaded preview image
  rectangles: [],          // Array of rectangle objects (see newRect())
  global: {
    fonts:  [],            // [{ name, url }]
    colors: [],            // ['#hex', ...]
    images: [],            // [{ name, url }]
  },
  selectedRectId: null,
});

let state = loadState();

// ─────────────────────────────────────────────────────────────────────────────
// Persistence
// ─────────────────────────────────────────────────────────────────────────────

function loadState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return Object.assign(defaultState(), JSON.parse(raw));
  } catch (_) {}
  return defaultState();
}

function saveState() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch (_) {}
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

let _rectCounter = state.rectangles.length;

function newRect(x = 50, y = 50, w = 200, h = 60) {
  _rectCounter++;
  return {
    id:          `rect${_rectCounter}`,
    label:       `Field ${_rectCounter}`,
    type:        'text',
    placeholder: 'Enter your text…',
    maxChars:    0,
    x: Math.round(x),
    y: Math.round(y),
    width:  Math.round(w),
    height: Math.round(h),
  };
}

function getRectById(id) {
  return state.rectangles.find(r => r.id === id);
}

function uid() {
  return Math.random().toString(36).slice(2, 8);
}

// ─────────────────────────────────────────────────────────────────────────────
// Canvas
// ─────────────────────────────────────────────────────────────────────────────

const canvas      = document.getElementById('admin-canvas');
const ctx         = canvas.getContext('2d');
const placeholder = document.getElementById('canvas-placeholder');
const dimsLabel   = document.getElementById('canvas-dims');

let loadedImage  = null;   // HTMLImageElement
let displayScale = 1;      // native-px → CSS-px ratio

// Drag state
const drag = {
  active: false,
  mode:   null,   // 'draw' | 'move' | 'resize'
  startX: 0, startY: 0,
  origX: 0, origY: 0, origW: 0, origH: 0,
  drawRect: null,
  handle:   null, // 'nw'|'ne'|'se'|'sw' for resize
};

const HANDLE_SIZE  = 8;   // pixels (CSS)
const HANDLE_HIT   = 12;  // slightly larger hit zone
const MIN_RECT_SIZE = 20;

function canvasToImageCoords(cssX, cssY) {
  const rect = canvas.getBoundingClientRect();
  const relX  = cssX - rect.left;
  const relY  = cssY - rect.top;
  return {
    x: Math.round(relX / displayScale),
    y: Math.round(relY / displayScale),
  };
}

function imageToCanvasCoords(imgX, imgY) {
  return {
    x: imgX * displayScale,
    y: imgY * displayScale,
  };
}

function loadImageFromUrl(url) {
  if (!url) return;
  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.onload = () => {
    loadedImage = img;
    canvas.width  = img.naturalWidth;
    canvas.height = img.naturalHeight;
    placeholder.style.display = 'none';
    fitCanvas();
    renderCanvas();
    dimsLabel.textContent = `${img.naturalWidth} × ${img.naturalHeight} px`;
  };
  img.onerror = () => alert('Could not load image. Check the URL or CORS settings.');
  img.src = url;
}

function fitCanvas() {
  const wrap   = document.getElementById('canvas-wrap');
  const maxW   = wrap.clientWidth  - 32;
  const maxH   = wrap.clientHeight - 32;
  const nativeW = canvas.width;
  const nativeH = canvas.height;

  if (nativeW > maxW || nativeH > maxH) {
    const scaleW = maxW / nativeW;
    const scaleH = maxH / nativeH;
    displayScale = Math.min(scaleW, scaleH);
  } else {
    displayScale = 1;
  }

  canvas.style.width  = Math.round(nativeW * displayScale) + 'px';
  canvas.style.height = Math.round(nativeH * displayScale) + 'px';
}

function renderCanvas() {
  const W = canvas.width;
  const H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  if (loadedImage) {
    ctx.drawImage(loadedImage, 0, 0, W, H);
  }

  const showOverlay = document.getElementById('show-overlay').checked;
  if (!showOverlay) return;

  state.rectangles.forEach(rect => {
    const isSelected = rect.id === state.selectedRectId;

    // Fill
    ctx.save();
    ctx.fillStyle = isSelected ? 'rgba(59,130,246,0.18)' : 'rgba(255,255,255,0.15)';
    ctx.fillRect(rect.x, rect.y, rect.width, rect.height);

    // Border
    ctx.strokeStyle = isSelected ? '#3b82f6' : '#fff';
    ctx.lineWidth   = isSelected ? 2.5 : 1.5;
    ctx.setLineDash(isSelected ? [] : [4, 3]);
    ctx.strokeRect(rect.x, rect.y, rect.width, rect.height);
    ctx.setLineDash([]);

    // Label
    const labelText = `${rect.label} (${rect.type})`;
    ctx.font      = `bold ${Math.max(11, Math.min(16, rect.height * 0.35))}px sans-serif`;
    ctx.fillStyle = isSelected ? '#1d4ed8' : '#fff';
    ctx.textBaseline = 'top';
    ctx.shadowColor  = 'rgba(0,0,0,0.6)';
    ctx.shadowBlur   = 3;
    ctx.fillText(labelText, rect.x + 4, rect.y + 4, rect.width - 8);
    ctx.shadowBlur = 0;

    // Corner handles (only when selected)
    if (isSelected) {
      [
        [rect.x,               rect.y              ],
        [rect.x + rect.width,  rect.y              ],
        [rect.x + rect.width,  rect.y + rect.height],
        [rect.x,               rect.y + rect.height],
      ].forEach(([hx, hy]) => {
        ctx.fillStyle   = '#fff';
        ctx.strokeStyle = '#3b82f6';
        ctx.lineWidth   = 1.5;
        ctx.beginPath();
        ctx.arc(hx, hy, HANDLE_SIZE / displayScale, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
      });
    }

    ctx.restore();
  });
}

// ── Hit detection ─────────────────────────────────────────────────────────────

function hitHandle(imgX, imgY, rect) {
  const hs = HANDLE_HIT / displayScale;
  const corners = [
    { name: 'nw', x: rect.x,              y: rect.y               },
    { name: 'ne', x: rect.x + rect.width, y: rect.y               },
    { name: 'se', x: rect.x + rect.width, y: rect.y + rect.height },
    { name: 'sw', x: rect.x,              y: rect.y + rect.height },
  ];
  return corners.find(c => Math.abs(imgX - c.x) <= hs && Math.abs(imgY - c.y) <= hs) || null;
}

function hitRect(imgX, imgY) {
  // Iterate in reverse so top-most rectangles are hit first
  for (let i = state.rectangles.length - 1; i >= 0; i--) {
    const r = state.rectangles[i];
    if (imgX >= r.x && imgX <= r.x + r.width &&
        imgY >= r.y && imgY <= r.y + r.height) {
      return r;
    }
  }
  return null;
}

// ── Mouse events ──────────────────────────────────────────────────────────────

canvas.addEventListener('mousedown', e => {
  if (!loadedImage) return;
  e.preventDefault();
  const { x: imgX, y: imgY } = canvasToImageCoords(e.clientX, e.clientY);

  // 1. Check resize handle on selected rect
  if (state.selectedRectId) {
    const selRect = getRectById(state.selectedRectId);
    if (selRect) {
      const handle = hitHandle(imgX, imgY, selRect);
      if (handle) {
        drag.active  = true;
        drag.mode    = 'resize';
        drag.handle  = handle.name;
        drag.startX  = imgX;
        drag.startY  = imgY;
        drag.origX   = selRect.x;
        drag.origY   = selRect.y;
        drag.origW   = selRect.width;
        drag.origH   = selRect.height;
        return;
      }
    }
  }

  // 2. Check if clicking an existing rect
  const hit = hitRect(imgX, imgY);
  if (hit) {
    selectRect(hit.id);
    drag.active = true;
    drag.mode   = 'move';
    drag.startX = imgX;
    drag.startY = imgY;
    drag.origX  = hit.x;
    drag.origY  = hit.y;
    return;
  }

  // 3. Start drawing a new rect
  drag.active  = true;
  drag.mode    = 'draw';
  drag.startX  = imgX;
  drag.startY  = imgY;
  drag.drawRect = newRect(imgX, imgY, 1, 1);
  state.rectangles.push(drag.drawRect);
  selectRect(drag.drawRect.id);
});

canvas.addEventListener('mousemove', e => {
  if (!drag.active) return;
  const { x: imgX, y: imgY } = canvasToImageCoords(e.clientX, e.clientY);

  if (drag.mode === 'draw') {
    const r = drag.drawRect;
    r.x      = Math.min(imgX, drag.startX);
    r.y      = Math.min(imgY, drag.startY);
    r.width  = Math.max(Math.abs(imgX - drag.startX), 1);
    r.height = Math.max(Math.abs(imgY - drag.startY), 1);
    renderCanvas();
    syncPropsPanel();

  } else if (drag.mode === 'move') {
    const selRect = getRectById(state.selectedRectId);
    if (!selRect) return;
    selRect.x = Math.max(0, drag.origX + (imgX - drag.startX));
    selRect.y = Math.max(0, drag.origY + (imgY - drag.startY));
    renderCanvas();
    syncPropsPanel();

  } else if (drag.mode === 'resize') {
    const selRect = getRectById(state.selectedRectId);
    if (!selRect) return;
    const dx = imgX - drag.startX;
    const dy = imgY - drag.startY;
    const h  = drag.handle;

    let nx = drag.origX, ny = drag.origY, nw = drag.origW, nh = drag.origH;

    if (h === 'nw') { nx += dx; ny += dy; nw -= dx; nh -= dy; }
    if (h === 'ne') {            ny += dy; nw += dx; nh -= dy; }
    if (h === 'se') {                      nw += dx; nh += dy; }
    if (h === 'sw') { nx += dx;            nw -= dx; nh += dy; }

    selRect.x      = Math.round(nx);
    selRect.y      = Math.round(ny);
    selRect.width  = Math.max(MIN_RECT_SIZE, Math.round(nw));
    selRect.height = Math.max(MIN_RECT_SIZE, Math.round(nh));
    renderCanvas();
    syncPropsPanel();
  }
});

window.addEventListener('mouseup', () => {
  if (drag.active) {
    drag.active = false;
    if (drag.mode === 'draw') {
      const r = drag.drawRect;
      if (r.width < MIN_RECT_SIZE || r.height < MIN_RECT_SIZE) {
        // Too small — remove it
        state.rectangles = state.rectangles.filter(x => x.id !== r.id);
        selectRect(null);
      } else {
        updateAllUI();
      }
      drag.drawRect = null;
    }
    saveState();
    updateExport();
  }
});

// Cursor feedback
canvas.addEventListener('mousemove', e => {
  if (drag.active) return;
  if (!loadedImage) return;
  const { x, y } = canvasToImageCoords(e.clientX, e.clientY);
  let cursor = 'crosshair';

  if (state.selectedRectId) {
    const selRect = getRectById(state.selectedRectId);
    if (selRect) {
      const handle = hitHandle(x, y, selRect);
      if (handle) {
        const map = { nw: 'nw-resize', ne: 'ne-resize', se: 'se-resize', sw: 'sw-resize' };
        cursor = map[handle.name];
      } else if (hitRect(x, y)) {
        cursor = 'move';
      }
    }
  } else if (hitRect(x, y)) {
    cursor = 'move';
  }

  canvas.style.cursor = cursor;
});

// ─────────────────────────────────────────────────────────────────────────────
// Rectangle list (sidebar)
// ─────────────────────────────────────────────────────────────────────────────

function renderRectList() {
  const list = document.getElementById('rect-list');
  list.innerHTML = '';

  if (state.rectangles.length === 0) {
    list.innerHTML = '<p class="field-hint">No rectangles yet. Draw one on the canvas or click Add.</p>';
    return;
  }

  state.rectangles.forEach(rect => {
    const item = document.createElement('div');
    item.className   = 'rect-list-item' + (rect.id === state.selectedRectId ? ' is-selected' : '');
    item.dataset.id  = rect.id;

    const typeIcon = rect.type === 'text' ? '✎' : '⬛';
    item.innerHTML = `
      <span class="rect-list-icon">${typeIcon}</span>
      <span class="rect-list-label">${escapeHtml(rect.label)}</span>
      <span class="rect-list-meta">${rect.width}×${rect.height}</span>
    `;
    item.addEventListener('click', () => {
      selectRect(rect.id);
    });
    list.appendChild(item);
  });
}

function selectRect(id) {
  state.selectedRectId = id;
  renderRectList();
  syncPropsPanel();
  renderCanvas();
}

// ─────────────────────────────────────────────────────────────────────────────
// Properties panel
// ─────────────────────────────────────────────────────────────────────────────

const propsPanel = document.getElementById('rect-props');

function syncPropsPanel() {
  const rect = getRectById(state.selectedRectId);
  if (!rect) {
    propsPanel.style.display = 'none';
    return;
  }
  propsPanel.style.display = '';

  document.getElementById('prop-label').value       = rect.label;
  document.getElementById('prop-type').value        = rect.type;
  document.getElementById('prop-placeholder').value = rect.placeholder || '';
  document.getElementById('prop-maxchars').value    = rect.maxChars || 0;
  document.getElementById('prop-x').value           = rect.x;
  document.getElementById('prop-y').value           = rect.y;
  document.getElementById('prop-w').value           = rect.width;
  document.getElementById('prop-h').value           = rect.height;

  // Show/hide text-only fields
  document.querySelectorAll('.prop-condition.text-only').forEach(el => {
    el.parentElement.style.opacity = rect.type === 'text' ? '1' : '0.4';
  });
}

function bindPropsPanel() {
  function onPropChange() {
    const rect = getRectById(state.selectedRectId);
    if (!rect) return;

    rect.label       = document.getElementById('prop-label').value;
    rect.type        = document.getElementById('prop-type').value;
    rect.placeholder = document.getElementById('prop-placeholder').value;
    rect.maxChars    = parseInt(document.getElementById('prop-maxchars').value, 10) || 0;
    rect.x           = parseInt(document.getElementById('prop-x').value, 10) || 0;
    rect.y           = parseInt(document.getElementById('prop-y').value, 10) || 0;
    rect.width       = parseInt(document.getElementById('prop-w').value, 10) || MIN_RECT_SIZE;
    rect.height      = parseInt(document.getElementById('prop-h').value, 10) || MIN_RECT_SIZE;

    updateAllUI();
    saveState();
    updateExport();
  }

  ['prop-label','prop-type','prop-placeholder','prop-maxchars',
   'prop-x','prop-y','prop-w','prop-h'].forEach(id => {
    const el = document.getElementById(id);
    el.addEventListener('input',  onPropChange);
    el.addEventListener('change', onPropChange);
  });

  document.getElementById('delete-rect-btn').addEventListener('click', () => {
    if (!state.selectedRectId) return;
    if (!confirm('Delete this rectangle?')) return;
    state.rectangles = state.rectangles.filter(r => r.id !== state.selectedRectId);
    selectRect(null);
    updateAllUI();
    saveState();
    updateExport();
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Global settings: Fonts
// ─────────────────────────────────────────────────────────────────────────────

function renderFontsList() {
  const list = document.getElementById('fonts-list');
  list.innerHTML = '';
  state.global.fonts.forEach((font, i) => {
    const row = document.createElement('div');
    row.className = 'item-row';
    row.innerHTML = `
      <input type="text" class="field-input field-sm font-name" placeholder="Font name" value="${escapeHtml(font.name)}" />
      <input type="url"  class="field-input field-sm font-url"  placeholder="URL (blank = system font)" value="${escapeHtml(font.url || '')}" />
      <button class="btn btn-danger btn-xs item-remove" data-index="${i}" title="Remove">✕</button>
    `;
    row.querySelectorAll('input').forEach(inp => {
      inp.addEventListener('input', () => {
        state.global.fonts[i].name = row.querySelector('.font-name').value;
        state.global.fonts[i].url  = row.querySelector('.font-url').value  || null;
        saveState(); updateExport();
      });
    });
    row.querySelector('.item-remove').addEventListener('click', () => {
      state.global.fonts.splice(i, 1);
      renderFontsList(); saveState(); updateExport();
    });
    list.appendChild(row);
  });
}

document.getElementById('add-font-btn').addEventListener('click', () => {
  state.global.fonts.push({ name: 'Arial', url: null });
  renderFontsList(); saveState(); updateExport();
});

// ─────────────────────────────────────────────────────────────────────────────
// Global settings: Colors
// ─────────────────────────────────────────────────────────────────────────────

function renderColorsList() {
  const list = document.getElementById('colors-list');
  list.innerHTML = '';
  state.global.colors.forEach((color, i) => {
    const row = document.createElement('div');
    row.className = 'item-row';
    row.innerHTML = `
      <input type="color" class="color-picker" value="${color}" title="${color}" />
      <input type="text"  class="field-input field-sm color-hex" value="${color}" placeholder="#000000" />
      <button class="btn btn-danger btn-xs item-remove" data-index="${i}" title="Remove">✕</button>
    `;
    const picker = row.querySelector('.color-picker');
    const hex    = row.querySelector('.color-hex');

    picker.addEventListener('input', () => {
      hex.value = picker.value;
      state.global.colors[i] = picker.value;
      saveState(); updateExport();
    });
    hex.addEventListener('input', () => {
      if (/^#[0-9a-fA-F]{6}$/.test(hex.value)) {
        picker.value = hex.value;
        state.global.colors[i] = hex.value;
        saveState(); updateExport();
      }
    });
    row.querySelector('.item-remove').addEventListener('click', () => {
      state.global.colors.splice(i, 1);
      renderColorsList(); saveState(); updateExport();
    });
    list.appendChild(row);
  });
}

document.getElementById('add-color-btn').addEventListener('click', () => {
  state.global.colors.push('#000000');
  renderColorsList(); saveState(); updateExport();
});

// ─────────────────────────────────────────────────────────────────────────────
// Global settings: Image library
// ─────────────────────────────────────────────────────────────────────────────

function renderImagesList() {
  const list = document.getElementById('images-list');
  list.innerHTML = '';
  state.global.images.forEach((img, i) => {
    const row = document.createElement('div');
    row.className = 'item-row';
    row.innerHTML = `
      <input type="text" class="field-input field-sm img-name" placeholder="Name" value="${escapeHtml(img.name || '')}" />
      <input type="url"  class="field-input field-sm img-url"  placeholder="Image URL" value="${escapeHtml(img.url  || '')}" />
      <button class="btn btn-danger btn-xs item-remove" title="Remove">✕</button>
    `;
    row.querySelectorAll('input').forEach(inp => {
      inp.addEventListener('input', () => {
        state.global.images[i].name = row.querySelector('.img-name').value;
        state.global.images[i].url  = row.querySelector('.img-url').value;
        saveState(); updateExport();
      });
    });
    row.querySelector('.item-remove').addEventListener('click', () => {
      state.global.images.splice(i, 1);
      renderImagesList(); saveState(); updateExport();
    });
    list.appendChild(row);
  });
}

document.getElementById('add-image-btn').addEventListener('click', () => {
  state.global.images.push({ name: '', url: '' });
  renderImagesList(); saveState(); updateExport();
});

// ─────────────────────────────────────────────────────────────────────────────
// Export JSON
// ─────────────────────────────────────────────────────────────────────────────

function buildProductConfig() {
  return {
    enabled: true,
    rectangles: state.rectangles.map(r => {
      const out = {
        id:     r.id,
        label:  r.label,
        type:   r.type,
        x:      r.x,
        y:      r.y,
        width:  r.width,
        height: r.height,
      };
      if (r.type === 'text') {
        if (r.placeholder) out.placeholder = r.placeholder;
        if (r.maxChars > 0) out.maxChars   = r.maxChars;
      }
      return out;
    }),
  };
}

function updateExport() {
  const productJson = JSON.stringify(buildProductConfig(), null, 2);
  const globalJson  = JSON.stringify(state.global, null, 2);

  document.getElementById('export-product').value  = productJson;
  document.getElementById('export-global').value   = globalJson;
  document.getElementById('export-preview').value  = state.previewUrl || '';
}

function bindCopyButtons() {
  const pairs = [
    ['copy-product-btn', 'export-product'],
    ['copy-global-btn',  'export-global'],
    ['copy-preview-btn', 'export-preview'],
  ];
  pairs.forEach(([btnId, taId]) => {
    document.getElementById(btnId).addEventListener('click', () => {
      const ta  = document.getElementById(taId);
      navigator.clipboard.writeText(ta.value).then(() => {
        const btn = document.getElementById(btnId);
        const orig = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = orig; }, 1500);
      });
    });
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Image loading controls
// ─────────────────────────────────────────────────────────────────────────────

document.getElementById('load-image-btn').addEventListener('click', () => {
  const urlInput = document.getElementById('preview-url');
  const fileInput = document.getElementById('preview-file');

  if (fileInput.files && fileInput.files[0]) {
    const reader = new FileReader();
    reader.onload = e => {
      state.previewUrl = e.target.result;
      urlInput.value   = '';
      loadImageFromUrl(state.previewUrl);
      saveState(); updateExport();
    };
    reader.readAsDataURL(fileInput.files[0]);
  } else if (urlInput.value.trim()) {
    state.previewUrl = urlInput.value.trim();
    loadImageFromUrl(state.previewUrl);
    saveState(); updateExport();
  } else {
    alert('Enter an image URL or choose a local file.');
  }
});

document.getElementById('preview-url').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('load-image-btn').click();
});

// ─────────────────────────────────────────────────────────────────────────────
// Add rect button
// ─────────────────────────────────────────────────────────────────────────────

document.getElementById('add-rect-btn').addEventListener('click', () => {
  if (!loadedImage) {
    alert('Load a preview image first.');
    return;
  }
  const r = newRect(40, 40, 200, 60);
  state.rectangles.push(r);
  selectRect(r.id);
  updateAllUI();
  saveState();
  updateExport();
});

// ─────────────────────────────────────────────────────────────────────────────
// Tabs
// ─────────────────────────────────────────────────────────────────────────────

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('is-active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.add('is-hidden'));
    btn.classList.add('is-active');
    document.getElementById(`tab-${btn.dataset.tab}`).classList.remove('is-hidden');

    if (btn.dataset.tab === 'export') updateExport();
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Show-overlay toggle
// ─────────────────────────────────────────────────────────────────────────────

document.getElementById('show-overlay').addEventListener('change', renderCanvas);

// ─────────────────────────────────────────────────────────────────────────────
// Resize canvas when window resizes
// ─────────────────────────────────────────────────────────────────────────────

window.addEventListener('resize', () => {
  if (loadedImage) { fitCanvas(); renderCanvas(); }
});

// ─────────────────────────────────────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────────────────────────────────────

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function updateAllUI() {
  renderRectList();
  syncPropsPanel();
  renderCanvas();
}

// ─────────────────────────────────────────────────────────────────────────────
// Boot
// ─────────────────────────────────────────────────────────────────────────────

function boot() {
  bindPropsPanel();
  bindCopyButtons();

  // Restore state
  renderFontsList();
  renderColorsList();
  renderImagesList();
  renderRectList();
  updateExport();

  // Restore preview image (skip data-URLs for cross-origin canvas reasons)
  if (state.previewUrl && state.previewUrl.startsWith('http')) {
    document.getElementById('preview-url').value = state.previewUrl;
    loadImageFromUrl(state.previewUrl);
  } else if (state.previewUrl && state.previewUrl.startsWith('data:')) {
    loadImageFromUrl(state.previewUrl);
  }
}

boot();
