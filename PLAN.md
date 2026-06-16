# AutoAnime 项目计划

## 1. 项目定位

AutoAnime 是面向 NAS 的动画媒体自动化应用。当前阶段只专注一条主线：Mikan 追番、Nyaa/其他种子索引器补番、PikPak 云盘入库、NAS 本地同步观看。

核心原则：

- 云盘是长期媒体库，容量大，默认保留资源。
- 本地 NAS 是观看缓存，只保存正在看、想看、正在追的内容。
- Jellyfin 只扫描本地真实文件，不再挂载云盘目录。
- 取消同步只清理本地文件和本地 NFO，不删除云盘内容。
- 云盘 provider 需要抽象化；当前只实现 PikPak，后续再接其他云盘。
- 同步优先走云盘 provider API 下载到本地，不要求用户配置同步命令。
- 当前不把电影、美剧、Jellyfin API 集成作为主线，先把追番/补番体验做稳。

目标工作流：

```txt
Mikan RSS / Nyaa 搜索 / 其他补番索引器
-> 元数据匹配与合并
-> 云盘库入库或离线下载
-> 云盘任务轮询
-> 按“想看/追更”规则同步到本地
-> 本地生成 NFO
-> Jellyfin 扫描本地库
```

当前第一阶段仍以 Mikan RSS + PikPak 为主：

```txt
Mikan RSS -> 读取 Mikan bangumiId -> 番剧聚合与旧数据修复 -> Bangumi/TMDB 匹配
-> 字幕组/分辨率/语言自动选择 -> PikPak 离线下载
-> 云盘状态轮询 -> 按同步意图自动同步到 NAS 本地
-> 本地 NFO -> Jellyfin 本地库
```

后续主线应继续支持：

- 本季番追更
- 老番补全和云盘导入
- 多补番索引器，例如 Nyaa、动漫花园/末日动漫、ACG.RIP、蜜柑历史资源等
- PikPak 云盘状态识别和本地同步
- 明确的任务状态和失败原因

## 2. 目标架构

### 媒体分层

```txt
source adapters
  Mikan RSS / Nyaa / 动漫花园或其他补番种子索引器 / 手动种子或 magnet

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
  只扫描本地真实文件；当前不接 Jellyfin API
```

### 当前代码结构

```txt
autoanime/
  backend/
    app/
      main.py             FastAPI 应用、JSON API、静态前端托管
      db.py               SQLite schema、配置、日志、迁移
      scanner.py          RSS 扫描、发布聚合、云盘队列、PikPak 任务处理
      parser.py           RSS 标题解析：标题、字幕组、分辨率、集数、语言、Bangumi ID
      pikpak_service.py   PikPakAPI 封装、token 登录、云盘目录扫描
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
- Mikan RSS 的 `bangumiId` 已按 Bangumi.tv subject ID 读取，并优先作为稳定身份。
- RSS 扫描不做隐藏式暂存；发现的候选应先进入候选/待确认状态，完整入库需要稳定元数据身份。
- 旧 release 如果在重扫时解析到正确 Bangumi ID，会迁回正确番剧；原错误空壳会自动隐藏。
- 标题指纹归一化已实现：
  - 处理常见简繁差异。
  - 去除常见发布标签、标点、空格、集数后缀和分辨率标签。
- 相同 `bangumi_id` 的重复番剧会在数据库迁移和 Bangumi 元数据刷新后合并。
- PikPak 离线提交已接入。
- PikPak 提交前会初始化 captcha；如果返回 `Verification code is invalid`，会刷新 captcha 并重试一次。
- 下载任务支持提交、轮询、失败重试。
- 新扫描到且没有 `metadata_source` 的番剧会尝试刷新 Bangumi 元数据。
- 当前代码仍保留扫描后生成一份旧 NFO；后续需要彻底移除，只保留“同步到本地后生成 NFO”。
- 发布语言解析已接入，支持简体、繁体、日语、中文和常见 CHS/CHT/BIG5/GB/JP 标记。
- 自动选择已扩展为字幕组、分辨率、语言三维过滤。
- 自动下载到云盘必须过滤后唯一；不唯一时会跳过并写日志原因。
- 已新增云盘资源、本地同步规则、本地资源、同步任务数据模型。
- PikPak 任务完成后会登记为云盘资源。
- 已支持同步意图开关：未入云盘时也可以先开启同步，后续云盘资源入库后自动同步。
- 已支持“取消同步”，会关闭后续同步并清理本地文件。
- 同步成功后会在本地媒体库生成 NFO。
- 取消同步会删除本地媒体文件和单集 NFO，但保留云盘资源。
- 同步已改为通过 PikPak API 获取下载链接并直接下载到本地。
- 已支持扫描配置的 PikPak 云盘库根目录，把明确匹配的云盘已有视频文件登记到 `cloud_assets`。
- 云盘库扫描优先按路径中的 `bangumi-xxxx` 匹配，其次按已知标题归一化匹配；匹配不到会跳过，避免错归。
- 默认自动扫描间隔为 60 分钟，同时保留手动扫描和手动刷新。
- 定时任务只跑增量链路：RSS 扫描、PikPak 任务轮询、同步意图调和、本地同步任务处理。
- PikPak 云盘库全量扫描只通过手动按钮触发，不放进定时任务。

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
- 番剧详情抽屉中“同步到本地”已改为本地同步开关，不再要求必须先显示为已入云盘才能开启。
- 番剧详情支持“删除误识别”，只删除应用记录和本地同步记录，不删除云盘文件。
- 任务页已增加“扫描云盘库”入口。
- 番剧库筛选已把“已下载”改为“已入云盘”和“已同步”。

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
POST /api/cloud/scan
POST /api/series/{series_id}/download
POST /api/series/{series_id}/metadata
POST /api/series/{series_id}/nfo
POST /api/releases/{release_id}/download
POST /api/series/{series_id}/sync
POST /api/series/{series_id}/sync/cancel
POST /api/sync/tasks/process
DELETE /api/series/{series_id}
```

这些 API 仍是旧语义：`download` 基本等于提交 PikPak 离线任务。后续需要把 API 改名和拆分为：

```txt
POST /api/cloud/download
POST /api/cloud/tasks/process
POST /api/cloud/tasks/poll
POST /api/cloud/scan
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
- `cloud_assets`: 已在云盘中的资源，包括本程序任务完成和云盘库扫描导入。
- `sync_rules`: 每个番剧的本地同步意图和本地根目录。
- `local_assets`: NAS 本地真实文件记录。
- `sync_tasks`: 云盘到本地的同步任务。
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
   - Mikan RSS 的 `bangumiId` 是 Bangumi.tv subject ID，应优先使用。
   - RSS 扫描后先按稳定 ID 归并，再尝试 Bangumi/TMDB 匹配。
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

10. 任务边界必须清楚。
   - RSS 定时扫描允许全量拉 RSS feed，但只处理新增或变更发布。
   - RSS 发现条目先是“候选”，不等于正式入库。
   - 正式入库标准：必须绑定 Bangumi 或 TMDB，且标题、年份、季、集数等关键元数据足够明确。
   - 入库后才检查云盘资产；没有云盘资产才加入 PikPak 入库队列。
   - 云盘资产存在后才检查本地资产；没有本地资产且同步意图开启才加入本地同步队列。
   - 全量检查云盘/本地所有状态只能由手动按钮触发，不能作为普通定时任务。

11. UI 不能把自动流程做成一堆手动按钮。
   - 主界面展示 Mikan -> PikPak -> 本地 的流水线状态。
   - 用户只处理配置、冲突、元数据确认和是否同步到本地。
   - 云盘入库和本地同步不作为日常操作按钮出现。
   - 手动扫描 RSS、扫描云盘库、重试失败任务都属于维护/补救操作，应放在问题处理或维护区域。
   - 番剧详情页只保留规则、同步意图和危险/维护操作，不放“下载/同步”推进按钮。
   - dashboard 必须是只读状态视图，不能在刷新时恢复、隐藏或合并数据，避免“看一眼界面就改变数据库”。
   - NFO 只在本地同步完成后生成；RSS 扫描阶段不生成本地媒体元数据。

## 8. 已知缺口

### 高优先级

- 现有 `download_tasks` 仍是旧云盘下载任务表，已经补充 `cloud_assets`，但还没有完全重命名为 `cloud_tasks`。
- 扫描阶段仍会生成一份旧 NFO，后续应彻底移除扫描后生成 NFO 的行为，只保留同步完成后本地 NFO。
- 目前还没有真正的“候选暂存队列”和“正式入库标准”表，RSS 发现仍会直接写入 `series/releases`；下一阶段需要拆分。
- 真实 PikPak 端到端提交、云盘目录扫描和云盘文件直链下载尚未在 NAS 上完整验证。
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
- Mikan RSS `bangumiId` 已优先用于稳定合并；Bangumi 自动搜索仍需增加候选确认，避免启发式第一结果误配。
- 没有 Bangumi/TMDB 稳定身份的条目不应进入正式番剧库，应进入待确认候选。

### 语言和发布选择

- 语言解析和语言优先级已接入。
- 过滤后不唯一已阻止自动下载，并写日志；后续可在番剧卡片上直接展示跳过原因。

### 同步和本地库

- 云盘到本地的同步任务模型已接入。
- 已支持同步意图开关，开启后新云盘资源会自动同步到本地。
- 已支持取消同步并删除本地文件，但保留云盘资源。
- 已支持通过 PikPak API 下载到本地。
- 已支持云盘库扫描，把明确匹配的 PikPak 已有文件登记为云盘资源。
- 需要在 NAS 上用真实 PikPak file_id 验证目录扫描、下载链接和大文件稳定性。
- Jellyfin API 刷新尚未实现。

### 补全和导入

- 老番补全还只是计划模块。
- 当前 RSS 不能可靠补历史集，需要可搜索来源。
- “补全全部”配置已存储，但没有搜索源前不能真正拉取缺失历史发布。
- 电影、欧美剧、收藏导入仍是占位方向。

### UI 和产品

- 暂无认证。
- 暂无路由级浏览器 URL，当前 Vue 使用内部状态。
- 番剧卡片已展示云盘数量和本地数量；后续仍需展示更明确的阻塞原因和自动选择状态。
- 日历需要 Bangumi/TMDB 放送数据。

## 9. 下一阶段路线图

当前路线图只围绕“追番/补番 -> PikPak -> 本地同步”。电影、美剧、收藏导入和 Jellyfin API 暂停，避免主线过散。

### P0: 可观测任务和数据安全

目标：先解决“点了没反应”和“看起来数据被清空”的问题。

- 所有长任务写入 `operations`：
  - running
  - completed
  - failed
  - message
- 控制台展示最近操作。
- 刷新/扫描/同步按钮返回 operation_id。
- 不再用扫描中的 hidden 状态隐藏正常条目。
- 启动和 dashboard 刷新时恢复“有资源但被隐藏”的番剧。
- “删除误识别”改为软隐藏，不删除 releases/tasks/cloud/local 记录。
- 系统设置增加只读诊断：
  - 当前 DB 路径
  - DB 文件大小
  - `/data` 是否可写
  - 关键表计数
- 新库从零开始时，先通过诊断确认写入路径和表计数，不再做旧库恢复型操作。
- 前端保存设置和后台操作必须显示失败原因，不能静默失败。

状态：部分完成。`operations`、系统诊断、设置保存错误提示已接入；后续还需要把所有旧按钮统一成更清晰的流程入口。

### P0.5: 流水线 UI 收敛

目标：界面围绕自动流程，而不是让用户手动点下载/同步。

- 侧边栏收敛为：
  - 自动流水线
  - 番剧库
  - 问题处理
  - 设置
- 控制台展示 Mikan RSS、元数据确认、PikPak 入库、本地同步四段状态。
- 问题处理页展示元数据缺失、字幕组冲突、分辨率冲突、云盘失败、本地同步失败。
- 日常界面不显示“存入云盘”“处理同步”“处理云盘任务”。
- 手动扫描 RSS、刷新 PikPak 状态、扫描云盘库、重试失败只放在维护操作区。

状态：已完成第一版。后续需要把“问题处理”接入更精确的后端候选/冲突表。

### P1: 修复自动入云盘语义

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

状态：部分完成。按钮反馈已通过 operations 改进；自动入云盘仍需把跳过原因直接展示到番剧卡片。

### P2: 拆分云盘入库和本地同步

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

状态：部分完成。`cloud_assets`、`sync_rules`、`local_assets`、`sync_tasks` 已建立；云盘库扫描已接入；旧 `download_tasks` 仍待重命名或迁移为 `cloud_tasks`。

### P3: 本地同步执行器

目标：把云盘资源复制到 NAS 本地真实目录。

- 配置本地库目录，默认 `/media/pikpak-anime`。
- 同步执行方式优先使用云盘 provider API。
- 支持同步意图开关：未入云盘也能先开启。
- 支持“取消同步”，只删除本地文件和本地 NFO。
- 同步成功后生成 NFO。
- 同步成功后可触发 Jellyfin 扫描。

状态：部分完成。同步意图开关、取消同步、同步后 NFO 已实现；新增同步意图调和，云盘已有资源可自动排同步；真实 PikPak 文件下载需要 NAS 环境验证。

### P4: 元数据优先合并

目标：扫描时尽早建立稳定身份。

- Mikan RSS `bangumiId` 已作为稳定 ID 使用。
- RSS 扫描应先进入候选/待确认队列，元数据完整后才正式入库。
- 增加 TMDB 匹配。
- 增加匹配候选 UI。
- 建立 `identity_key`。
- 按 Bangumi/TMDB ID 合并重复媒体。

状态：部分完成。Mikan `bangumiId` 稳定合并、旧错归 release 修复已实现；候选暂存队列、正式入库门槛、TMDB、候选 UI 和完整 `identity_key` 仍待实现。

### P5: 语言过滤和三维自动选择

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
- 语言解析识别 `简日`、`繁日`、`简繁`、`简英`、`繁英` 等复合标签。
- 语言优先级按包含关系保守匹配：例如 `简体` 可匹配 `简日`，但如果同时存在多个简体复合候选且没有更精确规则，不自动任选，进入问题处理。

### P6: Mikan 追番闭环

目标：让 Mikan RSS 新集自动进入 PikPak，并按同步意图落到本地。

- Mikan RSS 周期扫描，默认 60 分钟。
- Mikan `bangumiId` 稳定识别番剧。
- RSS 扫描只处理新增/变更发布，不触发云盘库全量扫描。
- 唯一候选自动入 PikPak。
- 多字幕组/分辨率/语言时按优先级选择，不唯一则进入需要处理。
- 新集入云盘后自动调和本地同步。
- 同步完成后生成 NFO。

状态：部分完成。RSS 扫描、自动入云盘、同步意图和 NFO 已有；需要完善“需要处理”列表和真实 NAS 验证。

### P7: Nyaa/其他索引器补番

目标：为老番补全提供可搜索来源，不再假装 RSS 能补历史集。

- 增加 indexer adapter：
  - Nyaa
  - 动漫花园或镜像
  - ACG.RIP
  - 其他 AutoBangumi 已验证可用来源
- 按番剧、季、集数搜索缺失集。
- 搜索结果进入同一套字幕组/分辨率/语言选择规则。
- 补番只负责入 PikPak；本地是否同步由同步意图决定。
- 支持手动 magnet/torrent 导入作为兜底。

状态：未开始。

### P8: 云盘已有资源导入

目标：让 PikPak 里已经存在的动画能进入管理和本地同步流程。

- 扫描配置的 PikPak 云盘根目录。
- 优先使用目录名里的 `bangumi-xxxx`。
- 其次用标题归一化保守匹配。
- 匹配不到的进入“待确认导入”，不要自动归错。
- 已入云盘后自动调和同步任务。

状态：部分完成。保守扫描已实现；待确认导入 UI 未实现。

### P9: 全量状态审计

目标：提供一个手动按钮，完整检查所有番剧的云盘和本地状态，用于纠偏，不参与定时任务。

- 检查每部正式入库番剧是否有云盘资产。
- 检查每个云盘资产是否仍能获取 provider file id 和下载链接。
- 检查每个本地资产文件是否仍存在。
- 标记用户绕过系统删除的本地文件。
- 对缺失项只生成待处理任务，不直接全量下载。

状态：未开始。

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
