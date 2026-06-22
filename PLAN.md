# AniTrack 结构拆分优先改造计划

## Summary

当前优先级是先降低代码复杂度，避免继续在大文件里堆功能。硬约束：人工维护源码单文件不超过 1000 行。

执行顺序：

1. 删除/停用现有单元测试，避免旧测试继续干扰。
2. 拆后端大文件。
3. 拆前端大文件和样式。
4. 清理 NFO/旧概念。
5. 再补 TMDB、字幕上传、收录引导体验。

每个阶段完成后提交；需要测试前运行 `package-clean.bat`。

## 已完成阶段

- 单元测试已删除，不再维护 pytest/unittest 体系。
- 后端入口、路由、服务、schema、运行时辅助模块已拆分。
- 前端 `App.vue`、页面模板、动作逻辑和样式已拆分，所有前端源码文件低于 1000 行。
- NFO 当前按禁用处理：UI 不展示，README 不写为当前主流程；数据库字段暂保留。

## Key Changes

### 1. 去掉单元测试

- 删除或停用现有 `backend/tests` 和 `Test` 下的单元测试。
- 不新增 unittest/pytest 体系。
- README 不再要求运行单元测试。
- 保留简单验证手段：
  - Python 编译检查。
  - 前端 build。
  - clean package。
  - 手动 NAS 流程测试。

### 2. 后端拆分

- `backend/app/main.py` 只保留 FastAPI 装配、生命周期、静态资源和路由注册。
- 路由模块拆分为 dashboard/runtime/settings/rss/media/resources/uploads。
- 服务模块拆分为 dashboard_service、media_service、runtime_service、settings_service 等。
- `schemas.py` 放 Pydantic payload。
- `pipeline_schema.py` 放 pipeline 初始化。
- `maintenance.py` 放诊断和清理运行数据能力。
- API 路径保持不变，旧导入接口继续 404。

### 3. 前端拆分

- `frontend/src/App.vue` 改成壳层和状态聚合。
- 页面组件拆为 Dashboard、Seasonal、MediaCatalog、Calendar、Logs、Settings。
- 详情和弹窗拆为 EntryDrawer、EntryDialogs、PriorityList。
- 动作逻辑拆到 `composables/appActions.js`。
- 展示/解析工具拆到 `composables/viewHelpers.js`。
- CSS 按原顺序拆到 `styles/style-part-*.css`。

### 4. 旧概念清理

- NFO 暂时禁用：
  - UI 不展示 NFO。
  - README 不写 NFO 为当前流程。
  - 保留数据库字段，暂不迁移删除。
- 清理旧导入、旧云盘媒体库、旧任务表相关文案。
- 控制台只展示 Runtime 队列、定时任务、日志维护，不暴露旧任务概念。

### 5. 后续功能补齐

- TMDB 搜索：
  - 设置增加 TMDB token。
  - `/api/metadata/search?provider=tmdb&keyword=...` 返回真实候选。
  - 收录引导和编辑弹窗可搜索并回填。
- 字幕上传：
  - 单集字幕配置支持真实上传。
  - 批量字幕配置支持多文件上传。
  - 按文件名集数自动匹配到 `episode_subtitles`。
- 收录引导：
  - 名字、磁链、下载链接、本地文件共用同一向导。
  - 第二步展示 Bangumi/TMDB 候选，没有候选则手动补充。
  - 第三步展示资源/字幕匹配预览。
  - 最后一步按来源进入下载队列或上传整理队列。

## Public Interfaces

- 现有 API 路径保持兼容。
- 新增或补全：
  - `GET /api/metadata/search?provider=tmdb&keyword=...`
  - `POST /api/subtitles/upload`
  - `POST /api/entries/{entry_id}/subtitles/uploads/import`
- 设置接口新增：
  - `tmdb_token`
- 旧导入接口继续 404。

## Verification

不做单元测试，只做必要验证：

- `python -m py_compile` 检查后端语法。
- `npm run build` 检查前端构建。
- `package-clean.bat` 检查 clean 包。
- 手动确认源码文件行数均小于 1000。
- NAS 上做正式流程测试：
  - RSS 扫描到下载。
  - 下载并发配置。
  - 本地上传整理。
  - 媒体收录向导。
  - 字幕上传与匹配。

## Assumptions

- 单元测试全部不维护，后续靠构建检查和 NAS 实测。
- 1000 行限制只约束人工维护源码，不约束 lock 文件、构建产物、第三方文件。
- 先拆结构，再补功能，避免继续在大文件里堆代码。
- NFO 暂时禁用，不做主流程能力。
