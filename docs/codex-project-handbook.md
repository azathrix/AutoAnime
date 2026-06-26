# AniTrack Codex Project Handbook

这个文档用于让后续 Codex 快速理解 AniTrack 的产品方向、协作流程和 UI 改造方法，避免用户每次重复说明上下文。

## 项目定位

AniTrack 是一个面向 NAS / Jellyfin 的番剧与媒体下载整理工具。当前重点是：

- RSS / Mikan 追新番。
- 自动识别作品和集数资源。
- 通过下载器下载磁链 / 种子资源。
- 下载完成后整理到本地媒体目录。
- 在媒体库中展示作品、集数、本地可观看状态和元数据。

核心产品语义：

- PikPak / rclone / aria2 / qb 等都应被视为“下载器”，不是媒体库事实本身。
- 媒体事实以作品、条目、集数、本地文件状态为准。
- UI 不应直接暴露内部 processor / pipeline 细节，应显示为扫描任务、元数据任务、下载任务、整理任务、缓存任务等用户可理解的任务。

## 当前重要分支和快照

- UI 试装分支：`codex/ui-mochi-style`。
- 真实页面快照目录：`docs/ui-mockups/live-captures-20260626/`。
- 快照索引：`docs/ui-mockups/live-captures-20260626/manifest.md`。
- 设计规范：`docs/design-guidelines.md`。

UI 继续优化时，优先对照真实页面快照，不要直接从想象或旧 mockup 重新设计。

## 协作流程

每个任务开始时：

1. 查看当前分支和工作区状态。
2. 判断任务类型：业务修复、UI 改造、文档整理、打包部署。
3. 只改当前任务相关文件，保留用户和其他工具的无关改动。
4. 文件搜索优先用 `rg`。
5. 手工编辑优先用 `apply_patch`。

每个阶段完成时：

1. 做必要验证。
2. 说明验证命令和结果。
3. 提交一次，提交信息要能表达阶段目标。

如果需要用户部署测试：

1. 运行 `npm run build`。
2. 运行 `package-clean.bat`。
3. 告知产物目录 `build/AniTrack-clean`。

纯文档、快照整理、manifest 更新不需要构建，但最终回复要明确没有运行构建，因为没有代码路径变化。

## UI 工作流

AniTrack UI 当前方向是 `Mochi AniTrack`：浅色、现代、二次元、马卡龙、轻动效、工具化文案。

推荐 UI 改造流程：

1. 先确认用户要改的是实际运行页面还是概念设计。
2. 如果要和当前后台一致，使用 SingleFile 或用户提供的抓取页生成静态快照。
3. 将快照放到 `docs/ui-mockups/live-captures-YYYYMMDD/`。
4. 给每个快照按页面和状态重命名，例如：
   - `01-seasonal-list.html`
   - `02-seasonal-entry-detail.html`
   - `09-dashboard.html`
   - `14-settings-search-sources.html`
5. 写 `manifest.md` 记录原始文件和页面状态。
6. 让用户或其他 AI 先改静态 HTML 定稿。
7. 再把确认后的结构和样式回灌到 Vue 组件和 CSS 分片。

不要做的事：

- 不要只改颜色冒充结构重做。
- 不要让页面和真实后台结构脱节。
- 不要把复杂表单直接摊在主页面。
- 不要让按钮、文件选择按钮、弹窗按钮出现不同尺寸和风格。

## UI 设计规则摘要

详细规则见 `docs/design-guidelines.md`。这里保留高频约束：

- 主页面使用卡片、列表、状态摘要。
- 复杂编辑进入弹窗或抽屉。
- 设置页使用左侧分组 + 右侧面板。
- 搜索源、下载器使用列表 + 弹窗编辑。
- 空状态居中。
- 彩色按钮文字必须清晰，优先白色。
- Toggle 统一为开关在前、说明在后。
- 媒体卡片显示封面、标题、季 / 年份、可观看、评分、最近更新。
- 长路径、磁链、调试信息放展开区或详情里，不要塞进主表格。
- 动效只用于 hover、切换、加载和进度，保持轻量。

## 静态快照整理规范

当用户提供 SingleFile HTML：

1. 不移动用户 Downloads 中的原始文件。
2. 复制到 `docs/ui-mockups/live-captures-YYYYMMDD/`。
3. 按页面 / 状态重命名。
4. 生成 `manifest.md`。
5. 如果文件是超长单行，这是 SingleFile 正常现象，不要为了格式化而破坏它。

## 构建与打包

常用命令：

```powershell
npm run build
```

工作目录：`frontend`

```powershell
.\package-clean.bat
```

工作目录：项目根目录。

打包成功后产物目录：

```text
build/AniTrack-clean
```

`package-clean.bat` 会再次触发前端构建并写入版本号。需要用户测试前必须运行。

## 提交规范

- 阶段性提交，不要攒太多无关改动。
- UI 结构改造、静态快照整理、业务修复、文档更新分别提交。
- 提交前看 `git status --short --branch` 和 `git diff --stat`。
- 不要提交无关临时日志或本地运行残留。

## 常见陷阱

- Dashboard 不应承载所有页面大数据；页面数据应按页面接口加载。
- UI 分支不要顺手改后端业务逻辑。
- SingleFile 快照可能很大且单行，这是正常的。
- 页面视觉优化前先确认用户是要“真实后台快照”还是“概念原型”。
- 文件超过 1000 行时优先拆分，不要继续堆。
