/* VOSR2-Lite 图像对比查看器（window.VOSR2Viewer）。
 *
 * 超分场景的核心质检工具：把「原图」和「修复后」叠在同一坐标系里，
 * 拖动分割线看同一处的差异，并可缩放到 1:1 原生像素判断细节是否真的恢复。
 *
 * 几何模型（关键）：
 *   canvas 的尺寸 = 图片在「适配」状态下的显示尺寸（保持图片长宽比），
 *   图片以 100% 填满 canvas。缩放/平移只作用于 canvas 的 transform。
 *   ——若把 canvas 设成舞台大小、图片在内部 contain，放大时两侧留白
 *     会跟着一起放大，把图片推出可视区（2026-10-24 实测踩过）。
 *
 * 能力：
 * - 前后对比分割线（拖拽、←/→ 微调）
 * - 滚轮以光标为锚点缩放、拖拽平移
 * - 适配（小图不放大到失真）/ 1:1 原生像素 / 缩放百分比 HUD
 * - 放大镜（同时显示分割线两侧，跟随光标）
 * - 预览图先出、原图后台替换（6144² 结果也不卡首屏）
 */
(function () {
    'use strict';

    const LOUPE_ZOOM = 2.5;      // 放大镜相对屏幕的倍率
    const MAX_ONE_TO_ONE = 8;    // 最大放大 = 8× 原生像素
    const DIVIDER_HIT = 18;      // 分割线命中半径（px）

    class CompareViewer {
        constructor(root, opts) {
            this.root = root;
            this.stage = root.querySelector('.sv-viewer-stage');
            this.canvas = root.querySelector('.sv-viewer-canvas');
            this.beforeImg = root.querySelector('.sv-viewer-img-before');
            this.afterWrap = root.querySelector('.sv-viewer-after');
            this.afterImg = root.querySelector('.sv-viewer-img-after');
            this.divider = root.querySelector('.sv-viewer-divider');
            this.loupe = root.querySelector('.sv-viewer-loupe');
            this.loupeBefore = root.querySelector('.loupe-before');
            this.loupeAfterWrap = root.querySelector('.loupe-after-wrap');
            this.loupeAfter = root.querySelector('.loupe-after');
            this.hud = root.querySelector('.sv-viewer-hud');
            this.emptyEl = root.querySelector('.sv-viewer-empty');

            this.mag = 1;
            this.tx = 0;
            this.ty = 0;
            this.pos = 0.5;           // 分割线位置（0-1，相对 canvas）
            this.compare = true;
            this.natW = 0;
            this.natH = 0;
            this.oneToOneMag = 1;
            this.fitMag = 1;
            this.drag = null;
            this.loupeOn = false;
            this._pointer = { x: 0, y: 0, inside: false };
            this._loaded = 0;
            this._ro = null;

            this._bind();
        }

        // ── 对外 API ──────────────────────────────────────────────────────

        setImages({ before, after, beforeFull, afterFull }) {
            this.root.classList.remove('is-ready');
            this._loaded = 0;
            this.natW = 0;
            this.natH = 0;

            this.beforeImg.onload = () => this._onLoad();
            this.afterImg.onload = () => this._onLoad();
            this.beforeImg.src = before || '';
            this.afterImg.src = after || '';
            // 放大镜用同一批图（先预览，稍后随主图升级到原图）
            this.loupeBefore.src = before || '';
            this.loupeAfter.src = after || '';
            this.beforeImg.dataset.full = beforeFull || '';
            this.afterImg.dataset.full = afterFull || '';
            this.beforeImg.dataset.upgraded = '';
            this.afterImg.dataset.upgraded = '';

            this.pos = 0.5;
            this.mag = 1;
            this.tx = 0;
            this.ty = 0;
            this._apply();
        }

        clear() {
            [this.beforeImg, this.afterImg, this.loupeBefore, this.loupeAfter]
                .forEach(img => img.removeAttribute('src'));
            this.root.classList.remove('is-ready');
            this.natW = this.natH = 0;
        }

        setCompareEnabled(on) {
            this.compare = !!on;
            this.root.classList.toggle('is-split', this.compare);
            if (!this.compare) this.afterWrap.style.clipPath = '';
            const btn = this.root.querySelector('[data-viewer-action="compare"]');
            if (btn) btn.classList.toggle('active', this.compare);
            this._applyDivider();
            this._syncHud();
        }

        toggleCompare() { this.setCompareEnabled(!this.compare); }

        /** 适配窗口；图片小于舞台时按原生像素显示，避免浏览器插值失真。 */
        fit() {
            this.mag = this.fitMag || 1;
            this.tx = 0;
            this.ty = 0;
            this._clampPan();
            this._apply();
        }

        oneToOne() {
            if (!this.natW) return;
            this.mag = this.oneToOneMag;
            // 以图像中心为锚点：1:1 时先看到画面中心，而不是左上角
            const c = this._stageCenter();
            this.tx = c.x - (this.canvas.offsetWidth * this.mag) / 2;
            this.ty = c.y - (this.canvas.offsetHeight * this.mag) / 2;
            this._clampPan();
            this._apply();
        }

        zoomBy(factor, anchor) {
            this._setMag(this.mag * factor, anchor || this._stageCenter());
        }

        toggleMagnifier() {
            this.loupeOn = !this.loupeOn;
            this.root.classList.toggle('loupe-on', this.loupeOn);
            if (!this.loupeOn) {
                this.loupe.hidden = true;
            } else if (this._pointer.inside) {
                this._updateLoupe(this._pointer.x, this._pointer.y);
            }
            this._syncHud();
        }

        destroy() {
            if (this._ro) this._ro.disconnect();
        }

        // ── 加载与尺寸 ────────────────────────────────────────────────────

        _onLoad() {
            this._loaded += 1;
            if (this._loaded < 2) return;
            this.natW = this.afterImg.naturalWidth || this.beforeImg.naturalWidth;
            this.natH = this.afterImg.naturalHeight || this.beforeImg.naturalHeight;
            if (!this.natW) return;

            this.root.classList.add('is-ready');
            if (this.emptyEl) this.emptyEl.hidden = true;
            this._measure();
            this.fit();
            this._upgradeToFull();
            this._syncHud();
        }

        /** canvas 尺寸 = 图片适配舞台后的显示尺寸（保持长宽比）。 */
        _measure() {
            const rect = this.stage.getBoundingClientRect();
            const sw = Math.max(1, rect.width);
            const sh = Math.max(1, rect.height);
            const ar = this.natW / this.natH;
            let dispW = sw;
            let dispH = sw / ar;
            if (dispH > sh) { dispH = sh; dispW = sh * ar; }

            this.canvas.style.width = Math.round(dispW) + 'px';
            this.canvas.style.height = Math.round(dispH) + 'px';
            this.oneToOneMag = this.natW / dispW;
            this.fitMag = Math.min(1, this.oneToOneMag);
        }

        /** 预览图先显示；随后换成原图，1:1 才是真原生像素。 */
        _upgradeToFull() {
            const pairs = [
                [this.beforeImg, this.loupeBefore],
                [this.afterImg, this.loupeAfter],
            ];
            pairs.forEach(([img, loupeImg]) => {
                const full = img.dataset.full;
                if (!full || img.dataset.upgraded) return;
                img.dataset.upgraded = '1';
                const probe = new Image();
                probe.onload = () => {
                    // 原图与结果尺寸不同（如 774 vs 3096），canvas 按结果尺寸建立；
                    // 原图在 canvas 内被等比拉伸，正好等价于「朴素放大」对照
                    if (probe.naturalWidth > 0) {
                        img.src = full;
                        loupeImg.src = full;
                    }
                };
                probe.src = full;
            });
        }

        // ── 视图变换 ──────────────────────────────────────────────────────

        _setMag(next, anchor) {
            const min = this.fitMag || 1;
            const max = Math.max(this.oneToOneMag * MAX_ONE_TO_ONE, min);
            const mag = Math.min(max, Math.max(min, next));
            if (mag === this.mag) return;
            const ratio = mag / this.mag;
            this.tx = anchor.x - (anchor.x - this.tx) * ratio;
            this.ty = anchor.y - (anchor.y - this.ty) * ratio;
            this.mag = mag;
            this._clampPan();
            this._apply();
        }

        _clampPan() {
            const rect = this.stage.getBoundingClientRect();
            const cw = this.canvas.offsetWidth * this.mag;
            const ch = this.canvas.offsetHeight * this.mag;
            const vw = rect.width;
            const vh = rect.height;
            if (cw <= vw) this.tx = (vw - cw) / 2;
            else this.tx = Math.min(0, Math.max(vw - cw, this.tx));
            if (ch <= vh) this.ty = (vh - ch) / 2;
            else this.ty = Math.min(0, Math.max(vh - ch, this.ty));
        }

        _apply() {
            this.canvas.style.transform =
                `translate(${this.tx}px, ${this.ty}px) scale(${this.mag})`;
            this.canvas.style.setProperty('--sv-mag', this.mag);
            this._applyDivider();
            this._syncHud();
        }

        _applyDivider() {
            if (!this.compare) {
                this.afterWrap.style.clipPath = '';
                this.divider.style.display = 'none';
                return;
            }
            this.divider.style.display = '';
            const pct = this.pos * 100;
            this.afterWrap.style.clipPath = `inset(0 0 0 ${pct}%)`;
            this.divider.style.left = pct + '%';
        }

        _stageCenter() {
            const r = this.stage.getBoundingClientRect();
            return { x: r.width / 2, y: r.height / 2 };
        }

        /** 分割线在舞台坐标系里的 x（用于命中判定）。 */
        _dividerScreenX() {
            return this.tx + this.pos * this.canvas.offsetWidth * this.mag;
        }

        _syncHud() {
            if (!this.hud) return;
            const pct = this.natW ? Math.round(this.mag / this.oneToOneMag * 100) : 0;
            const dims = this.natW ? `${this.natW}×${this.natH}` : '';
            this.hud.textContent = [dims, pct + '%'].filter(Boolean).join(' · ');
            this.root.dispatchEvent(new CustomEvent('viewer:zoom', {
                detail: { percent: pct, mag: this.mag },
            }));
        }

        // ── 事件绑定 ──────────────────────────────────────────────────────

        _bind() {
            this._onWheel = e => {
                if (!this.natW) return;
                e.preventDefault();
                const r = this.stage.getBoundingClientRect();
                const factor = Math.exp(-e.deltaY * 0.0016);
                this._setMag(this.mag * factor, {
                    x: e.clientX - r.left, y: e.clientY - r.top,
                });
            };
            this.stage.addEventListener('wheel', this._onWheel, { passive: false });

            this.stage.addEventListener('pointerdown', e => this._onPointerDown(e));
            this.stage.addEventListener('pointermove', e => this._onPointerMove(e));
            this.stage.addEventListener('pointerup', e => this._onPointerUp(e));
            this.stage.addEventListener('pointercancel', e => this._onPointerUp(e));
            this.stage.addEventListener('pointerleave', () => {
                this._pointer.inside = false;
                this.loupe.hidden = true;
            });
            this.stage.addEventListener('dblclick', e => this._onDblClick(e));

            this._onKey = e => this._onKeyDown(e);
            this.root.addEventListener('keydown', this._onKey);

            this._ro = new ResizeObserver(() => {
                if (!this.natW) return;
                const ratio = this.mag / this.oneToOneMag;
                this._measure();
                this.mag = Math.max(this.fitMag || 1, this.oneToOneMag * ratio);
                this._clampPan();
                this._apply();
            });
            this._ro.observe(this.stage);

            this.root.querySelectorAll('[data-viewer-action]').forEach(btn => {
                btn.addEventListener('click', () => {
                    const act = btn.dataset.viewerAction;
                    if (act === 'fit') this.fit();
                    else if (act === 'one') this.oneToOne();
                    else if (act === 'in') this.zoomBy(1.25);
                    else if (act === 'out') this.zoomBy(1 / 1.25);
                    else if (act === 'compare') this.toggleCompare();
                    else if (act === 'loupe') this.toggleMagnifier();
                });
            });
        }

        _pointerPos(e) {
            const r = this.stage.getBoundingClientRect();
            return { x: e.clientX - r.left, y: e.clientY - r.top };
        }

        _onPointerDown(e) {
            if (!this.natW) return;
            const p = this._pointerPos(e);
            const nearDivider = this.compare &&
                Math.abs(p.x - this._dividerScreenX()) <= DIVIDER_HIT;
            this.drag = {
                mode: nearDivider ? 'divider' : 'pan',
                startX: e.clientX,
                startY: e.clientY,
                startTx: this.tx,
                startTy: this.ty,
                pointerId: e.pointerId,
            };
            // 合成事件（自动化测试）没有真实指针，捕获会抛 NotFoundError
            try { this.stage.setPointerCapture(e.pointerId); } catch (_) { /* noop */ }
            this.root.classList.add(nearDivider ? 'dragging-divider' : 'dragging-pan');
            if (nearDivider) e.preventDefault();
        }

        _onPointerMove(e) {
            const p = this._pointerPos(e);
            this._pointer = { ...p, inside: true };

            if (this.loupeOn) this._updateLoupe(p.x, p.y);

            if (!this.drag) {
                const near = this.compare &&
                    Math.abs(p.x - this._dividerScreenX()) <= DIVIDER_HIT;
                this.stage.style.cursor = near ? 'ew-resize'
                    : (this.mag > (this.fitMag || 1) ? 'grab' : 'default');
                return;
            }

            if (this.drag.mode === 'divider') {
                const span = this.canvas.offsetWidth * this.mag;
                if (span > 0) {
                    this.pos = Math.min(1, Math.max(0, (p.x - this.tx) / span));
                    this._applyDivider();
                }
            } else {
                this.tx = this.drag.startTx + (e.clientX - this.drag.startX);
                this.ty = this.drag.startTy + (e.clientY - this.drag.startY);
                this._clampPan();
                this._apply();
            }
        }

        _onPointerUp(e) {
            if (!this.drag) return;
            this.root.classList.remove('dragging-divider', 'dragging-pan');
            try { this.stage.releasePointerCapture(e.pointerId); } catch (_) { /* noop */ }
            this.drag = null;
        }

        _onDblClick(e) {
            if (!this.natW) return;
            e.preventDefault();
            const atNative = this.mag / this.oneToOneMag > 0.9;
            if (atNative) this.fit();
            else this._setMag(this.oneToOneMag, this._pointerPos(e));
        }

        _onKeyDown(e) {
            if (!this.natW) return;
            const step = e.shiftKey ? 0.1 : 0.02;
            switch (e.key) {
                case 'ArrowLeft':
                    if (!this.compare) return;
                    this.pos = Math.max(0, this.pos - step);
                    this._applyDivider(); e.preventDefault(); break;
                case 'ArrowRight':
                    if (!this.compare) return;
                    this.pos = Math.min(1, this.pos + step);
                    this._applyDivider(); e.preventDefault(); break;
                case '+': case '=':
                    this.zoomBy(1.25); e.preventDefault(); break;
                case '-': case '_':
                    this.zoomBy(1 / 1.25); e.preventDefault(); break;
                case '0':
                    this.fit(); e.preventDefault(); break;
                case '1':
                    this.oneToOne(); e.preventDefault(); break;
                case 'm': case 'M':
                    this.toggleMagnifier(); e.preventDefault(); break;
                case 'c': case 'C':
                    this.toggleCompare(); e.preventDefault(); break;
                default: break;
            }
        }

        // ── 放大镜 ────────────────────────────────────────────────────────

        _updateLoupe(px, py) {
            if (!this.loupeOn || !this.natW) return;
            const stageW = this.stage.clientWidth;
            const stageH = this.stage.clientHeight;
            const size = this.loupe.offsetWidth || 150;
            const k = this.mag * LOUPE_ZOOM;
            const cw = this.canvas.offsetWidth;
            const ch = this.canvas.offsetHeight;

            // 光标在 canvas 坐标系（未缩放）中的位置
            const cx = (px - this.tx) / this.mag;
            const cy = (py - this.ty) / this.mag;
            if (cx < 0 || cy < 0 || cx > cw || cy > ch) {
                this.loupe.hidden = true;
                return;
            }

            const w = cw * k;
            const h = ch * k;
            const left = size / 2 - cx * k;
            const top = size / 2 - cy * k;

            [this.loupeBefore, this.loupeAfter].forEach(img => {
                img.style.width = w + 'px';
                img.style.height = h + 'px';
                img.style.left = left + 'px';
                img.style.top = top + 'px';
            });

            // 放大镜内同样保留分割线：左侧原图、右侧修复后
            if (this.compare) {
                const loupeX = size / 2 + (this.pos * cw - cx) * k;
                const pct = Math.min(100, Math.max(0, loupeX / size * 100));
                this.loupeAfterWrap.style.clipPath = `inset(0 0 0 ${pct}%)`;
                this.loupeAfterWrap.style.display = '';
            } else {
                this.loupeAfterWrap.style.display = 'none';
            }

            // 靠边时把放大镜挪到光标另一侧，避免溢出舞台
            const margin = 16;
            let lx = px + 20;
            let ly = py + 20;
            if (lx + size + margin > stageW) lx = px - size - 20;
            if (ly + size + margin > stageH) ly = py - size - 20;
            this.loupe.style.left = Math.max(margin, lx) + 'px';
            this.loupe.style.top = Math.max(margin, ly) + 'px';
            this.loupe.hidden = false;
        }
    }

    window.VOSR2Viewer = {
        create(root, opts) {
            const v = new CompareViewer(root, opts || {});
            if (opts && (opts.before || opts.after)) v.setImages(opts);
            return v;
        },
    };
})();
