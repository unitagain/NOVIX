# 阶段 7：桌面联网接入 · 交接快照

> 本文档遵循 `plan_now.md` 第 6 节统一交接模板。
> 本阶段在 `WenShape-main/desktop` + `WenShape-main/frontend` 内完成，**不影响**已上线的 `wenshape-cloud` 与 `wenshape-web`。

---

## 模板填写

```text
阶段编号：7
完成时间：2026-04-25（待 e2e 验收后由负责人签字）
负责人：（填）

1) 输入条件是否满足：
   - https://api.wenshape.cn/health 200 ✅
   - https://wenshape.cn 可访问 ✅
   - 桌面端阶段 0-4 已交付 ✅

2) 本阶段范围内功能完成情况：
   - [x] 主进程 cloud 客户端（带超时、401 自动刷新、错误归一化）
   - [x] safeStorage 加密的 Token 持久化（访问/刷新 Token）
   - [x] 启动时会话恢复，自动调用 /auth/me 验活
   - [x] 渲染进程 CloudAuthProvider 与 useCloudAuth hook
   - [x] 标题栏右侧账户按钮 + 登录/注册 Dialog
   - [x] 版本检查（启动 2.5s + 每 6 小时一次，silent，不阻塞）
   - [x] 新版本提示横幅，可"本版本不再提醒"
   - [x] 登录成功后自动 device register（每用户一次，失败不阻塞）

3) 验收结果（通过/未通过）：
   - 待负责人执行下方"端到端验收清单"后填写

4) 已知问题与风险：
   - 版本检查依赖管理员通过 wenshape-cloud 发布 release；若云端无 release 则永远 has_update=false（符合预期）
   - 当前版本号写在 desktop/config/shell.manifest.json 的 product.version；阶段 10 接入 CI/CD 后会改为构建注入
   - 桌面登录后并未限制本地创作；"权益校验"仅作用于云服务，留给阶段 11（支付与订阅），桌面本地创作永久免费

5) 回滚方案：
   - 本阶段所有改动可通过开关位 WENSHAPE_CLOUD_DISABLE=1 关闭联网模块（保留入口但跳过 IPC handler 注册），见"应急开关"
   - 若发生大面积异常，可临时把 desktop/config/shell.manifest.json 的 cloud.apiBaseUrl 改为不可达地址（让所有 cloud 调用 fast-fail，但不影响本地创作）

6) 线上地址与关键配置：
   - 云端 API：https://api.wenshape.cn
   - 官网：https://wenshape.cn
   - Token 文件：<userData>/cache/auth.dat（safeStorage 加密）

7) 下一阶段启动条件是否满足：
   - 下一阶段为阶段 8（运营 / 监控后台），启动需要：本阶段 e2e 通过
   - 阶段 8 仅在确认 e2e 通过后开工
```

---

## 一、本阶段交付的代码模块

### 主进程（`desktop/main/`）

| 文件 | 角色 |
| :--- | :--- |
| `cloud-client.cjs` | 与 `wenshape-cloud` 的 HTTP 客户端，封装 register/login/refresh/logout/me/devices/releases。含 12s 超时、401 自动刷新、`CloudApiError` 归一化错误。 |
| `auth-store.cjs` | Token 持久化。Buffer 首字节标记位：`0x01` safeStorage 加密、`0x00` 明文回退。文件路径 `<userData>/cache/auth.dat`，权限 0o600。 |
| `desktop-info.cjs` | 设备信息构造：`device_name=os.hostname` / `platform=win32→windows`、`darwin→macos`、`linux` / `app_version` 取自 manifest。 |
| `cloud-controller.cjs` | 业务编排：rehydrate、login/logout、device register、版本检查循环、IPC handlers、auth-state 推送。 |

### 配置（`desktop/config/shell.manifest.json`）

新增字段：

```jsonc
{
  "product": { ..., "version": "0.1.0-phase7" },
  "cloud": {
    "apiBaseUrl": "https://api.wenshape.cn",
    "webBaseUrl": "https://wenshape.cn",
    "defaultChannel": "stable"
  }
}
```

### 预加载（`desktop/preload/index.cjs`）

`window.wenshapeDesktop.cloud.*` 暴露：

- `getStatus / getAppInfo`
- `login / register / logout / me`
- `listDevices / removeDevice`
- `requestPasswordReset / confirmPasswordReset`
- `checkVersion`
- `onAuthState(listener) / onUpdateAvailable(listener)`

### 渲染层（`frontend/src/`）

| 文件 | 角色 |
| :--- | :--- |
| `utils/cloud.js` | bridge 适配层 + 订阅 |
| `context/CloudAuthContext.jsx` | `<CloudAuthProvider>` + `useCloudAuth()` |
| `components/cloud/CloudLoginDialog.jsx` | 登录/注册一体对话框 |
| `components/cloud/CloudAccountButton.jsx` | TitleBar 右侧账户按钮 + 用户菜单 |
| `components/cloud/UpdateAvailableBanner.jsx` | 右下角新版本提示横幅，可关闭/不再提醒 |

挂载点：

- `main.jsx`：用 `<CloudAuthProvider>` 包裹整棵 App
- `App.jsx`：在 `<ErrorBoundary>` 内追加 `<UpdateAvailableBanner />`
- `components/ide/TitleBar.jsx`：右侧 AI 面板按钮前插入 `<CloudAccountButton />`

---

## 二、IPC 通道一览

| Channel | 方向 | Payload | 说明 |
| :--- | :--- | :--- | :--- |
| `wenshape:cloud-status` | invoke | — | 查询当前会话 + baseUrl + 上次版本检查 |
| `wenshape:cloud-app-info` | invoke | — | 当前版本号、deviceInfo |
| `wenshape:cloud-login` | invoke | `{ email, password }` | 登录并 persist token |
| `wenshape:cloud-register` | invoke | `{ email, password, nickname? }` | 注册（不会自动登录） |
| `wenshape:cloud-logout` | invoke | — | 服务端 + 本地双向清空 |
| `wenshape:cloud-me` | invoke | — | 强制刷新 user |
| `wenshape:cloud-list-devices` | invoke | — | 设备列表 |
| `wenshape:cloud-remove-device` | invoke | `id: string` | 远程登出某设备 |
| `wenshape:cloud-request-password-reset` | invoke | `email` | 触发邮箱验证码 |
| `wenshape:cloud-confirm-password-reset` | invoke | `{ email, code, new_password }` | 确认重置 |
| `wenshape:cloud-check-version` | invoke | `{ silent? }` | 主动版本检查 |
| `wenshape:cloud-auth-state` | event → renderer | `{ authenticated, user, baseUrl, updatedAt }` | 状态变化推送 |
| `wenshape:cloud-update-available` | event → renderer | `{ current, latest, checkedAt, silent }` | 发现新版本推送 |

所有 invoke 通道返回 `{ ok: true, data }` 或 `{ ok: false, error: { name, status, message } }`，便于渲染层统一处理。

---

## 三、端到端验收清单（必须执行）

### 准备

```bash
# 在桌面工程目录
cd WenShape-main/desktop
npm run dev                # 启动开发壳
```

> 也可直接安装阶段 7 之后构建的安装包，效果一致。

### 1. 离线优先（**最重要**）

- [x] 拔网/或将 `cloud.apiBaseUrl` 改为不可达 URL 启动
- [x] 桌面正常打开、能创建项目、能写作（**不阻塞**）
- [x] TitleBar 右侧仅显示 "登录" 按钮，**不弹错**

### 2. 注册 + 登录

- [x] 点击 TitleBar "登录" → 切换到"注册"标签 → 填邮箱+密码 → 注册成功后自动登录 → 头像出现
- [x] 重启应用 → 仍为登录状态（token 持久化生效）

### 3. 已存在账户登录

- [x] 用阶段 6 的 wenshape.cn 网站注册的账号登录 → 成功
- [x] 在官网"设备管理"应能看到本机条目（设备注册成功）

### 4. Token 刷新

- [x] 按下方“4A 手把手验收”把测试环境的 `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` 临时改成 `1`
- [x] 重新登录桌面端，等待旧 access token 自然过期
- [x] 触发任意需要 auth 的操作（推荐打开账户菜单或刷新账户信息）
- [x] 应自动调用 `/auth/refresh` 换新 token，并继续成功；**不应**死循环、不应弹错误、也不应自动掉线

#### 4A 手把手验收（推荐做法：缩短过期时间，而不是“手改数据库”）

> 说明：当前 `wenshape-cloud` 的 access token 是 JWT，默认过期时间 30 分钟。  
> 它不是数据库里一条可以直接改失效状态的记录，所以**不要再按旧文档去“改数据库让 access_token 失效”**。  
> 正确做法是：在测试环境把 access token 过期时间临时改短，然后观察桌面端是否自动 refresh。

1. 打开云端服务目录：

```bash
cd /opt/wenshape-cloud
```

2. 编辑 `.env`，找到或加入这一行：

```env
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1
```

3. 重启 cloud API：

```bash
docker compose up -d --force-recreate --no-deps api
```

4. 确认 API 正常：

```bash
curl https://api.wenshape.cn/health
```

预期看到：

```json
{"status":"ok","service":"wenshape-cloud"}
```

5. 完全退出桌面端，再重新启动桌面端。

6. 用一个正常账号重新登录桌面端。

7. 登录后**什么都不要做，等待 70-90 秒**。  
   因为 access token 现在只有 1 分钟有效期，等它自然过期即可。

8. 等待完成后，在桌面端触发一个需要登录态的动作，推荐任选其一：

- 点击右上角账户按钮，查看账户信息
- 如果界面里有“刷新账户信息”/`auth/me` 相关动作，就点它
- 如果有设备列表入口，就打开设备列表

9. 观察结果：

- 正确结果：界面正常、数据正常返回、不会报登录失效
- 正确结果：后端会自动走一次 `/auth/refresh`
- 错误结果：弹登录失败、反复请求、界面卡住、自动登出

10. 如需在服务器侧辅助观察，可开一个日志窗口：

```bash
cd /opt/wenshape-cloud
docker compose logs -f api
```

你重点看两类请求：

- `GET /api/v1/auth/me` 先返回 401（旧 access token 过期）
- 紧接着 `POST /api/v1/auth/refresh` 成功
- 然后新的 `GET /api/v1/auth/me` 或其他 auth 请求成功

11. 验收完成后，把 `.env` 改回正式值：

```env
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
```

然后再次重启 API：

```bash
docker compose up -d --force-recreate --no-deps api
```

### 5. 版本检查

- [x] 按下方“5A 手把手验收”向 cloud 数据库 `releases` 表插入一条 version 高于 `0.1.0-phase7` 的记录
- [x] 重启桌面 → 启动约 2.5s 后右下角弹出新版本横幅
- [x] 点“本版本不再提醒” → 再次重启后不再弹（除非数据库里再发布更高版本）
- [x] 强制更新（`is_mandatory=true`）时不显示关闭按钮

#### 5A 手把手验收（发布一条测试 release）

> 当前桌面端版本写在 `desktop/config/shell.manifest.json` 中，为 `0.1.0-phase7`。  
> 所以测试时，你插入的 release 版本必须比它更高，推荐直接用 `0.1.1`。

> ⚠️ **最容易踩的坑：`platform` / `arch` / `channel` 必须与桌面端实际发送值逐字相等。**  
> 云端 `/releases/check`（`app/api/releases.py`）是 `platform == ... AND arch == ... AND channel == ...` 精确匹配，任一字段对不上就返回 `has_update=false`、不弹任何横幅。  
> 桌面端在 **Windows 上发送的 `platform` 是 `windows`（不是 `win32`！见 `desktop/main/desktop-info.cjs` 的 `getPlatformLabel`）**，`arch` 通常是 `x64`，`channel` 是 `stable`。  
> 不确定时，在桌面端打开 DevTools 控制台执行，按返回值填下面的 SQL：
>
> ```js
> await window.wenshapeDesktop.cloud.getAppInfo()
> // 读 deviceInfo.platform / deviceInfo.arch
> ```

1. 登录服务器并进入 cloud 目录：

```bash
cd /opt/wenshape-cloud
```

2. 打开 PostgreSQL：

```bash
docker compose exec db psql -U wenshape -d wenshape
```

3. 先看一下有没有旧的测试 release：

```sql
SELECT id, version, platform, arch, channel, is_mandatory, published_at
FROM releases
ORDER BY published_at DESC
LIMIT 10;
```

4. 插入一条“普通更新”测试记录（Windows 桌面）：

```sql
INSERT INTO releases (
  version,
  platform,
  arch,
  channel,
  download_url,
  release_notes,
  is_mandatory
) VALUES (
  '0.1.1',
  'windows',
  'x64',
  'stable',
  'https://example.com/wenshape-0.1.1.exe',
  'Phase 7 test release',
  false
);
```

5. 再确认插入成功：

```sql
SELECT id, version, platform, arch, channel, is_mandatory, published_at
FROM releases
WHERE platform = 'windows' AND arch = 'x64' AND channel = 'stable'
ORDER BY published_at DESC
LIMIT 5;
```

6. 输入：

```sql
\q
```

退出 `psql`。

7. 完全退出桌面端，再重新启动桌面端。

8. 启动后等待约 3-5 秒。  
   预期现象：右下角出现“发现新版本”的横幅。  
   也可以不重启，直接点账户菜单里的“检查更新”立即触发一次（无更新会显示“已是最新版本”）。  
   若横幅没出现、且“检查更新”显示“已是最新版本”，几乎一定是 `platform/arch/channel` 没对上——回第 4 步用 `getAppInfo()` 的值重填记录。

9. 点击“本版本不再提醒”。

10. 再次完全退出桌面端并重新启动。

11. 预期现象：

- 如果没有发布更高版本，刚才这个 `0.1.1` 不应再提示
- 说明“本版本不再提醒”生效

12. 继续验证“强制更新”：

再次进入 `psql`，插入一条更高版本且强制更新的记录：

```sql
INSERT INTO releases (
  version,
  platform,
  arch,
  channel,
  download_url,
  release_notes,
  is_mandatory
) VALUES (
  '0.1.2',
  'windows',
  'x64',
  'stable',
  'https://example.com/wenshape-0.1.2.exe',
  'Phase 7 mandatory test release',
  true
);
```

13. 重启桌面端，再等待约 3-5 秒。

14. 预期现象：

- 右下角再次弹出更新横幅
- 因为 `is_mandatory=true`，横幅不应提供关闭入口

15. 验收完成后，如果你不想保留测试数据，可删除这两条测试记录：

```bash
docker compose exec db psql -U wenshape -d wenshape -c "DELETE FROM releases WHERE version IN ('0.1.1', '0.1.2');"
```

### 6. 登出

- [x] 点账户按钮 → 登出 → 头像消失，恢复"登录"按钮
- [x] 重启 → 仍为未登录状态

### 7. 异常路径

- [x] 邮箱密码错误 → Dialog 显示 "邮箱或密码错误"（或后端 detail）
- [x] 注册时密码 <8 → 当前实现应由前端直接显示“密码至少 8 位”，且不发请求；如果仍出现 “Invalid email or password”，按“7A 排查说明”记录为缺陷或确认是否运行了旧构建
- [x] 网络抖动（断网中点登录）→ 显示"无法连接到云端服务"，不闪退

#### 7A 排查说明（针对“密码 <8 仍显示 Invalid email or password”）

当前 `CloudLoginDialog.jsx` 的实现里，注册页有两层限制：

- 前端提交前显式判断 `password.length < 8`
- 注册密码框本身还带 `minLength={8}`

因此，**理论上不应该进入后端登录/注册失败的通用提示**。  
如果实际验收时你仍看到 `Invalid email or password`，优先按下面顺序排查：

1. 确认你点的是“注册”标签，不是“登录”标签
2. 确认你运行的是最新桌面端开发环境或最新构建，而不是旧安装包
3. 确认你提交时密码确实少于 8 位，而不是邮箱/密码都错误导致走了登录失败提示
4. 如果以上都确认无误，记录为前端缺陷：  
   “注册前置校验未按预期拦截，错误信息回落为通用登录错误”

### 8. 多设备协同

- [x] 在另一台机器上登录同账号 → 两台都在官网设备列表
- [x] 在官网"设备管理"远程移除当前设备
- [x] 当前桌面下次任意 cloud 调用应触发 401 → refresh 也 401 → 自动登出，UI 回到未登录态

---

## 四、应急开关

### 关闭整个 cloud 子系统（紧急回滚）

在 `desktop/main/index.cjs` 中找到：

```js
cloudController = createCloudController({ ... });
cloudController.registerIpc();
cloudController.rehydrate().catch(...);
cloudController.startVersionCheckLoop();
```

临时注释掉这四行即可。`window.wenshapeDesktop.cloud` 在 preload 仍存在，但所有 invoke 都会因为没有 handler 而 reject——前端 useCloudAuth 会静默回到"未登录"状态，本地创作完全不受影响。

### 临时改 cloud 地址

修改 `desktop/config/shell.manifest.json`：

```jsonc
"cloud": { "apiBaseUrl": "http://127.0.0.1:65535", ... }
```

或环境变量：

```bash
WENSHAPE_CLOUD_API_URL=http://127.0.0.1:65535 npm run dev
```

---

## 五、验收通过后的下一步

按 `plan_now.md` 第 5 节依赖关系（已按“免费安全闭环优先、收费在后”重排）：

1. **阶段 8 运营 / 监控后台**：版本发布管理 / 用户 / 设备 / 版本分布看板 / 操作审计（去订单）→ 直接承接本阶段
2. **阶段 9 签名 / 自动更新 / 发布流水线**：达成免费版「下载 → 使用 → 监控」安全闭环
3. **阶段 10 云同步与备份**：拿出可付费的云服务价值
4. **阶段 11 支付与订阅**：把云服务变现，需要本阶段稳定的“登录态” + 阶段 8 回补的订单运营

> 阶段 11 支付上线前**严禁**在桌面端引入“权益校验阻断”的代码路径；权益只作用于云服务，桌面本地创作永久免费。
