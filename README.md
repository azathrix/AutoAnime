# AniTrack

AniTrack 是面向 NAS 的新番追更和本地媒体库整理工具。当前主线是把 RSS 发布解析成标准集数资源，按规则自动选择资源，交给下载器下载，完成后整理到本地 `media` 目录，供 Jellyfin/Plex 扫描。

```txt
RSS 订阅 -> Mikan/Bangumi 匹配 -> 条目入库 -> 自动选集
-> 下载器下载 -> 本地整理 -> Jellyfin/Plex 扫描本地媒体库
```

PikPak 只是下载器之一，不再作为媒体数据核心。媒体库以 NAS 本地文件为最终状态。

## 当前能力

- Mikan RSS 订阅扫描。
- 新番条目按季/篇章入库，不按作品折叠。
- Bangumi 元数据补全。
- 字幕组、分辨率、语言优先级自动选集。
- 支持下载器优先级列表：PikPak rclone、PikPak API、aria2、qBittorrent。
- 单个资源失败 3 次后切换下一个下载器。
- 下载完成后整理到本地媒体目录。
- NFO 暂时禁用，当前不作为主流程能力。
- 新番、番剧、电影、电视剧使用统一媒体库页面。
- 番剧/电影/电视剧导入和添加向导已预留 UI 骨架。
- 控制台展示运行队列和定时任务，日志独立页面查看和导出。

## 媒体目录

容器内媒体根目录固定为：

```txt
/media
```

应用会按媒体类型使用以下目录：

```txt
/media/anime
/media/movies
/media/tv
```

Docker 部署时通常映射到 NAS 的真实媒体目录，例如：

```yaml
/volume1/Assets3/Media:/media
```

Jellyfin/Plex 只需要扫描 NAS 本地真实目录。

## Docker 部署

当前部署脚本和默认容器目录仍沿用历史名称 `autoanime`，避免已有 NAS 路径和容器名被强制迁移；后续发布 Docker Hub 镜像时可以再统一调整镜像名。

先在本机生成干净源码目录：

```bat
package-clean.bat
```

产物目录：

```txt
build\AniTrack-clean
```

上传干净目录内容到 NAS：

```sh
/volume1/docker/autoanime
```

如果使用上传脚本，请进入 clean 包目录运行，脚本会同步“当前目录”到 NAS：

```bat
cd build\AniTrack-clean
upload-clean.bat
```

启动或更新：

```sh
cd /volume1/docker/autoanime
docker compose down --remove-orphans
docker compose up -d --build
```

也可以使用部署脚本：

```sh
cd /volume1/docker/autoanime
chmod +x deploy-nas.sh
./deploy-nas.sh
```

部署脚本会停止旧容器并重新构建启动。`./data` 和映射的 `/media` 不会被删除。

如果 NAS 构建需要代理：

```sh
AUTOANIME_PROXY=http://192.168.31.146:10808 ./deploy-nas.sh
```

访问地址：

```txt
http://NAS_IP:32888
```

## 上传包规则

需要上传的是 `build\AniTrack-clean` 目录内容。`upload-clean.bat` 也按这个约定上传脚本所在目录。不要手动上传开发环境目录：

```txt
frontend/node_modules
data
test-data
*.zip
*.log
```

`package-clean.bat` 会构建前端，并把 `backend/frontend_dist` 放入干净包中。

## 配置要点

- RSS 订阅在“新番”页面添加。
- RSS 定时扫描和恢复调度在“控制台”的“定时任务”中配置。
- 下载器在“设置 -> 下载器”中按优先级配置。
- 命名模板在“设置 -> 媒体库”中配置。
- 自动选集规则在“设置 -> 自动选择”中按动画、电影、电视剧分别配置。

## 说明

- 重启后运行队列可能丢失，最终媒体数据和配置会保留。
- 导入老番、电影、电视剧的真实解析流程还在预留阶段。
- Jellyfin/Plex API 刷新暂未接入，当前由媒体服务器自行扫描本地目录。
