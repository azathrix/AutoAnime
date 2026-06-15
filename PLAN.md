# AutoAnime 项目计划

## 1. 项目定位

AutoAnime 是面向 NAS 的媒体自动化应用。目标不是只做 RSS 下载器，而是做一个“云盘长期媒体库 + 本地观看缓存”的管理入口。

核心原则：

- 云盘是长期媒体库，容量大，默认保留资源。
- 本地 NAS 是观看缓存，只保存正在看、想看、正在追的内容。
- Jellyfin 只扫描本地真实文件，不再挂载云盘目录。
- 取消同步只清理本地文件和本地 NFO，不删除云盘内容。
- 云盘 provider 需要抽象化；当前先实现 PikPak，后续可能接入其他云盘。
- 同步优先走云盘 provider API 下载到本地，不要求用户配置同步命令。

目标工作流：

```txt
RSS/搜索/导入/收藏
-> 元数据匹配与合并
-> 云盘库入库或离线下载
-> 云盘任务轮询
-> 按“想看/追更”规则同步到本地
-> 本地生成 NFO
-> Jellyfin 扫描本地库
```

当前第一阶段仍以 Mikan RSS + PikPak 为主：

```txt
Mikan RSS -> 番剧聚合 -> Bangumi/TMDB 匹配 -> 字幕组/分辨率/语言自动选择
-> PikPak 离线下载 -> 云盘状态轮询 -> 手动或追更自动同步到 NAS 本地
-> 本地 NFO -> Jellyfin 本地库
```

后续应继续支持：

- 本季番追更
- 老番补全和导入
- 电影导入
- 欧美剧导入和追更
- 用户收藏内容管理
- 多索引器
- 多下载器
- 多云盘 provider
- Jellyfin 刷新和同步

## 2. 目标架构

### 媒体分层

```txt
source adapters
  Mikan RSS / 搜索源 / 手动导入 / 收藏导入 / 未来其他来源

metadata layer
  Bangumi / TMDB / 标题归一化 / 身份合并

cloud library
  provider: pikpak / future providers
  cloud assets / cloud download tasks / cloud paths / file ids

watch cache
  想看 / 正在追 / 手动同步 / 自动追更同步

local library
  NAS 本地真实文件
  /volume1/Assets3/Media
  容器内挂载为 /media
  PikPak 默认同步到 /media/pikpak-anime

jellyfin
  只扫描本地真实文件
```

### 当前代码结构

```txt
autoanime/
  backend/
    app/
      main.py             FastAPI 应用、JSON API、静态前端托管
      db.py               SQLite schema、配置、日志、迁移
      scanner.py          RSS 扫描、发布聚合、下载队列、PikPak 任务处理
      parser.py           RSS 标题解析：标题、字幕组、分辨率、集数
      pikpak_service.py   PikPakAPI 封装、token 登录
      metadata.py         Bangumi 元数据、NFO 生成
      library.py          Jellyfin 友好的目录和文件名渲染
      config.py           数据目录和默认配置
    requirements.txt
  frontend/
    src/
      App.vue             Vue 管理台
      api.js              Axios API 客户端
      style.css           应用样式
      main.js             前端入口
    package.json
    vite.config.js        构建产物输出到 backend/frontend_dist
  Dockerfile
  docker-compose.yml
  README.md
  PLAN.md
```

### 后端栈

- Python 3.12
- FastAPI
- SQLite
- APScheduler
- httpx
- feedparser
- PikPakAPI

### 前端栈

- Vue 3
- Vite
- Element Plus
- vuedraggable
- axios

## 3. 本地库和部署

容器内默认数据路径：

```txt
APP_DATA_DIR=/data
/data/autoanime.db
```

Jellyfin 媒体库应指向 NAS 本地真实文件目录，不再指向云盘挂载目录。

NAS 推荐本地库路径：

```txt
/volume1/Assets3/Media/pikpak-anime
```

容器建议挂载整个媒体根目录：

```yaml
volumes:
  - ./data:/data
  - /volume1/Assets3/Media:/media
```

UI 中本地媒体库目录设置为：

```txt
本地媒体库目录: /media/pikpak-anime
```

NFO 生成到本地媒体文件旁边：

```txt
/media/pikpak-anime/{title_cn} ({year}) [bangumi-{bangumi_id}]/Season 01/*.nfo
```

部署路径：

```sh
/volume1/docker/autoanime
```

启动：

```sh
docker compose up -d --build
```

端口：

```txt
32888:8080
```

## 4. 已实现能力

### 后端

- FastAPI JSON API 已创建。
- SQLite 保存配置、番剧、集数、RSS 发布、下载任务和日志。
- Mikan RSS 扫描、番剧聚合、发布入库已实现。
- 标题指纹归一化已实现：
  - 处理常见简繁差异。
  - 去除常见发布标签、标点、空格、集数后缀和分辨率标签。
- 相同 `bangumi_id` 的重复番剧会在数据库迁移和 Bangumi 元数据刷新后合并。
- PikPak 离线提交已接入。
- PikPak 提交前会初始化 captcha；如果返回 `Verification code is invalid`，会刷新 captcha 并重试一次。
- 下载任务支持提交、轮询、失败重试。
- 新扫描到且没有 `metadata_source` 的番剧会尝试刷新 Bangumi 元数据。
- 当前代码会在扫描后生成 NFO；后续需要改为“同步到本地后生成 NFO”。
- 发布语言解析已接入，支持简体、繁体、日语、中文和常见 CHS/CHT/BIG5/GB/JP 标记。
- 自动选择已扩展为字幕组、分辨率、语言三维过滤。
- 自动下载到云盘必须过滤后唯一；不唯一时会跳过并写日志原因。
- 已新增云盘资源、本地同步规则、本地资源、同步任务数据模型。
- PikPak 任务完成后会登记为云盘资源。
- 已支持手动“同步到本地”和“取消同步”。
- 同步成功后会在本地媒体库生成 NFO。
- 取消同步会删除本地媒体文件和单集 NFO，但保留云盘资源。
- 同步已改为通过 PikPak API 获取下载链接并直接下载到本地。
- 默认自动扫描间隔为 60 分钟，同时保留手动扫描和手动刷新。

### 前端

- Vue + Element Plus 管理台已搭建。
- 侧边栏导航、仪表盘、番剧库、下载队列、日历占位、设置中心已实现。
- 番剧详情抽屉已实现。
- 字幕组优先级和分辨率优先级支持拖拽排序。
- 仪表盘支持自动刷新：
  - 默认 5 秒。
  - 可切换手动/自动。
  - 设置页激活或番剧抽屉打开时暂停刷新，避免覆盖正在编辑的数据。
- 仪表盘展示活跃队列：
  - `/api/dashboard` 返回 `task_counts` 和 `active_tasks`。
  - UI 展示 pending、running、submitted、failed 等状态。
- `POST /api/tasks/retry-failed` 已接入 UI，用于重试失败任务。

### 构建和验证

已完成的本地验证：

- `npm install`
- `npm run build`
- `python -m compileall backend/app`
- 本地 uvicorn 访问 `/` 返回 200
- 本地 uvicorn 访问 `/api/settings` 返回 200

已知构建提示：

- `npm audit` 报告 3 个 high severity 漏洞。
- 未执行 `npm audit fix --force`，因为可能引入破坏性依赖升级。
- Vite 提示主 JS chunk 超过 500 kB，当前可接受，后续可通过动态导入拆分。

## 5. 现有 API

```txt
GET  /api/dashboard
GET  /api/settings
PUT  /api/settings
GET  /api/series/{series_id}
PUT  /api/series/{series_id}
POST /api/scan
POST /api/tasks/process
POST /api/tasks/poll
POST /api/tasks/retry-failed
POST /api/series/{series_id}/download
POST /api/series/{series_id}/metadata
POST /api/series/{series_id}/nfo
POST /api/releases/{release_id}/download
```

这些 API 仍是旧语义：`download` 基本等于提交 PikPak 离线任务。后续需要把 API 改名和拆分为：

```txt
POST /api/cloud/download
POST /api/cloud/tasks/process
POST /api/cloud/tasks/poll
POST /api/sync/start
POST /api/sync/cancel
POST /api/sync/tasks/process
POST /api/local/nfo
POST /api/jellyfin/scan
```

## 6. 目标数据模型

当前表：

- `settings`: 全局 key/value 配置
- `series`: 聚合后的番剧/剧集条目
- `episodes`: 单集记录
- `releases`: RSS 发布记录
- `download_tasks`: PikPak 提交和轮询任务
- `logs`: 操作日志

目标表需要重构为更通用的媒体库模型：

- `media_items`: 番剧、电影、剧集等媒体主体
- `episodes`: 单集记录，番剧和剧集共用
- `releases`: 来源发现的候选发布
- `metadata_matches`: Bangumi/TMDB 搜索候选和人工确认结果
- `cloud_providers`: 云盘 provider 配置，当前先支持 PikPak
- `cloud_assets`: 已在云盘中的文件或目录
- `cloud_tasks`: 下载、转存、导入到云盘的任务
- `sync_rules`: 哪些媒体需要同步到本地
- `local_assets`: NAS 本地真实文件记录
- `sync_tasks`: 云盘到本地的同步任务
- `logs`: 操作日志

关键身份字段：

- `media_items.bangumi_id`
- `media_items.tmdb_id`
- `media_items.identity_key`
- `media_items.media_type`
- `cloud_assets.provider`
- `cloud_assets.provider_file_id`
- `cloud_assets.cloud_path`
- `local_assets.local_path`
- `sync_rules.sync_enabled`
- `sync_rules.auto_sync_following`

身份合并优先级：

```txt
bangumi:{id} > tmdb:{id} > title:{normalized_title}
```

## 7. 设计约束

1. “下载”和“同步”必须分开。
   - 下载：把资源放入云盘长期库。
   - 同步：把云盘资源复制到 NAS 本地观看缓存。
   - 取消同步只删除本地，不删除云盘。

2. Jellyfin 只扫描本地真实文件。
   - 不再挂载云盘给 Jellyfin。
   - NAS 媒体根目录使用 `/volume1/Assets3/Media`。
   - 容器内挂载为 `/media`。
   - PikPak 默认本地目录为 `/media/pikpak-anime`，其他云盘可指定到 `/media/baidu-anime` 等目录。

3. 云盘 provider 不写死。
   - 当前 provider 是 PikPak。
   - 后续应通过 adapter 支持其他云盘。
   - 业务层使用 `cloud_assets` 和 `cloud_tasks`，不要直接依赖 PikPak 字段。

4. 自动下载到云盘默认开启，但必须能选出唯一发布。
   - 如果有多个字幕组、分辨率或语言候选，必须按优先级过滤。
   - 过滤后仍不唯一时，不自动下载，标记为“需要选择”。

5. 自动选择维度包括：
   - 字幕组优先级
   - 分辨率优先级
   - 语言优先级，例如 `简体 > 繁体 > 日语`

6. 元数据应尽早匹配。
   - RSS 扫描后先尝试 Bangumi/TMDB 匹配。
   - 根据元数据 ID 合并重复条目。
   - 手动确认的元数据优先级最高。

7. NFO 在本地同步完成后生成。
   - NFO 跟随本地媒体目录。
   - 云盘库不负责 Jellyfin 直接扫描。

8. PikPak 认证优先使用：

```txt
access_token + refresh_token
```

   账号密码只作为备用方式。注意：当前 `DEFAULT_SETTINGS` 里的初始值仍是 `password`，后续需要改成 `token`，或在首次配置体验中明确推荐 token。

9. 后续扩展应优先增加 adapter。
   - source adapter：RSS、搜索、导入、收藏。
   - cloud adapter：PikPak、未来其他云盘。
   - metadata adapter：Bangumi、TMDB。

## 8. 已知缺口

### 高优先级

- 现有 `download_tasks` 仍是旧云盘下载任务表，已经补充 `cloud_assets`，但还没有完全重命名为 `cloud_tasks`。
- 扫描阶段仍会生成一份旧 NFO，后续应彻底移除扫描后生成 NFO 的行为，只保留同步完成后本地 NFO。
- 真实 PikPak 端到端提交尚未用真实 token 验证。
- PikPak 任务 ID 和 file ID 提取逻辑需要对照真实响应确认。
- 云端重命名依赖有效 `file_id`，缺失时只能先标记完成并等待后续补拿。
- API 错误响应还不够规范，后台任务也缺少统一操作状态。
- 自动选择和自动下载已先按现有配置修正，但后续仍应把 `auto_download_unique` 重命名为更明确的 `auto_cloud_download_enabled`。
- 补全按钮仍需要搜索源支持；没有搜索源前应继续明确显示不可用。

### 元数据

- Bangumi 自动搜索目前只是第一结果启发式。
- TMDB 尚未实现。
- 集标题和放送日期还没有完整填充。
- 需要支持 Bangumi/TMDB 搜索候选选择和手动确认。
- RSS 扫描时应先匹配元数据，再按稳定 ID 合并。

### 语言和发布选择

- 语言解析和语言优先级已接入。
- 过滤后不唯一已阻止自动下载，并写日志；后续可在番剧卡片上直接展示跳过原因。

### 同步和本地库

- 云盘到本地的同步任务模型已接入。
- 已支持手动同步到本地。
- 需要支持追更自动同步到本地。
- 已支持取消同步并删除本地文件，但保留云盘资源。
- 已支持通过 PikPak API 下载到本地。
- 需要在 NAS 上用真实 PikPak file_id 验证下载链接和大文件稳定性。
- Jellyfin API 刷新尚未实现。

### 补全和导入

- 老番补全还只是计划模块。
- 当前 RSS 不能可靠补历史集，需要可搜索来源。
- “补全全部”配置已存储，但没有搜索源前不能真正拉取缺失历史发布。
- 电影、欧美剧、收藏导入仍是占位方向。

### UI 和产品

- 暂无认证。
- 暂无路由级浏览器 URL，当前 Vue 使用内部状态。
- 番剧卡片需要展示云盘状态、本地同步状态、追更状态。
- 日历需要 Bangumi/TMDB 放送数据。

## 9. 下一阶段路线图

### P0: 修复按钮反馈和自动下载语义

目标：先解决“点了没反应”和自动下载条件不清的问题。

- 所有操作 API 返回明确结果：
  - queued
  - skipped
  - failed
  - reason
- UI 显示跳过原因。
- 补全在没有搜索源时显示“当前不可用”，不要假装执行。
- 修复前端 `dashboard.active_tasks` 默认值。
- 明确当前“下载”按钮语义为“下载到云盘”。
- 自动下载只在候选唯一时执行。

状态：已完成。

### P1: 拆分云盘下载和本地同步

目标：建立新状态模型。

- 新增或迁移到 `cloud_assets`、`cloud_tasks`。
- 新增 `sync_rules`、`local_assets`、`sync_tasks`。
- 保留旧表迁移兼容。
- API 拆分为 cloud 和 sync 两组。
- UI 增加状态：
  - 未入云盘
  - 云盘下载中
  - 已在云盘
  - 需要同步
  - 同步中
  - 已在本地
  - 本地已删除
  - 失败

状态：部分完成。`cloud_assets`、`sync_rules`、`local_assets`、`sync_tasks` 已建立；旧 `download_tasks` 仍待重命名或迁移为 `cloud_tasks`。

### P2: 本地同步执行器

目标：把云盘资源复制到 NAS 本地真实目录。

- 配置本地库目录，默认 `/media/pikpak-anime`。
- 同步执行方式优先使用云盘 provider API。
- 支持手动“同步到本地”。
- 支持“取消同步”，只删除本地文件和本地 NFO。
- 同步成功后生成 NFO。
- 同步成功后可触发 Jellyfin 扫描。

状态：部分完成。手动同步、取消同步、同步后 NFO 已实现；Jellyfin 扫描暂缓，真实 PikPak 文件下载需要 NAS 环境验证。

### P3: 元数据优先合并

目标：扫描时尽早建立稳定身份。

- RSS 扫描后先尝试 Bangumi 匹配。
- 增加 TMDB 匹配。
- 增加匹配候选 UI。
- 建立 `identity_key`。
- 按 Bangumi/TMDB ID 合并重复媒体。

### P4: 语言过滤和三维自动选择

目标：让自动下载可靠。

- 增加语言解析。
- 增加语言优先级设置，默认：

```txt
简体
繁体
日语
```

- 自动选择顺序：
  1. 字幕组优先级
  2. 分辨率优先级
  3. 语言优先级
  4. 唯一性检查
- 如果最终不唯一，进入“需要选择”状态。

### P5: 追更同步

目标：让正在追的内容自动落到本地。

- 媒体条目增加“追更同步”开关。
- 新集下载到云盘完成后，如果该条目开启追更同步，则自动同步到本地。
- 同步完成后生成 NFO。
- 同步完成后触发 Jellyfin 扫描。

### P6: 补全、导入和收藏

目标：让云盘库成为长期影院库。

- 增加可搜索 indexer adapter。
- 支持老番补全到云盘。
- 支持电影导入到云盘。
- 支持欧美剧导入到云盘。
- 支持云盘已有资源扫描入库。
- 支持收藏条目只入云盘、不占本地空间。

### P7: Jellyfin 集成和前端完善

目标：提升日常使用体验。

- 增加 Jellyfin URL 和 API Key 配置。
- 支持手动触发 Jellyfin 扫描。
- 支持同步完成后自动触发扫描。
- 增加浏览器路由。
- 接入真实日历数据。
- 增加更好的空状态和首轮配置引导。
- 按需拆分前端 chunk。

## 10. 上传和交接规则

不要上传生成目录、运行时目录或大文件到 NAS：

```txt
frontend/node_modules
backend/frontend_dist
backend/app/__pycache__
data
test-data
*.zip
```

只上传源码和部署文件：

```txt
backend/
frontend/src/
frontend/index.html
frontend/package.json
frontend/package-lock.json
frontend/vite.config.js
Dockerfile
docker-compose.yml
README.md
PLAN.md
.dockerignore
.gitignore
```

原因：

- `frontend/node_modules` 文件数量巨大，复制到 NAS 很慢。
- Docker build 会在 NAS 上重新安装依赖并构建前端。
- `data/` 是运行时状态，不应被源码包覆盖。

交接给另一个 AI 或上传 NAS 测试前，创建干净源码包，并排除以上路径。

## 11. 维护规则

- 不要回滚用户或 NAS 上已有的特定修改。
- 保持 NAS 友好和 Docker 友好。
- 优先通过 source/cloud/metadata adapter 扩展能力。
- 当前云盘 provider 是 PikPak，但不要把业务模型写死为 PikPak。
- Jellyfin 永远只面对本地真实文件。
- 控制前端依赖数量。
- 每次修改架构、数据模型、任务流程、部署方式或关键 UI 行为后，同步更新本文件。
