# 阶段 9：自动更新 · 灰度发布 · 发布流水线 · 规划方案

> 本文是阶段 9 的**实施规划**（开工前蓝图），遵循 `plan_now.md` 阶段 9 范围与第 6 节交接模板约定。
> 主战场为 `WenShape-main/desktop`（桌面自动更新、打包、版本注入）+ `wenshape-cloud`（release 完整性字段）+ `wenshape-admin`（发布登记表单）+ CI。
> **范围调整（与你确认一致）**：本阶段**先不采购证书、不做代码签名**；用 **SHA256 完整性校验**代替"签名校验"，把"自动更新 + 灰度 + 发布流水线"先打通。签名/公证/消除"未知发布者"作为**阶段 9.5（证书到位后）**的增强，文末留扩展位。

---

## 1. 目标与范围

**目标**：补齐免费安全闭环里的"可信下载"（完整性校验）与"稳定使用"（自动更新）两环——让用户在桌面端一键完成"检测新版本 → 下载 → 校验 → 安装"，并让发布方能灰度、可回滚地放量。

**本阶段范围内**：

- ✅ 桌面端**辅助更新**：检测 → 后台下载安装包 → **SHA256 校验** → 引导用户确认安装（无证书可用）
- ✅ 完整性保障：`Release` 增加 `sha256` / `file_size`，下载产物逐字节校验，校验失败拒绝安装
- ✅ 灰度发布：`dev / beta / stable` 三通道全链路打通，桌面端可切换更新通道
- ✅ 发布流水线：CI 自动构建多平台安装包 + 产出 SHA256；安装包托管到**国内对象存储 + CDN**；在 `wenshape-admin` 后台**手动登记** release（半自动、可控、留审计）
- ✅ 版本号构建注入：从 git tag 注入到 manifest 与 Electron 包版本，去除手填

**本阶段不做（明确延后）**：

- ❌ Windows/macOS 代码签名与公证、消除 SmartScreen "未知发布者"（→ 阶段 9.5，需先采购证书）
- ❌ 完全静默后台自动安装（无签名下体验差、风险高；辅助更新已满足闭环）
- ❌ 任何"权益校验阻断"桌面创作的代码路径（红线，权益只作用于云服务）

---

## 2. 现状基线（阶段 7/8 已具备 → 阶段 9 缺口）

| 能力 | 现状 | 阶段 9 缺口 |
| :--- | :--- | :--- |
| 版本检测 | ✅ `cloud-controller.cjs` 启动 2.5s + 每 6h 调 `/releases/check`，命中 emit `wenshape:cloud-update-available` | 无 |
| 更新提示 | ✅ `UpdateAvailableBanner.jsx` 右下角横幅，强制更新无关闭按钮，可"本版本不再提醒" | 仅"前往下载"跳浏览器，**无下载/校验/安装** |
| Release 数据 | ✅ model: version/platform/arch/channel/download_url/release_notes/is_mandatory；`/releases/check` 精确匹配 + `_version_tuple` 比较 | **缺 `sha256`/`file_size`**，无法做完整性校验 |
| 通道 | ✅ `channel` 字段全链路存在；manifest `releaseChannels:[dev,beta,stable]`、`cloud.defaultChannel:stable` | 桌面端通道**固定 stable、不可切换** |
| 打包 | ✅ `build-release.mjs` 注入 `WENSHAPE_DESKTOP_BUILD_VERSION` 等；win→`build-windows-msi.mjs`，mac→forge dmg/zip；`forge.config.ts` `appVersion` 读 env | 产物**无 SHA256 清单**；版本号**手填** manifest；无 CI |
| 后台发布 | ✅ 阶段 8 `wenshape-admin` 可创建/下架 release（审计） | 发布表单**缺 sha256/file_size** 字段 |

**关键既有契约（阶段 9 复用，不破坏）**：

- `desktop/main/desktop-info.cjs`：`buildVersionCheckPayload(manifest, channel)` → `{ current_version, platform, arch, channel }`；`getPlatformLabel()`(win32→windows)、`getArchLabel()`、`getAppVersion(manifest)`
- `cloud-controller.cjs`：IPC handler 统一返回 `{ ok:true, data } | { ok:false, error }`；`emitToRenderer?.(channel, payload)` 推事件
- `preload/index.cjs`：`window.wenshapeDesktop.cloud.*`；`makeListenerBinder(channel)` 返回 unsubscribe
- cloud `/releases/check`：`VersionCheckResponse { has_update, latest: ReleaseOut? }`

---

## 3. 技术选型与理由

| 决策点 | 选型 | 理由 |
| :--- | :--- | :--- |
| 更新方式 | **辅助更新**（下载 + SHA256 校验 + 引导安装） | 无证书即可用；复用现有 `/releases/check`+`download_url` 体系，改动可控；不需维护 electron-updater 的 `latest.yml`/`blockmap` 平行元数据 |
| 完整性 | **SHA256**（流式边下边算） | 防 CDN 损坏 / 中间人篡改；无签名场景下唯一可靠的"包未被改"保证 |
| 安装包托管 | **国内对象存储 + CDN** | 面向国内用户下载快、稳定；域名已备案可直接挂；与 cloud release 表解耦 |
| 发布自动化 | **CI 构建 + 手动登记** | CI 出包 + 算 SHA256 省力；人工在 admin 后台填表发布，保留"放行闸口"与审计，少一套 CI 自动鉴权的复杂度与凭证泄露面 |
| 版本号 | **git tag 注入纯 semver** | 去手填、可追溯；`vX.Y.Z` → manifest + 包版本 |

> 选型不影响未来升级：阶段 9.5 证书到位后，可在辅助更新基础上叠加"安装包签名校验"，或平滑切换到 electron-updater 静默更新；本阶段的 `sha256` 字段、CI、通道机制届时全部复用。

---

## 4. 整体架构

### 4.1 更新闭环（桌面端运行时）

```
启动 2.5s / 每 6h / 手动「检查更新」
        │
        ▼
POST /api/v1/releases/check  ──►  has_update + latest{ download_url, sha256, file_size, is_mandatory }
        │
        ├─ 无 sha256（旧记录/降级）──► 横幅仅显示「前往下载」（跳官网，沿用现状）
        │
        └─ 有 sha256 ──► 横幅显示「下载并更新」
                              │  ① host 白名单校验 download_url
                              ▼
                         下载到 <userData>/updates/  ── 边下边算 SHA256（进度事件）
                              │
                              ▼
                         ② 校验 SHA256 == latest.sha256 ?
                              │否──► 删除文件 + 报错 + 回退「前往下载」
                              │是
                              ▼
                         横幅「立即安装」──► ③ 启动安装包（msiexec /i 或 openPath；过一次 UAC/SmartScreen）──► app.quit()
```

### 4.2 发布流程（CI 构建 + 手动登记）

```
开发者打 tag  v0.1.1
        │
        ▼
CI：注入版本号 → build（win MSI / mac dmg）→ 计算 SHA256 + file_size → 上传为 CI 产物 + 打印校验摘要
        │
        ▼（人工）
下载 CI 产物 → 上传安装包到 国内 OSS/CDN（得到 download_url）
        │
        ▼（人工）
wenshape-admin 后台「版本发布」：填 version/platform/arch/channel/download_url/sha256/file_size/notes/is_mandatory → 发布（写审计）
        │
        ▼
桌面端按 channel 命中 → 走 §4.1 更新闭环
        │
        ▼
回滚：admin 后台「下架」该 release（阶段 8 已有，写审计）或发布更高版本覆盖
```

---

## 5. 任务分解与关键代码

### 9.1 cloud：Release 增加完整性字段

**`wenshape-cloud/app/models/models.py`** — `Release` 增列（可空，兼容旧记录）：

```python
sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)      # 安装包 SHA256（小写 hex）
file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)   # 字节数，用于显示/进度
```

**`migrations/versions/003_release_integrity.py`**（`down_revision="002_audit_logs"`）：

```python
def upgrade():
    op.add_column("releases", sa.Column("sha256", sa.String(length=64), nullable=True))
    op.add_column("releases", sa.Column("file_size", sa.BigInteger(), nullable=True))

def downgrade():
    op.drop_column("releases", "file_size")
    op.drop_column("releases", "sha256")
```

**`app/schemas/schemas.py`** — `ReleaseCreate` / `ReleaseUpdate` / `ReleaseOut` 各加：

```python
sha256: Optional[str] = None
file_size: Optional[int] = None
```

> `VersionCheckResponse.latest` 用 `ReleaseOut`，自动带上新字段，桌面端即可读到。
> `_version_tuple` 比较逻辑不变；建议本阶段起版本号统一纯 semver（见 9.6），避免 `-phaseN` 后缀被解析为 0。

### 9.2 desktop：新增辅助更新器 `desktop/main/updater.cjs`

职责：host 白名单校验 → 流式下载 + 边下边算 SHA256 → 校验 → 启动安装。核心骨架：

```js
const { app, shell } = require("electron");
const { createWriteStream, promises: fsp } = require("node:fs");
const { createHash } = require("node:crypto");
const { join } = require("node:path");
const { spawn } = require("node:child_process");

function assertTrustedHost(url, allowHosts) {
  const host = new URL(url).host;                       // 抛错即非法 URL
  if (!allowHosts.includes(host)) throw new Error(`下载源不受信任：${host}`);
}

async function downloadAndVerify({ url, sha256, allowHosts, onProgress }) {
  assertTrustedHost(url, allowHosts);                   // ① 只允许白名单域名
  const dir = join(app.getPath("userData"), "updates");
  await fsp.mkdir(dir, { recursive: true });
  const file = join(dir, decodeURIComponent(new URL(url).pathname.split("/").pop()));

  const res = await fetch(url);                          // Electron 41 内置 fetch
  if (!res.ok) throw new Error(`下载失败 HTTP ${res.status}`);
  const total = Number(res.headers.get("content-length")) || 0;

  const hash = createHash("sha256");
  const out = createWriteStream(file);
  let received = 0;
  for await (const chunk of res.body) {                  // ② 边下边算，省内存
    hash.update(chunk);
    received += chunk.length;
    out.write(chunk);
    onProgress?.({ phase: "downloading", received, total, percent: total ? Math.round(received / total * 100) : 0 });
  }
  await new Promise((r, j) => out.end(e => e ? j(e) : r()));

  const actual = hash.digest("hex");                     // ③ 校验
  if (sha256 && actual.toLowerCase() !== sha256.toLowerCase()) {
    await fsp.rm(file, { force: true });
    throw new Error("安装包校验失败（SHA256 不匹配），已删除");
  }
  return { file };
}

async function installAndQuit(file) {                     // ④ 引导安装（过一次 UAC/SmartScreen）
  if (process.platform === "win32") {
    spawn("msiexec", ["/i", file], { detached: true, stdio: "ignore" }).unref();
  } else {
    await shell.openPath(file);                           // macOS: 打开 dmg，用户拖拽
  }
  setTimeout(() => app.quit(), 1200);
}

module.exports = { downloadAndVerify, installAndQuit };
```

> 设计要点：①**host 白名单**是无签名下防"cloud 被篡改投毒"的关键防线，白名单来自 manifest（见 9.3）。②下载到 `<userData>/updates/`，启动时清理历史残留。③`sha256` 缺失时不自动安装（由调用方降级为"前往下载"）。④Windows 用 `msiexec /i`（MSI 产物）；若改 exe 安装器则 `shell.openPath`。

### 9.3 desktop：controller / preload / manifest 扩展

**`shell.manifest.json`** — `cloud` 段加下载源白名单（替换为你的 OSS/CDN 域名）：

```jsonc
"cloud": {
  "apiBaseUrl": "https://api.wenshape.cn",
  "webBaseUrl": "https://wenshape.cn",
  "defaultChannel": "stable",
  "downloadHosts": ["dl.wenshape.cn", "wenshape.cn"]   // 安装包 CDN 域名白名单
}
```

**`cloud-controller.cjs`** — 复用现有模式新增 handler + 进度事件：

```js
const { downloadAndVerify, installAndQuit } = require("./updater.cjs");
let downloadedFile = null;

async function downloadUpdate() {
  const latest = lastUpdateAvailablePayload?.latest;
  if (!latest?.download_url) throw new CloudApiError(0, "没有可下载的更新");
  if (!latest.sha256) throw new CloudApiError(0, "该版本缺少校验值，请前往官网下载");
  const allowHosts = manifest?.cloud?.downloadHosts || [];
  const { file } = await downloadAndVerify({
    url: latest.download_url, sha256: latest.sha256, allowHosts,
    onProgress: (p) => emitToRenderer?.("wenshape:cloud-update-progress", p),
  });
  downloadedFile = file;
  emitToRenderer?.("wenshape:cloud-update-progress", { phase: "verified", file });
  return { file };
}
async function installUpdate() {
  if (!downloadedFile) throw new CloudApiError(0, "请先下载更新");
  await installAndQuit(downloadedFile);
  return { ok: true };
}
```

新增/调整 IPC（沿用 `{ ok, data } | { ok, error }`）：

| Channel | 方向 | 说明 |
| :--- | :--- | :--- |
| `wenshape:cloud-download-update` | invoke | 下载 + 校验当前 latest |
| `wenshape:cloud-install-update` | invoke | 启动安装并退出 |
| `wenshape:cloud-update-progress` | event→renderer | `{ phase:'downloading'\|'verified', received, total, percent, file? }` |
| `wenshape:cloud-get-update-channel` | invoke | 读当前更新通道 |
| `wenshape:cloud-set-update-channel` | invoke | 设置并持久化通道 |

> `checkVersion` 改为按"用户所选通道"取值：`const channel = getUpdatePrefs().channel || manifest.cloud.defaultChannel`（见 9.5）。

**`preload/index.cjs`** — `cloud` 对象补充：

```js
downloadUpdate() { return ipcRenderer.invoke("wenshape:cloud-download-update"); },
installUpdate()  { return ipcRenderer.invoke("wenshape:cloud-install-update"); },
getUpdateChannel(){ return ipcRenderer.invoke("wenshape:cloud-get-update-channel"); },
setUpdateChannel(c){ return ipcRenderer.invoke("wenshape:cloud-set-update-channel", c); },
onUpdateProgress: makeListenerBinder("wenshape:cloud-update-progress"),
```

### 9.4 frontend：升级 `UpdateAvailableBanner.jsx`

把单一"前往下载"升级为状态机：`idle → downloading(进度条) → verified → installing`。要点（不破坏现有 dismiss/强制更新逻辑）：

- `latest.sha256` 存在 → 显示**「下载并更新」**；点击调 `cloud.downloadUpdate()`，订阅 `onUpdateProgress` 显示百分比进度条
- `phase==='verified'` → 按钮变**「立即安装」**，调 `cloud.installUpdate()`
- `latest.sha256` 缺失 → 退化为现有**「前往下载」**（`download_url` 跳官网）
- 失败 → toast 报错并回退到「前往下载」；强制更新（`is_mandatory`）仍隐藏关闭/不再提醒
- `frontend/src/utils/cloud.js` 增加 `downloadUpdate/installUpdate/subscribeUpdateProgress` 等 bridge 适配（与阶段 7 同风格）

### 9.5 desktop：灰度通道切换

**`desktop/main/update-prefs.cjs`**（轻量持久化，仿 `auth-store` 路径）：读写 `<userData>/cache/update-prefs.json`，结构 `{ channel: "stable" }`；提供 `getUpdatePrefs()` / `setUpdateChannel(channel)`，对非法值回退默认。

- `checkVersion` 用 `prefs.channel`（默认取 `manifest.cloud.defaultChannel`）
- IPC `get/set-update-channel`（见 9.3），切换后立即触发一次 `checkVersion({ silent:false })`
- 渲染层 **System 设置页**加"更新通道"下拉（`dev/beta/stable`），切到 `dev/beta` 给"仅供测试"提示
- **进阶（可选，标注后续）**：百分比灰度——cloud `/releases/check` 按设备 ID hash 取模放量（如"stable 仅对 20% 设备返回新版本"），需 release 加 `rollout_percent` 字段；本阶段先做通道，不做百分比

### 9.6 CI：版本号注入 + 多平台构建 + SHA256

**版本号注入**：`build-release.mjs` 已读 `WENSHAPE_DESKTOP_BUILD_VERSION`；CI 从 tag 解析（`v0.1.1`→`0.1.1`）注入；并在构建前把 `manifest.product.version` 同步为该值（一个小脚本 `scripts/sync-version.mjs`，或在 CI 步骤内 `node -e` 写入）。`forge.config.ts` 的 `appVersion` 已消费该 env。建议把 manifest 默认版本从 `0.1.0-phase7` 改为纯 semver `0.1.0`。

**产物 SHA256**：`build-release.mjs` 末尾的 `wenshape-release.json` 增加 `artifacts:[{ file, sha256, size }]`（对 `out/make` 下安装包逐个 `createHash('sha256')`）。CI 再把它打印到 Job Summary，供人工登记直接复制。

**CI 骨架**（GitHub Actions 示例；若代码不在 GitHub，等价迁移到对应 CI）：

```yaml
name: desktop-release
on: { push: { tags: ["v*"] } }
jobs:
  build:
    strategy:
      matrix:
        include:
          - { os: windows-latest, platform: win32,  arch: x64 }
          - { os: macos-latest,   platform: darwin, arch: arm64 }   # mac 可后置
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 20 }
      - run: npm ci
        working-directory: WenShape-main/desktop
      - name: Build installer
        working-directory: WenShape-main/desktop
        env:
          WENSHAPE_DESKTOP_BUILD_VERSION: ${{ github.ref_name }}   # vX.Y.Z（脚本内去掉 v）
        run: npm run make:${{ matrix.platform == 'win32' && 'windows' || 'macos:arm64' }}
      - uses: actions/upload-artifact@v4
        with: { name: wenshape-${{ matrix.platform }}-${{ matrix.arch }}, path: WenShape-main/desktop/out/make/** }
      - name: Print SHA256
        run: cat WenShape-main/desktop/.artifacts/**/wenshape-release.json   # 含 artifacts[].sha256
```

> Python sidecar 在 CI 的构建较重（需 `build:sidecar:isolated` 隔离 venv + 各平台 Python）。落地时先把 **Windows** 跑通（你的主要目标平台），macOS 作为第二步。

### 9.7 发布登记 SOP（手动）+ admin 表单加字段

**`wenshape-cloud/app/api/admin.py`**：`POST/PATCH /admin/releases` 用 `ReleaseCreate/Update`，9.1 加字段后自动接收；审计 detail 已记录关键字段，可加上 `sha256` 摘要。

**`wenshape-admin`**（阶段 8 前端）：`src/types/api.ts` 的 `Release` 加 `sha256?/file_size?`；`releases/page.tsx` 发布表单加两个输入（`sha256` 必填校验 64 位 hex、`file_size` 可由上传后自动得到）。

**发布 SOP**（写入交接文档）：

1. 打 tag `v0.1.1` → 等 CI 出包，下载 win/mac 产物，记下 Job Summary 里的 SHA256/size
2. 上传安装包到国内 OSS/CDN 的版本目录（如 `dl.wenshape.cn/desktop/0.1.1/WenShape-0.1.1-x64.msi`），得到 `download_url`
3. `admin.wenshape.cn` →「版本发布」→ 填 version/platform/arch/channel/download_url/**sha256**/file_size/notes/is_mandatory → 发布
4. 用一台测试机（或改测试机本地版本号）验证：横幅出现 → 下载 → 校验通过 → 安装成功
5. 先发 `beta` 通道灰度，确认无问题再发 `stable`
6. 回滚：admin「下架」对应记录，或发布更高版本

### 9.8 官网下载页 / 安装引导（无证书的用户体验补偿）

因本阶段不签名，Windows 首次运行弹 SmartScreen"未知发布者"、macOS 弹 Gatekeeper。需在 `wenshape-web` 下载页与桌面横幅给**清晰引导**：

- Windows：弹窗点"更多信息 → 仍要运行"
- macOS：右键"打开"，或系统设置→隐私与安全性→"仍要打开"
- 下载页公示**官方 SHA256**，供谨慎用户手动核对（与安装包内嵌校验互补）

---

## 6. 安全与隐私约束（硬性）

1. **完整性优先**：有 `sha256` 才允许自动下载安装；校验不通过立即删包、拒绝安装、回退官网下载。
2. **下载源白名单**：`download_url` 的 host 必须在 `manifest.cloud.downloadHosts` 内，杜绝从任意域名拉可执行文件。
3. **离线优先不回退**：更新检查/下载全部失败均**静默**，绝不阻塞本地创作（沿用阶段 7 原则）。
4. **强制更新边界**：`is_mandatory` 只隐藏"关闭/不再提醒"、强引导更新，**不得**禁用或退出本地创作功能。
5. **最小采集**：不新增任何桌面端遥测；更新检查仅发送既有的 `current_version/platform/arch/channel`。
6. **不引入权益阻断**：阶段 9 不得在桌面端写入任何"按订阅/权益限制创作"的代码路径（留阶段 11 且只作用于云服务）。
7. **发布凭证**：手动登记走管理员后台（人审 + 审计）；本阶段不引入 CI 自动发布令牌，减少凭证泄露面。

---

## 7. 验收清单

**完整性 / 后端**
- [ ] `alembic upgrade head` 成功，`releases` 有 `sha256`/`file_size` 列
- [ ] `/releases/check` 返回的 `latest` 含 `sha256`/`file_size`
- [ ] admin 后台发布带 `sha256` 的 release，审计含该字段

**桌面自动更新**
- [ ] 发布高版本（带正确 sha256）→ 横幅出现「下载并更新」→ 进度条到 100% → 「立即安装」→ 安装包启动、应用退出、装后版本更新
- [ ] **故意填错 sha256** → 下载后校验失败、删除文件、提示错误并回退「前往下载」
- [ ] `download_url` 指向白名单外域名 → 拒绝下载并报错
- [ ] 旧 release（无 sha256）→ 横幅仅「前往下载」，不报错
- [ ] 断网 / 下载中断 → 不崩溃、可重试、不阻塞创作
- [ ] 强制更新（`is_mandatory`）→ 无关闭/不再提醒入口

**灰度**
- [ ] System 设置切换 `beta` → 立即检查命中 beta 版本；切回 `stable` 不再命中 beta
- [ ] 通道选择重启后保持（持久化生效）

**流水线**
- [ ] 打 tag → CI 产出安装包 + 正确 SHA256（与本地 `certutil -hashfile` / `shasum -a 256` 一致）
- [ ] 安装包版本号 = tag 版本（构建注入生效）
- [ ] 走完 §5.9.7 发布 SOP 一次，端到端命中更新

---

## 8. 风险与回滚

| 风险 | 缓解 / 回滚 |
| :--- | :--- |
| 无签名导致 SmartScreen 拦截、用户疑虑 | 下载页/横幅引导 + 公示 SHA256；阶段 9.5 上证书根治 |
| cloud 被改写 `download_url` 投毒 | host 白名单 + SHA256 双重校验；登记走人审 + 审计 |
| 安装包托管 CDN 故障 | 横幅校验失败回退「前往下载」；可临时下架 release |
| 迁移 003 出错 | `alembic downgrade -1` 删两列，不影响既有数据 |
| 版本号注入错误导致误判更新 | 统一纯 semver；发布前测试机验证；可下架 |
| CI sidecar 构建不稳定 | 先只跑 Windows；mac 后置；保留本地 `make:windows` 兜底 |
| 强制更新被滥用打断创作 | 约束 `is_mandatory` 仅用于严重安全/兼容问题；代码层不接入创作禁用 |

---

## 9. 落地顺序（实施 checklist）

1. **后端先行**：9.1 model+migration 003+schema → `alembic upgrade head` → Swagger 验 `/releases/check` 带新字段
2. **桌面更新器**：9.2 `updater.cjs` → 9.3 controller/preload/manifest → 9.4 横幅 → 本地用 mock release + 手算 sha256 跑通"下载→校验→安装"
3. **灰度**：9.5 通道持久化 + 设置页
4. **后台表单**：9.7 admin 加 sha256/file_size 字段
5. **流水线**：9.6 版本注入 + 产物 SHA256 + CI（先 Windows）
6. **联调**：走 §5.9.7 SOP，先 `beta` 后 `stable`，过 §7 验收
7. **交接**：补本目录 `PHASE9.md` 的"交接快照"（见 §10），更新 `plan_now.md` 阶段 9 状态

---

## 10. 交接模板（阶段结束填，遵循 plan_now.md 第 6 节）

```text
阶段编号：9
完成时间：（填）
负责人：（填）
1) 输入条件是否满足：阶段 8 后台可发布 release ✅ / 国内 OSS+CDN 就绪（填）
2) 范围内功能完成情况：自动更新 / SHA256 校验 / 灰度通道 / 版本注入 / CI 构建 / 发布 SOP
3) 验收结果：（按 §7 勾选）
4) 已知问题与风险：无证书 → SmartScreen 仍提示（待阶段 9.5）
5) 回滚方案：admin 下架 release；alembic downgrade -1；CI/更新模块均可独立关停
6) 线上地址与关键配置：OSS/CDN 域名、downloadHosts 白名单、各通道当前版本
7) 下一阶段启动条件：阶段 10 云同步依赖本阶段发布链路稳定
```

---

## 11. 需你提供 / 后续确认

1. **安装包 CDN 域名**：用于 `download_url` 与 `manifest.cloud.downloadHosts`（示例用了 `dl.wenshape.cn`，请确认实际域名/OSS 厂商）。
2. **CI 平台**：代码是否托管在 GitHub（决定 §9.6 是直接用 Actions 还是迁移到其他 CI）。
3. **版本号起点**：是否同意把 `manifest.product.version` 从 `0.1.0-phase7` 规范化为纯 semver（如 `0.1.0`），由 tag 注入正式版本。
4. **首发平台**：是否本阶段只做 Windows 自动更新闭环，macOS 留到阶段 9 后半段/9.5。

---

## 12. 扩展位：阶段 9.5（证书到位后）

- Windows OV/EV 代码签名（消除 SmartScreen）、macOS 签名 + 公证（notarization）
- 安装包**签名校验**叠加在 SHA256 之上（验证书链而非仅哈希）
- 可选切换 electron-updater 静默更新（复用本阶段 channel / 托管 / 版本注入）
- 证书为长周期采购项，**与阶段 10/11 并行推进**，不阻塞免费闭环里程碑 M9
