# E2E（浏览器级）测试层

> 回应 TD-014「无 E2E/UI 测试层：无 Playwright/浏览器测试；desktop 壳窗口交互无 UI 自动化；
> 前端回归靠人肉」。本文件是本层的说明书：怎么跑、测了什么、**刻意没测什么**、以及为什么。

## 一、一句话结论

- 主旅程「**上传 → 推理(mock) → 对比 → 下载**」已由 Playwright 驱动真实浏览器覆盖；推理用
  桩引擎，不依赖 GPU 与权重。当前本层共 **9 个用例、145 条断言**（主旅程 3 例 / 批量+取消+
  部分失败 3 例 / 像素 diff 1 例 / CSRF 开启模式 2 例），本机实测 `pytest tests/e2e` `rc=0`
  （热跑约 39 秒）；全量 `pytest tests` 是 `329 passed / 0 failed / 0 skipped`、`rc=0`
  （2026-11-09 实测）。
- TD-014 未覆盖清单里的 **(b) 多文件批量队列、中途取消、视觉像素 diff**、批量「部分失败」混合
  路径，以及 **CSRF 开启模式**（协议 + 浏览器两端）均已补齐（口径与残余缺口见 §七）；
  **非 Chromium** 仍未覆盖。
- 桌面壳的**真窗口交互**（托盘、拖拽、更新弹窗）属运行态，需要 Tauri WebDriver + WebView2，
  本轮按口径**不做**；但把壳最容易静默失效的那一面——命令注册 / 权限 / capability / 桥接注入——
  做成了每次 `pytest` 都跑的**静态契约闸**（`tests/test_desktop_bridge_contract.py`，6 个用例）。
- E2E 首跑就抓到一个真问题（见 §五）：单张失败时 UI 只显示「有任务失败: 1」，**失败原因不上屏**。
  该问题已修，并有一条断言专门守着它。本轮又抓到并修掉一条长期存在的 UI 缺口：取消后**未执行**
  的队列行一直显示「排队中」（看起来像还在等），最小修与影响面见 §七 第 3 条。

## 二、跑法

```powershell
# 1) 装依赖（与 requirements-dev.txt 分开：浏览器二进制约 150 MB，日常单测不需要）
.venv\Scripts\python.exe -m pip install -r requirements-e2e.txt
.venv\Scripts\python.exe -m playwright install chromium   # Linux 上再加 --with-deps

# 2) 只跑 E2E（9 个用例；本机实测热跑约 39 秒，机器慢时按条数估算，勿当硬指标）
.venv\Scripts\python.exe -m pytest tests/e2e -q

# 3) 全量（E2E 正常收集；未装 Playwright 时这 9 个用例**显式 skip**，不是静默跳过）
.venv\Scripts\python.exe -m pytest tests -q
```

未安装 Playwright 时用例会 skip 并给出原因（`未安装 Playwright：pip install -r requirements-e2e.txt`）。
这样设计是有意的：**静默地 0 个用例**会让「测试层消失」被读成「全部通过」。

## 三、这套层是怎么搭的（每个选择都有代价，写清楚）

| 选择 | 做法 | 为什么 |
|---|---|---|
| 真 uvicorn 而不是 `TestClient` | `tests/e2e/conftest.py` 在后台线程起 `uvicorn.Server`，端口用系统分配的空闲端口 | `EventSource` 进度流、相对 URL 静态资源、下载的 `Content-Disposition`、CSRF cookie 只有真连接才成立；`TestClient` 会把它们全部短路 |
| 桩引擎而不是真推理 | `monkeypatch` 把 `ModelManager.get_engine` 换成只做「按 `upscale` 放大 + 按阶段回调进度」的桩 | E2E 验的是链路（路由 / 队列 / SSE / 模板 / 前端 JS），不是 GPU 与权重；CI 与本机都没有可用检查点。桩只保留了真实引擎也会做的两件事，所以链路其余部分**全走真实实现** |
| 临时目录 + 白名单同源 | 上传 / 输出 / 导出都指向 `tmp_path`，并把 `runtime.security.allowed_base_dirs` 指到同一批目录 | 少一个就撞 `security/path_guard.py` 的 403 —— 那是守卫在正常工作。顺带保证 E2E 不往仓里的 `data/uploads`、`outputs` 写垃圾 |
| 阶段各 0.25 s | 桩每阶段 `sleep(0.25)` | 慢到足以让浏览器看见 SSE 中间态（否则进度条 0 → 100 一闪而过，「进度是真的」测不出来），快到一个用例仍在秒级完成 |
| 取消用例临时放慢到 0.6 s | 用例内改 `stub.stage_delay`，`try/finally` 复原 | 取消必须落在「任务已建、还没跑完」的窗口里才有判别力；0.25 s 时从看到第一个进度到整张跑完不足 0.3 s，点慢一点就撞上「任务已完成 → `/cancel` 返 400」，会变成一条偶发红的用例 |
| CSRF 模式**另起一个服务** | `e2e_server_csrf` 依赖 `e2e_server`，deepcopy 其 config、只把 `runtime.security.csrf.enable` 打成 `true`，共用同一批临时目录与桩引擎 | CSRF 是在 `create_app()` 里按开关装配的，同一个 app 实例加不了第二次中间件；默认服务必须保持「关闭」形态。共用目录保证两边都用「快照差集」判断落盘，独立 `history.db` 避免两个服务抢同一个 sqlite 文件锁；密钥用固定的 `VOSR2_SECRET_KEY`，既不往仓里 `data/` 落文件，也让断言能离线复算签名 |

夹具 `e2e_server` 额外暴露 `exports`（导出目录）——取消用例要证明「取消不往导出目录留半成品」，
只看上传/输出两个目录是不够的。

## 四、测了什么（`tests/e2e/`）

四个文件、九个用例：`test_restore_journey.py`（主旅程 3 例）、`test_restore_batch.py`
（批量队列 + 中途取消 + 部分失败）、`test_restore_pixel_diff.py`（视觉像素 diff）、
`test_csrf_mode.py`（CSRF 开启模式 2 例）。

**用例 1 `test_journey_upload_infer_compare_download`**
1. `/restore` 页面打开、`#dropzone` 可见；
2. 选图后 `#pickedBox` 出现、`#pickedName` 真的是 `cat.png`、`#btnStart` 解禁
   （防「选中了但没解析出文件」）；
3. 点开始后进度百分比出现**介于 0 与 100 之间**的值，阶段文案非空（SSE 中间态真的到页面）；
4. 结果卡出现，`#viewer` 拿到 `is-ready`——这是 viewer 在**两张图都 `onload` 之后**才加的类，
   等价于「预览端点两张图都 200 且可解码」；
5. 左图 `naturalWidth` = 原图宽、右图 = 原图宽 × 4 —— 一条断言同时锁住「参数传到引擎」「输出落盘」
   「预览端点」「viewer 尺寸链」；
6. 对比是**可交互**的：默认 `is-split` → 点 `#btnCompare` 关掉 → 再点恢复；鼠标拖动分割线后
   `.sv-viewer-after` 的 `clipPath` 变为 `inset(...)`；
7. 下载是浏览器真实 `download` 事件：文件名 `.png`、字节以 PNG 魔数开头、解码尺寸 256×192；
8. 桩引擎记录到最后一次 `upscale == 4`；输出目录里确实有 `*_vosr2_*.png`。

**用例 2 `test_download_format_switch_produces_jpeg`**
把 `#downloadFormat` 切到 `jpeg` 后下载：文件名 `.jpeg`（服务端按 `format` 原样拼扩展名，
是 `.jpeg` 不是 `.jpg`，这里把既有契约锁住）、字节以 JPEG SOI 开头、`format == "JPEG"`、尺寸不变。

**用例 3 `test_fake_png_is_rejected_in_ui_without_inference`**
上传一个扩展名是 `.png`、内容却是文本的文件：UI 落到 `#errorCard`、原因**含「不符」**（服务端魔数校验的
原文）、不再只是聚合计数、结果卡不出现，且桩引擎调用次数**未增加**（证明「拒绝」是真的没进推理，
不是前端把错误藏起来）。

### 用例 4~5：批量队列与中途取消（`tests/e2e/test_restore_batch.py`）

这两条覆盖的是 `restore.js::startRun` 那个 `for` 循环的两侧：**顺序跑完**与**中途停下**。
共用的小工具（造图、选文件、等终态、读队列行、目录快照）抽在 `tests/e2e/_e2e_helpers.py`
（文件名以 `_` 开头，pytest 不当用例收集）。

**用例 4 `test_batch_queue_three_files_in_order`** — 3 张不同纯色的 64×48 图
1. 多选后走队列分支：`#queueBox` 可见、`#pickedBox` 收起、按钮文案带 `(3)`；
2. 队列 3 行的**名字顺序**等于选中顺序，初始全是 `idle` / 进度 `0%`；
3. 用 `MutationObserver` 记录每次行变化，断言三行**依次**进入 `running`（序号 0 < 1 < 2）
   —— 这是「串行、按序」的直接证据，不是看最后结果猜的；
4. 每行都出现过 1..99 的**中间进度**（不是「跑完直接跳已完成」）；
5. 终态三行 `done`、进度条 `100%`、`#queueSummary` 以 `3/3` 开头；
6. 落盘**恰好 3 个**新成品，命名 `<输入名>_vosr2_<task_id 前 8 位>.png`，三个 8 位前缀互不相同
   （三张是三个独立任务）；尺寸 256×192；
7. 每张成品的**中心像素**等于自己那张输入的纯色（±6）—— 防「只处理了第一张却把结果复制三份」；
8. 批量结束后停在**最后一张**：`#btnDownload` 的 href 里那个 task_id 查 `/result`，
   输出文件名以 `bq_c_vosr2_` 开头；
9. 桩引擎恰好被调用 3 次、每次 `size` 都是 64×48、`upscale` 都是 4。

**用例 5 `test_cancel_mid_batch_stops_remaining_items`** — 3 张选好后，等第 1 张出现中间进度
（此时前端已拿到 `taskId`，`/cancel` 打的是「存在且未结束」的任务）→ 点 `#btnCancel`
1. 被取消那行 `status=cancelled`、徽标含「已取消」（状态上屏，不是静默结束）；
2. **取消不当成失败**：`#errorCard` 与 `#resultCard` 都不出现；
3. 后续两行既不是 `running/done/failed`，进度条仍是 `0%`；
4. 桩引擎**只多 1 次调用**（后续项没进引擎）；
5. 上传目录**只多 1 个文件**（后续项连上传都没发生）—— 比只看引擎次数更靠前一层；
6. 无残留：成品数量不变、导出目录与上传目录都没有 `tmp*` / `*.part` / `*.crdownload` 之类半成品；
7. 服务端也认这笔账：`/api/system/history?status=cancelled` 里该记录 `status=cancelled`、
   `error=用户取消`、**没有 `output_file`**；该任务的 `/result` 返回 `cancelled`，
   `/preview` 与 `/download` 都是 **400**（用户拿不到半成品）。

### 用例 6：视觉像素 diff（`tests/e2e/test_restore_pixel_diff.py`）

**这条用例验的是「浏览器真的把引擎产出的那张图按原生像素画出来了」**：尺寸对、DOM 状态对，
但画错图 / 画的是有损预览 / 被 CSS 拉伸过，前两类断言都看不出来，像素比对看得见。

做法：
1. 输入是 64×48 的**硬边图案**（四象限异色 + 8px 明暗格）。纯色不行——纯色经 BICUBIC ×4 后
   与「朴素拉伸」在像素上几乎一致，既测不出差异，也做不了镜像负控；
2. 跑完主旅程后**关掉分割线**（分割线是压在画布中缝的线，两条角标也压在图上），
   切 **1:1**（一个图像像素对应一个 CSS 像素，deviceScaleFactor 默认 1）；
3. 截 `.sv-viewer-canvas` 元素图 → 与输出目录里那个成品**逐像素**比：
   单像素容差 8 色阶、超差像素占比 ≤ 1%、平均最大通道差 ≤ 1.0；
4. 顶部 44 px **角标带裁掉**：角标是文字，属 UI 不属于图像像素；
5. **自带负控**：同一张截图再与「左右镜像的期望图」比一次，必须超差 ≥ 15%
   （实测 100%）——否则说明容差宽到「随便一张图都能过」。

**为什么没有落盘的跨机 golden 基线**：截图基线会把「字体渲染 / 图像缩放实现 / 显卡」的跨平台
差异固化成一条每跑必红的用例（本层还要在 Linux runner 上跑）。所以基线不落盘，改成**在同一次
运行里由桩引擎的确定性输出算出来**：桩只做 BICUBIC ×4，同一输入必得同一批像素，期望图直接读
服务端落盘的那个成品，判据与机器无关。

**实测**（本机 Windows / Chromium 1248）：截图 256×193、期望 256×192（「1:1」的显示宽度取整带来
1 px 差异，裁剪时按公共尺寸对齐）、超差像素 **0** 个、平均差 **0.57**、镜像负控 **100%** 超差。

**踩到的坑（已写进断言）**：viewer 先挂预览图、随后才把 `src` 升级成 `/download` 的原文件，
而**预览在无 alpha 时是 JPEG q88 有损编码**。若截图落在升级之前，比的就不是引擎产出而是预览的
再压缩结果——断言会变成时序赌局。所以用例显式等 `afterImg.src` 含 `/download` 且解码完成。
（这个坑是负控暴露的：第一次把 `after`（预览 URL）换成 JPEG，用例**没红**，正说明截图拿的是
升级后的原图——于是把负控改打到 `afterFull` 上，才判红。）

### 用例 7：批量「部分失败」的混合路径（`tests/e2e/test_restore_batch.py`）

3 张里**中间**那张是「扩展名 `.png`、内容是纯文本」——服务端在**写盘之前**用魔数校验 400 拒掉
（`security/magic_check.py`），所以这条失败是**确定的**（不依赖随机种子、文件大小或桩行为），
又正好落在批量循环中间，能同时测到「失败项自己红」和「其余项照旧完成」。

1. 终态逐行各就各位：`done / failed / done`，徽标「已完成 / 失败 / 已完成」；
2. 失败原因上屏到**队列行**（含服务端原文「不符」），不是只给聚合计数；
3. 无跨项串扰：两个成功行既没被标失败、也没沾上失败原因；
4. 失败项从未真的跑：观察器日志里它没有 >0 的中间进度、也没有 `done`；
5. 汇总层 `2/3 · 失败 1`；错误卡显示「有任务失败: 1」（批量只给计数，单张才给原因，见用例 3）；
6. 结果卡停在**最后一个成功项**（`pf_c`），失败项没有顶掉它；
7. 落盘**恰好 2 个**成品、没有失败项成品，两张的中心像素各对得上自己的输入（防跨项串用）；
8. 上传目录只多 2 个文件（失败项连落盘都没发生），且没有临时文件；
9. 导出目录快照不变（没有半成品被导出）；
10. 桩引擎只被调用 2 次，`size` / `upscale` 一致；
11. 服务端**没有**为失败输入建历史记录——证明拒绝点在 `add_record` 之前，而不是「先建任务再回滚」。

### 用例 8：CSRF 开启模式的协议面（`tests/e2e/test_csrf_mode.py`）

`e2e_server_csrf` 把 `runtime.security.csrf.enable` 打开（其余配置与默认服务同源），用 `httpx`
直连验「Double Submit Cookie」的契约：

1. `GET /restore` 种 `csrf_token`，且 cookie **不是 HttpOnly**（否则 `app.js` 读不到、前端必 403）；
2. token 形如 `nonce.HMAC-SHA256(secret, nonce)`：用例用夹具里固定的 `VOSR2_SECRET_KEY`
   **离线复算签名**并比中——证明这枚 token 是服务端用密钥签的，不是随机串；
3. 四种不该通过的组合全是 `403` + `error.code=CSRF_INVALID`：无 cookie 无 header / 有 cookie
   无 header / 有 cookie 但 header 签名被改（同 nonce、签名全 0）/ 有 header 无 cookie；
4. 这四次拒绝**没有业务副作用**：上传目录没多文件、桩引擎调用次数不变——403 必须发生在业务之前；
5. 403 响应会补种一枚**新的、签名合法**的 cookie（浏览器里坏 token 下一次请求自愈）；
6. 带正确 token：真的建任务（`task_id` 16 位）、跑完、落盘 1 个成品且尺寸 = 输入 ×4。

### 用例 9：CSRF 开启模式的浏览器面（`tests/e2e/test_csrf_mode.py`）

协议面只能证明「中间件装上了」，装上了但前端不回传才是开启 CSRF 后最常见的坏法（页面能开、
一点开始就 403），所以这条用真浏览器跑完整旅程：

1. 页面里 `document.cookie` **读得到** `csrf_token`，且签名可离线复算（与用例 8 同一把密钥）；
2. **自带的活体负控**：同一页面、同一端点打一发**不带 `X-CSRF-Token`** 的 `fetch POST`，必须拿到
   `403` + `CSRF_INVALID`——它证明这个服务上的拒绝链真的闭合；同样的请求若 CSRF 没生效，会走到
   表单校验的 422/400，而不是 403；
3. 正常旅程（选图 → 开始）走完：进度卡收起、错误卡**不出现**、结果卡出现、`#viewer` 拿到
   `is-ready`（预览端点也一并验到）；
4. 下载链接里的 `task_id` 查 `/result` 是 `completed`，落盘恰好 1 张、尺寸 64×48 → 256×192，
   中心像素对得上输入纯色（±6），桩引擎只多 1 次调用。

### 桌面壳那一半：`tests/test_desktop_bridge_contract.py`

壳的真窗口交互不做，但 Tauri v2 的命令要**同时**满足四件事才真的能用，任何一件漏掉都是
「浏览器里一切正常、桌面端按钮全无反应」的静默失效：

1. Rust 里写了 `#[tauri::command]`；
2. `main.rs` 的 `generate_handler!` 里注册了（漏注册 → 前端 invoke 报 command not found）；
3. `permissions/autogenerated/<cmd>.toml` 里有 `allow-<kebab>` 权限；
4. `capabilities/main.json` 放行了该权限（漏放行 → 运行时被 ACL 拒绝）。

外加两条反向约束：`desktop-bridge.js` 里写死的 invoke 名字必须都是已注册命令（拼错一个字母＝
该功能永久静默）；桥接脚本必须被 `window.rs` 用 `include_str!` 内嵌并以 `initialization_script`
注入（否则「JS 写好了但壳没注入」在运行时同样表现为按钮无反应）。

当前实测：**25 个命令 / 25 个 allow 权限 / 25 条 capability 放行（另有 3 条插件默认权限）/ 13 个桥接字面量**，四向对齐。

## 五、首跑抓到的问题（已修）

`app/integrated_app/static/js/restore.js` 的失败汇聚分支原来只有一句：

```js
finishWithError(t('restore.batch_failed', '有任务失败') + `: ${failed}`);   // failed 是计数
```

单张失败时队列行是隐藏的（`refreshRow` 在 `state.files.length === 1` 时直接 return），于是
`item.error` 里那句真实原因（例如「文件内容与扩展名不符，疑似伪造格式」）**没有任何地方显示**，
用户只看到「有任务失败: 1」。现在单张失败会把 `item.error` 拼进消息；批量仍显示计数。
断言 `assert "不符" in msg` 与 `assert not msg.endswith(": 1")` 就是这条修复的守门人
（负控 C 把它改回计数，用例立刻判红）。

## 六、负控（证明这些断言真的会咬人）

每个方向破一次，跑该用例确认判红，再复原并比对 SHA256。

**第一轮（主旅程 3 例）**：

| # | 破坏方式 | 结果 |
|---|---|---|
| A | 桩引擎忽略 `upscale`（右图不再放大） | 用例 1 判红（`after == 64` ≠ `256`） |
| B | `viewer.setCompareEnabled` 里 `this.compare` 恒为 `false`（对比开关失效） | 用例 1 判红（`is-split` 不出现） |
| C | 失败消息回退成 `: ${failed}` 聚合计数 | 用例 3 判红（原因不上屏） |

**第二轮（B08 新增 3 例）**，改动都在 `app/integrated_app/static/js/restore.js`，
每轮改完 `git checkout --` 复原，末次 SHA256 = `2ac529aa…0324`（与改动前一致）：

| # | 破坏方式 | 结果 |
|---|---|---|
| D | 删掉 `startRun` 循环开头的 `if (state.cancelRequested) break;` | 用例 5 判红：「取消后出现了结果卡（没有可用结果却报成功）」——后续两项真的跑完了，日志里能看到 `cancel_b` / `cancel_c` 先后完成 |
| E | 循环上界改成 `Math.min(1, state.files.length)`（只跑第一张） | 用例 4 判红：「有队列行从未进入 running: `{0: 4}`」 |
| F | `paintQueueRow` 里 `running: pct + '%'` 改成固定文案 | 用例 4 判红：「行 0 没有中间进度，实际序列 `[]`」 |
| G | `showResult` 里 `afterFull` 换成 `/download?format=jpeg&quality=40` | 用例 6 判红：「超差 1493 个像素（3.94%），平均差 2.94」（阈值 1% / 1.0） |

G 的第一次尝试打在 `after`（预览 URL）上，**用例没红**：viewer 随后会把 `src` 升级到 `afterFull`，
截图拿到的仍是原文件。这不是负控失败，而是暴露了「截图必须先等原图升级完成」这个前提——
补上等待后，改打 `afterFull` 才判红（见 §四 用例 6 的「踩到的坑」）。

**第三轮（本轮新增 3 例 + 1 处最小修）**，改动分别落在 `app.js` / `middleware/csrf.py` /
`restore.js`；每轮改完从**改动后的备份**复原（不能 `git checkout`——`restore.js` 本轮起有
待提交的最小修），末次 SHA256 与本轮基线逐一相同：

| # | 破坏方式 | 结果 |
|---|---|---|
| H | `app.js::csrfHeaders` 恒返回 `{}`（前端不回传 token） | 用例 9 判红：「开启 CSRF 后前端请求被拒：有任务失败: CSRF token 验证失败」 |
| I | `should_enable_csrf` 恒返回 `False`（开关失效、中间件不装配） | 用例 8 判红：「GET /restore 没有种 csrf_token：None」——第一个断言就咬住 |
| J | 批量循环的 `catch` 里加 `break`（一项失败就停整批） | 用例 7 判红：`['done','running','idle'] != ['done','failed','done']`（失败项停在 running，第三项从未开始） |
| K | 删掉「取消后把剩余 idle 行回写 cancelled」这段最小修 | 用例 5 判红：「`{'idx': 1, 'status': 'idle', 'badge': '排队中', …}`」——正是被修掉的旧行为 |

复原后 SHA256：`restore.js` = `0156ab54…12e8`、`app.js` = `06c511a6…2604`、
`csrf.py` = `42c577a9…8398`（`app.js` / `csrf.py` 与 `git HEAD` 逐字节相同，即负控没留残渣）。

## 七、刻意没测的 / 还没测到的（写清楚，别当成"已经覆盖"）

1. **桌面壳真窗口交互**：托盘菜单、拖拽入窗、更新弹窗、最小化/最大化按钮的**行为**。需要
   `tauri-driver` + WebView2 + 已构建的壳二进制，属运行态；本轮按口径不做，只做 §四 的静态契约闸。
   （真要做时的入口：`desktop/README.md`，需要 `cargo tauri build` 产物 + `tauri-driver`。）
2. **批量「部分失败」的混合路径**：**已覆盖**（用例 7）。注入方式是「扩展名 `.png`、内容文本」，
   由服务端魔数校验确定性地 400 拒掉（不依赖桩引擎按文件名失败）。未覆盖的只剩「推理阶段才失败
   （引擎抛异常）」这一支——那种失败要看 SSE 的 `failed` 状态与错误文案，属于桩引擎要按文件名注入
   异常才做得稳，本轮没做。
3. **取消后「未执行项」的文案**（**已修，最小改动**）：根因是 `startRun` 的 `break` 只跳出循环，
   没有回头把剩下的 `idle` 行回写状态，于是它们一直显示「排队中」、进度 0%，看起来像「还在等」。
   修法 12 行（`restore.js`：循环结束后若有 `cancelRequested`，把仍为 `idle` 的项标成 `cancelled`
   并重绘该行）。影响面：只动「取消后未执行项」的徽标与状态，不改变服务端行为、不影响正常完成/
   部分失败路径；`updateQueueSummary` 只统计 done/failed，故汇总文案不变；`btnRetry` 本来就会把
   `cancelled` 重置为 `idle` 后整批重跑，语义不变。用例 5 的断言已从「只锁没跑」升级为
   「`status=cancelled` + 徽标含「已取消」+ 进度仍 0%」。
4. **取消的毫秒级竞态**：若 `/cancel` 恰好落在「SSE 报 completed 之后、循环 break 之前」，
   那一行会显示「已完成」而不是「已取消」。用例 5 用「看到 1..99 的中间进度就点取消」+
   临时把桩放慢到 0.6 s/阶段，把这个窗口压到可忽略，但**没有断言覆盖这个边界**。
5. **视觉像素对比**：本层现在做的是「浏览器渲染 vs 引擎落盘产物的**同机**逐像素复核」，
   **不是跨机视觉基线**，也**不验画质**（画质回归在 `evalset/` + `scripts/eval_quality.py` 那条线上）。
   跨机基线的取舍理由见 §四 用例 6。
6. **非 Chromium 浏览器**：只跑 Playwright 自带 Chromium；Firefox/Safari 未配置。
7. **CSRF 开启模式**：**已覆盖**（用例 8/9，协议 + 浏览器两端）。仍未覆盖的是「CSRF 与 Basic Auth
   同时开启」的组合（中间件顺序为 CSRF 在内、Auth 在外，未认证应先 401 而不是 403）——本层只开
   CSRF、不开 Auth。
8. **桌面壳运行态**与 `cargo test`：由 `desktop-shell.yml` 负责，不在本层。

## 八、CI

`.github/workflows/e2e.yml`（**手动触发 + 每周定时**，不挂在每次 push 上）：

- 每次 push 都跑的成本是「几百 MB 依赖 + 浏览器 + 约 15 分钟」，而共享 runner 的时序抖动会让
  进度中间态这类断言偶发红；日常回归由 `pytest tests` 里的单测承担，E2E 按周提供一次权威结论。
- 触发方式：Actions 页 `workflow_dispatch`，或每周一 03:00 UTC 自动；失败时按 `docs/agent-contract.md`
  的常规节奏排查。
- 本机（Windows）`pytest tests --cov=app` 的实测（2026-11-09，本轮复测）：**67.22%**（漏测语句
  931 条，与上一轮相同——本轮新增的 CSRF/部分失败用例打的是已被单测覆盖的分支，没有新增语句
  覆盖），仍低于 `pyproject.toml` 的 `fail_under=68`。`rc=1` 来自覆盖率阈值，**不是用例失败**：
  不带 `--cov` 的 `pytest tests` 是 `329 passed / 0 failed / 0 skipped`、`rc=0`。
- 关于「本地为什么比阈值低」：本节原先的解释是「本地有若干仅 Linux 覆盖的用例被 skip」，
  但本轮实测 **0 skipped**，这条解释站不住——本轮没有查清真实原因（怀疑是平台分支语句与
  CI 环境差异），也不打算顺手动阈值（`pyproject.toml` 仍有他人在制改动）。按「只记录、不猜」
  处理：结论是「阈值是既有红，与 E2E 无关」，原因待下次专门核对。

## 九、加用例的约定

- 新用例优先加「**只有真浏览器能验**」的断言（交互状态、真实下载、DOM 级联），
  纯逻辑判断放回 `tests/` 单测——不然 E2E 会变成又慢又脆的重复测试。
- 涉及新目录（上传/输出/缓存）时，同步在 `conftest.py` 的 `allowed_base_dirs` 里登记，
  否则会撞 403 而不是测出真实缺陷。
- 每个用例只走一条主路径；失败分支用「桩引擎调用次数」「结果卡隐藏」这类**副作用断言**验证，
  不要只断言文案。
- **不要把「运行中的中间态」当成就绪信号**：`startRun()` 在循环结束时会立刻把 `#progressCard`
  收起，上传被 400 拒掉这类毫秒级失败只会让进度卡可见极短一瞬。曾经在 `_upload_and_start` 里
  写 `wait_for_selector("#progressCard", visible)`，实测约 1/10 概率撞上「已经收起来了」而超时
  （连跑 8 次复现到 1 次，日志里是 `locator resolved to hidden`）。正确写法是等**任一终态**
  （进度卡还在 / 错误卡 / 结果卡），三者必有一个开着。
- **服务端目录是会话级共享的**：`e2e_server.outputs` / `uploads` / `exports` 由同一个 session
  夹具复用，前一个用例的成品还在里面。要断言「这次落盘了几个」，先 `dir_snapshot()` 拍照、再取差集，
  别直接 `len(os.listdir(...))`。
- **截图比对必须等「原图升级完成」**：viewer 先挂预览（无 alpha 时是 JPEG q88），随后才换原文件；
  截在预览上比的就不是引擎产出。判据是 `afterImg.src` 含 `/download` 且 `complete`。
- **基线不落盘**：像素 diff 的期望图在**同一次运行**里由桩引擎的确定性输出现算，不提交 golden
  截图——跨平台字体/缩放差异会把基线变成「每跑必红」（本层还要在 Linux runner 上跑）。
- **时序类用例（取消）先把桩放慢再点**：`try/finally` 改 `stub.stage_delay` 并复原，
  否则「从看到进度到整张跑完」不足 0.3 s，偶发撞上「已完成 → `/cancel` 返 400」。
- **「没执行」要连状态一起断言，别只锁「没跑」**：取消后剩余项的文案曾经是既有缺口，早期用例
  刻意只断言「不是 running/done/failed」，于是那段「排队中」被当成既成事实绕过了一年。凡是断言
  「某项没有发生」，都要同时说清「它现在**应该**显示成什么」——状态机缺一条边，就只能靠这种
  双向断言抓出来。
- **安全开关类用例自带「活体负控」**：CSRF 这类「装上才生效」的中间件，光断言「带 token 能过」
  是空转（开关根本没开也一样过）。必须**在同一服务上打一发不带 token 的请求并拿到 403**，
  再补一条「被拒请求没有业务副作用」（上传目录没多文件、桩引擎没多调用）——否则 403 可能只是
  一个状态码，挡不住请求。
- **两个服务共享一套夹具时，写库的部件要分开**：`e2e_server_csrf` 与默认服务共用目录与桩，
  但 `history.db` 各自一份——同一个 sqlite 文件被两个服务同时写会撞锁，表现是偶发的 500/超时，
  比断言失败更难查。目录可以共享（靠快照差集），可变存储不行。
