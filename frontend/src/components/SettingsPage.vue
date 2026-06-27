<script>
import draggable from 'vuedraggable'
import { appContextComponent } from '../composables/appContext'
import PriorityList from './PriorityList.vue'

export default appContextComponent({ draggable, PriorityList })
</script>

<template>
  <section v-if="view === 'settings'" class="settings-page mochi-settings-page">
    <el-card class="settings-workshop-card">
      <template #header>
        <div class="settings-card-head">
          <div>
            <strong>设置中心</strong>
            <span>全局行为、下载器、搜索源和维护操作</span>
          </div>
          <el-button type="primary" :loading="savingSettings" @click="saveAllSettings">保存设置</el-button>
        </div>
      </template>

      <el-form :model="settings" label-position="top" class="settings-form">
        <el-tabs tab-position="left" class="settings-workshop-tabs">
          <el-tab-pane label="基础">
            <div class="settings-panel-grid">
              <div class="settings-summary-card">
                <div class="settings-summary-head">
                  <h3>全局代理</h3>
                  <p>所有外部请求默认复用这里的代理；留空则直连。</p>
                </div>
                <el-form-item label="代理地址">
                  <el-input v-model="settings.rss_proxy" placeholder="例: http://NAS_IP:20171" />
                </el-form-item>
              </div>

              <div class="settings-summary-card">
                <div class="settings-summary-head">
                  <h3>TMDB Token</h3>
                  <p>用于电影、电视剧和部分动画的元数据搜索。</p>
                </div>
                <el-form-item label="API Read Access Token">
                  <el-input v-model="settings.tmdb_token" show-password placeholder="请输入 TMDB token" />
                </el-form-item>
              </div>

              <div class="settings-summary-card">
                <label class="settings-switch-line">
                  <el-switch v-model="settings.backfill_current_season" />
                  <span><strong>补全本季</strong><em>RSS 扫描新番后尝试补齐当前季缺失集数。</em></span>
                </label>
              </div>

              <div class="settings-summary-card">
                <label class="settings-switch-line">
                  <el-switch v-model="settings.auto_generate_nfo" />
                  <span><strong>生成 NFO 元数据</strong><em>整理或刷新元数据后校验 Jellyfin 可读的 NFO。</em></span>
                </label>
                <transition name="fade-slide">
                  <div v-if="settings.auto_generate_nfo" class="nfo-inline-options">
                    <el-form-item label="写入模式">
                      <el-select v-model="settings.nfo_write_mode">
                        <el-option label="空缺补齐" value="fill_missing" />
                        <el-option label="覆盖更新" value="overwrite" />
                      </el-select>
                    </el-form-item>
                  </div>
                </transition>
              </div>
            </div>
          </el-tab-pane>

          <el-tab-pane label="自动选择">
            <el-tabs tab-position="left" class="nested-settings-tabs">
              <el-tab-pane label="动画">
                <div class="settings-section-toolbar">
                  <div><strong>动画自动选集</strong><span>用于新番和番剧的资源优先级</span></div>
                  <el-button plain @click="resetSelectionRules('anime')">重置动画规则</el-button>
                </div>
                <div class="priority-layout">
                  <PriorityList title="字幕组优先级" v-model="settings.subtitle_priority" placeholder="添加字幕组" />
                  <PriorityList title="分辨率优先级" v-model="settings.resolution_priority" placeholder="添加分辨率" />
                  <PriorityList title="主字幕语言优先级" v-model="settings.language_priority" placeholder="添加主字幕语言" />
                  <PriorityList title="副字幕语言优先级" v-model="settings.secondary_language_priority" placeholder="添加副字幕语言" />
                </div>
              </el-tab-pane>
              <el-tab-pane label="电影">
                <div class="settings-section-toolbar">
                  <div><strong>电影自动选集</strong><span>只影响电影收录和资源选择</span></div>
                  <el-button plain @click="resetSelectionRules('movie')">重置电影规则</el-button>
                </div>
                <div class="priority-layout">
                  <PriorityList title="画质优先级" v-model="settings.movie_quality_priority" placeholder="添加画质，如 2160p" />
                  <PriorityList title="来源优先级" v-model="settings.movie_source_priority" placeholder="添加来源，如 BluRay" />
                  <PriorityList title="字幕优先级" v-model="settings.movie_subtitle_priority" placeholder="添加字幕，如 简体" />
                </div>
              </el-tab-pane>
              <el-tab-pane label="电视剧">
                <div class="settings-section-toolbar">
                  <div><strong>电视剧自动选集</strong><span>只影响电视剧收录和资源选择</span></div>
                  <el-button plain @click="resetSelectionRules('tv')">重置电视剧规则</el-button>
                </div>
                <div class="priority-layout">
                  <PriorityList title="画质优先级" v-model="settings.tv_quality_priority" placeholder="添加画质，如 1080p" />
                  <PriorityList title="来源优先级" v-model="settings.tv_source_priority" placeholder="添加来源，如 WEB-DL" />
                  <PriorityList title="字幕优先级" v-model="settings.tv_subtitle_priority" placeholder="添加字幕，如 双语" />
                </div>
              </el-tab-pane>
            </el-tabs>
          </el-tab-pane>

          <el-tab-pane label="下载器">
            <div class="settings-section-toolbar">
              <div><strong>下载器优先级</strong><span>按顺序尝试可用下载器；详细字段在编辑窗口里配置。</span></div>
              <div class="settings-toolbar-actions">
                <el-button plain @click="openProcessorSettings">下载并发</el-button>
                <el-button plain @click="openDownloaderDialog(-1)">添加下载器</el-button>
                <el-button type="primary" :loading="savingSettings" @click="saveAllSettings">保存设置</el-button>
              </div>
            </div>
            <draggable v-model="settings.downloaders" item-key="id" handle=".drag-handle" class="summary-list">
              <template #item="{ element, index }">
                <div class="summary-row" :class="{ disabled: !element.enabled }">
                  <span class="drag-handle">⋮⋮</span>
                  <span class="summary-rank">{{ index + 1 }}</span>
                  <div class="summary-main">
                    <strong>{{ element.name || downloaderTypeText(element.type) }}</strong>
                    <span>{{ downloaderTypeText(element.type) }} · {{ downloaderSummary(element) }}</span>
                  </div>
                  <el-tag :type="element.enabled ? 'success' : 'info'" size="small">{{ element.enabled ? '启用' : '停用' }}</el-tag>
                  <div class="summary-actions">
                    <el-button size="small" plain @click="openDownloaderDialog(index)">编辑</el-button>
                    <el-button size="small" type="danger" plain @click="removeDownloader(index)">删除</el-button>
                  </div>
                </div>
              </template>
            </draggable>
            <el-empty v-if="!(settings.downloaders || []).length" description="暂无下载器，添加一个后保存设置即可使用。" />
          </el-tab-pane>

          <el-tab-pane label="搜索源">
            <div class="settings-section-toolbar">
              <div><strong>搜索源</strong><span>发现页和本季补全会使用启用的来源。</span></div>
              <el-button type="primary" plain @click="openSearchSourceDialog(null)">添加源</el-button>
            </div>
            <div class="summary-list" v-loading="searchSourcesLoading">
              <div v-for="item in searchSources" :key="item.id" class="summary-row simple-summary-row" :class="{ disabled: !Number(item.enabled || 0) }">
                <div class="summary-main">
                  <strong>{{ item.name }}</strong>
                  <span>{{ searchSourceKindText(item.kind) }} · {{ item.base_url || '未配置地址' }}</span>
                  <small v-if="item.last_error">{{ item.last_error }}</small>
                </div>
                <el-tag :type="Number(item.enabled || 0) ? 'success' : 'info'" size="small">{{ Number(item.enabled || 0) ? '启用' : '停用' }}</el-tag>
                <div class="summary-actions">
                  <el-switch :model-value="Number(item.enabled || 0)" :active-value="1" :inactive-value="0" @change="toggleSearchSource(item)" />
                  <el-button size="small" plain @click="testSearchSource(item)">测试</el-button>
                  <el-button size="small" plain @click="openSearchSourceDialog(item)">编辑</el-button>
                  <el-popconfirm title="确定删除该搜索源？" @confirm="deleteSearchSource(item.id)">
                    <template #reference><el-button size="small" type="danger" plain>删除</el-button></template>
                  </el-popconfirm>
                </div>
              </div>
              <el-empty v-if="!searchSources.length" description="暂无搜索源，添加后可在发现页搜索资源。" />
            </div>
          </el-tab-pane>

          <el-tab-pane label="定时器">
            <div class="summary-list">
              <div v-for="item in dashboard.schedules || []" :key="item.id" class="summary-row simple-summary-row">
                <div class="summary-main">
                  <strong>{{ item.name || item.action_name || item.action }}</strong>
                  <span>{{ item.action_name || item.action }} · {{ Number(item.interval_minutes || 0) }} 分钟 · {{ item.last_run_at || '未执行' }}</span>
                </div>
                <el-tag :type="Number(item.enabled || 0) ? 'success' : 'info'" size="small">{{ Number(item.enabled || 0) ? '启用' : '关闭' }}</el-tag>
                <div class="summary-actions">
                  <el-button size="small" plain @click="triggerSchedule(item)">立即执行</el-button>
                  <el-button size="small" type="primary" plain @click="openScheduledSettings(item)">设置</el-button>
                </div>
              </div>
              <el-empty v-if="!(dashboard.schedules || []).length" description="暂无定时器" />
            </div>
          </el-tab-pane>

          <el-tab-pane label="维护">
            <div class="maintenance-grid-layout compact-maintenance">
              <div class="maintenance-group-card">
                <div class="group-header"><h3>缓存</h3><p>清理 RSS 与处理缓存。</p></div>
                <div class="group-body">
                  <div class="action-item-row"><div class="action-desc"><strong>清除 RSS 缓存</strong><span>重新处理订阅条目。</span></div><el-button plain @click="runAction('/cache/rss/clear')">清除</el-button></div>
                  <div class="action-item-row"><div class="action-desc"><strong>清除过期缓存</strong><span>移除已过期缓存记录。</span></div><el-button plain @click="runAction('/cache/expired/clear')">清理</el-button></div>
                  <div class="action-item-row warning-border"><div class="action-desc"><strong>清空全部处理缓存</strong><span>包含元数据和匹配缓存。</span></div><el-popconfirm title="会清空全部处理缓存，包括元数据和匹配缓存。确定？" @confirm="runAction('/cache/clear')"><template #reference><el-button type="warning" plain>清空</el-button></template></el-popconfirm></div>
                </div>
              </div>

              <div class="maintenance-group-card">
                <div class="group-header"><h3>元数据与本地状态</h3><p>刷新媒体信息和本地文件可观看状态。</p></div>
                <div class="group-body">
                  <div class="action-item-row"><div class="action-desc"><strong>刷新全部本地状态</strong><span>检测本地文件是否真实存在。</span></div><el-button plain @click="refreshAllLocalStatus">刷新</el-button></div>
                  <div class="action-item-row"><div class="action-desc"><strong>刷新全部元数据</strong><span>按现有 ID 重抓 Bangumi/TMDB。</span></div><el-popconfirm title="会按现有 Bangumi/TMDB ID 刷新所有条目元数据，并按设置校验 NFO。确定执行？" @confirm="refreshAllMetadata"><template #reference><el-button plain>刷新</el-button></template></el-popconfirm></div>
                  <div class="action-item-row"><div class="action-desc"><strong>整理全部本地资源</strong><span>把已绑定文件整理到命名规则路径。</span></div><el-popconfirm title="会把已绑定且存在的本地文件整理到当前命名规则路径，目标同名文件会被覆盖。确定执行？" @confirm="organizeAllLocalFiles"><template #reference><el-button plain>整理</el-button></template></el-popconfirm></div>
                </div>
              </div>

              <div class="maintenance-group-card">
                <div class="group-header"><h3>数据修复</h3><p>处理旧数据或无效集数。</p></div>
                <div class="group-body">
                  <div class="action-item-row"><div class="action-desc"><strong>迁移集数模型</strong><span>迁移为每集一条资源。</span></div><el-popconfirm title="迁移前会自动备份数据库。确定把旧资源模型迁移为每集一条资源，并按纯作品名目录计算路径？" @confirm="migrateEpisodeModel"><template #reference><el-button plain>迁移</el-button></template></el-popconfirm></div>
                  <div class="action-item-row"><div class="action-desc"><strong>修复为纯作品名路径</strong><span>移动识别到的旧文件并同步数据库。</span></div><el-popconfirm title="会把已识别到的旧本地文件移动到纯作品名目录，并同步修复数据库状态。确定执行？" @confirm="repairLocalPaths"><template #reference><el-button plain>修复</el-button></template></el-popconfirm></div>
                  <div class="action-item-row warning-border"><div class="action-desc"><strong>清理无效集数</strong><span>清理无法识别集数的记录。</span></div><el-popconfirm title="会清理无法识别集数的发布、资源、字幕和下载记录。确定执行？" @confirm="runAction('/maintenance/cleanup-invalid-episodes')"><template #reference><el-button type="warning" plain>清理</el-button></template></el-popconfirm></div>
                </div>
              </div>

              <div class="maintenance-group-card danger-card">
                <div class="group-header"><h3>危险操作</h3><p>不会删除本地媒体文件，但会清空系统记录。</p></div>
                <div class="group-body">
                  <div class="action-item-row danger-border"><div class="action-desc"><strong>清除所有数据</strong><span>清空番剧、候选、任务、本地资源记录和日志。</span></div><el-popconfirm title="会清空番剧、候选、任务、下载记录、本地资源记录和日志。确定？" @confirm="runAction('/system/clear-data')"><template #reference><el-button type="danger" plain>清除</el-button></template></el-popconfirm></div>
                </div>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </el-form>
    </el-card>
  </section>

  <el-dialog v-model="searchSourceDialogOpen" :title="searchSourceEditingId ? '编辑搜索源' : '添加搜索源'" width="620px" top="6vh" class="config-dialog">
    <el-form :model="searchSourceForm" label-position="top">
      <div class="form-row">
        <el-form-item label="名称"><el-input v-model="searchSourceForm.name" placeholder="例如 Mikan / Prowlarr" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="searchSourceForm.kind">
            <el-option label="Mikan" value="mikan" />
            <el-option label="RSS" value="rss" />
            <el-option label="Torznab" value="torznab" />
            <el-option label="Prowlarr" value="prowlarr" />
            <el-option label="Jackett" value="jackett" />
          </el-select>
        </el-form-item>
      </div>
      <el-form-item label="地址"><el-input v-model="searchSourceForm.base_url" placeholder="https://..." /></el-form-item>
      <el-form-item label="API Key / Token"><el-input v-model="searchSourceForm.api_key" show-password placeholder="可选" /></el-form-item>
      <el-form-item label="分类"><el-input v-model="searchSourceForm.categories" placeholder="可选，例如 5070,5080" /></el-form-item>
      <div class="form-row">
        <el-form-item label="超时秒数"><el-input v-model="searchSourceForm.timeout_seconds" type="number" min="1" /></el-form-item>
        <el-form-item label="限速秒数"><el-input v-model="searchSourceForm.rate_limit_seconds" type="number" min="0" /></el-form-item>
      </div>
      <label class="settings-switch-line dialog-switch">
        <el-switch v-model="searchSourceForm.enabled" />
        <span><strong>启用搜索源</strong><em>关闭后发现页和补全不会使用它。</em></span>
      </label>
    </el-form>
    <template #footer>
      <el-button @click="searchSourceDialogOpen = false">取消</el-button>
      <el-button type="primary" @click="saveSearchSource">保存</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="downloaderDialogOpen" :title="downloaderEditingIndex >= 0 ? '编辑下载器' : '添加下载器'" width="720px" top="5vh" class="config-dialog">
    <el-form :model="downloaderForm" label-position="top">
      <div class="form-row">
        <el-form-item label="名称"><el-input v-model="downloaderForm.name" placeholder="例如 PikPak" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="downloaderForm.type">
            <el-option label="PikPak rclone" value="pikpak_rclone" />
            <el-option label="PikPak API" value="pikpak_api" />
            <el-option label="aria2" value="aria2" />
            <el-option label="qBittorrent" value="qb" />
          </el-select>
        </el-form-item>
      </div>
      <div class="form-row">
        <el-form-item label="远端目录 / 临时目录"><el-input v-model="downloaderForm.remote_dir" placeholder="/Temp" /></el-form-item>
        <el-form-item label="失败重试次数"><el-input v-model="downloaderForm.max_attempts" type="number" min="1" /></el-form-item>
      </div>
      <template v-if="downloaderForm.type === 'pikpak_rclone'">
        <div class="form-row">
          <el-form-item label="rclone remote"><el-input v-model="downloaderForm.rclone_remote" placeholder="pikpak" /></el-form-item>
          <el-form-item label="rclone 命令"><el-input v-model="downloaderForm.rclone_command" placeholder="rclone" /></el-form-item>
        </div>
        <el-form-item label="rclone.conf 路径"><el-input v-model="downloaderForm.rclone_config_path" placeholder="/data/rclone/rclone.conf" /></el-form-item>
        <div class="form-row">
          <el-form-item label="PikPak 用户名"><el-input v-model="downloaderForm.username" /></el-form-item>
          <el-form-item label="PikPak 密码"><el-input v-model="downloaderForm.password" show-password /></el-form-item>
        </div>
      </template>
      <template v-if="downloaderForm.type === 'pikpak_api'">
        <el-form-item label="认证方式">
          <el-select v-model="downloaderForm.auth_mode">
            <el-option label="Token" value="token" />
            <el-option label="账号密码" value="password" />
          </el-select>
        </el-form-item>
        <div v-if="downloaderForm.auth_mode === 'password'" class="form-row">
          <el-form-item label="用户名"><el-input v-model="downloaderForm.username" /></el-form-item>
          <el-form-item label="密码"><el-input v-model="downloaderForm.password" show-password /></el-form-item>
        </div>
        <div v-else class="form-row">
          <el-form-item label="Access Token"><el-input v-model="downloaderForm.access_token" show-password /></el-form-item>
          <el-form-item label="Refresh Token"><el-input v-model="downloaderForm.refresh_token" show-password /></el-form-item>
        </div>
      </template>
      <template v-if="['aria2', 'qb'].includes(downloaderForm.type)">
        <el-form-item label="RPC / Web UI 地址"><el-input v-model="downloaderForm.rpc_url" placeholder="http://..." /></el-form-item>
      </template>
      <el-form-item v-if="downloaderForm.type === 'aria2'" label="aria2 Token"><el-input v-model="downloaderForm.token" show-password /></el-form-item>
      <div v-if="downloaderForm.type === 'qb'" class="form-row">
        <el-form-item label="qB 用户名"><el-input v-model="downloaderForm.username" /></el-form-item>
        <el-form-item label="qB 密码"><el-input v-model="downloaderForm.password" show-password /></el-form-item>
      </div>
      <label class="settings-switch-line dialog-switch">
        <el-switch v-model="downloaderForm.enabled" />
        <span><strong>启用下载器</strong><em>关闭后下载任务不会选择它。</em></span>
      </label>
    </el-form>
    <template #footer>
      <el-button @click="downloaderDialogOpen = false">取消</el-button>
      <el-button type="primary" @click="saveDownloaderDialog">保存</el-button>
    </template>
  </el-dialog>
</template>
