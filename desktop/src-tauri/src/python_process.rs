use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use anyhow::{Result, anyhow};
use serde::Serialize;
use tauri::{AppHandle, Emitter};
#[cfg(windows)]
use std::os::windows::process::CommandExt;

use crate::port_manager::find_free_port;

/// 从 HTTP 响应头里取 CSRF token：优先响应头 `X-CSRF-Token`（家族部分仓由中间件直接下发），
/// 回退解析 `Set-Cookie: csrf_token=...`（VOSR2 的 CSRF 中间件只种 cookie，不发头）。
fn extract_csrf_token(headers: &str) -> Option<String> {
    let mut cookie_token: Option<String> = None;
    for line in headers.lines() {
        let lower = line.to_ascii_lowercase();
        if let Some(rest) = lower.strip_prefix("x-csrf-token:") {
            let idx = line.len() - rest.len();
            let value = line[idx..].trim();
            if !value.is_empty() {
                return Some(value.to_string());
            }
        }
        if lower.starts_with("set-cookie:") && lower.contains("csrf_token=") {
            // 形如：Set-Cookie: csrf_token=<value>; Path=/; SameSite=Strict
            let after = &line[line.find("csrf_token=")? + "csrf_token=".len()..];
            let value = after.split(';').next().unwrap_or("").trim();
            if !value.is_empty() {
                cookie_token = Some(value.to_string());
            }
        }
    }
    cookie_token
}

/// 请求 Python 服务本机优雅关闭（桌面端 B-5）。
///
/// 自适应 CSRF：先 GET `/api/system/health` 取 token（若后端启用了 CSRF 中间件或
/// 家族仓的 X-CSRF-Token 下发），再以 double-submit（cookie + 头同值）POST
/// `/api/system/shutdown`；取不到 token 时按「CSRF 关闭」直接 POST。
///
/// 之所以要自适应：家族三仓实现分叉——Image 强制带 token（CSRF 默认开）、
/// SeedVR2/TTS 直接裸 POST（CSRF 默认关）。任一固定写法在另一种配置下都会
/// 静默失效（403 → 退化成 taskkill，优雅关闭形同虚设）。
fn request_graceful_shutdown(port: u16) -> bool {
    use std::io::{Read, Write};
    use std::net::TcpStream;
    use std::time::Duration;

    let Ok(addr) = format!("127.0.0.1:{port}").parse::<std::net::SocketAddr>() else {
        return false;
    };

    // 1) GET 健康检查，顺带取 CSRF token（中间件在安全方法响应上补种）
    let token = {
        let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(800)) else {
            return false;
        };
        let _ = s.set_read_timeout(Some(Duration::from_millis(1500)));
        let req = format!(
            "GET /api/system/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
        );
        if s.write_all(req.as_bytes()).is_err() {
            return false;
        }
        let mut buf = Vec::new();
        if s.read_to_end(&mut buf).is_err() {
            return false;
        }
        let head = String::from_utf8_lossy(&buf);
        let headers = head.split("\r\n\r\n").next().unwrap_or("");
        extract_csrf_token(headers)
    };

    // 2) POST 关闭端点（有 token 走 double-submit，无 token 裸 POST）
    let Ok(mut s) = TcpStream::connect_timeout(&addr, Duration::from_millis(800)) else {
        return false;
    };
    let _ = s.set_read_timeout(Some(Duration::from_millis(1500)));
    let req = match token {
        Some(tok) => format!(
            "POST /api/system/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nCookie: csrf_token={tok}\r\nX-CSRF-Token: {tok}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        ),
        None => format!(
            "POST /api/system/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        ),
    };
    if s.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 128];
    let _ = s.read(&mut buf);
    true
}

/// Python 进程状态
#[derive(Debug, Clone, Serialize)]
pub enum PythonStatus {
    Stopped,
    Starting,
    Running,
    Crashed,
}

/// Python 子进程管理器
pub struct PythonProcess {
    child: Option<Child>,
    port: u16,
    runtime_dir: PathBuf,
    app_dir: PathBuf,
    log_dir: PathBuf,
    status: PythonStatus,
    restart_count: u32,
    max_restarts: u32,
}

impl PythonProcess {
    pub fn new(runtime_dir: PathBuf, app_dir: PathBuf, log_dir: PathBuf) -> Self {
        Self {
            child: None,
            port: 0,
            runtime_dir,
            app_dir,
            log_dir,
            status: PythonStatus::Stopped,
            restart_count: 0,
            max_restarts: 3,
        }
    }

    /// 启动 Python 子进程
    pub fn start(&mut self) -> Result<u16> {
        // 找空闲端口
        let port = find_free_port()?;
        self.port = port;

        // 确定 Python 可执行文件路径
        let python_exe = self.runtime_dir.join("python.exe");
        if !python_exe.exists() {
            return Err(anyhow!("Python 可执行文件不存在: {}", python_exe.display()));
        }

        // 启动脚本：开发态位于 <app_dir>/scripts/start_portable.py，打包态位于 <app_dir>/start_portable.py
        let start_script = {
            let dev = self.app_dir.join("scripts").join("start_portable.py");
            let packed = self.app_dir.join("start_portable.py");
            if dev.exists() {
                dev
            } else {
                packed
            }
        };
        if !start_script.exists() {
            return Err(anyhow!("启动脚本不存在: {}", start_script.display()));
        }

        // 确保日志目录存在
        std::fs::create_dir_all(&self.log_dir)?;
        let log_file = self.log_dir.join(format!(
            "python_{}.log",
            chrono::Local::now().format("%Y%m%d_%H%M%S")
        ));
        let log_writer = std::fs::File::create(&log_file)?;

        // 启动子进程
        let child = Command::new(&python_exe)
            .arg(&start_script)
            .arg("--port")
            .arg(port.to_string())
            .arg("--host")
            .arg("127.0.0.1")
            .current_dir(&self.app_dir)
            .stdout(Stdio::from(log_writer.try_clone()?))
            .stderr(Stdio::from(log_writer))
            .env("PYTHONNOUSERSITE", "1")
            .env("PYTHONUNBUFFERED", "1")
            .creation_flags(0x08000000) // CREATE_NO_WINDOW
            .spawn()?;

        self.child = Some(child);
        self.status = PythonStatus::Starting;
        log::info!("Python 进程已启动，PID={}, 端口={}", self.child.as_ref().unwrap().id(), port);

        Ok(port)
    }

    /// 标记为运行中
    pub fn mark_running(&mut self) {
        self.status = PythonStatus::Running;
        self.restart_count = 0;
    }

    /// 检查进程是否存活
    pub fn is_alive(&mut self) -> bool {
        if let Some(child) = &mut self.child {
            match child.try_wait() {
                Ok(None) => true,
                Ok(Some(status)) => {
                    log::warn!("Python 进程已退出，状态={}", status);
                    false
                }
                Err(_) => false,
            }
        } else {
            false
        }
    }

    /// 尝试重启（受 max_restarts 限制）
    pub fn try_restart(&mut self) -> Result<bool> {
        if self.restart_count >= self.max_restarts {
            self.status = PythonStatus::Crashed;
            return Ok(false);
        }
        self.restart_count += 1;
        log::info!("正在重启 Python 进程（第 {}/{} 次）", self.restart_count, self.max_restarts);
        self.stop()?;
        self.start()?;
        Ok(true)
    }

    /// 停止 Python 进程（Windows 下杀整棵进程树：uvicorn/torch 会 spawn 子进程，
    /// 只杀直接子进程会留下孤儿持有 app/ 文件句柄，导致更新换载 rename 失败）
    pub fn stop(&mut self) -> Result<()> {
        if let Some(mut child) = self.child.take() {
            let pid = child.id();
            #[cfg(windows)]
            {
                // B-5: 先请求本机 /api/system/shutdown 优雅关闭（停队列、卸载模型、
                // 关历史库），最多等 6 秒；未退出才 taskkill /F 强杀兜底（幂等）。
                let shutdown_sent = if self.port > 0 {
                    request_graceful_shutdown(self.port)
                } else {
                    false
                };
                if shutdown_sent {
                    log::info!("已请求 Python 服务优雅关闭（port={}）", self.port);
                    for _ in 0..60 {
                        match child.try_wait() {
                            Ok(Some(_)) => break,
                            Ok(None) => std::thread::sleep(std::time::Duration::from_millis(100)),
                            Err(_) => break,
                        }
                    }
                }
                // taskkill /T 级联终止子进程树；失败再回退 kill()
                let ok = Command::new("taskkill")
                    .args(["/PID", &pid.to_string(), "/T", "/F"])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status()
                    .map(|s| s.success())
                    .unwrap_or(false);
                if !ok {
                    let _ = child.kill();
                }
            }
            #[cfg(not(windows))]
            {
                let _ = child.kill();
            }
            let _ = child.wait();
            log::info!("Python 进程树已停止（根 PID={pid}）");
        }
        // 兜底：扫描并终止所有属于本应用的 python 进程（父进程已退出的孤儿也一并清除，
        // 否则它们会持有 app/ 目录句柄/工作目录，导致更新换载 rename app 失败 os error 32）
        #[cfg(windows)]
        {
            let app_dir = resolve_app_dir();
            // 命令行可能是 <app_dir>\scripts\start_portable.py（开发态）或 <app_dir>\start_portable.py（打包态），
            // 以 app_dir 为锚、basename 通配匹配两种布局。
            let esc = app_dir.display().to_string().replace('\'', "''");
            let ps = format!(
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {{ $_.CommandLine -like '*{esc}*start_portable.py*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
            );
            let _ = Command::new("powershell")
                .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", &ps])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            log::info!("已扫描清除本应用残留 python 进程");
        }
        self.status = PythonStatus::Stopped;
        Ok(())
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn status(&self) -> PythonStatus {
        self.status.clone()
    }
}

/// 全局共享的 Python 进程管理器
pub struct PythonState {
    pub process: Mutex<PythonProcess>,
}

/// 启动事件负载
#[derive(Serialize, Clone)]
struct StartupStatus {
    message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

/// 向发送启动状态
pub fn emit_startup_status(app: &AppHandle, message: &str, error: Option<String>) {
    let _ = app.emit("startup-status", StartupStatus {
        message: message.to_string(),
        error,
    });
}

/// 当前 Python 运行时的形态（决定壳把用户带到哪个页面）
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PythonMode {
    /// 完整运行时 `app/runtime/`（含 torch），可正常推理
    Full,
    /// 引导解释器 `app/bootstrap/`（只有 Web 依赖、无 torch）：
    /// 瘦包首次启动就是这个形态，此时只能提供「下载管理」页
    Bootstrap,
    /// 开发态 .venv 或系统 Python
    Dev,
}

/// 判定当前运行时形态。与 `resolve_runtime_dir` 使用同一套优先级，
/// 改动时必须两处同步，否则会出现「跑在引导解释器上却导航到首页」的错位。
pub fn detect_python_mode(app_dir: &Path) -> PythonMode {
    if app_dir.join("runtime").join("python.exe").exists() {
        return PythonMode::Full;
    }
    let bootstrap = app_dir.join("bootstrap");
    if bootstrap.join("python.exe").exists() {
        // 环境包装的就是 torch wheels，安装时装进 bootstrap 自身的 site-packages，
        // 不另起一份解释器——所以装完后 bootstrap 目录名字没变，但已经是完整运行时。
        // 靠 torch 包目录判定，比启动子进程 import torch（数秒）便宜得多。
        if bootstrap
            .join("Lib")
            .join("site-packages")
            .join("torch")
            .exists()
        {
            return PythonMode::Full;
        }
        return PythonMode::Bootstrap;
    }
    PythonMode::Dev
}

/// 运行时未装齐时，主窗口应该落到哪个入口（空串 = 首页）
pub fn startup_entry(mode: PythonMode) -> &'static str {
    match mode {
        PythonMode::Bootstrap => "downloads",
        PythonMode::Full | PythonMode::Dev => "",
    }
}

/// 解析运行时目录：优先侧载 runtime，其次瘦包内置引导解释器 bootstrap，
/// 开发模式回退项目 .venv，再退系统 Python
pub fn resolve_runtime_dir(app_dir: &Path) -> PathBuf {
    // 1. 打包后：应用目录下的 runtime/（完整运行时，含 torch）
    let bundled = app_dir.join("runtime");
    if bundled.join("python.exe").exists() {
        return bundled;
    }

    // 2. 瘦包：内置引导解释器 app/bootstrap/。
    //    它只有 Web 依赖、没有 torch，唯一职责是把「下载管理」页跑起来，
    //    让用户可以安装完整运行时与模型。缺这一层时瘦包会直接启动失败。
    let bootstrap = app_dir.join("bootstrap");
    if bootstrap.join("python.exe").exists() {
        return bootstrap;
    }

    // 3. 开发模式 A：app_dir 即项目根（resolve_app_dir 的开发分支），.venv 在其下
    let dev_local = app_dir.join(".venv").join("Scripts");
    if dev_local.join("python.exe").exists() {
        return dev_local;
    }

    // 4. 开发模式 B：app_dir 为 exe_dir/app，.venv 位于上两级（历史布局）
    let dev_venv = app_dir
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.join(".venv").join("Scripts"));
    if let Some(venv) = dev_venv {
        if venv.join("python.exe").exists() {
            return venv;
        }
    }

    // 5. 回退：系统 PATH 中的 python
    PathBuf::from("python")
}

/// 解析应用代码目录
pub fn resolve_app_dir() -> PathBuf {
    // 1. 打包后：优先当前可执行文件目录下的 app/（要求含 start_portable.py，
    //    避免把无 payload 的裸壳目录误判为应用根）。
    if let Ok(exe) = std::env::current_exe() {
        let bundled = exe.parent().unwrap().join("app");
        if bundled.join("start_portable.py").exists() {
            return bundled;
        }
    }

    // 2. 开发模式：CARGO_MANIFEST_DIR 上溯到项目根（desktop/src-tauri → 项目根）
    let dev_app = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf();
    if dev_app.join("scripts").join("start_portable.py").exists() {
        return dev_app;
    }

    dev_app
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extract_token_prefers_header() {
        let headers = "HTTP/1.1 200 OK\r\nX-CSRF-Token: abc.def\r\nSet-Cookie: csrf_token=zzz; Path=/";
        assert_eq!(extract_csrf_token(headers).as_deref(), Some("abc.def"));
    }

    #[test]
    fn extract_token_falls_back_to_cookie() {
        let headers = "HTTP/1.1 200 OK\r\nset-cookie: csrf_token=nonce.sig; Path=/; SameSite=Strict\r\n";
        assert_eq!(extract_csrf_token(headers).as_deref(), Some("nonce.sig"));
    }

    #[test]
    fn extract_token_returns_none_when_absent() {
        let headers = "HTTP/1.1 200 OK\r\ncontent-type: application/json";
        assert_eq!(extract_csrf_token(headers), None);
    }

    #[test]
    fn extract_token_ignores_other_cookies() {
        let headers = "HTTP/1.1 200 OK\r\nset-cookie: theme=dark; Path=/\r\n";
        assert_eq!(extract_csrf_token(headers), None);
    }

    /// 造一个带指定解释器子目录的临时 app 目录；返回 (app_dir, 清理闭包所需路径)
    fn make_app_dir(tag: &str, subdir: Option<&str>) -> std::path::PathBuf {
        let base = std::env::temp_dir().join(format!("vosr2lite-mode-{}-{}", tag, std::process::id()));
        if let Some(sub) = subdir {
            let dir = base.join(sub);
            std::fs::create_dir_all(&dir).expect("创建子目录");
            std::fs::write(dir.join("python.exe"), b"").expect("写入占位解释器");
        } else {
            std::fs::create_dir_all(&base).expect("创建目录");
        }
        base
    }

    #[test]
    fn mode_is_full_when_runtime_present() {
        let app = make_app_dir("full", Some("runtime"));
        assert_eq!(detect_python_mode(&app), PythonMode::Full);
        assert_eq!(startup_entry(PythonMode::Full), "");
        let _ = std::fs::remove_dir_all(&app);
    }

    #[test]
    fn mode_is_bootstrap_when_only_bootstrap_present() {
        let app = make_app_dir("boot", Some("bootstrap"));
        assert_eq!(detect_python_mode(&app), PythonMode::Bootstrap);
        // 引导态必须落到下载管理页，否则用户看到的只是个跑不了推理的空壳
        assert_eq!(startup_entry(PythonMode::Bootstrap), "downloads");
        let _ = std::fs::remove_dir_all(&app);
    }

    #[test]
    fn mode_is_full_when_bootstrap_already_has_torch() {
        let app = make_app_dir("boot-torch", Some("bootstrap"));
        // 环境包装进 bootstrap 自身后目录名不变，靠 site-packages/torch 判定
        let site = app.join("bootstrap").join("Lib").join("site-packages").join("torch");
        std::fs::create_dir_all(&site).unwrap();
        assert_eq!(detect_python_mode(&app), PythonMode::Full);
        assert_eq!(startup_entry(detect_python_mode(&app)), "");
        let _ = std::fs::remove_dir_all(&app);
    }

    #[test]
    fn mode_prefers_runtime_over_bootstrap() {
        let app = make_app_dir("both", Some("bootstrap"));
        std::fs::create_dir_all(app.join("runtime")).unwrap();
        std::fs::write(app.join("runtime").join("python.exe"), b"").unwrap();
        assert_eq!(detect_python_mode(&app), PythonMode::Full);
        let _ = std::fs::remove_dir_all(&app);
    }

    #[test]
    fn mode_is_dev_when_neither_present() {
        let app = make_app_dir("dev", None);
        assert_eq!(detect_python_mode(&app), PythonMode::Dev);
        let _ = std::fs::remove_dir_all(&app);
    }

    #[test]
    fn resolve_runtime_dir_falls_back_to_bootstrap() {
        let app = make_app_dir("resolve", Some("bootstrap"));
        assert_eq!(resolve_runtime_dir(&app), app.join("bootstrap"));
        let _ = std::fs::remove_dir_all(&app);
    }
}
