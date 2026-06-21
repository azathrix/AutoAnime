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
- 新番追更域与番剧库域分离：
  - 新番域只处理“当季追更/正在追/想看同步”
  - 番剧库域处理“补番/归档/老番导入/云盘已有资源导入”
  - 两个域共享后半段任务能力，但不能继续混成一个列表语义

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

### 业务域分层

后续业务模型不能再只围绕一张 `series` 表展开，而要明确分成两个上层域：

```txt
seasonal tracker（新番域）
  Mikan RSS 订阅
  当季追更
  自动选集
  自动入云盘
  自动同步到本地

library（番剧库域）
  老番补番
  Nyaa/其他索引器搜索
  云盘已有资源导入
  本地导入
  完结归档
```

两个域共享：

- 候选池
- 元数据
- 季/篇章识别
- 云盘任务
- 本地同步
- NFO

但展示层和上层实体必须区分：

- 新番域强调“本季在追的具体条目”
- 番剧库强调“作品归档、季度/篇章层级、长期保存”

因此后续至少要补出：

- `works`：作品根，例如“咒术回战”“石纪元”
- `entries`：具体季度/篇章/部分/OVA 条目
- `seasonal_entries`：新番域中的追更条目或订阅关系
- `library_entries`：番剧库中的补番/归档条目

约束：

- 相同 `root_name`、不同 `display_name` 的条目，在新番域必须显示为不同 item。
- 篇章与季度在“具体可管理条目”维度上同级，不能因为归一化目录名而直接合并成一条。
- UI 可以做“作品 -> 季度/篇章条目”的二层展开，但任务执行仍以具体条目为单位。

### Pipeline + Processor 重构目标

当前队列已经拆成多张任务表，但任务之间的流向仍写死在处理器和 `main.py` 的 handler 里。下一阶段要把“处理器做什么”和“处理完去哪”拆开：

```txt
pipeline
  定义业务流程、领域、步骤顺序、分支规则

processor
  只处理一种任务，不关心上层业务目标

task table
  保存某个 processor 的待处理、运行中、失败、完成状态

transition
  根据 processor result 决定写入哪些后续任务表
```

目标原则：

- 任务处理器只做单一动作，例如拉 RSS、解析 Mikan、刷新元数据、选资源、提交云盘、轮询云盘、登记云盘资产、生成同步计划、本地同步、生成 NFO。
- 流水线负责定义“这个任务成功/跳过/冲突/失败后进入哪个步骤”，处理器内部不再写死下一步。
- 每个任务都必须带 `pipeline_id` 和 `run_id`，同一个处理器可以被多个流水线复用。
- 新番流水线、补番流水线、云盘导入流水线可以并行运行，并共享后半段处理器。
- 分支要由配置驱动，而不是写在 if/else 里。例如：
  - `success -> metadata`
  - `conflict -> manual_resolution`
  - `skipped_existing_cloud -> sync_plan`
  - `failed_retryable -> same_step_after_retry`
  - `failed_terminal -> issue_queue`
- 处理器执行完成后返回结构化结果：`status / reason / stats / next_payloads / retry_after / events`。
- UI 不再把“队列”和“定时任务、日志、维护”混在一起；控制台主视图应以流水线 tab + processor 列表为主。

建议新增核心表：

- `pipelines`
  - `id`
  - `key`
  - `name`
  - `domain_kind`：`seasonal` / `library` / `cloud_import` / `maintenance`
  - `enabled`
  - `created_at`
  - `updated_at`
- `pipeline_steps`
  - `id`
  - `pipeline_id`
  - `step_key`
  - `processor_key`
  - `sort_order`
  - `enabled`
  - `max_concurrency`
  - `debounce_seconds`
  - `retry_policy_json`
  - `config_json`
- `pipeline_transitions`
  - `id`
  - `pipeline_id`
  - `from_step_key`
  - `result_status`
  - `to_step_key`
  - `condition_json`
  - `payload_map_json`
- `pipeline_runs`
  - `id`
  - `pipeline_id`
  - `trigger_source`
  - `status`
  - `progress`
  - `message`
  - `stats_json`
  - `started_at`
  - `finished_at`
- `processor_tasks`
  - `id`
  - `pipeline_id`
  - `run_id`
  - `step_id`
  - `processor_key`
  - `domain_kind`
  - `subject_type`
  - `subject_id`
  - `payload_json`
  - `status`
  - `attempts`
  - `retry_after`
  - `progress`
  - `progress_text`
  - `last_error`
  - `created_at`
  - `updated_at`
- `processor_events`
  - `id`
  - `task_id`
  - `level`
  - `event_key`
  - `message`
  - `data_json`
  - `created_at`

第一阶段可以先保留现有专用任务表，但要新增一层 pipeline runtime 适配器：

- 旧表仍作为实际状态表，避免一次性大迁移。
- 新增 `pipeline_runs / processor_events` 用于可观测性。
- 每个现有 handler 先包装成 processor adapter，统一返回结构化结果。
- 后续再把多个旧任务表迁移为通用 `processor_tasks`，或保留高频任务的专用表作为性能优化。

新番流水线建议：

```txt
seasonal_mikan_tracking
-> rss_fetch
-> rss_candidate_persist
-> mikan_match
-> bangumi_metadata
-> seasonal_merge
-> release_selection
-> season_backfill
-> cloud_presence
-> cloud_submit
-> cloud_poll
-> cloud_asset_register
-> sync_plan
-> local_sync
-> nfo_generate
-> local_presence
```

补番流水线建议：

```txt
library_backfill
-> source_search
-> candidate_persist
-> bangumi_metadata
-> library_merge
-> release_selection
-> cloud_presence
-> cloud_submit
-> cloud_poll
-> cloud_asset_register
-> sync_plan
-> local_sync
-> nfo_generate
```

云盘导入流水线建议：

```txt
cloud_import
-> cloud_scan
-> cloud_identity_match
-> bangumi_metadata
-> library_merge
-> cloud_asset_register
-> sync_plan
```

日志和事件策略：

- 任务状态以 `processor_tasks` 或专用任务表为准。
- 日志只做补充审计，不再作为判断系统是否卡住的主要来源。
- 每个任务 item 必须有 `progress_text / reason / last_error / retry_after`。
- `operations` 只用于手动动作或 pipeline run 的总状态，不再代表所有后台队列。
- 服务器文件日志保留，前端日志页只负责搜索、清空、下载或查看最近错误。

性能策略：

- 当前单容器 NAS 部署优先使用 SQLite WAL + 短事务 + 索引 + 批量 claim，不优先引入 Redis。
- Redis 适合后续多 worker、多容器、实时推送、短期锁、任务 broker，但不应该保存正式业务状态。
- 任务表压力主要来自已完成任务、事件和日志，必须通过 `cleanup` 做保留策略：
  - completed 任务保留短窗口或固定数量
  - failed 任务长期保留直到用户处理或自动恢复
  - processor_events 按时间裁剪
  - 大批量源候选按 pipeline run 归档
- 所有任务表必须至少有索引：
  - `(status, retry_after, updated_at)`
  - `(pipeline_id, status)`
  - `(run_id)`
  - 业务幂等键，例如 `candidate_id / release_id / cloud_asset_id`
- worker claim 必须短事务完成：先 `UPDATE ... SET status='running' ... WHERE status in (...) LIMIT N`，提交后再做网络 IO。
- 网络 IO、rclone 调用、文件复制不能持有数据库连接。
- 日志写入和任务状态更新不能嵌在一个长事务里。

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
- 后续应补一张 `mikan_bangumi_id -> bangumi_subject_id` 映射缓存表，避免同一番组每次扫描都重复抓取 Mikan 页面。
- 元数据任务成功后才创建正式 `series/releases`，再进入 PikPak 入库队列。
- 云盘入库、PikPak 状态同步、云盘资源登记、本地状态同步、本地同步、NFO 生成、全量审计等操作都必须独立建表，不能复用别的队列状态。
- 到下一阶段的数据必须完整；例如 `metadata_tasks` 失败时不能写 `series/releases`，`mikan_match_tasks` 失败时不能写 `metadata_tasks`。
- 用户点击“扫描全部”只负责触发 RSS 源头扫描；日常运行仍由各队列自动触发或定时触发。
- 队列失败不长期占住 worker。失败任务写入 `last_error` 和 `retry_after` 后回到 `pending`，冷却结束后自动重试；worker 继续处理其他可执行任务。
- PikPak 入库拆成三段任务表：
  - `download_tasks`: 只负责提交离线任务到 PikPak，默认通过 `rclone backend addurl`。
  - `cloud_poll_tasks`: 只负责轮询 PikPak 离线任务状态；rclone 模式下没有任务 ID 时，通过扫描目标目录判断是否已完成。
  - `cloud_asset_tasks`: 只负责把完成的 PikPak 任务登记为 `cloud_assets`。

### 调度模型修订

当前项目后续不再继续朝“单次扫描串行跑完整条流水线”演进，而是明确切换为“触发式队列 + 独立 worker + 定时任务独立维护”的模型。

核心原则：

- `扫描全部` 只是一个触发入口，不是主流程编排器。
- `扫描全部` 触发的实际动作只需要是 RSS 源头扫描；后续 `Mikan -> 元数据 -> 选集 -> 补全 -> 云盘 -> 同步 -> NFO` 全部由任务触发自动推进。
- UI 和 operation 文案已开始按这个模型统一，不再宣称“扫描全部”会手工串行推进整条链路。
- 每种任务表由自己的 worker 处理，收到新任务后自动触发，不依赖“回头再跑一遍总流程”。
- 上游任务成功后，只负责写下一阶段任务；是否执行由下一阶段 worker 自己决定。
- 任务之间是 DAG 式单向依赖，不是单线程流水线。
- 允许多个队列并行推进；同一队列内部允许设置 worker 并发数。
- 每个 task 必须继续细拆，尽量做到“一个 task 只专心干一件事”，不要把多种职责揉在一个处理器里。
- 所有队列都支持 10 秒防抖触发：
  - 新任务进入时，启动一个 10 秒倒计时。
  - 倒计时期间如果继续有新任务进入，则重置 10 秒。
  - 到时后批量处理当前可执行任务。
  - worker 正在处理时新任务进入，只加入下一批，不打断当前批次。
- 失败任务不能阻塞队列：
- 单项失败后立刻记录 `last_error`、`retry_after`。
- worker 继续处理后续任务。
- 冷却结束后该任务自动重新进入可执行状态。

当前已开始按该模型落地：

- 新增 `queue_bridge` 作为轻量桥接层。
- `main.py` 负责注册真实的 `trigger_queue` 实现。
- `scanner.py` / `sync_service.py` 在写入下游任务表后，直接请求触发对应队列。
- 现阶段已经接通的自动触发链包括：
  - `rss_candidates -> mikan_match`
  - `mikan_match -> metadata`
  - `metadata -> selection/backfill`
  - `selection -> cloud_presence`
  - `cloud_presence -> download_enqueue`
  - `download_enqueue -> download`
  - `download -> cloud_poll/cloud_asset`
  - `cloud_asset -> sync_plan/sync`
  - `sync -> nfo/local_presence`
- `扫描全部` 与 `定时 RSS 扫描` 已收窄为源头触发，不再手工串行触发所有后续阶段。
- 已开始收口旧的手工触发 API：
  - `单条入云盘`
  - `单条本地同步`
  - `单条补全`
  - `单条 release 入云盘`
  这些入口现在主要负责写任务；后续推进交由任务桥接触发。
- 控制台状态表达已开始按“排障视角”调整：
  - 队列卡片增加 `state_detail`
  - 队列详情增加统一的 `display_title` / `display_reason`
  - 同步等任务显示 `progress/progress_text`
  - 区分 `待处理 / 聚合中 / 待重跑 / 等待重试 / 处理中 / 已完成`
- 已新增统一队列触发入口：
  - `/api/queues/{queue_name}/trigger`
  - 控制台“立即执行该队列”不再借道其他业务 API，而是直接触发对应队列
  - `rss` 作为特殊入口，仍映射到完整扫描
- handler 内部也在逐步收口：
  - 只优先保留“本队列还有剩余任务时自我续跑”的兜底
  - 跨队列推进尽量交回任务桥接，避免再次退回到串行调度
- 控制台与运行时队列名已开始对齐：
  - 展示 key 允许保留业务语义，如 `cloud`
  - 运行时统一映射到真实队列名，如 `download`
  - 触发接口、状态判断、防抖状态统一走 canonical queue key
- `sync_plan` 已开始从虚拟阶段改为正式任务表：
  - 新增 `sync_plan_tasks`
  - 同步计划不再只靠 `ready_count + reconcile_sync_intents()` 的虚拟估算
  - 同步相关入口已收口到 `sync_plan_tasks -> process_sync_plan_tasks() -> sync_tasks`
- 原本每分钟全局扫一遍队列的 `queue_dispatch` 已降级为恢复/补漏调度：
  - 主链依赖“入队即触发”
  - `queue_dispatch` 只负责应用重启、异常中断后的挂起队列恢复
- 旧架构残留已开始剔除：
  - `series_state_tasks` 已不再参与当前运行链路
  - 现阶段保留旧表本身只为兼容已有数据库文件，不再作为主架构的一部分
- 队列触发桥已调整为事件循环下一拍触发：
  - `request_queue_trigger()` 不再在当前写库流程中立即写调度状态。
  - 这样 RSS 候选写入、任务入队等事务提交后，再触发后续 worker，降低同一时刻抢写 `scheduled_jobs` 的概率。
- 扫描入口已补更细的状态回写：
  - 请求 RSS 源
  - RSS 获取完成
  - 写入候选 `N/X`
  - 补排 Mikan 匹配任务
  - 这些状态通过 `operations.message` 暴露给控制台进度条，不再只显示“正在扫描 RSS 源”。

### 当前阶段已落地

- SQLite 连接已补 `WAL + busy_timeout`，降低扫描与后台队列并发时的 `database is locked` 风险。
- 已新增 Pipeline/Processor 第一阶段数据底座：
  - `pipelines`
  - `pipeline_steps`
  - `pipeline_transitions`
  - `pipeline_runs`
  - `processor_tasks`
  - `processor_events`
- 已 seed 首批 3 条流水线：
  - `seasonal_mikan_tracking`
  - `library_backfill`
  - `cloud_import`
- 已给现有正式任务表补 `pipeline_id / run_id / processor_key` 字段和常用索引，后续可逐个处理器迁移。
- 手动“扫描全部”现在会创建 `seasonal_mikan_tracking` 的 `pipeline_run`，并同步记录进度和完成/失败结果。
- `/api/dashboard` 与 `/api/pipelines` 已开始暴露流水线总览，供后续控制台按流水线 tab 展示。
- 已补旧库任务表唯一索引迁移，修复 `metadata_tasks(candidate_id)` 等表在旧结构下触发 `ON CONFLICT` 报错的问题。
- “清除所有数据”现在会先取消运行中的 operation 与队列防抖任务，再清库并重置运行代际与调度状态，避免清空后立刻被旧任务回写。
- 控制台已支持“活跃 / 全部”队列视图；默认可只看有待处理、运行中、失败、等待重试的队列。
- 左侧导航已补“最近同步新番”独立栏位，用于快速打开最近 7 天有本地同步记录的新番条目。
- 打包脚本已修复版本文件写入问题，后续每次打包都会更新 `frontend/src/version.js` 并同步到 `build/AutoAnime-clean`。
- 数据库访问层已开始收口到 `backend/app/database.py`：
  - 统一由 SQLAlchemy engine 建连
  - 业务模块不再直接各自 `sqlite3.connect`
  - 现阶段先保留原生 SQL，后续再逐步把高频读写迁到更明确的 session/repository 边界
- 控制台去掉顶部重复队列卡片和内嵌 4 周日历，只保留主队列工作台作为日常观察入口。
- 新增独立“更新日历”页面：
  - 左侧导航提供入口。
  - 只展示新番域更新。
  - 顶部使用周选择器，只能按周查看。
  - 主体为 7 列周视图，每天展示更新卡片，卡片显示作品、条目、集数和已更新/已同步状态。

### 当前阶段待继续

- 新番页与番剧库页继续分层：
  - 新番页恢复以“追番条目”为主的卡片视图
  - 番剧库页继续做“作品 -> 季度/篇章条目”二层卡片/展开布局
- 最近更新日历继续只服务新番域，不混入番剧库；已从控制台侧栏独立为“更新日历”页面。
- 后续继续补队列详情里的失败重试入口、任务说明优化、番剧库归档与导入入口。

反对继续使用的旧模式：

- 不再使用“RSS -> Mikan -> 元数据 -> 选集 -> 补全 -> 云盘 -> 同步”这种一次性串到底的扫描式流程作为主模型。
- 不再依赖单个 `scheduled_queue_tick()` 顺序扫描所有队列表来推进系统。
- 不再把“队列状态”与“定时任务状态”混在一个视图或一个逻辑里。

推荐的新触发链路：

```txt
RSS 定时任务 / 手动扫描
-> rss_candidates
-> mikan_match_tasks
-> metadata_tasks
-> series/releases 正式入库
-> selection_tasks
-> backfill_tasks
-> cloud_presence_tasks
-> download_enqueue_tasks
-> download_tasks
-> cloud_poll_tasks
-> cloud_asset_tasks
-> sync_plan_tasks
-> sync_tasks
-> nfo_tasks
```

补全链路必须是自我闭环的：

```txt
backfill_tasks
-> 新增 rss_candidates
-> 新增 mikan_match_tasks
-> 新增 metadata_tasks
-> 新增 selection_tasks
-> 新增 download_tasks
```

这意味着整季补全产生的新候选，不允许等待下一次“扫描全部”才继续处理；它们必须自动触发下游 worker。

### Task 细拆原则

后续所有任务表统一遵循“单职责 task”原则。每个 task 只做一件事，不允许一个 task 同时承担“判断 + 提交 + 轮询 + 入库 + 同步”多种动作。

建议拆分方向：

- `rss_fetch_tasks`
  - 只负责拉 RSS feed
- `rss_candidate_tasks`
  - 只负责把 RSS item 写入候选
- `mikan_match_tasks`
  - 只负责 `Episode -> Mikan Bangumi -> Bangumi subject`
- `metadata_tasks`
  - 只负责拉 Bangumi 元数据
- `library_merge_tasks`
  - 只负责把通过门槛的数据正式写入 `series/releases`
- `selection_tasks`
  - 只负责根据规则选中候选发布
- `backfill_tasks`
  - 只负责根据 Mikan Bangumi 页面补整季候选
- `cloud_presence_tasks`
  - 只负责检查云盘是否已存在该集，避免重复提交
- `download_enqueue_tasks`
  - 只负责判断是否需要提交云盘任务
- `download_tasks`
  - 只负责向云盘提交下载
- `cloud_poll_tasks`
  - 只负责轮询远端任务状态
- `cloud_asset_tasks`
  - 只负责把云盘完成结果登记为 `cloud_assets`
- `sync_plan_tasks`
  - 只负责根据同步意图和资源状态生成同步计划
- `sync_tasks`
  - 只负责把云盘文件拉到本地
- `local_presence_tasks`
  - 只负责检查本地文件是否存在
- `nfo_tasks`
  - 只负责生成或刷新 NFO
- `cleanup_tasks`
  - 只负责清理已完成任务、过期记录、孤儿状态

当前已落地的新拆分阶段补充：

- `cloud_presence_tasks`
  - 已从下载提交里拆出，专门判断云盘是否已存在资源，避免重复提交到 PikPak。
- `download_enqueue_tasks`
  - 已从下载执行器里拆出，专门负责生成 `download_tasks` 与 `cloud_submissions`。
- `nfo_tasks`
  - 已从本地同步执行器里拆出，独立生成和重建 NFO。
- `local_presence_tasks`
  - 已独立检查本地文件与 NFO 是否仍存在，并回写本地状态。
- `cleanup_tasks`
  - 已独立负责运行期清理：
    - 裁剪 `operations` 的已完成/失败记录
    - 裁剪各任务表中过多的 `completed/superseded/synced` 历史项
    - 避免控制台长期堆积无用已完成任务

注意：

- “检查云盘是否已存在”必须从提交下载里拆出去，避免重复下载。
- “判断是否需要同步”和“真正执行同步”必须拆开。
- “生成 NFO”不能挂在同步函数里顺手做，必须是单独任务。
- 后续若增加“归档完结番”“移动目录”“重建文件名”等动作，也必须各自独立建表。

### 定时任务模型

定时任务不再通过写死的 APScheduler job 名称直接绑定业务函数，而是引入独立调度表，例如：

- `scheduled_jobs`
- `scheduled_job_runs`

建议字段：

`scheduled_jobs`

- `id`
- `job_key`
- `job_type`
- `enabled`
- `cron_expr` 或 `interval_minutes`
- `debounce_seconds`
- `max_concurrency`
- `last_run_at`
- `next_run_at`
- `last_status`
- `last_error`
- `created_at`
- `updated_at`

`scheduled_job_runs`

- `id`
- `job_id`
- `status`
- `trigger_source` (`system/manual`)
- `started_at`
- `finished_at`
- `message`
- `stats_json`

首批拆分的定时任务类型：

- `rss_scan`
- `mikan_match_dispatch`
- `metadata_dispatch`
- `selection_dispatch`
- `backfill_dispatch`
- `download_dispatch`
- `cloud_poll_dispatch`
- `cloud_asset_dispatch`
- `sync_dispatch`
- `cloud_reconcile`
- `local_reconcile`
- `cleanup`
- `archive_completed_series`（后续）

原则：

- 定时任务负责“定期触发某类队列处理”，不是直接代表队列本身。
- 队列有没有待处理，由各任务表实时决定。
- 控制台里定时任务应作为单独一组信息展示，而不是塞进队列状态里。

### 控制台重构方向

控制台页面后续按“左侧总览 + 右侧详情”的模型重做，不再以当前这种大杂烩表格堆叠为主。

目标布局：

- 左侧：任务域导航 / 队列摘要 / 定时任务摘要
- 右侧：当前选中项详情

左侧一级分组建议：

- 队列
- 定时任务
- 系统日志
- 维护

`队列` 下动态显示当前存在的任务队列，例如：

- RSS 候选
- RSS 拉取
- Mikan 匹配
- 元数据
- 正式入库
- 自动选集
- 整季补全
- 云盘存在性检查
- 云盘提交准备
- 云盘提交
- 云盘轮询
- 云盘入库
- 同步计划
- 本地同步
- 本地存在性检查
- NFO

每个队列列表项至少展示：

- 队列名
- `pending/running/failed/cooling/completed` 数量
- 当前是否有 worker 正在处理
- 下次自动触发时间
- 最近一次错误摘要

右侧队列详情应展示：

- 队列说明
- 处理规则
- 当前运行 worker 数
- 并发上限
- 防抖时间
- 最近一次启动时间
- 最近一次完成时间
- 最近一次批次统计
- 当前批次正在处理的 item
- 待处理 item 列表
- 失败 item 列表
- 完成 item 列表（支持自动清理或手动清空）

item 级详情至少包括：

- `id`
- `status`
- `reason`
- `attempts`
- `retry_after`
- `last_error`
- `created_at`
- `updated_at`
- 与上下游的关联 id

例如：

- `candidate_id`
- `series_id`
- `release_id`
- `download_task_id`
- `provider_file_id`

交互要求：

- 左侧点击一个队列，右侧只显示这个队列的详情。
- 队列详情中的待处理、失败、运行中、已完成通过 Tab 切换，不要全部堆一起。
- 所有详情窗体高度固定，避免点击不同队列时布局一长一短。
- 已完成 item 默认只保留短时间，之后自动清理；同时保留“清空已完成”按钮。
- 失败 item 支持单条重试、批量重试。
- 卡在冷却中的 item 要清楚显示剩余等待时间，避免用户误以为卡死。

控制台不再依赖“操作日志”理解系统状态：

- 用户应当主要通过队列本身看到系统当前在做什么。
- 每个队列项必须直接展示：
  - 当前正在处理什么
  - 为什么在等待
  - 为什么失败
  - 下次什么时候重试
- 因此后续可以逐步弱化甚至移除现有 `operations` 在控制台中的中心地位。
- `operations` 若继续保留，也只作为后台批次记录和排障辅助，不作为主视图。

系统日志区域仍然保留，但它只是细节补充，不承担主流程解释责任。

### 维护与观察性

维护页不再承担主流程操作，只放补救动作：

- 手动扫描 RSS
- 手动触发指定队列
- 重试失败任务
- 扫描云盘库
- 全量审计云盘状态
- 全量审计本地状态
- 清理已完成任务
- 清除运行数据

日志和操作分开：

- `operations` 后续降级为辅助记录，主观察性来自各队列自身状态。
- 服务日志用于细粒度输出每个 worker、每批任务、每个失败项的详细原因。
- 控制台日志页需要支持：
  - 搜索
  - 清空日志
  - 按队列过滤
  - 按级别过滤
  - 最低显示高度，避免内容少时界面塌陷

### 后续实施顺序

后续重构建议按下面顺序推进：

1. 移除“串行扫描推进主流程”的依赖，保留 `扫描全部` 仅作为 RSS 源头触发。
2. 继续把 task 拆细，补齐单职责任务表，尤其是：
   - `cloud_presence_tasks`
   - `download_enqueue_tasks`
   - `sync_plan_tasks`
   - `nfo_tasks`
3. 抽象队列 worker 注册表：
   - 每个队列定义自己的 `fetch/handle/schedule/summary`。
4. 抽象触发中心：
   - 新任务写入后调用统一 `trigger_queue(queue_name)`。
5. 抽出定时任务表与定时任务执行记录。
6. 控制台改成“队列树 + 详情面板”。
7. 让控制台从“操作日志视角”切到“队列状态视角”。
8. 为每个队列补充详细 item 状态与运行批次日志。
9. 再做多 worker 并发参数化和任务限流配置。

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
  - 提交前必须先 `rclone mkdir` 目标目录；否则 PikPak 会把 addurl 资源放到默认 `My Pack`。
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

结合当前动画主线，后续更推荐的细化模型为：

- `works`
  - 作品根；用于归档、别名、合集展示
- `entries`
  - 具体季度/篇章/部分/OVA；是实际配置、下载、同步的最小管理单元
- `seasonal_entries`
  - 新番域中的追更关系；例如 RSS 订阅、是否追更、是否自动同步
- `library_entries`
  - 番剧库域中的归档关系；例如来源、是否补番、是否收藏、是否完结归档
- `episodes`
- `releases`
- `rss_candidates`
- `mikan_match_tasks`
- `metadata_tasks`
- `selection_tasks`
- `backfill_tasks`
- `cloud_presence_tasks`
- `download_enqueue_tasks`
- `download_tasks`
- `cloud_poll_tasks`
- `cloud_asset_tasks`
- `sync_plan_tasks`
- `sync_tasks`
- `nfo_tasks`
- `logs`

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

### 当前项目体检结论

本次检查确认当前代码的主要瓶颈不是单个接口缺失，而是职责边界过宽：

- `backend/app/main.py` 约 110 KB，同时承担 API 路由、队列注册、调度、dashboard 读模型、手动操作入口。
- `backend/app/scanner.py` 约 91 KB，同时承担 RSS 解析、Mikan 匹配、元数据入库、选择、补全、云盘提交前置逻辑。
- `backend/app/sync_service.py` 约 57 KB，同时承担云盘登记、本地同步、NFO、本地存在性检查。
- `backend/app/db.py` 约 58 KB，同时承担 schema、迁移、设置、日志、operation、诊断、清理。
- `frontend/src/App.vue` 约 55 KB，控制台、新番、番剧库、设置、日历、详情抽屉混在一个组件里。

直接后果：

- 任务推进路径分散在多个函数里，很难判断一个任务完成后会触发哪里。
- 同一业务动作可能同时写任务表、日志表、operation、scheduled_jobs，容易制造 SQLite 写锁竞争。
- 页面状态依赖多个兼容聚合字段，导致“看起来卡住”和“实际已完成”经常不一致。
- 日志既承担审计又承担状态说明，但日志本身不是结构化任务状态，排障效率低。
- 清空数据只清表，不能修复旧库 schema 约束缺失；因此可能出现 `ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint`。

当前必须先修的阻断问题：

- `metadata_tasks` 旧库可能缺少 `candidate_id` 唯一约束，导致元数据队列写入失败。
- 类似风险还存在于其他使用 `ON CONFLICT(...)` 的任务表，迁移必须补唯一索引，不能只依赖 `CREATE TABLE IF NOT EXISTS`。
- `database is locked` 的根因是多个后台任务、日志写入和 dashboard/operation 更新并发写 SQLite；短期要减少长事务，长期要用 repository + worker claim 统一写入边界。
- 网络 IO、rclone 调用、HTML 抓取、文件复制期间不能持有数据库连接。
- `scheduled_jobs` 目前同时表达定时任务和队列防抖状态，语义混乱，应拆成调度表和队列运行时状态。

Redis 取舍：

- 现阶段不建议立刻引入 Redis 作为必要依赖。
- SQLite 继续保存正式业务状态、任务状态和失败原因，保证 NAS 单容器重启后可恢复。
- Redis 可以作为后续增强：
  - 多进程 worker 的轻量 broker
  - 短期分布式锁
  - websocket 推送缓存
  - 高并发队列通知
- 即使引入 Redis，也不能把任务最终状态只放 Redis；任务状态仍必须落库。
- 当前优先级是：索引、短事务、批量 claim、事件表裁剪、后台任务不持连接做 IO。

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
   - Mikan RSS 的 `bangumiId` 是 Mikan 自己的番组 ID，不是 Bangumi.tv subject ID。
   - RSS 扫描后先通过 Mikan 条目页反查 `/Home/Bangumi/{mikan_id}`，再从 Mikan 番组页解析 `https://bgm.tv/subject/{bangumi_id}`。
   - 拿到 Bangumi.tv subject ID 后，才允许按稳定 ID 归并和刷新 Bangumi 元数据。
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
- 扫描全部只负责触发 RSS 源头扫描，并显示源头扫描进度；后续队列自动推进，运行中禁止重复点击。
   - 手动扫描云盘库、刷新 PikPak 状态、重试失败任务属于维护/补救操作，放在控制台维护 Tab。
   - 番剧详情页展示 RSS 发布、云盘任务、云盘资源、本地资源，方便追踪误识别来源。
   - dashboard 必须是只读状态视图，不能在刷新时恢复、隐藏或合并数据，避免“看一眼界面就改变数据库”。
   - NFO 只在本地同步完成后生成；RSS 扫描阶段不生成本地媒体元数据。
   - RSS 没有提供 Bangumi ID 时，不能自动用标题搜索并绑定第一个 Bangumi 结果，必须进入待处理。

## 8. 已知缺口

### 高优先级

- 当前 `metadata` 队列报错 `ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint`，原因是旧库表结构没有补出 `metadata_tasks(candidate_id)` 唯一约束。已在迁移中补唯一索引；后续所有使用 `ON CONFLICT` 的任务表都必须有迁移级约束校验。
- 当前 `database is locked` 不能靠全局 Python 锁硬压；需要通过任务 claim 短事务、日志异步化、operation 降频、dashboard 只读化解决。
- `main.py/scanner.py/sync_service.py/db.py/App.vue` 都已经过大，后续大改必须先拆模块：
  - `backend/app/api/routes/*`
  - `backend/app/services/pipeline_runtime.py`
  - `backend/app/services/processors/*`
  - `backend/app/repositories/*`
  - `backend/app/schemas/*`
  - `frontend/src/views/*`
  - `frontend/src/components/*`
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

优先级原则：

- 系统重构 > 调度重构 > 数据正确性 > 自动化闭环 > 控制台重构 > 小型 UI 优化
- 能影响“任务是否正确推进”的问题，优先级永远高于展示细节
- 样式、最小高度、按钮文案、版面微调等都归为后置优化

### P0: 触发式调度重构

目标：彻底把当前“串行扫描推进全流程”的模式切换成“源头触发 + 独立队列自动推进”。

- 移除 `scheduled_queue_tick()` 作为主编排器的职责。
- `扫描全部` 只触发 RSS 扫描，不再顺序强推全链路。
- 建立统一的 `trigger_queue(queue_name)` 调度中心。
- 每个队列收到新任务后 10 秒防抖触发。
- 每个队列支持独立 worker 和并发配置。
- 同一队列失败项不阻塞其他项。
- 补全后新增的任务自动触发下游队列，而不是等待下一次扫描。

状态：部分完成。当前主链已改成“入队即触发 + 队列防抖聚合 + 正式任务表自动推进”；`queue_dispatch` 已降级为恢复/补漏调度，不再承担主编排职责。剩余工作主要是继续清理少量兼容入口和提升状态解释。

### P1: 任务表继续细拆

目标：让每种 task 只做一件事，降低串扰和状态混乱。

- 从现有任务表继续拆出：
  - `cloud_presence_tasks`
  - `download_enqueue_tasks`
  - `sync_plan_tasks`
  - `local_presence_tasks`
  - `nfo_tasks`
  - `cleanup_tasks`
- 检查、规划、执行分开，不允许一个 task 混做多件事。
- 为每种 task 建立统一字段：
  - `status`
  - `attempts`
  - `retry_after`
  - `last_error`
  - `created_at`
  - `updated_at`
  - `batch_id`

状态：部分完成。`cloud_presence_tasks / download_enqueue_tasks / sync_plan_tasks / local_presence_tasks / nfo_tasks / cleanup_tasks` 已落地并接入主链；剩余工作主要是继续统一字段语义、补 `batch_id` 这类批次观察能力，以及进一步收缩兼容 helper。

本阶段新增完成：

- 已正式补出 `nfo_tasks`：
  - 本地同步完成后不再直接在同步执行器里顺手生成 NFO
  - 改为写入 `nfo_tasks`
  - 由独立 `nfo` 队列处理
- 控制台已补 `NFO` 队列摘要与详情。
- 这一步是任务细拆的第一块落地，后续可以按同样模式继续拆：
  - `cloud_presence_tasks`
  - `download_enqueue_tasks`
  - `local_presence_tasks`
  - `cleanup_tasks`

本阶段新增完成：

- 已正式补出：
  - `cloud_presence_tasks`
  - `download_enqueue_tasks`
- 追番下载前半段现已拆成：
  - 自动选集
  - 云盘存在性检查
  - 下载准备
  - 真正提交 PikPak
- `queue_release()` 不再直接做“云盘是否已存在 + 是否已提交 + 建 download_tasks/cloud_submissions”三件事，而是先写入 `cloud_presence_tasks`。
- 控制台已补：
  - `云盘存在性检查`
  - `下载准备`

这样后续如果要再接其他云盘 provider、补番搜索源、或者做云盘状态纠偏，就能在下载前半段独立扩展，而不会继续把判断逻辑塞进提交执行器里。

本阶段新增完成：

- 已正式补出 `local_presence_tasks`。
- 本地文件是否仍然存在、NFO 是否仍然存在，现在开始由独立队列检查，而不是只靠：
  - `sync_tasks` 成功时的副作用
  - `cancel_sync_for_series()` 的删除逻辑
- 控制台已补 `本地存在性检查` 队列。

下一步再继续拆 `cleanup_tasks`，把“已完成任务自动清理 / 短期保留 / 清空已完成”也从 UI 按钮和零散逻辑里收成独立队列。

### P1.5: 新番域 / 番剧库域分离

目标：把“追更”和“补番/归档”从上层语义上拆开，避免后续功能继续挤在一张表和一个列表里。

- 引入 `works -> entries` 二层结构。
- 新番域使用：
  - `seasonal_entries`
  - 强调 RSS、订阅、追更、同步意图
- 番剧库域使用：
  - `library_entries`
  - 强调补番、归档、导入、长期保存
- 同一个 `work` 下允许多个 `entry`：
  - 第 X 季
  - 第 X 部分
  - 前篇 / 后篇
  - OVA / SP / 特别篇
- 新番页显示具体 `entry`，不因为 `root_name` 相同而自动合并成一条。
- 番剧库页显示 `work` 分组，并支持展开到 `entry`。
- 所有后半段任务仍挂在 `entry_id` 上，而不是挂在 `work_id` 上。

状态：进行中。

已完成：

- `works / entries / seasonal_entries / library_entries` 表已落地。
- 新番首页统计与列表已优先读取 `seasonal_entries -> entries`。
- 最近 7 天已同步到本地的新番日历已接入 `seasonal_entries` 读模型。
- 自动选集与整季补全任务已开始以 `entry_id` 作为执行单位：
  - `selection_tasks.entry_id`
  - `backfill_tasks.entry_id`
- 新番详情接口已切到“路径名保留 `/api/series/{id}`，内部按 `entry.id` 查询”的兼容模式。
- 保存新番配置时，已优先更新 `entries` 并重新入队 `selection/backfill`。
- 同步意图和同步计划已开始以 `entry_id` 为主：
  - `sync_rules.entry_id`
  - `sync_tasks.entry_id`
  - `queue_sync_for_series(...)` 内部优先解析为 `entry_id`
- 云盘提交链已开始以 `entry_id + episode_number` 去重，避免不同条目误共用同一集任务。

当前剩余：

- `cloud_assets / local_assets / cloud_submissions` 的所有查询与控制台明细仍需彻底切到 `entry_id` 主视角。
- 云盘库扫描导入仍主要按旧 `series` 兼容逻辑运行，后续要拆成番剧库域专属入口。
- 新番域和番剧库域的前端二层展示（`work -> entry`）还未完成。

本阶段新增完成：

- `cloud_assets / local_assets / sync_tasks / sync_rules` 主链已开始以 `entry_id` 为默认关联键。
- 控制台的云盘队列、PikPak 状态、云盘资源登记、本地同步列表已改为优先展示 `entries.display_title`。
- 新番页失败筛选和问题列表已按 `entry_id` 关联失败任务。

本阶段仍保留的兼容层：

- `series` 汇总列表和云盘库扫描导入还保留一部分旧 `series_id` 兼容查询。
- `generate_nfo_for_series(...)` 仍使用旧 `series` 模型，后续需要补 `entry` 版 NFO 生成入口。

新增完成：

- 已补 `refresh_entry_metadata(...)`，新番详情页的“刷新元数据”接口已改为按 `entry.id` 执行。
- 已补 `generate_nfo_for_entry(...)`，本地同步完成后和手动生成 NFO 都已切到 `entry` 视角。
- 新番首页列表已不再回退到旧 `series` 汇总作为主数据源，后续可以继续安全收缩 `series` 兼容视图。

剩余调整：

- `metadata.py` 和 `sync_service.py` 中仍保留旧 `series` 版兼容函数，后续等番剧库域稳定后再统一裁剪。
- dashboard 返回里的 `series` 汇总仍存在，当前只作为兼容副视图。

本阶段新增完成：

- 番剧库页前端已开始独立使用 `library_items`，不再和新番页共用同一套主数据源。
- dashboard 中旧 `series` 聚合汇总已降级为轻量兼容数据，不再承载首页核心统计。
- 新番页核心统计已明确绑定 `seasonal_items`。

接下来要做：

- 为番剧库域补独立的详情/编辑入口，而不是继续复用新番详情抽屉。
- 把云盘库扫描导入正式接到番剧库域，而不是混回新番域逻辑。

本阶段新增完成：

- 前端打开详情时已开始区分 `seasonal` / `library` 域。
- 番剧库条目详情不再完全套用新番的“自动选集 / 自动同步”交互语义。
- 详情接口返回已带 `domain_kind`，便于后续继续拆域内行为。

后续继续：

- 番剧库域还需要独立的保存规则和导入动作，不应继续复用新番保存逻辑。
- 云盘库扫描后，应优先进入 `library_entries`，而不是默认进入新番域。

本阶段新增完成：

- 云盘库扫描导入已补 `ensure_library_entry_for_series(...)`，会显式确保对应条目落到 `library_entries`。
- 番剧库读模型已补 `episode_count / release_count / cloud_asset_count / local_asset_count`，导入后能在番剧库页直接看到资源状态。
- 云盘库扫描产生的资源不再隐式依赖新番域条目存在。

下一步继续：

- 番剧库域需要独立的保存逻辑，避免保存时误触发新番补全/选集队列。
- 还要补“手动导入老番/搜索补番”这类只属于番剧库域的入口。

本阶段新增完成：

- `PUT /api/series/{id}` 已按 `domain_kind` 拆保存行为：
  - `seasonal` 保存后继续触发自动选集与整季补全
  - `library` 保存只更新条目本身，不再误触发追番队列
- 前端保存提示已区分“番剧设置已保存”和“番剧库条目已保存”。
- 已新增番剧库域专属详情/保存 API：
  - `GET /api/library/{entry_id}`
  - `PUT /api/library/{entry_id}`
- 已新增番剧库域专属动作 API：
  - `DELETE /api/library/{entry_id}`
  - `POST /api/library/{entry_id}/metadata`
  - `POST /api/library/{entry_id}/nfo`
  - `POST /api/library/{entry_id}/sync`
  - `POST /api/library/{entry_id}/sync/cancel`
- 前端详情抽屉已按域分流：
  - 新番域继续使用 `/api/series/{id}`
  - 番剧库域改走 `/api/library/{id}`
  - 番剧库保存不再通过新番兼容接口绕行
  - 番剧库的元数据、NFO、同步、隐藏也已改走 `/api/library/*`

后续继续：

- 番剧库域还需要独立的“导入 / 补番 / 搜索”动作入口。
- 新番和番剧库最终应拆成不同的详情 API，而不是只靠同一路径下的域分支。

本阶段新增完成：

- 已新增番剧库域动作入口骨架：
  - `POST /api/library/import`
  - `POST /api/library/{entry_id}/backfill`
- 当前 `library/import` 已先接云盘扫描导入；搜索源、磁力、手动导入先返回 `planned`，占住结构位置，后续接真实补番来源时不再挤回新番域。
- 番剧库页工具栏已增加“导入云盘到番剧库”入口。
- 番剧库条目详情已增加“补全条目”入口，直接入 `backfill_tasks`，不再借维护区动作表达番剧库语义。

本阶段新增完成：

- 番剧库页面已开始按 `work -> entry` 分组展示，而不是继续把所有条目平铺成一层。
- 番剧库详情抽屉的统计读取已按域分流：
  - 新番详情继续读取 `seasonal_items`
  - 番剧库详情改读 `library_items`
- 这一步先只调整读模型和展示层，不改任务表，给后续“老番导入 / 归档 / 多篇章管理”留稳定的页面结构。

本阶段新增完成：

- dashboard 已新增 `library_summary` 读模型，先提供：
  - 作品数
  - 条目数
  - 待关联条目数
  - 失败条目数
  - 云盘资源数
  - 本地资源数
- 控制台维护区已移除“扫描云盘库”主按钮，改由番剧库页承担“云盘导入到番剧库”的主入口，避免继续把番剧库动作放在全局维护语义里。

本阶段新增完成：

- 队列详情项已开始携带 `domain_kind`。
- 控制台队列表已支持直接从失败/待处理项打开对应条目详情。
- 队列表会直接标记该项属于：
  - `新番`
  - `番剧库`
- 控制台队列详情已增加按域筛选：
  - `全部`
  - `新番`
  - `番剧库`
- 已新增并切换到新番域专属 API：
  - `GET /api/seasonal/{entry_id}`
  - `PUT /api/seasonal/{entry_id}`
  - `DELETE /api/seasonal/{entry_id}`
  - `POST /api/seasonal/{entry_id}/download`
  - `POST /api/seasonal/{entry_id}/metadata`
  - `POST /api/seasonal/{entry_id}/nfo`
  - `POST /api/seasonal/{entry_id}/sync`
  - `POST /api/seasonal/{entry_id}/sync/cancel`
- 前端新番详情已改走 `/api/seasonal/*`。
- 旧 `/api/series/*` 兼容路由已删除，运行态只保留 `seasonal / library` 两套正式入口。
- 已删除不再参与运行路径的旧服务端 HTML 模板：
  - `backend/app/templates/index.html`
  - `backend/app/templates/series.html`
- 已清理前端不再使用的 `getSeries/saveSeries` dead API。
- 已开始收口 `seasonal / library / series` 三套重复动作实现：
  - 抽出统一 `entry` 动作 helper
  - 入口层不再保留 `series` 路由别名
  - 后续继续清理内部 `series_id` 兼容查询时，不需要再同步维护额外 HTTP 路由
- 同步与元数据的运行入口已继续去 `series` 语义：
  - `queue_sync_for_series / cancel_sync_for_series` 已改为 `queue_sync_for_entry / cancel_sync_for_entry`
  - `refresh_series_metadata / generate_nfo_for_series / resolve_series_choice` 已删除
  - Mikan 补回逻辑已改为按 `entry_id` 反查和回填 `mikan_bangumi_id`
- `cloud_asset -> sync_plan` 这段推进链已改为直接按 `entry_id` 聚合：
  - 云盘资源登记完成后不再先收集 `series_id` 再反查 `entry_id`
  - 现在直接把触达的 `entry_id` 送入 `sync_plan_tasks`
- 同步规则和云盘扫描读模型已继续转到 `entry` 语义：
  - `ensure_sync_rule()` 不再散落写 `SELECT series_id FROM releases ...`，统一走局部 `resolve_entry_series_id()`
  - `scan_cloud_library()` 的匹配输入已从 `series_rows` 改成 `entry_rows`
  - `match_cloud_file_to_series()` 已改为 `match_cloud_file_to_entry()`
- 下载前两级队列入口已继续去 `series` 传参：
  - `queue_release -> cloud_presence -> download_enqueue` 不再要求显式传入 `series_id`
  - 两级 helper 改为按 `entry_id` 自行补齐 `series_id` 落库字段
- 下载提交与轮询链已继续去 `series` 显式传参：
  - `sync_cloud_submission()` 现在会按 `entry_id` 自行补齐 `series_id`
  - 下载提交、失败重试、状态轮询各分支不再依赖调用方显式传入 `task.series_id`
- 下载任务创建已继续去 `release.series_id` 运行依赖：
  - `ensure_download_task_for_release()` 现在按 `release.entry_id` 反查 `series_id`
  - 不再把 `release.series_id` 作为创建 `download_tasks / cloud_submissions` 的运行前提
- 定时源头扫描与手动扫描入口已统一：
  - `scheduled_scan()` 不再绕过运行时触发链直接只做 `scan_and_queue()`
  - 现在定时 RSS 与手动“扫描全部”都走 `run_scan_source()`，统一回收挂起 Mikan 任务、补排匹配任务，并由后续队列自动推进
- 云盘扫描后的推进已继续去串行调用：
  - `scan_cloud_library()` 不再在函数尾部直接 `await process_sync_plan_tasks()` / `await process_sync_tasks()`
  - 云盘扫描现在只负责写 `cloud_assets` / `sync_plan_tasks`，由 `sync_plan` / `sync` 队列各自触发处理
- 失败重试入口已扩到完整正式任务链：
  - 新增重置 `cloud_presence_tasks / download_enqueue_tasks / sync_plan_tasks / nfo_tasks / local_presence_tasks / cleanup_tasks`
  - 重试失败后会按队列维度重新触发，而不是只重跑下载和同步的后半段
- 控制台任务说明已补强一轮：
  - `running / submitted / pending / waiting_retry` 都会给出更明确的 `display_reason`
  - 避免出现“卡住但看不出是在等 worker、等轮询还是等冷却”的空白状态
- 控制台队列可观察性已继续补强：
  - 队列卡片会显示防抖聚合剩余秒数，而不是只显示“聚合中”
  - 队列明细补出 `attempts / updated_at`
  - 前端 build 号继续显式更新，方便确认 NAS 上是否已部署到新版本
- 控制台主视图已开始收紧“活跃态优先”策略：
  - 队列明细默认只保留 `pending / running / failed`（下载队列额外保留 `submitted`）
  - 已完成历史不再长期堆在控制台主表里，避免误判为“卡住”
  - 历史完成项继续交给 `cleanup` 和独立历史视图处理，而不是留在主工作台
- 首页与详情读模型已开始向 `works -> entries` 统一口径收口：
  - 后端补出统一字段：`work_display_title / entry_scope_label / entry_badge_text / entry_display_title / entry_secondary_title`
  - 首页新番列表、番剧库列表、最近同步日历不再各自拼作品名/季名/篇章名
  - 条目详情接口已同步返回 `entry` 主字段，前端逐步摆脱 `series` 误导命名
- 条目详情子系统已继续整体切到 `entry` 语义：
  - 前端 API 已补 `getSeasonalItem / saveSeasonalItem / getLibraryItem / saveLibraryItem`
  - 详情抽屉状态已改为 `entryDrawerOpen / selectedEntryDetail / selectedEntryDomain`
  - 保存、同步、删除、详情表格、自动刷新暂停条件均不再依赖 `selectedSeries.series`
  - 本轮继续收口后，详情接口已不再返回兼容 `series` 字段
  - 前端交互函数已改为 `openEntry / saveCurrentEntry / toggleEntrySync / runEntryAction / deleteCurrentEntry`
  - 这一块已从“兼容过渡态”进入“entry-first 正式态”
- 设置/诊断读模型已开始去 `series` 主语义：
  - 设置页“番剧目录模板”已改成“作品目录模板”，与 `works -> entries` 模型一致
  - 诊断页主统计已改成 `作品 / 条目 / 发布` 和 `云盘 / 本地 / 同步规则`
  - `series` 只再作为 `legacy_series` 暂存展示，用来观察旧表残留规模
- 接口与设置 payload 命名已继续收口：
  - `SeriesPayload` 已改为 `EntryPayload`
  - 设置接口对外已补 `work_dir_template`，前端不再直接使用 `series_dir_template`
  - 内部仍保留 `series_dir_template` 作为底层设置键，仅做兼容映射，不再作为主界面语义
- 这一阶段结束后，用户可见主路径中的 `series` 术语已基本退到兼容层：
  - 详情抽屉、首页、番剧库、设置页、诊断页、控制台主视图都以 `works / entries` 为主语义
  - `series` 现在主要剩在底层兼容表、旧数据归并逻辑和少量内部 helper 中

这样后续做分域队列、分域失败重试和分域维护动作时，不需要再靠用户手动判断来源。

### P2: 可观测任务和数据安全

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

### P3: 控制台重构

目标：界面围绕“队列自身状态”，而不是“流水线”或“操作日志”。

- 侧边栏收敛为：
  - 控制台
  - 番剧库
  - 设置
- 控制台左侧展示动态队列树和定时任务树。
- 右侧展示当前队列的 item 详情、运行批次、失败项、等待项。
- 问题处理并入队列详情，不再单独做一个“操作日志式”入口。
- `operations` 降级为辅助信息，主界面不再依赖它解释系统状态。
- 日常界面不显示“存入云盘”“处理同步”“处理云盘任务”这种主流程按钮。
- 维护动作只放在维护区。
- 日历视图需要补一块“最近一周已同步到本地的新番”：
  - 只统计新番域（`seasonal_entries`），不统计番剧库域（`library_entries`）。
  - 时间窗口默认最近 7 天。
  - 数据来源应以本地同步完成时间为准，而不是放送日。
  - 展示至少包括：
    - 番剧名
    - 具体季度/篇章条目名
    - 集数
    - 同步完成时间
    - 本地路径
  - 后续可作为控制台或新番页的一个侧栏/卡片，而不是放到番剧库页。

状态：部分完成。控制台已补统一 `console_overview` 总览、队列/定时任务/运行操作/日志徽标，以及更一致的详情摘要；后续还需要继续把左侧导航进一步树化，并收掉剩余旧概览块的重复表达。

补充进展：

- 维护区和手动操作 API 已继续收口：
  - “立即处理云盘队列”“刷新 PikPak 状态”“本地同步”不再手工串行推进多个下游处理器
  - 现在统一改为触发对应正式队列，由任务链自动继续推进
- 控制台首页顶部“待处理”指标已改为直接读取 `console_overview.pending_task_count`
  - 不再继续依赖旧的兼容聚合块单独再算一份问题数
- `sync_service` 内部同步职责已继续拆分：
  - `queue_sync_for_series()` 现在只负责挂 `sync_plan_tasks`
  - 真正生成 `sync_tasks` 改由 `sync_plan` worker 内部的物化步骤负责
  - 同步计划与本地同步执行不再混在一个 helper 里
- 下载完成后的主链唤醒已继续收口：
  - `download/completed` 不再同时唤醒 `cloud_asset` 和 `sync_plan`
  - 现在统一改为先登记 `cloud_asset`，再由云盘资源登记成功后唤醒 `sync_plan`
- 运行时桥接层已移除多队列批量唤醒 helper：
  - 避免再次回退到“一个动作同时推进多个下游队列”的旧模式
- 同步链旧 helper 已继续收口：
  - 无引用的 `requeue_sync_tasks_for_series()` 已删除
  - `sync_service` 当前仍保留 `backfill_cloud_assets_from_completed_tasks()` 作为兼容补洞逻辑
  - 当前已在控制台语义上标记为“云盘资源登记 + 兼容补洞”；后续可继续考虑把这类补洞动作彻底下沉为恢复/维护语义
- 控制台首页与 `dashboard_data()` 已继续去兼容聚合：
  - 首页“失败/问题”感知已改为优先读取正式队列详情
  - `dashboard_data()` 顶层已移除一批首页不再使用的旧兼容字段与重复查询
  - 当前仍保留条目抽屉详情和旧 `/api/series/*` 兼容接口，后续再谨慎处理
- 条目详情接口已继续去掉无用兼容返回：
  - `build_entry_response()` 不再返回未被使用的 `legacy_series_id`
  - 旧 `/api/series/*` 路由本身暂时保留，只作为兼容壳层
- 同步与云盘资源登记的主链依赖已继续去 `series` 化：
  - `materialize_sync_tasks_for_entry()`、`upsert_cloud_asset*()` 已去掉仅用于存在性判断的 `series` 查询
  - 当前主链优先依赖 `entries / releases / cloud_assets / sync_tasks`
- 下载提交与 PikPak 轮询链已继续去 `series` 过滤：
  - `queue_release()`、`process_tasks()`、`poll_submitted_tasks()` 已改为优先使用 `entries.bangumi_id`
  - `series` 相关逻辑目前主要收缩在历史兼容、Mikan 修补和少数旧关联路径
- `cleanup_tasks.runtime` 已改为持续维护任务：
  - 执行完成后不再停在 `completed`
  - 现在会自动回到 `pending + retry_after`
  - 语义更接近后台维护，而不是一次性清理动作
- 控制台队列状态已补齐 `cleanup` 的等待时间表达：
  - 维护队列现在也会显示下次恢复/执行时间
  - 避免控制台把周期维护任务误显示成普通空闲队列
- `重试失败任务` 的恢复链已改为覆盖正式任务表：
  - 不再只唤醒 `mikan_match / download / sync`
  - 现在会按已重置的任务表对应唤醒 `metadata / selection / backfill / cloud_poll / cloud_asset` 等正式队列
- 控制台首页已开始直出新番条目的阻塞摘要：
  - 不再只通过队列详情间接观察“为什么没自动继续”
  - 当前已由后端在 `dashboard_data()` 统一生成 `status_summary / status_category / status_level / has_failed_task / needs_attention`
  - 前端不再自行推断失败条目、语言冲突、字幕组冲突，后续番剧库页和详情页可以直接复用同一套状态语义

### P4: 修复自动入云盘语义

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

### P5: 拆分云盘入库和本地同步

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

### P6: 本地同步执行器

目标：把云盘资源复制到 NAS 本地真实目录。

- 配置本地库目录，默认 `/media/pikpak-anime`。
- 同步执行方式优先使用云盘 provider API。
- 支持同步意图开关：未入云盘也能先开启。
- 支持“取消同步”，只删除本地文件和本地 NFO。
- 同步成功后生成 NFO。
- 同步成功后可触发 Jellyfin 扫描。

状态：部分完成。同步意图开关、取消同步、同步后 NFO 已实现；新增同步意图调和，云盘已有资源可自动排同步；同步失败会冷却后自动重试且不阻塞后续任务；真实 PikPak 文件下载需要 NAS 环境验证。

### P7: 元数据优先合并

目标：扫描时尽早建立稳定身份。

- Mikan RSS 的 `bangumiId` 只作为 Mikan 番组页线索，不作为稳定媒体身份。
- RSS 扫描先进入候选/待确认队列，Mikan 匹配拿到 Bangumi.tv subject ID 且元数据完整后才正式入库。
- 增加匹配候选 UI。
- 建立 `identity_key`。
- 按 Bangumi ID 合并重复媒体。

状态：部分完成。候选暂存队列和 Mikan 页面匹配队列已接入；正式入库门槛已改为必须经过 Bangumi 元数据任务。候选确认 UI 和完整 `identity_key` 仍待实现。

修正：RSS 没有提供 Bangumi.tv subject ID 时，不再自动按标题搜索 Bangumi 并绑定第一个结果，避免误识别为无关番剧；Mikan 来源先通过 Mikan 页面解析 subject ID，失败时停在 `mikan_match_tasks`。

### P8: 语言过滤和三维自动选择

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

### P9: Mikan 追番闭环

目标：让 Mikan RSS 新集自动进入 PikPak，并按同步意图落到本地。

- Mikan RSS 周期扫描，默认 60 分钟。
- Mikan 页面解析 `bgm.tv/subject/{id}` 稳定识别番剧。
- RSS 扫描只处理新增/变更发布，不触发云盘库全量扫描。
- 唯一候选自动入 PikPak。
- 多字幕组/分辨率/语言时按优先级选择，不唯一则进入需要处理。
- 新集入云盘后自动调和本地同步。
- 同步完成后生成 NFO。

状态：部分完成。RSS 扫描、自动入云盘、同步意图和 NFO 已有；需要完善“需要处理”列表和真实 NAS 验证。

### P10: Nyaa/其他索引器补番

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

### P11: 云盘已有资源导入

目标：让 PikPak 里已经存在的动画能进入管理和本地同步流程。

- 扫描配置的 PikPak 云盘根目录。
- 优先使用目录名里的 `bangumi-xxxx`。
- 其次用标题归一化保守匹配。
- 匹配不到的进入“待确认导入”，不要自动归错。
- 已入云盘后自动调和同步任务。

状态：部分完成。保守扫描已实现；待确认导入 UI 未实现。

### P12: 全量状态审计

目标：提供一个手动按钮，完整检查所有番剧的云盘和本地状态，用于纠偏，不参与定时任务。

- 检查每部正式入库番剧是否有云盘资产。
- 检查每个云盘资产是否仍能获取 provider file id 和下载链接。
- 检查每个本地资产文件是否仍存在。
- 标记用户绕过系统删除的本地文件。
- 对缺失项只生成待处理任务，不直接全量下载。

状态：未开始。

### P13: 小型 UI 优化

目标：只处理不影响主流程正确性的界面细节。

- 日志区域最低高度。
- 各详情容器固定高度。
- 按钮文案收敛。
- 队列列表显示更多摘要字段。
- 图标、间距、滚动体验优化。
- 网站 logo / favicon。

状态：进行中。仅接受不干扰主线重构的小修。

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
