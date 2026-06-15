# AutoAnime

NAS 上的媒体自动化入口。当前已实现第一条完整链路：

```txt
Mikan RSS -> 番剧聚合 -> 按番剧选择字幕组/分辨率 -> PikPak 离线下载
-> 轮询完成状态 -> 云端标准命名 -> NFO 生成 -> Jellyfin 扫描
```

项目名不绑定 Mikan/PikPak，后续可以继续扩展：

- 老番补全
- 电影下载
- 美剧追番
- 老美剧补全
- 多来源索引
- 多下载器
- Jellyfin 刷新和同步

## 当前功能

- Web UI，无登录
- Mikan RSS 扫描，可单独配置 RSS 代理
- 按番剧聚合 RSS 发布
- 每部番单独选择字幕组、分辨率、Bangumi ID、TMDB ID、季编号、补全策略
- 唯一字幕组/唯一分辨率时自动下载
- 按全局字幕组/分辨率优先级自动选择
- PikPak `access_token + refresh_token` 认证
- 可选 PikPak 账号密码认证
- PikPak 目标目录自动创建
- 提交后轮询 PikPak 任务状态
- 完成后尝试把云端文件重命名为 Jellyfin 标准单集名
- Bangumi 元数据刷新：中文名、年份、简介、封面 URL
- NFO 生成：`tvshow.nfo` 和每集 `.nfo`
- SQLite 保存配置和状态

## Docker 部署

```sh
cd /volume1/docker
# 上传 autoanime 到 /volume1/docker/autoanime
cd /volume1/docker/autoanime
docker compose up -d --build
```

Docker build 会自动构建 Vue + Element Plus 管理台，并打包进 FastAPI 镜像。

上传到 NAS 时不要带 `frontend/node_modules`、`backend/frontend_dist`、`data`。可以使用仓库外生成的干净包：

```txt
C:\Users\Administrator\source\autoanime-clean.zip
```

访问：

```txt
http://NAS_IP:32888
```

## 更新和数据

数据挂载在：

```txt
./data:/data
```

数据库：

```txt
./data/autoanime.db
```

NFO 输出：

```txt
默认 ./data/nfo
```

如果希望 Jellyfin 直接读取 NFO，请在 Web UI 的 `NFO 输出目录` 填 rclone 挂载到容器内的媒体目录，例如：

```txt
/media/anime
```

并在 compose 里额外挂载：

```yaml
    volumes:
      - ./data:/data
      - /volume1/Assets3/Media/pikpak-anime:/media/anime
```

只要不删除 `./data`，也不执行 `docker compose down -v`，更新代码和重新 build 不会重置配置。

源码部署更新：

```sh
docker compose up -d --build
```

## 推荐配置

PikPak 认证方式：

```txt
Access + Refresh Token
```

Mikan RSS 需要代理：

```txt
RSS 代理: http://NAS_IP:20171
```

PikPak 直连正常：

```txt
PikPak 代理: 留空
```

## 命名模板

番剧目录：

```txt
{title_cn} ({year}) [bangumi-{bangumi_id}]
```

季目录：

```txt
Season {season:02d}
```

单集名：

```txt
{title_cn} - S{season:02d}E{episode:02d} - {episode_title}
```

示例：

```txt
/Anime/葬送的芙莉莲 (2023) [bangumi-400602]/Season 01/葬送的芙莉莲 - S01E01 - 第01话.mkv
```

## 注意

- Bangumi/TMDB 的深度匹配还在第一阶段，当前优先支持手动 Bangumi ID 后刷新元数据。
- Mikan 历史补全需要搜索源，不是只靠 RSS 就能完整补旧集；这个会作为“老番补全/本季补全”模块继续扩展。
- PikPak 云端重命名依赖 PikPak 返回的 file_id；如果某些任务没有返回 file_id，会先标记完成，后续轮询再尝试拿回。
