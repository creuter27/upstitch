/**
 * Shopify Customizer Plugin — Storefront Script
 * ==============================================
 * Handles live-preview personalization with text and image rectangles.
 *
 * Designed for easy extension:
 *   - Customer image upload: hook into buildImageControl() and add an upload
 *     button that reads a File, draws it to a temporary canvas, and stores the
 *     result as a data-URL in this.values[rect.id].imageUrl.
 *   - Google Drive image library: populate global.images from the Drive API
 *     before calling init(), or lazy-load them inside buildImageControl().
 */

(function () {
  'use strict';

  // ─────────────────────────────────────────────────────────────────────────────
  // Customizer class
  // ─────────────────────────────────────────────────────────────────────────────

  class Customizer {
    constructor(wrapper) {
      this.wrapper = wrapper;

      try {
        this.config = JSON.parse(wrapper.dataset.config || '{}');
        this.global = JSON.parse(wrapper.dataset.global || '{}');
      } catch (e) {
        console.error('[Customizer] Failed to parse data attributes:', e);
        return;
      }

      this.previewUrl   = wrapper.dataset.preview || '';
      this.canvas       = wrapper.querySelector('#customizer-canvas');
      this.ctx          = this.canvas.getContext('2d');
      this.controlsEl   = wrapper.querySelector('#customizer-controls');
      this.propertiesEl = wrapper.querySelector('#customizer-line-item-properties');
      this.loadingEl    = wrapper.querySelector('#customizer-loading');
      this.errorEl      = wrapper.querySelector('#customizer-error');

      // Per-rectangle current values
      // { [rectId]: { type, text?, font?, color?, imageUrl?, imageName? } }
      this.values = {};

      // Cache for decoded library images (url → HTMLImageElement)
      this._imageCache = {};

      // Fonts we have already loaded so we don't call FontFace twice
      this._loadedFonts = new Set();

      this.init();
    }

    // ── Bootstrap ─────────────────────────────────────────────────────────────

    async init() {
      try {
        await this.loadFonts();
        await this.loadPreviewImage(this.previewUrl);
        this.initDefaultValues();
        this.buildControls();
        this.render();
        this.updateLineItemProperties();
        this.hookIntoAddToCart();
      } catch (e) {
        console.error('[Customizer] Init failed:', e);
      }
    }

    // ── Font loading ──────────────────────────────────────────────────────────

    async loadFonts() {
      const fonts = this.global.fonts || [];
      await Promise.all(
        fonts
          .filter(f => f.url && !this._loadedFonts.has(f.name))
          .map(async f => {
            try {
              const face = new FontFace(f.name, `url(${f.url})`);
              await face.load();
              document.fonts.add(face);
              this._loadedFonts.add(f.name);
            } catch (err) {
              console.warn(`[Customizer] Font "${f.name}" failed to load:`, err);
            }
          })
      );
    }

    // ── Preview image ─────────────────────────────────────────────────────────

    loadPreviewImage(url) {
      return new Promise((resolve, reject) => {
        if (!url) {
          this.loadingEl.style.display = 'none';
          this.errorEl.style.display   = 'block';
          reject(new Error('No preview image URL'));
          return;
        }

        const img = new Image();
        img.crossOrigin = 'anonymous';

        img.onload = () => {
          this.image              = img;
          this.canvas.width       = img.naturalWidth;
          this.canvas.height      = img.naturalHeight;
          this._applyCanvasScale();
          this.loadingEl.style.display = 'none';
          resolve();
        };

        img.onerror = () => {
          this.loadingEl.style.display = 'none';
          this.errorEl.style.display   = 'block';
          reject(new Error(`Image failed to load: ${url}`));
        };

        img.src = url;
      });
    }

    /** Call this when the variant changes (called from the liquid inline script). */
    async updatePreviewImage(url) {
      this.previewUrl = url;
      this.loadingEl.style.display = 'flex';
      this.errorEl.style.display   = 'none';
      try {
        await this.loadPreviewImage(url);
        this.render();
      } catch (_) {}
    }

    /** Scale the canvas CSS size to fit the container without distorting. */
    _applyCanvasScale() {
      const area   = this.wrapper.querySelector('.customizer-preview-area');
      const maxW   = area ? area.clientWidth : 600;
      const nativeW = this.canvas.width;
      const nativeH = this.canvas.height;

      if (nativeW > maxW) {
        this.canvas.style.width  = maxW + 'px';
        this.canvas.style.height = Math.round(nativeH * maxW / nativeW) + 'px';
      } else {
        this.canvas.style.width  = nativeW + 'px';
        this.canvas.style.height = nativeH + 'px';
      }
    }

    // ── Default values ────────────────────────────────────────────────────────

    initDefaultValues() {
      const fonts  = this.global.fonts  || [];
      const colors = this.global.colors || ['#000000'];
      const defaultFont  = fonts[0]  ? fonts[0].name  : 'Arial';
      const defaultColor = colors[0] || '#000000';

      (this.config.rectangles || []).forEach(rect => {
        if (rect.type === 'text') {
          this.values[rect.id] = {
            type: 'text', text: '', font: defaultFont, color: defaultColor,
          };
        } else if (rect.type === 'image') {
          this.values[rect.id] = {
            type: 'image', imageUrl: null, imageName: null,
          };
        }
      });
    }

    // ── Control building ──────────────────────────────────────────────────────

    buildControls() {
      this.controlsEl.innerHTML = '';

      (this.config.rectangles || []).forEach((rect, index) => {
        const group = document.createElement('div');
        group.className      = 'customizer-control-group';
        group.dataset.rectId = rect.id;

        const label = document.createElement('div');
        label.className = 'customizer-control-label';
        label.textContent =
          rect.label ||
          (rect.type === 'text' ? `Text ${index + 1}` : `Image ${index + 1}`);
        group.appendChild(label);

        if (rect.type === 'text') {
          group.appendChild(this._buildTextControl(rect));
        } else if (rect.type === 'image') {
          group.appendChild(this._buildImageControl(rect));
        }

        this.controlsEl.appendChild(group);
      });
    }

    // ── Text control ──────────────────────────────────────────────────────────

    _buildTextControl(rect) {
      const wrap    = document.createElement('div');
      wrap.className = 'customizer-text-control';

      // --- Text input + character counter ---
      const inputRow = document.createElement('div');
      inputRow.className = 'customizer-input-row';

      const input = document.createElement('input');
      input.type        = 'text';
      input.className   = 'customizer-text-input';
      input.placeholder = rect.placeholder || 'Enter your text…';
      if (rect.maxChars) input.maxLength = rect.maxChars;

      const onChange = () => {
        this.values[rect.id].text = input.value;
        if (rect.maxChars) counter.textContent = `${input.value.length} / ${rect.maxChars}`;
        this.render();
        this.updateLineItemProperties();
      };

      input.addEventListener('input',  onChange);
      input.addEventListener('change', onChange);
      inputRow.appendChild(input);

      const counter = document.createElement('span');
      counter.className = 'customizer-char-counter';
      if (rect.maxChars) {
        counter.textContent = `0 / ${rect.maxChars}`;
        inputRow.appendChild(counter);
      }

      wrap.appendChild(inputRow);

      // --- Font selector ---
      const fonts = this.global.fonts || [];
      if (fonts.length > 1) {
        const row = document.createElement('div');
        row.className = 'customizer-option-row';

        const lbl = document.createElement('span');
        lbl.className = 'customizer-option-label';
        lbl.textContent = 'Font:';
        row.appendChild(lbl);

        const sel = document.createElement('select');
        sel.className = 'customizer-font-select';
        fonts.forEach(f => {
          const opt = document.createElement('option');
          opt.value         = f.name;
          opt.textContent   = f.name;
          opt.style.fontFamily = f.name;
          sel.appendChild(opt);
        });
        sel.value = this.values[rect.id].font;
        sel.addEventListener('change', () => {
          this.values[rect.id].font = sel.value;
          this.render();
          this.updateLineItemProperties();
        });

        row.appendChild(sel);
        wrap.appendChild(row);
      }

      // --- Color swatches ---
      const colors = this.global.colors || [];
      if (colors.length > 0) {
        const row = document.createElement('div');
        row.className = 'customizer-option-row';

        const lbl = document.createElement('span');
        lbl.className = 'customizer-option-label';
        lbl.textContent = 'Color:';
        row.appendChild(lbl);

        const swatchWrap = document.createElement('div');
        swatchWrap.className = 'customizer-color-swatches';

        colors.forEach(color => {
          const btn = document.createElement('button');
          btn.type                  = 'button';
          btn.className             = 'customizer-color-swatch';
          btn.style.backgroundColor = color;
          btn.title                 = color;
          btn.setAttribute('aria-label', `Color: ${color}`);

          if (color === this.values[rect.id].color) {
            btn.classList.add('is-active');
          }

          btn.addEventListener('click', () => {
            swatchWrap
              .querySelectorAll('.customizer-color-swatch')
              .forEach(s => s.classList.remove('is-active'));
            btn.classList.add('is-active');
            this.values[rect.id].color = color;
            this.render();
            this.updateLineItemProperties();
          });

          swatchWrap.appendChild(btn);
        });

        row.appendChild(swatchWrap);
        wrap.appendChild(row);
      }

      return wrap;
    }

    // ── Image picker control ──────────────────────────────────────────────────

    _buildImageControl(rect) {
      const wrap = document.createElement('div');
      wrap.className = 'customizer-image-control';

      const images = this.global.images || [];

      if (images.length === 0) {
        const msg = document.createElement('p');
        msg.className   = 'customizer-no-images';
        msg.textContent = 'No images available.';
        wrap.appendChild(msg);
        return wrap;
      }

      const grid = document.createElement('div');
      grid.className = 'customizer-image-grid';

      // "None" option
      const noneBtn = document.createElement('button');
      noneBtn.type      = 'button';
      noneBtn.className = 'customizer-image-option customizer-image-none is-active';
      noneBtn.textContent = '✕';
      noneBtn.title     = 'None';
      noneBtn.addEventListener('click', () => {
        grid.querySelectorAll('.customizer-image-option').forEach(b => b.classList.remove('is-active'));
        noneBtn.classList.add('is-active');
        this.values[rect.id].imageUrl  = null;
        this.values[rect.id].imageName = null;
        this.render();
        this.updateLineItemProperties();
      });
      grid.appendChild(noneBtn);

      images.forEach(imgDef => {
        const btn = document.createElement('button');
        btn.type      = 'button';
        btn.className = 'customizer-image-option';
        btn.title     = imgDef.name || '';
        btn.setAttribute('aria-label', imgDef.name || 'Select image');

        const imgEl    = document.createElement('img');
        imgEl.src      = imgDef.url;
        imgEl.alt      = imgDef.name || '';
        imgEl.loading  = 'lazy';
        btn.appendChild(imgEl);

        if (imgDef.name) {
          const cap = document.createElement('span');
          cap.className   = 'customizer-image-caption';
          cap.textContent = imgDef.name;
          btn.appendChild(cap);
        }

        btn.addEventListener('click', () => {
          grid.querySelectorAll('.customizer-image-option').forEach(b => b.classList.remove('is-active'));
          btn.classList.add('is-active');
          this.values[rect.id].imageUrl  = imgDef.url;
          this.values[rect.id].imageName = imgDef.name || imgDef.url;
          this.render();
          this.updateLineItemProperties();
        });

        grid.appendChild(btn);
      });

      wrap.appendChild(grid);
      return wrap;
    }

    // ── Canvas rendering ──────────────────────────────────────────────────────

    render() {
      if (!this.image) return;

      const ctx = this.ctx;
      const W   = this.canvas.width;
      const H   = this.canvas.height;

      ctx.clearRect(0, 0, W, H);
      ctx.drawImage(this.image, 0, 0, W, H);

      (this.config.rectangles || []).forEach(rect => {
        const val = this.values[rect.id];
        if (!val) return;

        if (rect.type === 'text' && val.text) {
          this._renderText(rect, val);
        } else if (rect.type === 'image' && val.imageUrl) {
          this._renderImage(rect, val);
        }
      });
    }

    /** Auto-scale text to fill the rectangle, centred. */
    _renderText(rect, val) {
      const ctx      = this.ctx;
      const fontSize = this._fitFontSize(val.text, val.font, rect.width, rect.height);

      ctx.save();
      // Clip to rectangle boundaries
      ctx.beginPath();
      ctx.rect(rect.x, rect.y, rect.width, rect.height);
      ctx.clip();

      ctx.font         = `${fontSize}px "${val.font}"`;
      ctx.fillStyle    = val.color || '#000000';
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(val.text, rect.x + rect.width / 2, rect.y + rect.height / 2);
      ctx.restore();
    }

    /**
     * Binary-search for the largest integer font size where the rendered text
     * fits within (maxW × maxH).  Uses 1.2× line-height approximation.
     */
    _fitFontSize(text, font, maxW, maxH) {
      const ctx = this.ctx;
      let lo = 4;
      let hi = Math.floor(maxH);  // can never be taller than the box

      while (lo < hi - 1) {
        const mid = Math.floor((lo + hi) / 2);
        ctx.font = `${mid}px "${font}"`;
        const measuredW = ctx.measureText(text).width;
        const measuredH = mid * 1.2;  // approximate line height

        if (measuredW <= maxW && measuredH <= maxH) {
          lo = mid;
        } else {
          hi = mid;
        }
      }
      return lo;
    }

    /** Render a library image centred & contained within the rectangle. */
    _renderImage(rect, val) {
      const cached = this._imageCache[val.imageUrl];
      if (cached) {
        this._drawContained(cached, rect);
      } else {
        const img      = new Image();
        img.crossOrigin = 'anonymous';
        img.onload     = () => {
          this._imageCache[val.imageUrl] = img;
          this.render();  // Re-render once loaded
        };
        img.src = val.imageUrl;
      }
    }

    /** Draw img centered and scaled-to-fit (object-fit: contain) inside rect. */
    _drawContained(img, rect) {
      const imgAspect  = img.naturalWidth / img.naturalHeight;
      const rectAspect = rect.width / rect.height;

      let drawW, drawH;
      if (imgAspect > rectAspect) {
        drawW = rect.width;
        drawH = rect.width / imgAspect;
      } else {
        drawH = rect.height;
        drawW = rect.height * imgAspect;
      }

      const drawX = rect.x + (rect.width  - drawW) / 2;
      const drawY = rect.y + (rect.height - drawH) / 2;

      const ctx = this.ctx;
      ctx.save();
      ctx.beginPath();
      ctx.rect(rect.x, rect.y, rect.width, rect.height);
      ctx.clip();
      ctx.drawImage(img, drawX, drawY, drawW, drawH);
      ctx.restore();
    }

    // ── Line-item properties ──────────────────────────────────────────────────

    /**
     * Sync the current customization state into hidden inputs.
     * These are moved into the ATC form on submit.
     */
    updateLineItemProperties() {
      this.propertiesEl.innerHTML = '';

      (this.config.rectangles || []).forEach((rect, i) => {
        const val   = this.values[rect.id];
        if (!val) return;
        const label = rect.label || (rect.type === 'text' ? `Text ${i + 1}` : `Image ${i + 1}`);

        if (rect.type === 'text' && val.text) {
          this._addHidden(`properties[${label}]`,         val.text);
          this._addHidden(`properties[${label} - Font]`,  val.font);
          this._addHidden(`properties[${label} - Color]`, val.color);
        } else if (rect.type === 'image' && val.imageName) {
          this._addHidden(`properties[${label}]`,            val.imageName);
          this._addHidden(`properties[${label} - Image URL]`, val.imageUrl);
        }
      });
    }

    _addHidden(name, value) {
      const inp  = document.createElement('input');
      inp.type   = 'hidden';
      inp.name   = name;
      inp.value  = value || '';
      this.propertiesEl.appendChild(inp);
    }

    // ── ATC form hook ─────────────────────────────────────────────────────────

    hookIntoAddToCart() {
      // Standard Shopify product form selector — works with most themes
      const form = document.querySelector('form[action*="/cart/add"]');
      if (!form) {
        console.warn('[Customizer] Add-to-cart form not found. ' +
          'Line item properties will not be submitted automatically.');
        return;
      }

      form.addEventListener('submit', () => {
        // Clone hidden inputs into the form so they are included in the POST
        this.propertiesEl
          .querySelectorAll('input[type="hidden"]')
          .forEach(inp => form.appendChild(inp.cloneNode(true)));
      });
    }
  }

  // ─────────────────────────────────────────────────────────────────────────────
  // Boot
  // ─────────────────────────────────────────────────────────────────────────────

  function boot() {
    const wrapper = document.getElementById('customizer-wrapper');
    if (wrapper) {
      window.customizerInstance = new Customizer(wrapper);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

})();
