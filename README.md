# AutoAnime

NAS 上的媒体自动化入口。当前目标流程：

```txt
RSS/导入 -> 元数据匹配 -> 自动选择发布 -> 下载到云盘
-> 云盘完成 -> 同步到 NAS 本地 -> 生成 NFO -> Jellyfin 扫本地
```

云盘是长期媒体库，本地 NAS 是观看缓存。取消同步只删除本地文件和 NFO，不删除云盘资源。

## 当前能力

- Mikan RSS 扫描。
- 按番剧聚合 RSS 发布。
- Bangumi 元数据刷新。
- 字幕组、分辨率、语言优先级自动选择。
- PikPak 离线下载到云盘。
- PikPak 云盘任务轮询。
- 云盘完成后登记云盘资源。
- 手动同步到 NAS 本地。
- 追更自动同步开关。
- 同步成功后在本地媒体库生成 NFO。
- SQLite 保存配置和状态。

## Docker 部署

本地打干净包：

```bat
package-clean.bat
```

包会输出到：

```txt
build\
```

直接上传源码到 NAS 调试目录：

```bat
upload-clean.bat
```

默认上传到：

```txt
\\InputName\docker\autoanime
```

上传干净包到：

```sh
/volume1/docker/autoanime
```

启动或更新：

```sh
cd /volume1/docker/autoanime
docker compose down --remove-orphans
docker compose up -d --build
```

这会先停止并删除当前 compose 管理的容器，再重新构建启动。挂载的 `./data` 和媒体目录不会被删除。

访问：

```txt
http://NAS_IP:32888
```

## 数据和媒体挂载

运行数据：

```yaml
./data:/data
```

本地媒体根目录：

```yaml
/volume1/Assets3/Media:/media
```

默认 PikPak 同步目录：

```txt
/media/pikpak-anime
```

如果后续接入其他云盘，可以在 `/media` 下新增目录，例如：

```txt
/media/baidu-anime
/media/aliyun-movie
```

Jellyfin 只需要扫描 NAS 本地真实目录，例如：

```txt
/volume1/Assets3/Media/pikpak-anime
```

## 推荐配置

PikPak 认证方式：

```txt
Access + Refresh Token
```

扫描间隔默认：

```txt
60 分钟
```

也可以在 UI 手动点击“扫描 RSS”“刷新状态”“处理同步”。

## 上传包规则

不要上传：

```txt
frontend/node_modules
backend/frontend_dist
backend/app/__pycache__
data
test-data
*.zip
```

只上传源码和部署文件。Docker 会在 NAS 上重新安装依赖并构建前端。

## 注意

- 当前云盘 provider 先支持 PikPak，后续可扩展其他云盘。
- TMDB 深度匹配还未完成。
- 老番/电影/美剧补全需要后续搜索源或导入源。
- Jellyfin API 刷新暂时不接入，等主流程稳定后再做。
