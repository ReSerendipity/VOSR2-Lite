/**
 * VOSR2 Lite 桌面壳前端桥接（desktop-bridge.js）
 *
 * 注入方式：window.rs 在 WebView 初始化脚本里读取本文件并 eval
 * （见 src-tauri/src/window.rs），因此**不依赖任何 CDN、也不依赖
 * withGlobalTauri 暴露的 window.__TAURI__**：直接走内核注入的
 * `window.__TAURI_INTERNALS__` IPC 通道。
 *
 * 设计约束（与家族仓同构）：
 * 1. 浏览器直接访问 http://127.0.0.1:7871 时 `window.__TAURI_INTERNALS__` 不存在，
 *    本脚本整体 no-op，绝不报错——同一份前端代码既跑浏览器也跑桌面端。
 * 2. 主应用运行在后端源 http://127.0.0.1:PORT，内核只给「tauri 源页面」注入
 *    data-tauri-drag-region 脚本，因此标题栏拖拽/窗口按钮必须由本脚本自行注入，
 *    走 window_start_dragging / window_minimize 等显式命令。
 * 3. 本文件不假设后端 DOM 结构：任务态联动只在能检测到 #btnStart / #btnCancel 时生效。
 */
(function () {
  "use strict";

  var I = window.__TAURI_INTERNALS__;
  if (!I || typeof I.invoke !== "function") return; // 浏览器模式：整体 no-op
  if (window.__VOSR2_BRIDGE__) return; // 幂等：避免 init 脚本 + 内联双份执行
  window.__VOSR2_BRIDGE__ = true;

  var BRAND = "VOSR2 LITE";

  function invoke(cmd, args) {
    return I.invoke(cmd, args || {});
  }

  function listen(event, handler) {
    return invoke("plugin:event|listen", {
      event: event,
      target: { kind: "Any" },
      handler: I.transformCallback
        ? I.transformCallback(function (e) { handler(e.payload); })
        : function (e) { handler(e.payload); },
    }).then(function (id) {
      return function () {
        return invoke("plugin:event|unlisten", { event: event, eventId: id }).catch(function () {});
      };
    });
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function fmtSize(b) {
    if (!b || typeof b !== "number") return "-";
    var u = ["B", "KB", "MB", "GB"];
    var i = 0;
    var n = b;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return n.toFixed(n >= 10 || i === 0 ? 0 : 1) + " " + u[i];
  }

  // ---------- 通知 / 任务态 ----------
  function notifyShell(title, body) {
    invoke("show_notification", { title: title, body: body || "" }).catch(function () {});
  }

  function setBusy(busy) {
    invoke("set_window_busy", { busy: !!busy }).catch(function () {});
  }

  /** 供页面脚本（可选）调用：任务开始/结束时同步窗口态与托盘提示 */
  function onTaskStart() { setBusy(true); }
  function onTaskEnd(status) {
    setBusy(false);
    var ok = !status || status === "success" || status === "done";
    if (ok) notifyShell("VOSR2 Lite 处理完成", "任务已完成，点击查看详情");
    else notifyShell("VOSR2 Lite 处理结束", "任务" + (status || "结束") + "，请回到窗口查看");
  }

  /**
   * 任务态联动（可缺省）：只在页面存在 #btnStart 时挂载。
   * 不做 SSE 深度解析——VOSR2 的 /api/restore/{id}/progress 是 EventSource 自动重连，
   * 用「按钮可用性 + 取消按钮可见性」做保守推断，避免误判导致窗口长期显示"处理中"。
   */
  function watchTaskLifecycle() {
    var startBtn = document.getElementById("btnStart");
    if (!startBtn) return;
    startBtn.addEventListener("click", function () { setBusy(true); });
    var cancelBtn = document.getElementById("btnCancel");
    if (cancelBtn) cancelBtn.addEventListener("click", function () { setBusy(false); });
    // 兜底：页面隐藏（最小化到托盘）时不改变状态，仅在回到前台 30s 后自检一次
    setInterval(function () {
      var b = document.getElementById("btnStart");
      if (b && !b.disabled && document.visibilityState === "visible") setBusy(false);
    }, 30000);
  }

  // ---------- 更新 UI（自包含，不依赖后端页面元素）----------
  var dlgEl = null;

  function ensureDialog() {
    if (dlgEl || !document.body) return dlgEl;
    dlgEl = document.createElement("div");
    dlgEl.id = "vosr2-desktop-update-dialog";
    dlgEl.style.cssText = "position:fixed;inset:0;z-index:99998;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.45);font-family:'Segoe UI','Microsoft YaHei',system-ui,sans-serif";
    dlgEl.innerHTML =
      '<div style="width:440px;max-width:92vw;background:#fff;color:#222;border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,.35);overflow:hidden">' +
      '  <div style="padding:18px 22px;font-weight:600;font-size:17px;background:linear-gradient(135deg,#4f8cff,#26c6da);color:#fff">VOSR2 Lite 更新</div>' +
      '  <div id="vdu-body" style="padding:20px 22px;font-size:14px;line-height:1.7"></div>' +
      '  <div style="padding:0 22px 20px;display:flex;gap:10px;justify-content:flex-end" id="vdu-actions"></div>' +
      '</div>';
    document.body.appendChild(dlgEl);
    return dlgEl;
  }

  function closeDialog() { if (dlgEl) dlgEl.style.display = "none"; }

  function showUpdateDialog(info) {
    ensureDialog();
    var body = document.getElementById("vdu-body");
    var actions = document.getElementById("vdu-actions");
    if (!dlgEl || !body || !actions) return;
    if (!info) {
      body.innerHTML = "<div>当前已是最新版本。</div>";
      actions.innerHTML = '<button data-a="close" style="padding:8px 20px;border:none;border-radius:6px;background:#eee;cursor:pointer">关闭</button>';
    } else {
      body.innerHTML =
        '<div style="display:flex;justify-content:space-between;margin-bottom:8px"><span>当前版本</span><strong>' + escapeHtml(info.current || "-") + "</strong></div>" +
        '<div style="display:flex;justify-content:space-between;margin-bottom:12px"><span>最新版本</span><strong style="color:#4f8cff">' + escapeHtml(info.version || "-") + "</strong></div>" +
        '<div style="margin-bottom:6px"><span>更新包大小</span>：<strong>' + fmtSize(info.size) + "</strong></div>" +
        '<div style="margin:10px 0 4px;color:#666">变更日志</div>' +
        '<div style="max-height:160px;overflow:auto;background:#f7f7f9;border-radius:8px;padding:10px 12px;font-size:13px;white-space:pre-wrap">' + escapeHtml(info.changelog || "（无变更日志）") + "</div>";
      actions.innerHTML =
        '<button data-a="later" style="padding:8px 18px;border:1px solid #ddd;border-radius:6px;background:#fff;cursor:pointer">稍后提醒</button>' +
        '<button data-a="now" style="padding:8px 22px;border:none;border-radius:6px;background:#4f8cff;color:#fff;cursor:pointer;font-weight:600">立即更新</button>';
    }
    dlgEl.style.display = "flex";
    actions.onclick = function (e) {
      var a = e.target.getAttribute("data-a");
      if (a === "close") closeDialog();
      else if (a === "later") { invoke("dismiss_update").catch(function () {}); closeDialog(); }
      else if (a === "now") startUpdateFlow();
    };
  }

  function renderProgress(prog) {
    ensureDialog();
    var body = document.getElementById("vdu-body");
    var actions = document.getElementById("vdu-actions");
    if (!body || !actions) return;
    var labels = { download: "下载中", verify: "校验中", extract: "解压中", swap: "换载中", restart: "重启中" };
    var pct = typeof prog.percent === "number" ? prog.percent : 0;
    body.innerHTML =
      '<div style="margin-bottom:10px;font-weight:600">' + escapeHtml(labels[prog.phase] || prog.phase) + "</div>" +
      '<div style="height:10px;border-radius:6px;background:#eee;overflow:hidden"><div style="height:100%;width:' + pct + '%;background:linear-gradient(90deg,#4f8cff,#26c6da);transition:width .3s"></div></div>' +
      '<div style="margin-top:8px;color:#666;font-size:13px">' + escapeHtml(prog.message || (pct + "%")) + "</div>";
    actions.innerHTML = '<span style="color:#999;font-size:13px">请勿关闭窗口…</span>';
    dlgEl.style.display = "flex";
  }

  function showUpdateDialogError(msg) {
    ensureDialog();
    var body = document.getElementById("vdu-body");
    var actions = document.getElementById("vdu-actions");
    if (body) body.innerHTML = '<div style="color:#c0392b">更新失败（已自动回滚，应用不受影响）：</div><div style="margin-top:8px;font-size:13px">' + escapeHtml(msg) + "</div>";
    if (actions) {
      actions.innerHTML = '<button data-a="close" style="padding:8px 20px;border:none;border-radius:6px;background:#eee;cursor:pointer">关闭</button>';
      actions.onclick = function (e) { if (e.target.getAttribute("data-a") === "close") closeDialog(); };
    }
    if (dlgEl) dlgEl.style.display = "flex";
  }

  function startUpdateFlow() {
    renderProgress({ phase: "download", percent: 0, message: "准备下载…" });
    invoke("start_update").catch(function (e) {
      showUpdateDialogError(e && e.message ? e.message : String(e));
    });
  }

  function showUpdateDialogFromState() {
    Promise.all([
      invoke("get_update_state", {}),
      invoke("get_pending_update", {}).catch(function () { return null; }),
    ]).then(function (res) {
      var st = res[0] || {};
      var pending = (st.pending && st.pending.info) || res[1] || null;
      showUpdateDialog(pending);
    }).catch(function (e) {
      showUpdateDialogError("无法获取版本信息：" + (e && e.message ? e.message : e));
    });
  }

  function wireUpdateEvents() {
    listen("update-available", function (info) {
      // 后台发现更新只做角标提示，不主动打断用户；托盘「检查更新」才弹窗
      if (window.VOSR2Lite && window.VOSR2Lite.toast) {
        window.VOSR2Lite.toast("发现新版本 " + (info.version || ""), "info");
      }
    });
    listen("update-progress", function (p) { renderProgress(p); });
    listen("update-done", function (r) {
      notifyShell("VOSR2 Lite 更新完成", (r && r.message) || "应用已更新到最新版本");
      ensureDialog();
      var body = document.getElementById("vdu-body");
      var actions = document.getElementById("vdu-actions");
      if (body) body.innerHTML = '<div style="color:#27ae60;font-weight:600;font-size:16px;text-align:center;padding:20px 0">✓ ' + escapeHtml((r && r.message) || "更新完成") + "</div>";
      if (actions) {
        actions.innerHTML = '<button data-a="close" style="padding:8px 22px;border:none;border-radius:6px;background:#4f8cff;color:#fff;cursor:pointer">完成</button>';
        actions.onclick = function (e) { if (e.target.getAttribute("data-a") === "close") closeDialog(); };
      }
      if (dlgEl) dlgEl.style.display = "flex";
    });
    listen("update-error", function (e) { showUpdateDialogError((e && e.message) || "更新失败"); });
    listen("open-update-dialog", function () { showUpdateDialogFromState(); });
    listen("update-not-available", function () { showUpdateDialog(null); });
    listen("update-check-failed", function (e) {
      var m = (e && e.message) || "";
      var hint = m && /请求|连接|超时|TLS|网络|HTTP/i.test(m) ? "\n\n提示：无法连接到更新服务器（GitHub）。请检查网络连接或代理设置后重试。" : "";
      showUpdateDialogError("检查更新失败：" + m + hint);
    });
  }

  // ---------- 自绘标题栏（壳侧 decorations(false)）----------
  function installTitleBar() {
    if (!document.body) return;
    if (document.getElementById("vosr2-desktop-winbar")) return;

    var bar = document.createElement("div");
    bar.id = "vosr2-desktop-winbar";
    bar.style.cssText =
      "position:relative;display:flex;align-items:center;height:34px;flex:0 0 auto;" +
      "padding:0 6px 0 14px;user-select:none;-webkit-user-select:none;" +
      "font-family:'Segoe UI','Microsoft YaHei',Arial,sans-serif;font-size:11px;letter-spacing:1.5px;" +
      "background:rgba(127,127,127,.06);color:inherit;border-bottom:1px solid rgba(127,127,127,.14);";

    var brand = document.createElement("span");
    brand.textContent = BRAND;
    brand.style.cssText = "opacity:.5;font-weight:600;pointer-events:none;";

    var ctr = document.createElement("div");
    ctr.style.cssText = "margin-left:auto;display:flex;gap:2px;";

    function mkBtn(glyph, label, onClick, isClose) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = glyph;
      b.title = label;
      b.setAttribute("aria-label", label);
      b.style.cssText =
        "width:40px;height:28px;display:inline-flex;align-items:center;justify-content:center;" +
        "border:none;background:transparent;color:inherit;border-radius:5px;cursor:pointer;font-size:13px;line-height:1;";
      b.addEventListener("mouseenter", function () {
        b.style.background = isClose ? "#e81123" : "rgba(127,127,127,.28)";
        if (isClose) b.style.color = "#fff";
      });
      b.addEventListener("mouseleave", function () {
        b.style.background = "transparent";
        b.style.color = "";
      });
      b.addEventListener("click", onClick);
      return b;
    }

    var maxBtn;
    function syncMax() {
      invoke("window_is_maximized").then(function (m) {
        if (!maxBtn) return;
        maxBtn.textContent = m ? "\u29C9" : "\u25A1";
        maxBtn.title = m ? "向下还原" : "最大化";
      }).catch(function () {});
    }
    function toggleMax() {
      invoke("window_maximize_toggle").catch(function () {});
      setTimeout(syncMax, 160);
    }

    ctr.appendChild(mkBtn("\u2212", "最小化", function () { invoke("window_minimize").catch(function () {}); }));
    maxBtn = mkBtn("\u25A1", "最大化", toggleMax);
    ctr.appendChild(maxBtn);
    ctr.appendChild(mkBtn("\u2715", "关闭", function () {
      // 与系统 X 同语义：CloseRequested 由 Rust 按 close_to_tray 配置决定隐藏/退出
      invoke("window_request_close").catch(function () { invoke("window_close").catch(function () {}); });
    }, true));

    bar.appendChild(brand);
    bar.appendChild(ctr);
    bar.addEventListener("dblclick", function (e) {
      if (e.target === bar || e.target === brand) toggleMax();
    });
    bar.addEventListener("pointerdown", function (e) {
      if (e.button !== 0) return;
      if (e.target !== bar && e.target !== brand) return;
      invoke("window_start_dragging").catch(function () {});
    });
    document.body.insertBefore(bar, document.body.firstChild);
    syncMax();
  }

  function wire() {
    wireUpdateEvents();
    installTitleBar();
    watchTaskLifecycle();
  }

  // ---------- 主入口 ----------
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", wire);
    } else {
      wire();
    }
  }

  // 暴露给页面脚本/控制台（可选能力，调用方需判空）
  window.VOSR2Lite = window.VOSR2Lite || {};
  // 页面若已实现 toast（app.js）则尊重页面实现；否则用本桥接的内置轻量 toast
  window.VOSR2Lite.toast = window.VOSR2Lite.toast || function (msg, type) {
    if (!document.body) return;
    var t = document.createElement("div");
    var bg = type === "error" ? "#c0392b" : type === "success" ? "#27ae60" : "#333";
    t.textContent = String(msg == null ? "" : msg);
    t.style.cssText = "position:fixed;right:18px;bottom:18px;z-index:99999;max-width:360px;padding:10px 14px;border-radius:8px;" +
      "background:" + bg + ";color:#fff;font-size:13px;line-height:1.5;box-shadow:0 8px 24px rgba(0,0,0,.28);opacity:0;transition:opacity .2s";
    document.body.appendChild(t);
    requestAnimationFrame(function () { t.style.opacity = "1"; });
    setTimeout(function () {
      t.style.opacity = "0";
      setTimeout(function () { t.remove(); }, 250);
    }, 3200);
  };
  window.VOSR2Lite.notify = notifyShell;
  window.VOSR2Lite.onTaskStart = onTaskStart;
  window.VOSR2Lite.onTaskEnd = onTaskEnd;
  window.VOSR2Lite.checkUpdate = function () { return invoke("check_update", { auto: false }); };
  window.VOSR2Lite.showUpdateDialog = showUpdateDialog;
  window.__vosr2liteShell = window.VOSR2Lite;
})();
