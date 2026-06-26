# AniTrack Codex Guide

本文件是给 Codex / 其他代码代理的项目入口说明。开始改动前先读：

- `docs/codex-project-handbook.md`：项目目标、协作流程、构建打包、UI 快照流程。
- `docs/design-guidelines.md`：AniTrack UI 设计规范。
- `docs/ui-mockups/live-captures-20260626/manifest.md`：当前线上页面的 SingleFile 静态快照索引。

## 基本要求

- 使用中文和用户沟通。
- 不要擅自改 README，除非用户明确要求。
- 不要改后端 API、数据库或下载流程来完成纯 UI 任务。
- 可能已有用户改动；不要重置、回滚或覆盖无关文件。
- 手工文本编辑优先使用 `apply_patch`。
- 人工维护源码文件尽量控制在 1000 行以内；大文件继续拆组件、composable 或样式分片。
- 需要用户测试的前端改动，完成后运行 `package-clean.bat`，产物在 `build/AniTrack-clean`。
- 每个明确阶段完成后提交一次。

## UI 改造入口

当前 UI 试装分支为 `codex/ui-mochi-style`。

如果用户要求继续优化视觉：

1. 先看 `docs/ui-mockups/live-captures-20260626/` 的真实页面快照。
2. 如果用户提供改好的静态 HTML，先对照静态稿确认结构和样式，再回灌到 Vue。
3. 不要凭空重新设计一套和后台不一致的页面。
4. 控制台、媒体墙、发现页、设置页、详情抽屉、收录弹窗都要保持统一的 Mochi AniTrack 风格。

## 验证

- UI / 前端改动：至少运行 `npm run build`。
- 需要部署测试：运行 `package-clean.bat`。
- 纯文档或静态快照整理：不需要构建，但最终说明未运行构建的原因。
