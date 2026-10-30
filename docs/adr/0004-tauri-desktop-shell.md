# ADR-0004: 对外分发形态采用 Tauri v2 桌面壳

- **状态**: Accepted
- **日期**: 2026-10-30
- **决策者**: 项目维护者
- **关联**: ADR-0001（定位为「不依赖 ComfyUI 的独立 Web 工具箱」）、`docs/release-governance.md`

## 背景与问题

VOSR2-Lite v0.1~v0.2 的交付形态是「源码 + `start.bat` + 浏览器访问 `http://127.0.0.1:7871`」。
对开发者够用，但对「装完就用」的普通用户有三处硬伤：

1. 没有独立窗口/托盘/系统通知，浏览器标签页一关，后台推理服务仍在跑，用户以为已退出；
2. 端口固定在 7871，撞端口时只能靠日志发现问题（`find_available_port` 顺延后用户不知道该开哪个地址）；
3. 应用更新只能手工替换文件，无版本感知、无回滚。

同时，家族另外三个仓（Image-DuoModel / TTS-MultiModel / SeedVR2-Lite）已经各自落地了同一套
Tauri v2 壳（`desktop/src-tauri`，tauri crate 2.11.x），形成可复用的成熟范式。

## 评估的备选方案

| 方案 | 结论 | 理由 |
|---|---|---|
| **Tauri v2 壳（选定）** | 采纳 | 与家族三仓同构，壳代码 ≈3.4k 行可直接复用；WebView2 复用系统运行时，壳产物仅 ~17 MB；Rust 侧能精确托管 Python 子进程并杀进程树 |
| Electron | 否决 | 运行时体积 +80~120 MB，与「轻量本地工具」定位冲突；家族仓已用 Tauri，双栈维护成本高 |
| pywebview 薄封装 | 否决 | 无托盘/通知/更新器/单实例等系统集成，等于把三处硬伤原样保留（SeedVR2 曾用过并已迁走） |
| 继续纯浏览器 | 否决 | 上述三处硬伤无解；Windows 用户仍需理解「先开 bat 再开浏览器」 |
| 云服务化 | 否决 | 与「数据不出机器、仅绑 127.0.0.1」的安全基线冲突，且 GPU 成本不可控 |

## 决策

1. 对外正式分发形态 = **Tauri v2 桌面壳 + 侧载便携 Python**：壳只做窗口/托盘/通知/进程托管/更新，
   业务仍在 FastAPI 后端，壳通过 `http://127.0.0.1:<随机端口>/` 加载页面（**不维护第二套 UI**）。
2. 壳**不内置** Python 与模型：`runtime/` 与 `model/` 由数据卷提供，壳按 `resolve_runtime_dir` 顺序探测。
3. 壳只依赖两个后端契约：`GET /api/system/health`（就绪探测）与 `POST /api/system/shutdown`
   （优雅关闭，替代 `taskkill /F`）。为此新增 `scripts/start_portable.py`（`--port/--host`）与
   关闭端点，均由本 ADR 授权引入。
4. 数据分发采用「安装器 + 7z 分卷」：`VOSR2Lite-Setup-v<ver>.exe`（~4 MB，内嵌壳与 `version.json`）
   + `VOSR2Lite-Data.7z.001..`（数 GB）。壳更新走 `app-v<ver>.zip` 增量包，不重打数据卷。
5. 版本口径与家族四仓对齐：应用版本来自 `version.json`；**壳版本独立**并三处镜像
   （`Cargo.toml` / `package.json` / `tauri.conf.json`），`minimum_shell_version` 由 `version.json` 声明。

## 后果

**正面**

- 用户得到「双击即用」的原生窗口：托盘常驻、系统通知、单实例聚焦、崩溃自动重启（最多 3 次）、
  窗口大小/位置记忆、关闭到托盘可配置。
- 更新可增量（应用代码 ~1 MB）、失败自动回滚（`app.bak`），且壳与数据卷解耦。
- 与家族三仓共享同一套壳范式与踩坑经验（`docs/desktop-shell.md` 不变量清单），后续维护成本低。

**代价 / 约束**

- 新增 Rust 工具链依赖；CI 增加一个 windows-latest 的 `cargo clippy/test` job。
- 壳的一切 IPC 都要在 `build.rs` 与 `capabilities/main.json` 双侧登记，漏一处运行期静默失效。
- 更新签名私钥必须离线保管；公钥一次 base64 写进 conf，二次编码会让验签恒失败。
- 无代码签名证书，首次运行仍会触发 SmartScreen 提示（与家族仓同状态，属已知限制）。

## 验证

- `cargo check --all-targets`、`cargo clippy --all-targets -- -D warnings`、`cargo test` 通过。
- 后端：`tests/test_system_shutdown.py`（回环放行 / 非回环 403 / 无 Server 句柄时不误杀）。
- 手工冒烟：`npm run dev` → 启动页 → 健康检查通过 → 导航到应用页 → 托盘退出后无残留 python 进程。
