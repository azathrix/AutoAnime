# AutoAnime Refactor Plan

## 目标

AutoAnime 重新定位为追番资源管理网站：

- RSS/手动导入资源，自动整理番剧、集数、发布版本。
- 每集只选一个发布版本，避免重复下载。
- 下载器只负责把资源拉到本地媒体库，PikPak、qB、aria2 后续都走同一接口。
- 本地媒体库是最终事实，Jellyfin/Plex 只读取整理后的目录。
- UI 以“资源网站/追番站”的条目和集数视角呈现，而不是处理器队列视角。

## 核心架构

### Domain DB

数据库只保存最终事实：

- media_libraries
- works / entries
- seasonal_entries / library_entries
- episodes
- rss_candidates
- releases
- download_jobs
- download_artifacts
- local_assets
- settings

不再把普通运行过程当成业务事实。

### EpisodeJob Runtime

运行态以“每集”为主视角：

```text
EpisodeJob = entry_id + episode_number
```

阶段固定为：

```text
metadata -> select_release -> download -> localize -> nfo -> done
```

失败和重试是状态，不是新流程：

```text
pending / running / waiting / completed / failed
```

后端已经新增 `episode_jobs` 投影，先从 DB 事实 + Runtime 任务统一计算每集状态。后续 UI 只读这个数据源。

### 下载器接口

下载器统一抽象：

```text
submit(release, target)
poll(job)
fetch(remote_file, local_path)
cleanup(remote_file)
```

PikPak 只是默认 provider。后续可加 qB、aria2。

### 导入器接口

导入器分三类：

- RSS 导入：Mikan 新番追更。
- Torrent/Magnet 导入：手动给链接或搜索结果。
- Local 导入：扫描本地目录，解析文件名，整理入库。

导入器只产生候选和 release，后续统一进入 EpisodeJob。

## UI 改造方向

### 控制台

控制台主视角显示 EpisodeJob：

```text
番名 / 集数 / 当前阶段 / 状态 / 原因 / 操作
```

队列视角保留到维护/诊断页，不作为主要页面。

### 新番页

卡片式资源网站布局：

- 封面
- 标题
- 年份/季度/地区/标签
- 最新集状态
- 可观看/下载中/待配置/失败
- 点击进入集数列表和发布版本。

### 番剧库

与新番页同一组件体系：

- 支持媒体库筛选
- 年份、地区、类型、标签、多选筛选
- 本地可观看状态为主
- 云盘/下载器状态只作为后台信息

### 导入向导

分步骤：

1. 选择导入类型：本地 / 磁链 / 种子 / URL
2. 解析候选
3. 匹配 Bangumi/TMDB
4. 选择媒体库和命名规则
5. 确认入库并启动 EpisodeJob

## 测试策略

测试放在工程根目录 `Test/`。

必须覆盖：

- 字幕组/语言/分辨率/字幕形式解析
- 同集多发布自动选一条
- EpisodeJob 状态投影
- RSS 候选入库
- 下载器 provider fake 实现
- 本地导入命名整理
- API dashboard/episode-jobs 不 500

端到端 NAS 测试保留为人工验证：

```text
扫描 RSS -> 自动选集 -> 下载器 -> 本地文件 -> NFO -> Jellyfin 可看
```

## 分阶段交付

1. EpisodeJob Runtime 数据源和测试。
2. 下载器 service 收敛到 provider 接口。
3. UI 控制台改为 EpisodeJob 主视角。
4. 新番/番剧页统一资源卡片与筛选。
5. 本地/磁链导入向导。
6. 端到端测试和打包。
