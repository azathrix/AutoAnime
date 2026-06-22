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
- 下载器优先级列表：PikPak rclone、PikPak API、aria2、qBittorrent。
- 下载完成后整理到本地媒体目录。
- 新番、番剧、电影、电视剧使用统一媒体库页面。
- 控制台展示运行队列和定时任务，日志独立页面查看和导出。
- NFO 暂时禁用，当前不作为主流程能力。

## 媒体目录

容器内媒体根目录固定为：

```txt
/media
```

应用会按媒体类型使用以下目录，不存在时自动创建：

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

当前镜像名、容器名和 NAS 上传目录统一为 `anitrack`：

```txt
image: anitrack:local
container: anitrack
deploy dir: /volume1/docker/anitrack
```

先在本机生成干净源码目录：

```bat
package-clean.bat
```

产物目录：

```txt
build\AniTrack-clean
```

上传干净目录内容到 NAS：

```bat
cd build\AniTrack-clean
upload-clean.bat
```

`upload-clean.bat` 会上传脚本所在的当前目录，默认目标为：

```txt
\\InputName\docker\anitrack
```

如需改上传位置，可以设置：

```bat
set ANITRACK_UPLOAD_TARGET=\\InputName\docker\anitrack
upload-clean.bat
```

在 NAS 上启动或更新：

```sh
cd /volume1/docker/anitrack
chmod +x deploy-nas.sh
./deploy-nas.sh
```

部署脚本会重新构建并启动 `anitrack` 容器，也会顺手移除旧的 `autoanime` 容器，避免历史容器占用端口。`./data` 和映射的 `/media` 不会被删除。

如果 NAS 构建需要代理：

```sh
ANITRACK_PROXY=http://192.168.31.146:10808 ./deploy-nas.sh
```

访问地址：

```txt
http://NAS_IP:32888
```

## 上传包规则

只上传 `build\AniTrack-clean` 目录内容，不要上传开发环境目录。清理包会排除：

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

## GitHub 改名

GitHub 仓库可以直接改名：进入仓库 `Settings -> General -> Repository name`，把仓库名改成 `AniTrack` 或 `anitrack`。

改名后 GitHub 会保留旧地址跳转，但本地建议同步更新 remote：

```sh
git remote set-url origin git@github.com:<your-name>/AniTrack.git
```

如果你用 HTTPS：

```sh
git remote set-url origin https://github.com/<your-name>/AniTrack.git
```

Docker Hub 后续也建议使用 `anitrack` 作为镜像仓库名。

## 说明

- 重启后运行队列可能丢失，最终媒体数据和配置会保留。
- 导入老番、电影、电视剧的真实解析流程还在预留阶段。
- Jellyfin/Plex API 刷新暂未接入，当前由媒体服务器自行扫描本地目录。
