/* VOSR2-Lite 修复工作台控制器。
 *
 * 职责：文件选择（点击/拖拽/粘贴/多选）→ 参数与预设 → 提交任务（单张或批量队列）
 *       → SSE 进度 → 结果载入对比查看器 → 下载（可选格式）/ 再次修复 / 失败重试
 *       → 最近任务条。
 *
 * 依赖：window.VOSR2（api/toast/formatSize）、window.VOSR2Viewer。
 */
(function () {
    'use strict';

    const PRESET_KEY = 'vosr2-presets';
    const LAST_PARAMS_KEY = 'vosr2-last-params';
    const MAX_OUTPUT_SIDE = 6144;
    const PREVIEW_MAX = 1600;
    const ACCEPT_RE = /\.(png|jpe?g|webp|bmp|tiff?)$/i;
    const ACCEPT_MIME = ['image/png', 'image/jpeg', 'image/webp', 'image/bmp',
        'image/tiff'];

    const $ = id => document.getElementById(id);
    const I = () => window.__I18N__ || {};
    const t = (key, fallback) => I()[key] || fallback || key;

    document.addEventListener('DOMContentLoaded', () => {
        const el = {
            dropzone: $('dropzone'),
            fileInput: $('fileInput'),
            btnSelectFile: $('btnSelectFile'),
            btnChangeFile: $('btnChangeFile'),
            pickedBox: $('pickedBox'),
            pickedThumb: $('pickedThumb'),
            pickedName: $('pickedName'),
            pickedMeta: $('pickedMeta'),
            pickedEst: $('pickedEst'),
            queueBox: $('queueBox'),
            queueList: $('queueList'),
            queueSummary: $('queueSummary'),
            btnQueueClear: $('btnQueueClear'),
            btnStart: $('btnStart'),
            startHint: $('startHint'),
            progressCard: $('progressCard'),
            progressBar: $('progressBar'),
            progressPct: $('progressPct'),
            progressStage: $('progressStage'),
            progressText: $('progressText'),
            progressElapsed: $('progressElapsed'),
            progressEta: $('progressEta'),
            btnCancel: $('btnCancel'),
            errorCard: $('errorCard'),
            errorMsg: $('errorMsg'),
            btnRetry: $('btnRetry'),
            resultCard: $('resultCard'),
            resultBadge: $('resultBadge'),
            resultMeta: $('resultMeta'),
            btnDownload: $('btnDownload'),
            btnAgain: $('btnAgain'),
            downloadFormat: $('downloadFormat'),
            viewerEl: $('viewer'),
            presetSelect: $('presetSelect'),
            btnPresetSave: $('btnPresetSave'),
            btnPresetDelete: $('btnPresetDelete'),
            btnDice: $('btnDice'),
            recentSection: $('recentSection'),
            recentGrid: $('recentGrid'),
        };

        const state = {
            files: [],        // [{ file, url, natural, status, progress, taskId, size, error }]
            taskId: null,
            es: null,
            startedAt: 0,
            timer: null,
            batchIndex: 0,
            cancelRequested: false,
            fromHistory: false,
        };

        const viewer = window.VOSR2Viewer
            ? window.VOSR2Viewer.create(el.viewerEl, {})
            : null;

        // ── 参数读写 ──────────────────────────────────────────────────────

        const paramFields = {
            upscale: $('paramUpscale'),
            steps: $('paramSteps'),
            align: $('paramAlign'),
            seed: $('paramSeed'),
            tile: $('paramTile'),
            vaeTile: $('paramVaeTile'),
        };

        function readParams() {
            return {
                upscale: Number(paramFields.upscale.value),
                steps: Number(paramFields.steps.value),
                align: paramFields.align.value,
                seed: Number(paramFields.seed.value),
                tile: Number(paramFields.tile.value),
                vaeTile: Number(paramFields.vaeTile.value),
                format: el.downloadFormat ? el.downloadFormat.value : 'png',
            };
        }

        function writeParams(p) {
            if (!p) return;
            if (p.upscale != null) paramFields.upscale.value = String(p.upscale);
            if (p.steps != null) paramFields.steps.value = String(p.steps);
            if (p.align) paramFields.align.value = p.align;
            if (p.seed != null) paramFields.seed.value = String(p.seed);
            if (p.tile != null) paramFields.tile.value = String(p.tile);
            if (p.vaeTile != null) paramFields.vaeTile.value = String(p.vaeTile);
            if (p.format && el.downloadFormat) el.downloadFormat.value = p.format;
        }

        try {
            writeParams(JSON.parse(localStorage.getItem(LAST_PARAMS_KEY) || 'null'));
        } catch (_) { /* 忽略损坏的本地数据 */ }
        Object.values(paramFields).forEach(f => f.addEventListener('change', persistParams));
        function persistParams() {
            try {
                localStorage.setItem(LAST_PARAMS_KEY, JSON.stringify(readParams()));
            } catch (_) { /* 隐私模式可能禁用 localStorage */ }
        }

        // ── 预设 ──────────────────────────────────────────────────────────

        function loadPresets() {
            try {
                return JSON.parse(localStorage.getItem(PRESET_KEY) || '{}');
            } catch (_) { return {}; }
        }

        function savePresets(obj) {
            try { localStorage.setItem(PRESET_KEY, JSON.stringify(obj)); }
            catch (_) { /* noop */ }
        }

        function renderPresets(selected) {
            const presets = loadPresets();
            el.presetSelect.innerHTML = '';
            const opt0 = document.createElement('option');
            opt0.value = '';
            opt0.textContent = t('restore.preset_placeholder', '选择预设…');
            el.presetSelect.appendChild(opt0);
            Object.keys(presets).sort().forEach(name => {
                const opt = document.createElement('option');
                opt.value = name;
                opt.textContent = name;
                el.presetSelect.appendChild(opt);
            });
            if (selected && presets[selected]) el.presetSelect.value = selected;
            el.btnPresetDelete.disabled = !el.presetSelect.value;
        }

        el.presetSelect.addEventListener('change', () => {
            const name = el.presetSelect.value;
            el.btnPresetDelete.disabled = !name;
            const presets = loadPresets();
            if (name && presets[name]) {
                writeParams(presets[name]);
                persistParams();
                updateEstimate();
                VOSR2.toast(t('restore.preset_applied', '已应用预设') + ': ' + name, 'info');
            }
        });

        el.btnPresetSave.addEventListener('click', () => {
            const name = (window.prompt(t('restore.preset_name_prompt', '预设名称'),
                t('restore.preset_default_name', '我的参数')) || '').trim();
            if (!name) return;
            const presets = loadPresets();
            presets[name] = readParams();
            savePresets(presets);
            renderPresets(name);
            VOSR2.toast(t('restore.preset_saved', '预设已保存'), 'success');
        });

        el.btnPresetDelete.addEventListener('click', () => {
            const name = el.presetSelect.value;
            if (!name) return;
            if (!window.confirm(t('restore.preset_confirm_delete', '删除该预设？') + '\n' + name)) return;
            const presets = loadPresets();
            delete presets[name];
            savePresets(presets);
            renderPresets();
            VOSR2.toast(t('restore.preset_deleted', '预设已删除'), 'info');
        });

        el.btnDice.addEventListener('click', () => {
            paramFields.seed.value = String(Math.floor(Math.random() * 1000000));
            persistParams();
        });

        // ── 文件选择 ──────────────────────────────────────────────────────

        function isImageFile(file) {
            if (!file) return false;
            return ACCEPT_MIME.includes(file.type) || ACCEPT_RE.test(file.name || '');
        }

        function resetItems() {
            state.files.forEach(it => { if (it.url) URL.revokeObjectURL(it.url); });
            state.files = [];
        }

        function setFiles(fileList) {
            const incoming = Array.from(fileList || []);
            const valid = incoming.filter(isImageFile);
            if (!valid.length) {
                VOSR2.toast(t('restore.err_format', '不支持的图片格式'), 'error');
                return;
            }
            if (valid.length < incoming.length) {
                VOSR2.toast(t('restore.some_skipped', '已跳过非图片文件'), 'info');
            }
            resetItems();
            state.files = valid.map(file => ({
                file,
                url: URL.createObjectURL(file),
                natural: null,
                status: 'idle',
                progress: 0,
                taskId: '',
                size: 0,
                error: '',
            }));
            state.fromHistory = false;
            state.cancelRequested = false;

            el.dropzone.hidden = true;
            el.btnChangeFile.hidden = false;
            el.btnStart.disabled = false;
            el.startHint.textContent = '';

            if (state.files.length === 1) {
                renderSingle();
            } else {
                renderQueue();
            }
            updateEstimate();
        }

        function renderSingle() {
            const item = state.files[0];
            el.pickedBox.hidden = false;
            el.queueBox.hidden = true;
            el.pickedThumb.src = item.url;
            el.pickedName.textContent = item.file.name || 'clipboard.png';
            el.pickedMeta.textContent = VOSR2.formatSize(item.file.size);
            el.btnStart.innerHTML = '<i class="bi bi-lightning-charge"></i> ' + t('restore.start', '开始修复');
            probeSize(item);
        }

        function renderQueue() {
            el.pickedBox.hidden = true;
            el.queueBox.hidden = false;
            el.btnStart.innerHTML = '<i class="bi bi-lightning-charge"></i> '
                + t('restore.start', '开始修复') + ' (' + state.files.length + ')';
            el.queueList.innerHTML = '';
            state.files.forEach((item, idx) => {
                el.queueList.appendChild(queueRow(item, idx));
                probeSize(item);
            });
            updateQueueSummary();
        }

        function queueRow(item, idx) {
            const li = document.createElement('li');
            li.className = 'sv-queue-item';
            li.dataset.idx = String(idx);

            const thumb = document.createElement('img');
            thumb.className = 'sv-queue-thumb';
            thumb.src = item.url;
            thumb.alt = '';

            const body = document.createElement('div');
            body.className = 'sv-queue-body';

            const line = document.createElement('div');
            line.className = 'sv-queue-line';
            const name = document.createElement('span');
            name.className = 'sv-queue-name';
            name.textContent = item.file.name || 'clipboard.png';
            const badge = document.createElement('span');
            badge.className = 'sv-queue-badge';
            line.appendChild(name);
            line.appendChild(badge);

            const bar = document.createElement('div');
            bar.className = 'sv-queue-bar';
            const fill = document.createElement('div');
            fill.className = 'sv-queue-fill';
            bar.appendChild(fill);

            const meta = document.createElement('div');
            meta.className = 'sv-queue-meta';

            body.appendChild(line);
            body.appendChild(bar);
            body.appendChild(meta);

            const act = document.createElement('button');
            act.type = 'button';
            act.className = 'sv-tool-btn sv-queue-open';
            act.innerHTML = '<i class="bi bi-layout-split"></i>';
            act.title = t('history.open', '对比');
            act.addEventListener('click', () => {
                if (item.taskId && item.status === 'done') {
                    state.startedAt = 0;
                    showResult(item.taskId, { size: item.size });
                }
            });

            li.appendChild(thumb);
            li.appendChild(body);
            li.appendChild(act);
            paintQueueRow(li, item);
            return li;
        }

        function paintQueueRow(li, item) {
            const badge = li.querySelector('.sv-queue-badge');
            const fill = li.querySelector('.sv-queue-fill');
            const meta = li.querySelector('.sv-queue-meta');
            const open = li.querySelector('.sv-queue-open');
            const pct = Math.round((item.progress || 0) * 100);
            li.dataset.status = item.status;
            const labels = {
                idle: t('status.pending', '排队中'),
                running: pct + '%',
                done: t('status.completed', '已完成'),
                failed: t('status.failed', '失败'),
                cancelled: t('status.cancelled', '已取消'),
            };
            badge.textContent = labels[item.status] || item.status;
            fill.style.width = (item.status === 'done' ? 100 : pct) + '%';
            const dims = item.natural ? `${item.natural.w}×${item.natural.h}` : '';
            meta.textContent = [
                dims,
                VOSR2.formatSize(item.file.size),
                item.status === 'done' && item.size ? '→ ' + VOSR2.formatSize(item.size) : '',
                item.error || '',
            ].filter(Boolean).join(' · ');
            open.hidden = item.status !== 'done';
        }

        function updateQueueSummary() {
            const done = state.files.filter(i => i.status === 'done').length;
            const failed = state.files.filter(i => i.status === 'failed').length;
            el.queueSummary.textContent = [
                `${done}/${state.files.length}`,
                failed ? `· ${t('status.failed', '失败')} ${failed}` : '',
            ].filter(Boolean).join(' ');
        }

        function probeSize(item) {
            if (item.natural) return;
            const img = new Image();
            img.onload = () => {
                item.natural = { w: img.naturalWidth, h: img.naturalHeight };
                if (state.files.length === 1) updateEstimate();
                else {
                    const li = el.queueList.querySelector(`[data-idx="${state.files.indexOf(item)}"]`);
                    if (li) paintQueueRow(li, item);
                }
            };
            img.src = item.url;
        }

        function updateEstimate() {
            const first = state.files[0];
            if (!first || !first.natural) return;
            const p = readParams();
            const ow = first.natural.w * p.upscale;
            const oh = first.natural.h * p.upscale;
            const overLimit = Math.max(ow, oh) > MAX_OUTPUT_SIDE;
            const mp = (ow * oh) / 1e6;
            // 实测拟合（RTX 5070 Ti）：耗时 ≈ 1.5s + 0.17·MP²（注意力随 token 数平方增长）
            //   11.1 MP（3096²）≈ 22s，37.7 MP（6144²）≈ 243s
            const secs = Math.round(1.5 + 0.17 * mp * mp);
            const total = state.files.length;

            if (total === 1) {
                el.pickedMeta.textContent = [
                    `${first.natural.w}×${first.natural.h}`,
                    VOSR2.formatSize(first.file.size),
                ].join(' · ');
                el.pickedEst.textContent = [
                    `${t('restore.est_output', '预计输出')} ${ow}×${oh}`,
                    `${t('restore.est_time', '预计耗时')} ~${formatDuration(secs)}`,
                ].join(' · ') + (overLimit
                    ? ' · ' + t('restore.over_limit_hint', '超出上限，请降低倍率') : '');
                el.pickedEst.classList.toggle('is-warn', overLimit);
            } else {
                el.queueSummary.textContent = [
                    `${total} ${t('restore.queue_count', '张')}`,
                    `${t('restore.est_output', '预计输出')} ${ow}×${oh}`,
                    `~${formatDuration(secs * total)}`,
                ].join(' · ');
            }
        }

        el.dropzone.addEventListener('click', e => {
            if (e.target === el.btnSelectFile || e.target === el.dropzone) {
                el.fileInput.click();
            }
        });
        el.dropzone.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); el.fileInput.click(); }
        });
        el.btnSelectFile.addEventListener('click', e => {
            e.stopPropagation();
            el.fileInput.click();
        });
        el.btnChangeFile.addEventListener('click', () => el.fileInput.click());
        el.btnQueueClear.addEventListener('click', () => {
            resetItems();
            el.pickedBox.hidden = true;
            el.queueBox.hidden = true;
            el.dropzone.hidden = false;
            el.btnChangeFile.hidden = true;
            el.btnStart.disabled = true;
            el.startHint.textContent = t('restore.need_file', '请先选择图片');
        });

        ['dragenter', 'dragover'].forEach(ev => document.addEventListener(ev, e => {
            if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes('Files')) return;
            e.preventDefault();
            el.dropzone.classList.add('dragover');
        }));
        ['dragleave', 'drop'].forEach(ev => document.addEventListener(ev, e => {
            if (ev === 'dragleave' && e.relatedTarget) return;
            e.preventDefault();
            el.dropzone.classList.remove('dragover');
        }));
        document.addEventListener('drop', e => {
            const files = e.dataTransfer && e.dataTransfer.files;
            if (files && files.length) setFiles(files);
        });

        document.addEventListener('paste', e => {
            const items = (e.clipboardData && e.clipboardData.items) || [];
            for (const item of items) {
                if (item.type && item.type.startsWith('image/')) {
                    const f = item.getAsFile();
                    if (f) {
                        setFiles([f]);
                        e.preventDefault();
                        VOSR2.toast(t('restore.pasted', '已从剪贴板载入图片'), 'info');
                        return;
                    }
                }
            }
        });

        el.fileInput.addEventListener('change', () => {
            if (el.fileInput.files.length) setFiles(el.fileInput.files);
            el.fileInput.value = '';
        });

        // ── 提交 ──────────────────────────────────────────────────────────

        el.btnStart.addEventListener('click', () => {
            if (!state.files.length) {
                VOSR2.toast(t('restore.need_file', '请先选择图片'), 'error');
                return;
            }
            startRun();
        });

        async function startRun() {
            const p = readParams();
            persistParams();
            state.cancelRequested = false;
            el.btnStart.disabled = true;
            el.errorCard.hidden = true;
            el.resultCard.hidden = true;
            el.progressCard.hidden = false;
            state.startedAt = Date.now();
            startTimer();
            setProgress(0, t('status.pending', '排队中'), '');

            const single = state.files.length === 1;
            let lastOk = null;
            let failed = 0;

            for (let i = 0; i < state.files.length; i++) {
                if (state.cancelRequested) break;
                const item = state.files[i];
                state.batchIndex = i;
                if (!single) {
                    item.status = 'running';
                    refreshRow(item);
                    el.progressText.textContent =
                        `${t('restore.queue_progress', '第')} ${i + 1}/${state.files.length}`
                        + ` · ${item.file.name}`;
                }
                try {
                    const res = await submitAndWait(item, d => {
                        item.progress = d.progress || 0;
                        if (single) {
                            setProgress(d.progress || 0,
                                stageLabel(d.current_stage, d.status), d.message);
                        } else {
                            refreshRow(item);
                            const overall = (i + (d.progress || 0)) / state.files.length;
                            setProgress(overall,
                                `${t('restore.queue_progress', '第')} ${i + 1}/${state.files.length}`,
                                item.file.name);
                        }
                    });
                    item.status = 'done';
                    item.taskId = res.taskId;
                    item.size = res.size;
                    lastOk = item;
                } catch (err) {
                    if (err && err.cancelled) {
                        item.status = 'cancelled';
                    } else {
                        item.status = 'failed';
                        item.error = err.message || String(err);
                        failed += 1;
                    }
                }
                item.progress = item.status === 'done' ? 1 : item.progress;
                refreshRow(item);
            }

            stopTimer();
            el.progressCard.hidden = true;
            el.btnStart.disabled = false;

            if (lastOk) {
                showResult(lastOk.taskId, { size: lastOk.size });
            }
            if (failed) {
                finishWithError(t('restore.batch_failed', '有任务失败') + `: ${failed}`);
            } else if (state.cancelRequested && !lastOk) {
                VOSR2.toast(t('restore.cancelled', '已取消'), 'info');
            }
        }

        function refreshRow(item) {
            if (state.files.length === 1) return;
            const idx = state.files.indexOf(item);
            const li = el.queueList.querySelector(`[data-idx="${idx}"]`);
            if (li) paintQueueRow(li, item);
            updateQueueSummary();
        }

        /** 提交单张并等待终态；progress 回调收到 SSE 数据。 */
        async function submitAndWait(item, onProgress) {
            const p = readParams();
            const form = new FormData();
            form.append('file', item.file, item.file.name || 'paste.png');
            form.append('upscale', p.upscale);
            form.append('steps', p.steps);
            form.append('align_method', p.align);
            form.append('seed', p.seed);
            form.append('tile_size', p.tile);
            form.append('vae_tile_size', p.vaeTile);

            const data = await VOSR2.api.postForm('/api/restore/', form);
            const taskId = data.task_id;
            state.taskId = taskId;

            return new Promise((resolve, reject) => {
                const es = new EventSource(`/api/restore/${taskId}/progress`);
                state.es = es;
                es.onmessage = ev => {
                    let d;
                    try { d = JSON.parse(ev.data); } catch (_) { return; }
                    if (onProgress) onProgress(d);
                    if (d.status === 'completed') {
                        es.close();
                        resolve({ taskId, size: d.output_size });
                    } else if (d.status === 'failed' || d.status === 'cancelled') {
                        es.close();
                        const err = new Error(d.error || d.status);
                        err.cancelled = d.status === 'cancelled';
                        reject(err);
                    }
                };
                es.onerror = () => { /* EventSource 自动重连 */ };
            });
        }

        function startTimer() {
            stopTimer();
            state.timer = setInterval(() => {
                if (!state.startedAt) return;
                el.progressElapsed.textContent =
                    `${t('restore.elapsed', '已用时')} ${formatDuration((Date.now() - state.startedAt) / 1000)}`;
            }, 1000);
        }

        function stopTimer() {
            if (state.timer) { clearInterval(state.timer); state.timer = null; }
        }

        function setProgress(ratio, stageText, message) {
            const pct = Math.max(0, Math.min(100, Math.round((ratio || 0) * 100)));
            el.progressBar.style.width = pct + '%';
            el.progressPct.textContent = pct + '%';
            if (stageText) el.progressStage.textContent = stageText;
            el.progressText.textContent = message || '';
            if (pct > 3 && state.startedAt) {
                const elapsed = (Date.now() - state.startedAt) / 1000;
                const remain = Math.max(0, elapsed / (pct / 100) - elapsed);
                el.progressEta.textContent = remain > 1
                    ? `· ${t('restore.eta', '预计剩余')} ${formatDuration(remain)}` : '';
            }
        }

        function stageLabel(stage, status) {
            if (status === 'pending') return t('status.pending', '排队中');
            if (status === 'completed') return t('status.completed', '已完成');
            const map = {
                encode: 'restore.stage_encode',
                venc: 'restore.stage_venc',
                sample: 'restore.stage_sample',
                decode: 'restore.stage_decode',
                align: 'restore.stage_align',
                done: 'restore.stage_done',
            };
            const key = map[stage];
            return key ? t(key, stage) : t('status.processing', '处理中');
        }

        el.btnCancel.addEventListener('click', async () => {
            state.cancelRequested = true;
            if (!state.taskId) return;
            try {
                await VOSR2.api.post(`/api/restore/${state.taskId}/cancel`, {});
                VOSR2.toast(t('restore.cancelled', '已取消'), 'info');
            } catch (err) {
                VOSR2.toast(err.message, 'error');
            }
        });

        function finishWithError(msg) {
            stopTimer();
            el.errorMsg.textContent = msg;
            el.errorCard.hidden = false;
            el.btnStart.disabled = !state.files.length;
        }

        el.btnRetry.addEventListener('click', () => {
            if (!state.files.length) {
                VOSR2.toast(t('restore.need_file', '请先选择图片'), 'error');
                return;
            }
            // 重试失败项（全部失败则重跑全部）
            state.files.forEach(it => {
                if (it.status === 'failed' || it.status === 'cancelled') {
                    it.status = 'idle'; it.progress = 0; it.error = '';
                }
            });
            startRun();
        });

        // ── 结果展示 ──────────────────────────────────────────────────────

        async function showResult(taskId, info) {
            state.taskId = taskId;
            el.resultCard.hidden = false;
            el.errorCard.hidden = true;

            if (viewer) {
                viewer.setImages({
                    before: `/api/restore/${taskId}/preview?kind=input&max=${PREVIEW_MAX}`,
                    after: `/api/restore/${taskId}/preview?kind=output&max=${PREVIEW_MAX}`,
                    beforeFull: `/api/restore/${taskId}/input`,
                    afterFull: `/api/restore/${taskId}/download`,
                });
                viewer.setCompareEnabled(true);
            }

            updateDownloadHref();

            const meta = [];
            try {
                const res = await VOSR2.api.get(`/api/restore/${taskId}/result`);
                if (res.output_size) meta.push(VOSR2.formatSize(res.output_size));
            } catch (_) { /* 拿不到就只显示已知信息 */ }
            if (state.startedAt) {
                meta.push(`${t('restore.took', '耗时')} `
                    + formatDuration((Date.now() - state.startedAt) / 1000));
            }
            if (info && info.size && !meta.length) meta.push(VOSR2.formatSize(info.size));
            el.resultMeta.textContent = meta.join(' · ');
            el.resultBadge.textContent = t('status.completed', '已完成');

            el.resultCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            VOSR2.toast(t('restore.done', '修复完成'), 'success');
            loadRecent();
        }

        function updateDownloadHref() {
            if (!state.taskId) return;
            const fmt = el.downloadFormat.value;
            el.btnDownload.href =
                `/api/restore/${state.taskId}/download?format=${encodeURIComponent(fmt)}`;
        }

        el.downloadFormat.addEventListener('change', () => {
            updateDownloadHref();
            persistParams();
        });

        el.btnAgain.addEventListener('click', () => {
            if (!state.files.length) {
                VOSR2.toast(t('restore.again_need_file', '请重新选择图片后再修复'), 'info');
                return;
            }
            startRun();
        });

        // ── 最近任务 ──────────────────────────────────────────────────────

        async function loadRecent() {
            try {
                const data = await VOSR2.api.get('/api/system/history?page=1&page_size=6');
                const records = (data.records || []).filter(r => r.status === 'completed');
                if (!records.length) { el.recentSection.hidden = true; return; }
                el.recentGrid.innerHTML = '';
                records.forEach(r => el.recentGrid.appendChild(recentCard(r)));
                el.recentSection.hidden = false;
            } catch (_) {
                el.recentSection.hidden = true;
            }
        }

        function recentCard(r) {
            const a = document.createElement('button');
            a.type = 'button';
            a.className = 'sv-recent-card';
            const img = document.createElement('img');
            img.loading = 'lazy';
            img.alt = '';
            img.src = `/api/system/history/${r.id}/thumb?max=320`;
            const cap = document.createElement('div');
            cap.className = 'sv-recent-cap';
            const name = document.createElement('span');
            name.className = 'sv-recent-name';
            name.textContent = VOSR2.displayFileName(r.input_file);
            const meta = document.createElement('span');
            meta.className = 'sv-recent-meta';
            meta.textContent = [
                r.processing_time ? formatDuration(r.processing_time) : '',
                r.output_size ? VOSR2.formatSize(r.output_size) : '',
            ].filter(Boolean).join(' · ');
            cap.appendChild(name);
            cap.appendChild(meta);
            a.appendChild(img);
            a.appendChild(cap);
            a.addEventListener('click', () => {
                if (!r.task_id) {
                    VOSR2.toast(t('restore.recent_no_task', '该记录没有可对比的任务'), 'info');
                    return;
                }
                state.fromHistory = true;
                state.startedAt = 0;
                el.progressCard.hidden = true;
                showResult(r.task_id, { size: r.output_size });
                window.scrollTo({ top: 0, behavior: 'smooth' });
            });
            return a;
        }

        // ── 工具 ──────────────────────────────────────────────────────────

        function formatDuration(sec) {
            const s = Math.max(0, Math.round(sec));
            if (s < 60) return s + 's';
            const m = Math.floor(s / 60);
            const r = s % 60;
            return r ? `${m}m${r}s` : `${m}m`;
        }

        // ── 初始化 ────────────────────────────────────────────────────────

        renderPresets();
        loadRecent();

        const params = new URLSearchParams(window.location.search);
        const taskParam = params.get('task');
        if (taskParam && /^[0-9a-f]{16}$/.test(taskParam)) {
            state.taskId = taskParam;
            showResult(taskParam, null);
        }

        document.addEventListener('keydown', e => {
            const tag = (e.target.tagName || '').toLowerCase();
            if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
            if (!el.resultCard.hidden && ['0', '1', '+', '=', '-', 'm', 'M', 'c', 'C']
                .includes(e.key)) {
                el.viewerEl.focus();
            }
        });
    });
})();
