<div align="center">
  <img src="frontend/public/anitrack-logo.png" alt="AniTrack" width="240" />

  <h1>AniTrack</h1>
  <p><strong>面向 NAS 的新番追更、自动下载与本地媒体库整理工具。</strong></p>

  <p>
    <a href="#快速开始"><img alt="Quick Start" src="https://img.shields.io/badge/Quick%20Start-Docker-2563eb?style=for-the-badge"></a>
    <a href="#功能特色"><img alt="Features" src="https://img.shields.io/badge/Features-RSS%20%2B%20Media%20Library-16a34a?style=for-the-badge"></a>
    <a href="#docker-hub"><img alt="Docker Hub" src="https://img.shields.io/badge/Docker%20Hub-Coming%20Soon-0ea5e9?style=for-the-badge&logo=docker&logoColor=white"></a>
  </p>

  <p>
    <img alt="Vue" src="https://img.shields.io/badge/Vue%203-42b883?style=flat-square&logo=vuedotjs&logoColor=white">
    <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white">
    <img alt="Python" src="https://img.shields.io/badge/Python%203.12-3776ab?style=flat-square&logo=python&logoColor=white">
    <img alt="NAS" src="https://img.shields.io/badge/NAS-Friendly-f59e0b?style=flat-square">
  </p>
</div>

---

AniTrack 可以把 Mikan RSS 订阅解析成标准集数资源，自动匹配 Bangumi/TMDB 元数据，按字幕组、分辨率、语言和下载器优先级选择资源，然后下载并整理到 NAS 本地媒体目录，供 Jellyfin、Plex 等媒体服务器直接扫描。

## 功能特色

- 新番追更：支持 Mikan RSS 订阅扫描、按季/篇章入库、日历视图查看每周更新。
- 自动选集：可配置字幕组、分辨率、语言、画质、来源与字幕优先级。
- 多下载器：支持 PikPak rclone、PikPak API、aria2、qBittorrent，并可拖拽调整优先级。
- 媒体库整理：统一管理新番、番剧、电影、电视剧，自动整理到 `/media/anime`、`/media/movies`、`/media/tv`。
- 元数据补全：支持 Bangumi 与 TMDB 信息匹配，展示海报、评分、标签和简介。
- 本地资源管理：支持手动收录、批量导入集数资源、选择服务器本地视频与字幕文件。
- 控制台：实时查看扫描、元数据、下载、本地状态、缓存清理等任务进度。
- 运维页面：支持日志搜索导出、定时任务、缓存清理、本地状态刷新、元数据刷新和资源整理。
- Docker 部署：前后端一体镜像，适合部署在 NAS、家用服务器或轻量 Linux 主机。

## 界面模块

| 模块 | 用途 |
| --- | --- |
| 新番 | 添加 RSS、扫描订阅、查看本季条目与可观看集数 |
| 日历 | 按周查看新番更新与下载状态 |
| 番剧 / 电影 / 电视剧 | 管理本地媒体条目、手动收录、筛选和维护资源 |
| 控制台 | 查看扫描器、下载队列、后台任务和任务进度 |
| 日志 | 搜索、导出和清空服务日志 |
| 设置 | 配置代理、下载器、自动选择规则、媒体库命名和定时器 |

## 快速开始

本地构建并启动：

```sh
docker compose up -d --build
```

默认访问地址：

```text
http://localhost:8096
```

NAS 部署时，建议把容器内目录映射到真实媒体库：

```yaml
services:
  anitrack:
    image: anitrack:local
    container_name: anitrack
    restart: unless-stopped
    ports:
      - "8096:8096"
    environment:
      APP_DATA_DIR: /data
      TZ: Asia/Shanghai
    volumes:
      - ./data:/data
      - ./media:/media
```

媒体目录约定：

```text
/media/anime
/media/movies
/media/tv
```

## Docker Hub

后续上传到 Docker Hub 后，可以把 `image` 替换成你的镜像名：

```yaml
image: your-dockerhub-name/anitrack:latest
```

然后直接启动：

```sh
docker compose up -d
```

## NAS 打包部署

项目内保留了干净打包与 NAS 上传脚本。

在 Windows 本机生成干净源码包：

```bat
package-clean.bat
```

产物目录：

```text
build\AniTrack-clean
```

上传到 NAS：

```bat
cd build\AniTrack-clean
upload-clean.bat
```

在 NAS 上启动或更新：

```sh
cd /volume1/docker/anitrack
chmod +x deploy-nas.sh
./deploy-nas.sh
```

如果 NAS 构建需要代理：

```sh
ANITRACK_PROXY=http://192.168.31.146:10808 ./deploy-nas.sh
```

## 基础配置

1. 在“新番”页面添加 Mikan RSS 订阅。
2. 在“设置 -> 下载器”中配置 PikPak、aria2 或 qBittorrent。
3. 在“设置 -> 自动选择”中调整字幕组、分辨率、语言和来源优先级。
4. 在“设置 -> 媒体库”中配置命名模板和可选的 bangumi.ini / NFO 生成。
5. 在“控制台”或“设置 -> 定时器”中启用自动扫描与维护任务。

## 说明

- 媒体库以 NAS 本地文件为最终状态，PikPak 只是可选下载器之一。
- Jellyfin/Plex 目前通过扫描本地媒体目录识别资源。
- `./data` 保存应用数据和配置，升级容器时请保留该目录。
- 旧番、电影、电视剧支持手动收录和本地资源管理，自动化程度会继续完善。
