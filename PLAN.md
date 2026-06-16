# AutoAnime 项目计划

## 1. 项目定位

AutoAnime 是面向 NAS 的动画媒体自动化应用。当前阶段只专注一条主线：Mikan 追番、Nyaa/其他种子索引器补番、PikPak 云盘入库、NAS 本地同步观看。

核心原则：

- 云盘是长期媒体库，容量大，默认保留资源。
- 本地 NAS 是观看缓存，只保存正在看、想看、正在追的内容。
- Jellyfin 只扫描本地真实文件，不再挂载云盘目录。
- 取消同步只清理本地文件和本地 NFO，不删除云盘内容。
- 云盘 provider 需要抽象化；当前只实现 PikPak，后续再接其他云盘。
- 同步优先走 rclone PikPak backend 的命令行能力，不要求用户自己写同步命令；PikPak API 作为 fallback。
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
Mikan RSS -> RSS 候选暂存 -> Mikan 页面匹配 bgm.tv subject ID -> Bangumi 元数据刷新
-> 正式番剧入库与合并 -> 字幕组/分辨率/语言自动选择 -> PikPak 离线下载
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
  Bangumi / 标题归一化 / 身份合并

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

### 队列表规则

自动流程必须按阶段表单向流转，不能把未完成数据直接写入正式库。

- 新增一种操作，就新增一张对应任务表或队列表。
- 每个处理器只读取自己的任务表，只更新自己的状态和明确的下一阶段表。
- 失败只停在当前任务表，写 `failed` 和 `last_error`，不能继续入库。
- 成功并且字段齐全后，才允许写入下一阶段。
- 正式 `series/releases` 只保存已经通过元数据门槛的数据。
- RSS 扫描只写 `rss_candidates` 和后续匹配任务，不直接写正式番剧库。
- Mikan RSS 里的 `bangumiId` 是 Mikan 自己的番组 ID，不等于 Bangumi.tv subject ID，不能直接写入 `series.bangumi_id`。
- Mikan 匹配由 `mikan_match_tasks` 独立维护：先读取 RSS 条目页里的 `/Home/Bangumi/{id}`，再读取番组页里的 `https://bgm.tv/subject/{id}`，拿到 subject ID 后才写入 `metadata_tasks`。
- 元数据任务成功后才创建正式 `series/releases`，再进入 PikPak 入库队列。
- 云盘入库、PikPak 状态同步、云盘资源登记、本地状态同步、本地同步、NFO 生成、全量审计等操作都必须独立建表，不能复用别的队列状态。
- 到下一阶段的数据必须完整；例如 `metadata_tasks` 失败时不能写 `series/releases`，`mikan_match_tasks` 失败时不能写 `metadata_tasks`。
- 用户点击“扫描全部”只是按顺序立即触发各队列处理器；日常运行仍由各队列自动触发或定时触发。
- 队列失败不长期占住 worker。失败任务写入 `last_error` 和 `retry_after` 后回到 `pending`，冷却结束后自动重试；worker 继续处理其他可执行任务。
- PikPak 入库拆成三段任务表：
  - `download_tasks`: 只负责提交离线任务到 PikPak，默认通过 `rclone backend addurl`。
  - `cloud_poll_tasks`: 只负责轮询 PikPak 离线任务状态；rclone 模式下没有任务 ID 时，通过扫描目标目录判断是否已完成。
  - `cloud_asset_tasks`: 只负责把完成的 PikPak 任务登记为 `cloud_assets`。

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
- rclone

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

启动或更新：

```sh
cd /volume1/docker/autoanime
docker compose down --remove-orphans
docker compose up -d --build
```

说明：`down --remove-orphans` 只停止并删除当前 compose 管理的容器和孤儿容器，不删除挂载的 `./data` 目录和媒体目录。不要使用 `-v`，否则会删除 Docker volume 数据。

端口：

```txt
32888:8080
```

## 4. 已实现能力

### 后端

- FastAPI JSON API 已创建。
- SQLite 保存配置、番剧、集数、RSS 发布、下载任务和日志。
- Mikan RSS 扫描已实现，扫描结果先写入 `rss_candidates`。
- Mikan RSS 不直接提供 Bangumi.tv subject ID；当前通过 `mikan_match_tasks` 读取 Mikan 条目页/番组页解析 `bgm.tv/subject/{id}`。
- RSS 扫描不做隐藏式暂存；发现的候选先进入候选/待确认状态，完整入库需要稳定元数据身份。
- 旧 release 如果在重扫时解析到正确 Bangumi ID，会迁回正确番剧；原错误空壳会自动隐藏。
- 标题指纹归一化已实现：
  - 处理常见简繁差异。
  - 去除常见发布标签、标点、空格、集数后缀和分辨率标签。
- 相同 `bangumi_id` 的重复番剧会在数据库迁移和 Bangumi 元数据刷新后合并。
- PikPak 离线提交已接入。
- 默认云盘执行方式改为 rclone：
  - `rclone backend addurl` 提交 magnet/torrent 到 PikPak。
  - `rclone lsjson` 扫描云盘目录，发现已完成文件。
  - `rclone copyto` 同步云盘文件到 NAS 本地。
  - 如果 `/data/rclone/rclone.conf` 中没有默认 remote，会用现有 PikPak 用户名和密码自动生成 `type=pikpak` 配置。
  - Docker 镜像通过 rclone 官方 linux-amd64 zip 内置新版 rclone，避免使用官方 install.sh；构建支持 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY` build args。
  - Python PikPak API 保留为 fallback。
- PikPak 提交默认不预先初始化 captcha；只有返回验证码相关错误时才初始化 captcha 并重试一次，避免额外 API 调用触发限流。
- PikPak 提交、PikPak 状态轮询、云盘资源登记已拆成独立任务表。
- PikPak 限流时会给待提交任务统一写入冷却时间，避免继续撞接口。
- 提交成功但只拿到 `file_id`、没有离线任务 ID 时，会直接进入云盘资源登记，避免“云盘已有但本地不同步”。
- Mikan 匹配、元数据、本地同步失败后会写 `retry_after` 回到 `pending`，不再因为一个失败项卡住后续任务。
- 本地同步任务支持小并发处理，并在失败后冷却重试。
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
- 同步默认通过 rclone `copyto` 下载到本地；API 模式下通过 PikPak API 获取下载链接并直接下载。
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
- `download_tasks`: PikPak 离线任务提交队列
- `cloud_poll_tasks`: PikPak 状态轮询队列
- `cloud_asset_tasks`: 云盘资源登记队列
- `cloud_assets`: 已在云盘中的资源，包括本程序任务完成和云盘库扫描导入。
- `sync_rules`: 每个番剧的本地同步意图和本地根目录。
- `local_assets`: NAS 本地真实文件记录。
- `sync_tasks`: 云盘到本地的同步任务。
- `logs`: 操作日志

目标表需要重构为更通用的媒体库模型：

- `media_items`: 番剧、电影、剧集等媒体主体
- `episodes`: 单集记录，番剧和剧集共用
- `releases`: 来源发现的候选发布
- `metadata_matches`: Bangumi 搜索候选和人工确认结果
- `cloud_providers`: 云盘 provider 配置，当前先支持 PikPak
- `cloud_assets`: 已在云盘中的文件或目录
- `cloud_tasks`: 下载、转存、导入到云盘的任务
- `sync_rules`: 哪些媒体需要同步到本地
- `local_assets`: NAS 本地真实文件记录
- `sync_tasks`: 云盘到本地的同步任务
- `logs`: 操作日志

关键身份字段：

- `media_items.bangumi_id`
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
bangumi:{id} > title:{normalized_title}
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
   - RSS 扫描后先按稳定 ID 归并，再尝试 Bangumi 匹配。
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
   - metadata adapter：Bangumi。

10. 任务边界必须清楚。
   - RSS 定时扫描允许全量拉 RSS feed，但只处理新增或变更发布。
   - RSS 发现条目先是“候选”，不等于正式入库。
   - 正式入库标准：必须绑定 Bangumi，且标题、年份、季、集数等关键元数据足够明确。
   - 入库后才检查云盘资产；没有云盘资产才加入 PikPak 入库队列。
   - 云盘资产存在后才检查本地资产；没有本地资产且同步意图开启才加入本地同步队列。
   - 全量检查云盘/本地所有状态只能由手动按钮触发，不能作为普通定时任务。

11. UI 不使用“流水线”概念，改为控制台 + 队列状态。
   - 默认页面是控制台，番剧库放第二个入口，问题处理合并到控制台 Tabs。
   - 控制台展示 RSS、元数据、合并、PikPak 入库、云盘资源登记、本地同步等队列状态。
   - 用户只处理配置、冲突、元数据确认和是否同步到本地。
   - 云盘入库和本地同步不作为日常推进按钮出现。
   - 扫描全部会依次触发 RSS、元数据、云盘入库、PikPak 状态、本地同步，并显示进度，运行中禁止重复点击。
   - 手动扫描云盘库、刷新 PikPak 状态、重试失败任务属于维护/补救操作，放在控制台维护 Tab。
   - 番剧详情页展示 RSS 发布、云盘任务、云盘资源、本地资源，方便追踪误识别来源。
   - dashboard 必须是只读状态视图，不能在刷新时恢复、隐藏或合并数据，避免“看一眼界面就改变数据库”。
   - NFO 只在本地同步完成后生成；RSS 扫描阶段不生成本地媒体元数据。
   - RSS 没有提供 Bangumi ID 时，不能自动用标题搜索并绑定第一个 Bangumi 结果，必须进入待处理。

## 8. 已知缺口

### 高优先级

- 现有 `download_tasks` 仍是旧云盘下载任务表，已经补充 `cloud_assets`，但还没有完全重命名为 `cloud_tasks`。
- 扫描阶段仍会生成一份旧 NFO，后续应彻底移除扫描后生成 NFO 的行为，只保留同步完成后本地 NFO。
- 已建立 `rss_candidates`、`mikan_match_tasks`、`metadata_tasks` 三段入库前队列；后续还需要继续把云盘状态、本地状态、NFO 等拆成更细任务表。
- 真实 PikPak 端到端提交、云盘目录扫描和云盘文件直链下载尚未在 NAS 上完整验证。
- PikPak 任务 ID 和 file ID 提取逻辑需要对照真实响应确认。
- 云端重命名依赖有效 `file_id`，缺失时只能先标记完成并等待后续补拿。
- API 错误响应还不够规范，后台任务也缺少统一操作状态。
- 自动选择和自动下载已先按现有配置修正，但后续仍应把 `auto_download_unique` 重命名为更明确的 `auto_cloud_download_enabled`。
- 补全按钮仍需要搜索源支持；没有搜索源前应继续明确显示不可用。

### 元数据

- Bangumi 自动搜索目前只是第一结果启发式。
- 集标题和放送日期还没有完整填充。
- 需要支持 Bangumi 搜索候选选择和手动确认。
- Mikan RSS `bangumiId` 已优先用于稳定合并；Bangumi 自动搜索仍需增加候选确认，避免启发式第一结果误配。
- 没有 Bangumi 稳定身份的条目不应进入正式番剧库，应进入待确认候选。

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
- 日历需要 Bangumi 放送数据。

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

### P0.5: 控制台 UI 收敛

目标：界面围绕队列控制台，而不是“流水线”或让用户手动点下载/同步。

- 侧边栏收敛为：
  - 控制台
  - 番剧库
  - 设置
- 控制台展示 RSS、元数据、合并、PikPak 入库、云盘资源登记、本地同步等队列状态。
- 问题处理合并到控制台 Tab，展示元数据缺失、字幕组冲突、分辨率冲突、语言冲突、云盘失败、本地同步失败。
- 日常界面不显示“存入云盘”“处理同步”“处理云盘任务”。
- 扫描全部、刷新 PikPak 状态、扫描云盘库、重试失败只放在控制台维护 Tab。
- 扫描全部显示当前阶段进度，运行中不能重复点击。
- 操作日志读取服务器日志文件 `/data/autoanime.log` 尾部，同时展示 operations。
- 番剧详情页展示 RSS 发布、PikPak 任务、云盘资源、本地资源，方便追踪误识别来源。

状态：已完成第二版。后续需要把控制台队列迁移为真实独立队列表，并支持每个队列独立轮询间隔。

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

状态：部分完成。`cloud_assets`、`sync_rules`、`local_assets`、`sync_tasks` 已建立；PikPak 状态轮询已拆到 `cloud_poll_tasks`，云盘资源登记已拆到 `cloud_asset_tasks`；云盘库扫描已接入；旧 `download_tasks` 仍待重命名或迁移为 `cloud_tasks`。

### P3: 本地同步执行器

目标：把云盘资源复制到 NAS 本地真实目录。

- 配置本地库目录，默认 `/media/pikpak-anime`。
- 同步执行方式优先使用云盘 provider API。
- 支持同步意图开关：未入云盘也能先开启。
- 支持“取消同步”，只删除本地文件和本地 NFO。
- 同步成功后生成 NFO。
- 同步成功后可触发 Jellyfin 扫描。

状态：部分完成。同步意图开关、取消同步、同步后 NFO 已实现；新增同步意图调和，云盘已有资源可自动排同步；同步失败会冷却后自动重试且不阻塞后续任务；真实 PikPak 文件下载需要 NAS 环境验证。

### P4: 元数据优先合并

目标：扫描时尽早建立稳定身份。

- Mikan RSS 的 `bangumiId` 只作为 Mikan 番组页线索，不作为稳定媒体身份。
- RSS 扫描先进入候选/待确认队列，Mikan 匹配拿到 Bangumi.tv subject ID 且元数据完整后才正式入库。
- 增加匹配候选 UI。
- 建立 `identity_key`。
- 按 Bangumi ID 合并重复媒体。

状态：部分完成。候选暂存队列和 Mikan 页面匹配队列已接入；正式入库门槛已改为必须经过 Bangumi 元数据任务。候选确认 UI 和完整 `identity_key` 仍待实现。

修正：RSS 没有提供 Bangumi.tv subject ID 时，不再自动按标题搜索 Bangumi 并绑定第一个结果，避免误识别为无关番剧；Mikan 来源先通过 Mikan 页面解析 subject ID，失败时停在 `mikan_match_tasks`。

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
- 语言优先级按包含关系保守匹配：例如 `简体` 可匹配 `简日`，但如果同时存在多个简体复合候选且没有更精确规则，不自动任选，进入待处理。

### P6: Mikan 追番闭环

目标：让 Mikan RSS 新集自动进入 PikPak，并按同步意图落到本地。

- Mikan RSS 周期扫描，默认 60 分钟。
- Mikan 页面解析 `bgm.tv/subject/{id}` 稳定识别番剧。
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
deploy-nas.sh
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

当前 `package-clean.bat` 输出干净源码目录：

```txt
build\AutoAnime-clean
```

默认不再生成 zip，避免 Windows 或上传进程占用压缩包导致无法覆盖。

## 11. 维护规则

- 不要回滚用户或 NAS 上已有的特定修改。
- 保持 NAS 友好和 Docker 友好。
- 优先通过 source/cloud/metadata adapter 扩展能力。
- 当前云盘 provider 是 PikPak，但不要把业务模型写死为 PikPak。
- Jellyfin 永远只面对本地真实文件。
- 控制前端依赖数量。
- 每次修改架构、数据模型、任务流程、部署方式或关键 UI 行为后，同步更新本文件。
