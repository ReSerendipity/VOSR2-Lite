/* VOSR2-Lite 前端全局助手（window.VOSR2）。
 * api.get/post/postForm/del —— 统一信封解析，错误提取 error.message；
 * toast / theme / locale / escapeHtml / formatSize / formatTimestamp / displayFileName。
 */
(function () {
    'use strict';

    async function parseResponse(resp) {
        let body = null;
        try { body = await resp.json(); } catch (e) { /* 非 JSON */ }
        if (!resp.ok) {
            const msg = (body && body.error && body.error.message) || ('HTTP ' + resp.status);
            const err = new Error(msg);
            err.status = resp.status;
            err.body = body;
            throw err;
        }
        return body || {};
    }

    /* CSRF：服务端启用 CSRF 中间件（runtime.security.csrf.enable）时，非安全方法
     * 需回传 cookie 里的 token（Double Submit Cookie）。未启用时没有该 cookie，
     * 返回空对象，对现有请求零影响。 */
    function csrfHeaders() {
        const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]*)/);
        return m ? { 'X-CSRF-Token': decodeURIComponent(m[1]) } : {};
    }

    const api = {
        async get(url) {
            const resp = await fetch(url, { headers: { 'Accept': 'application/json' } });
            const body = await parseResponse(resp);
            return body.data !== undefined ? body.data : body;
        },
        async post(url, data) {
            const resp = await fetch(url, {
                method: 'POST',
                headers: Object.assign({ 'Content-Type': 'application/json' }, csrfHeaders()),
                body: data === undefined ? undefined : JSON.stringify(data),
            });
            const body = await parseResponse(resp);
            return body.data !== undefined ? body.data : body;
        },
        async postForm(url, form) {
            const resp = await fetch(url, { method: 'POST', headers: csrfHeaders(), body: form });
            const body = await parseResponse(resp);
            return body.data !== undefined ? body.data : body;
        },
        async del(url) {
            const resp = await fetch(url, { method: 'DELETE', headers: csrfHeaders() });
            const body = await parseResponse(resp);
            return body.data !== undefined ? body.data : body;
        },
    };

    function toast(message, type) {
        const container = document.getElementById('toastContainer');
        if (!container) { alert(message); return; }
        const el = document.createElement('div');
        el.className = 'sv-toast ' + (type || 'info');
        el.textContent = message;
        container.appendChild(el);
        setTimeout(() => {
            el.style.opacity = '0';
            el.style.transition = 'opacity .3s';
            setTimeout(() => el.remove(), 300);
        }, 3600);
    }

    function escapeHtml(str) {
        return String(str == null ? '' : str)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function formatSize(bytes) {
        if (!bytes && bytes !== 0) return '--';
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / 1024 / 1024).toFixed(1) + ' MB';
    }

    function formatTimestamp(ts) {
        // 后端格式 "YYYY-MM-DD HH:MM:SS"，直接展示
        return String(ts || '').replace('T', ' ').slice(0, 19);
    }

    function displayFileName(path) {
        if (!path) return '';
        const parts = String(path).split(/[\\/]/);
        let name = parts[parts.length - 1];
        // 剥掉 task_id 前缀（data/uploads/<task_id>_<name>）
        name = name.replace(/^[0-9a-f]{16}_/, '');
        return name;
    }

    function initThemeToggle() {
        const btn = document.getElementById('btnThemeToggle');
        const icon = document.getElementById('themeIcon');
        if (!btn) return;
        const sync = () => {
            const dark = document.documentElement.getAttribute('data-theme') === 'dark';
            if (icon) icon.className = dark ? 'bi bi-moon-stars-fill' : 'bi bi-sun-fill';
        };
        sync();
        btn.addEventListener('click', () => {
            const dark = document.documentElement.getAttribute('data-theme') === 'dark';
            const next = dark ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', next);
            localStorage.setItem('vosr2-theme', next);
            sync();
        });
    }

    function initLocaleDropdown() {
        const dd = document.getElementById('localeDropdown');
        const btn = document.getElementById('btnLocaleSwitch');
        const menu = document.getElementById('localeMenu');
        if (!dd || !btn || !menu) return;
        btn.addEventListener('click', e => {
            e.stopPropagation();
            dd.classList.toggle('open');
            btn.setAttribute('aria-expanded', dd.classList.contains('open'));
        });
        document.addEventListener('click', () => {
            dd.classList.remove('open');
            btn.setAttribute('aria-expanded', 'false');
        });
        menu.addEventListener('click', async e => {
            const item = e.target.closest('.sv-locale-item');
            if (!item) return;
            const locale = item.getAttribute('data-locale');
            try {
                await api.post('/api/system/locale', { locale: locale });
                window.location.reload();
            } catch (err) {
                toast(err.message, 'error');
            }
        });
    }

    function initSysWidget() {
        const widget = document.querySelector('.sv-sys-widget');
        const toggle = document.getElementById('sysWidgetToggle');
        if (!widget || !toggle) return;
        toggle.addEventListener('click', () => {
            widget.classList.toggle('collapsed');
            toggle.setAttribute('aria-expanded', !widget.classList.contains('collapsed'));
        });
        refreshSysWidget();
        setInterval(refreshSysWidget, 10000);
    }

    async function refreshSysWidget() {
        const dot = document.getElementById('statusDot');
        const model = document.getElementById('statusModel');
        const gpu = document.getElementById('statusGpu');
        try {
            const health = await api.get('/api/system/health');
            const g = health.gpu || {};
            if (dot) dot.className = 'sv-status-dot ' + (g.is_gpu_available ? 'online' : 'offline');
            if (gpu) gpu.textContent = 'GPU: ' + (g.is_gpu_available ? g.device_name : '--');
            const m = health.model || {};
            if (model) model.textContent = m.model_loaded
                ? (m.current_checkpoint || '') : (window.__I18N__ || {})['system.not_loaded'] || '未加载';
        } catch (e) {
            if (dot) dot.className = 'sv-status-dot offline';
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        initThemeToggle();
        initLocaleDropdown();
        initSysWidget();
    });

    window.VOSR2 = {
        api, toast, escapeHtml, formatSize, formatTimestamp, displayFileName,
        refreshSysWidget,
    };
})();
