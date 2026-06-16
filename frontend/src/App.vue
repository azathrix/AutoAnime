<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">A</div>
        <div>
          <strong>AutoAnime</strong>
          <span>Media automation</span>
        </div>
      </div>
      <nav>
        <button :class="{ active: view === 'dashboard' }" @click="view = 'dashboard'"><el-icon><DataBoard /></el-icon> 控制台</button>
        <button :class="{ active: view === 'library' }" @click="view = 'library'"><el-icon><Collection /></el-icon> 番剧库</button>
        <button :class="{ active: view === 'settings' }" @click="view = 'settings'"><el-icon><Setting /></el-icon> 设置</button>
      </nav>
    </aside>

    <main class="main">
      <header class="hero">
        <div>
          <p class="eyebrow">Mikan · PikPak · Local</p>
          <h1>{{ pageTitle }}</h1>
          <p class="hero-sub">队列自动轮询，手动扫描会按顺序触发完整追番处理。</p>
        </div>
        <div class="hero-actions">
          <el-switch
            v-model="autoRefresh"
            inline-prompt
            active-text="自动"
            inactive-text="手动"
          />
          <el-select v-model="refreshInterval" class="refresh-select" :disabled="!autoRefresh">
            <el-option label="3 秒" :value="3000" />
            <el-option label="5 秒" :value="5000" />
            <el-option label="10 秒" :value="10000" />
            <el-option label="30 秒" :value="30000" />
          </el-select>
          <el-button :icon="Refresh" @click="reload" :loading="loading">刷新状态</el-button>
          <el-button v-if="view === 'dashboard'" type="primary" :icon="Search" :disabled="scanRunning" @click="runAction('/scan')">扫描全部</el-button>
        </div>
      </header>

      <section v-if="view === 'dashboard'" class="content-grid">
        <div class="metric-card">
          <span>番剧</span>
          <strong>{{ dashboard.series.length }}</strong>
        </div>
        <div class="metric-card">
          <span>云盘资源</span>
          <strong>{{ cloudAssetTotal }}</strong>
        </div>
        <div class="metric-card">
          <span>本地资源</span>
          <strong>{{ localAssetTotal }}</strong>
        </div>
        <div class="metric-card">
          <span>待处理</span>
          <strong>{{ issueCount }}</strong>
        </div>

        <el-card class="span-4 console-card">
          <template #header>队列状态</template>
          <div v-if="scanOperation" class="scan-progress">
            <div>
              <strong>{{ scanOperation.name }}</strong>
              <span>{{ scanOperation.message || '正在执行' }}</span>
            </div>
            <el-progress :percentage="scanProgress" :status="scanOperation.status === 'failed' ? 'exception' : undefined" />
          </div>
          <div class="queue-grid">
            <div v-for="queue in dashboard.queue_summary" :key="queue.key" class="queue-card">
              <div class="queue-title">
                <strong>{{ queue.name }}</strong>
                <el-tag size="small" :type="queue.failed ? 'danger' : queue.running ? 'warning' : queue.pending ? 'info' : 'success'">
                  {{ queueState(queue) }}
                </el-tag>
              </div>
              <p>{{ queue.description }}</p>
              <div class="queue-counts">
                <span>待处理 <b>{{ queue.pending }}</b></span>
                <span>运行中 <b>{{ queue.running }}</b></span>
                <span>失败 <b>{{ queue.failed }}</b></span>
              </div>
            </div>
          </div>
        </el-card>

        <el-card class="span-4 console-card">
          <el-tabs v-model="consoleTab">
            <el-tab-pane label="待处理" name="issues">
              <el-alert
                v-if="dashboard.rss_candidates.length"
                type="info"
                show-icon
                :closable="false"
                title="RSS 发布会先进入暂存区；只有完成 Mikan 匹配和元数据刷新后，才会出现在番剧库并继续入云盘。"
                class="settings-alert"
              />
              <el-table v-if="dashboard.rss_candidates.length" :data="dashboard.rss_candidates" height="260" class="candidate-table">
                <el-table-column prop="status" label="状态" width="100">
                  <template #default="{ row }"><el-tag type="warning">{{ row.status }}</el-tag></template>
                </el-table-column>
                <el-table-column prop="series_title" label="解析标题" min-width="220" show-overflow-tooltip />
                <el-table-column prop="episode_number" label="集" width="70" />
                <el-table-column prop="subtitle_group" label="字幕组" width="130" show-overflow-tooltip />
                <el-table-column prop="resolution" label="分辨率" width="100" />
                <el-table-column prop="language" label="语言" width="90" />
                <el-table-column prop="reason" label="原因" min-width="180" show-overflow-tooltip />
                <el-table-column prop="title" label="RSS 标题" min-width="260" show-overflow-tooltip />
              </el-table>
              <el-empty v-if="!issues.length" description="当前没有需要人工处理的问题" />
              <el-table v-else :data="issues" height="420">
                <el-table-column prop="type" label="类型" width="130">
                  <template #default="{ row }"><el-tag :type="row.level">{{ row.type }}</el-tag></template>
                </el-table-column>
                <el-table-column prop="title" label="番剧" min-width="180" />
                <el-table-column prop="message" label="原因" min-width="260" />
                <el-table-column label="操作" width="120">
                  <template #default="{ row }"><el-button size="small" @click="row.series_id && openSeries(row.series_id)">处理</el-button></template>
                </el-table-column>
              </el-table>
            </el-tab-pane>

            <el-tab-pane label="云盘队列" name="cloud">
              <el-table :data="runningRows" height="420">
                <el-table-column prop="status" label="状态" width="120">
                  <template #default="{ row }"><el-tag :type="taskTag(row.status)">{{ row.status }}</el-tag></template>
                </el-table-column>
                <el-table-column prop="title_cn" label="番剧" min-width="180" />
                <el-table-column prop="episode_number" label="集" width="70" />
                <el-table-column prop="subtitle_group" label="字幕组" width="140" />
                <el-table-column prop="resolution" label="分辨率" width="110" />
                <el-table-column prop="language" label="语言" width="90" />
                <el-table-column prop="target_dir" label="目标目录" min-width="260" />
                <el-table-column prop="last_error" label="错误" min-width="220" />
              </el-table>
            </el-tab-pane>

            <el-tab-pane label="本地同步" name="sync">
              <el-table :data="dashboard.sync_tasks" height="420">
                <el-table-column prop="status" label="状态" width="120">
                  <template #default="{ row }"><el-tag :type="taskTag(row.status)">{{ row.status }}</el-tag></template>
                </el-table-column>
                <el-table-column prop="title_cn" label="番剧" min-width="180" />
                <el-table-column prop="source_path" label="云盘路径" min-width="260" />
                <el-table-column prop="target_path" label="本地路径" min-width="260" />
                <el-table-column prop="last_error" label="错误" min-width="220" />
              </el-table>
            </el-tab-pane>

            <el-tab-pane label="操作日志" name="logs">
              <div class="log-layout">
                <div class="operation-list">
                  <div v-for="op in dashboard.operations.slice(0, 10)" :key="op.id" class="operation-item">
                    <el-tag :type="taskTag(op.status)">{{ op.status }}</el-tag>
                    <div>
                      <strong>{{ op.name }}</strong>
                      <span>{{ op.message || '处理中' }}</span>
                    </div>
                  </div>
                </div>
                <pre class="server-log">{{ serverLogText }}</pre>
              </div>
            </el-tab-pane>

            <el-tab-pane label="维护" name="maintenance">
              <div class="maintenance-actions">
                <el-button type="primary" :icon="Search" :disabled="scanRunning" @click="runAction('/scan')">扫描全部</el-button>
                <el-button :icon="Refresh" @click="runAction('/tasks/poll')">刷新 PikPak 状态</el-button>
                <el-button @click="runAction('/cloud/scan')">扫描云盘库</el-button>
                <el-button type="warning" @click="runAction('/tasks/retry-failed')">重试失败任务</el-button>
                <el-popconfirm title="会清空番剧、候选、任务、云盘资源、本地同步记录和日志。确定？" @confirm="runAction('/system/clear-data')">
                  <template #reference>
                    <el-button type="danger" plain>清除所有数据</el-button>
                  </template>
                </el-popconfirm>
              </div>
            </el-tab-pane>
          </el-tabs>
        </el-card>
      </section>

      <section v-if="view === 'library'" class="library">
        <div class="toolbar">
          <el-input v-model="keyword" clearable placeholder="搜索番剧、Bangumi ID、字幕组" />
          <el-segmented v-model="seriesFilter" :options="['全部', '待配置', '已入云盘', '已同步', '失败']" />
        </div>
        <div class="anime-grid">
          <article v-for="item in filteredSeries" :key="item.id" class="anime-card" @click="openSeries(item.id)">
            <div class="cover">
              <img v-if="item.poster_url" :src="item.poster_url" />
              <span v-else>{{ item.title_cn?.slice(0, 2) || 'AN' }}</span>
            </div>
            <div class="anime-body">
              <h3>{{ item.title_cn }}</h3>
              <p>Bangumi: {{ item.bangumi_id || '未关联' }}</p>
              <div class="tagline">
                <el-tag size="small">{{ item.group_count }} 字幕组</el-tag>
                <el-tag size="small" type="success">{{ item.resolution_count }} 分辨率</el-tag>
                <el-tag size="small" type="info">{{ item.release_count }} 发布</el-tag>
                <el-tag size="small" type="warning">云盘 {{ item.cloud_asset_count || 0 }}</el-tag>
                <el-tag size="small" type="success">本地 {{ item.local_asset_count || 0 }}</el-tag>
              </div>
              <el-progress :percentage="progressOf(item)" :show-text="false" />
            </div>
          </article>
        </div>
      </section>

      <section v-if="view === 'settings'">
        <el-card>
          <template #header>全局设置</template>
          <el-form :model="settings" label-position="top" class="settings-form">
            <el-tabs>
              <el-tab-pane label="来源">
                <el-alert
                  type="info"
                  show-icon
                  :closable="false"
                  title="保存设置只会更新规则；要立即执行完整处理，请回到控制台点击“扫描全部”。"
                  class="settings-alert"
                />
                <el-form-item label="Mikan RSS"><el-input v-model="settings.rss_url" /></el-form-item>
                <el-form-item label="RSS 代理"><el-input v-model="settings.rss_proxy" placeholder="http://NAS_IP:20171" /></el-form-item>
                <div class="form-row">
                  <el-form-item label="自动扫描"><el-switch v-model="settings.auto_scan" /></el-form-item>
                  <el-form-item label="扫描间隔"><el-input-number v-model="settings.scan_interval_minutes" :min="1" /></el-form-item>
                  <el-form-item label="默认补全">
                    <el-select v-model="settings.default_backfill">
                      <el-option label="不补全" value="none" />
                      <el-option label="补全本季" value="season" />
                      <el-option label="补全全部" value="all" />
                    </el-select>
                  </el-form-item>
                </div>
              </el-tab-pane>
              <el-tab-pane label="自动选择">
                <div class="form-row">
                  <el-form-item label="唯一匹配自动入云盘"><el-switch v-model="settings.auto_download_unique" /></el-form-item>
                  <el-form-item label="按优先级选择"><el-switch v-model="settings.auto_download_by_priority" /></el-form-item>
                </div>
                <div class="priority-layout">
                  <PriorityList title="字幕组优先级" v-model="settings.subtitle_priority" placeholder="添加字幕组" />
                  <PriorityList title="分辨率优先级" v-model="settings.resolution_priority" placeholder="添加分辨率" />
                  <PriorityList title="主字幕语言优先级" v-model="settings.language_priority" placeholder="添加主字幕语言" />
                  <PriorityList title="副字幕语言优先级" v-model="settings.secondary_language_priority" placeholder="添加副字幕语言" />
                </div>
              </el-tab-pane>
              <el-tab-pane label="PikPak">
                <el-form-item label="认证方式">
                  <el-radio-group v-model="settings.pikpak_auth_mode">
                    <el-radio-button label="token">Access + Refresh Token</el-radio-button>
                    <el-radio-button label="password">账号密码</el-radio-button>
                  </el-radio-group>
                </el-form-item>
                <div class="form-row">
                  <el-form-item label="Access Token"><el-input v-model="settings.pikpak_access_token" type="password" show-password /></el-form-item>
                  <el-form-item label="Refresh Token"><el-input v-model="settings.pikpak_refresh_token" type="password" show-password /></el-form-item>
                </div>
                <div class="form-row">
                  <el-form-item label="用户名"><el-input v-model="settings.pikpak_username" /></el-form-item>
                  <el-form-item label="密码"><el-input v-model="settings.pikpak_password" type="password" show-password /></el-form-item>
                </div>
                <el-form-item label="PikPak 代理"><el-input v-model="settings.pikpak_proxy" placeholder="通常留空" /></el-form-item>
              </el-tab-pane>
              <el-tab-pane label="媒体库">
                <div class="form-row">
                  <el-form-item label="云盘库根目录"><el-input v-model="settings.library_root" /></el-form-item>
                <el-form-item label="本地同步目录"><el-input v-model="settings.local_library_root" placeholder="/media/pikpak-anime" /></el-form-item>
                </div>
                <el-form-item label="追更自动同步"><el-switch v-model="settings.auto_sync_following" /></el-form-item>
                <el-form-item label="NFO 输出目录"><el-input v-model="settings.nfo_output_root" placeholder="留空；同步后默认写入本地媒体库" /></el-form-item>
                <el-form-item label="番剧目录模板"><el-input v-model="settings.series_dir_template" /></el-form-item>
                <el-form-item label="季目录模板"><el-input v-model="settings.season_dir_template" /></el-form-item>
                <el-form-item label="单集名模板"><el-input v-model="settings.episode_name_template" /></el-form-item>
              </el-tab-pane>
              <el-tab-pane label="系统">
                <div class="diagnostics-grid">
                  <div><span>数据库</span><strong>{{ diagnostics.db_path || '-' }}</strong></div>
                  <div><span>数据目录可写</span><strong>{{ diagnostics.data_dir_writable ? '是' : '否' }}</strong></div>
                  <div><span>数据库大小</span><strong>{{ diagnostics.db_size || 0 }} bytes</strong></div>
                  <div><span>番剧 / 发布 / 云盘</span><strong>{{ diagnostics.tables?.series || 0 }} / {{ diagnostics.tables?.releases || 0 }} / {{ diagnostics.tables?.cloud_assets || 0 }}</strong></div>
                </div>
                <el-button :icon="Refresh" @click="reloadDiagnostics">刷新诊断</el-button>
              </el-tab-pane>
            </el-tabs>
            <div class="form-actions"><el-button type="primary" size="large" :loading="savingSettings" @click="saveAllSettings">保存设置</el-button></div>
          </el-form>
        </el-card>
      </section>
    </main>

    <el-drawer v-model="seriesDrawer" size="720px" :title="selectedSeries?.series?.title_cn || '番剧设置'">
      <template v-if="selectedSeries?.series">
        <el-alert
          type="info"
          show-icon
          :closable="false"
          title="这里只处理规则和冲突；云盘入库与本地同步由后台任务自动推进。"
          class="settings-alert"
        />
        <el-form :model="selectedSeries.series" label-position="top">
          <div class="form-row">
            <el-form-item label="中文标题"><el-input v-model="selectedSeries.series.title_cn" /></el-form-item>
            <el-form-item label="年份"><el-input-number v-model="selectedSeries.series.year" /></el-form-item>
          </div>
          <div class="form-row">
            <el-form-item label="Bangumi ID"><el-input v-model="selectedSeries.series.bangumi_id" /></el-form-item>
          </div>
          <div class="form-row">
            <el-form-item label="字幕组">
              <el-select v-model="selectedSeries.series.selected_group" clearable>
                <el-option v-for="g in selectedSeries.groups" :key="g" :label="g" :value="g" />
              </el-select>
            </el-form-item>
            <el-form-item label="分辨率">
              <el-select v-model="selectedSeries.series.selected_resolution" clearable>
                <el-option v-for="r in selectedSeries.resolutions" :key="r" :label="r" :value="r" />
              </el-select>
            </el-form-item>
          </div>
          <div class="form-row">
            <el-form-item label="自动入云盘">
              <el-select v-model="selectedSeries.series.auto_download">
                <el-option label="跟随全局" value="inherit" />
                <el-option label="开启" value="on" />
                <el-option label="关闭" value="off" />
              </el-select>
            </el-form-item>
            <el-form-item label="补全">
              <el-select v-model="selectedSeries.series.backfill_mode">
                <el-option label="跟随全局" value="inherit" />
                <el-option label="不补全" value="none" />
                <el-option label="补全本季" value="season" />
                <el-option label="补全全部" value="all" />
              </el-select>
            </el-form-item>
          </div>
        </el-form>
        <div class="sync-panel">
          <div>
            <strong>本地同步</strong>
            <span>{{ syncSummary }}</span>
          </div>
          <el-switch :model-value="syncWanted" @change="toggleSeriesSync" />
        </div>
        <div class="drawer-actions">
          <el-button type="primary" @click="saveCurrentSeries">保存</el-button>
          <el-popconfirm title="只从列表隐藏这个误识别条目，保留关联记录。确定隐藏？" @confirm="deleteCurrentSeries">
            <template #reference>
              <el-button type="danger" plain>隐藏误识别</el-button>
            </template>
          </el-popconfirm>
        </div>
        <el-divider />
        <el-tabs>
          <el-tab-pane label="RSS 发布">
            <el-table :data="selectedSeries.releases" height="320">
              <el-table-column prop="episode_number" label="集" width="70" />
              <el-table-column prop="subtitle_group" label="字幕组" width="140" />
              <el-table-column prop="resolution" label="分辨率" width="100" />
              <el-table-column prop="language" label="语言" width="90" />
              <el-table-column prop="guid" label="GUID" min-width="220" show-overflow-tooltip />
              <el-table-column prop="title" label="发布标题" min-width="260" show-overflow-tooltip />
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="云盘任务">
            <el-table :data="selectedSeries.tasks" height="320">
              <el-table-column prop="status" label="状态" width="110">
                <template #default="{ row }"><el-tag :type="taskTag(row.status)">{{ row.status }}</el-tag></template>
              </el-table-column>
              <el-table-column prop="target_dir" label="目标目录" min-width="220" show-overflow-tooltip />
              <el-table-column prop="pikpak_task_id" label="PikPak 任务" min-width="180" show-overflow-tooltip />
              <el-table-column prop="pikpak_file_id" label="文件 ID" min-width="180" show-overflow-tooltip />
              <el-table-column prop="last_error" label="错误" min-width="220" show-overflow-tooltip />
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="云盘资源">
            <el-table :data="selectedSeries.cloud_assets" height="320">
              <el-table-column prop="episode_number" label="集" width="70" />
              <el-table-column prop="provider" label="云盘" width="100" />
              <el-table-column prop="cloud_path" label="云盘路径" min-width="260" show-overflow-tooltip />
              <el-table-column prop="provider_file_id" label="文件 ID" min-width="180" show-overflow-tooltip />
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="本地资源">
            <el-table :data="selectedSeries.local_assets" height="320">
              <el-table-column prop="episode_number" label="集" width="70" />
              <el-table-column prop="status" label="状态" width="110" />
              <el-table-column prop="local_path" label="本地路径" min-width="260" show-overflow-tooltip />
              <el-table-column prop="nfo_status" label="NFO" width="110" />
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import draggable from 'vuedraggable'
import { ElMessage } from 'element-plus'
import { Collection, DataBoard, Refresh, Search, Setting } from '@element-plus/icons-vue'
import { deleteAction, getDashboard, getDiagnostics, getSeries, getSettings, postAction, saveSeries, saveSettings } from './api'

const view = ref('dashboard')
const consoleTab = ref('issues')
const loading = ref(false)
const savingSettings = ref(false)
const autoRefresh = ref(true)
const refreshInterval = ref(5000)
let refreshTimer = null
const keyword = ref('')
const seriesFilter = ref('全部')
const seriesDrawer = ref(false)
const selectedSeries = ref(null)
const dashboard = reactive({
  series: [],
  rss_candidates: [],
  tasks: [],
  sync_tasks: [],
  sync_rules: [],
  cloud_assets: [],
  operations: [],
  logs: [],
  server_logs: [],
  queue_summary: [],
  calendar: [],
  active_tasks: [],
  task_counts: {}
})
const settings = reactive({})
const diagnostics = reactive({ tables: {} })

const pageTitle = computed(() => ({
  dashboard: '控制台',
  library: '番剧库',
  settings: '设置中心'
}[view.value]))

const cloudAssetTotal = computed(() => dashboard.series.reduce((sum, item) => sum + Number(item.cloud_asset_count || 0), 0))
const localAssetTotal = computed(() => dashboard.series.reduce((sum, item) => sum + Number(item.local_asset_count || 0), 0))
const cloudQueueCount = computed(() => dashboard.tasks.filter(t => ['pending', 'running', 'submitted'].includes(t.status)).length)
const syncQueueCount = computed(() => dashboard.sync_tasks.filter(t => ['pending', 'running', 'failed'].includes(t.status)).length)
const metadataIssueCount = computed(() => dashboard.rss_candidates.length)
const issues = computed(() => {
  const rows = []
  for (const item of dashboard.rss_candidates) {
    if (item.status === 'failed') {
      rows.push({ type: 'RSS 候选', level: 'warning', title: item.series_title || item.title, message: item.reason || '候选处理失败', series_id: null })
    }
  }
  for (const item of dashboard.series) {
    if (!item.bangumi_id) {
      rows.push({ type: '元数据', level: 'warning', title: item.title_cn, message: '缺少 Bangumi 绑定，不能可靠入库', series_id: item.id })
    }
    if (Number(item.group_count || 0) > 1 && !item.selected_group && !priorityCanResolve(item, 'subtitle_group', settings.subtitle_priority)) {
      rows.push({ type: '字幕组', level: 'warning', title: item.title_cn, message: '存在多个字幕组，当前优先级无法唯一选择', series_id: item.id })
    }
    if (Number(item.resolution_count || 0) > 1 && !item.selected_resolution && !priorityCanResolve(item, 'resolution', settings.resolution_priority)) {
      rows.push({ type: '分辨率', level: 'warning', title: item.title_cn, message: '存在多个分辨率，当前优先级无法唯一选择', series_id: item.id })
    }
    if (Number(item.language_count || 0) > 1 && !subtitleLanguageCanResolve(item)) {
      rows.push({ type: '字幕语言', level: 'warning', title: item.title_cn, message: '存在多个字幕语言组合，当前主/副字幕优先级无法唯一选择', series_id: item.id })
    }
  }
  for (const task of dashboard.tasks.filter(t => t.status === 'failed')) {
    rows.push({ type: '云盘失败', level: 'danger', title: task.title_cn, message: task.last_error || 'PikPak 入库失败', series_id: task.series_id })
  }
  for (const task of dashboard.sync_tasks.filter(t => t.status === 'failed')) {
    rows.push({ type: '同步失败', level: 'danger', title: task.title_cn, message: task.last_error || '本地同步失败', series_id: task.series_id })
  }
  return rows
})
const issueCount = computed(() => issues.value.length)
const runningRows = computed(() => dashboard.tasks.filter(t => ['pending', 'running', 'submitted', 'failed'].includes(t.status)))
const scanOperation = computed(() => dashboard.operations.find(op => op.name === '扫描全部' && op.status === 'running'))
const scanRunning = computed(() => Boolean(scanOperation.value))
const scanProgress = computed(() => {
  const message = scanOperation.value?.message || ''
  const match = message.match(/(\d+)\/(\d+)/)
  if (!match) return scanRunning.value ? 12 : 0
  return Math.min(95, Math.round(Number(match[1]) / Number(match[2]) * 100))
})
const serverLogText = computed(() => (dashboard.server_logs || []).join('\n'))
const selectedSeriesStats = computed(() => {
  const id = selectedSeries.value?.series?.id
  return dashboard.series.find(item => item.id === id) || {}
})
const selectedSyncRule = computed(() => {
  const id = selectedSeries.value?.series?.id
  return dashboard.sync_rules.find(item => item.series_id === id) || {}
})
const syncWanted = computed(() => Boolean(selectedSyncRule.value.sync_enabled))
const syncSummary = computed(() => {
  const stats = selectedSeriesStats.value
  if (Number(stats.local_asset_count || 0) > 0) return `已同步 ${stats.local_asset_count} 集到本地`
  if (syncWanted.value && Number(stats.cloud_asset_count || 0) > 0) return '已开启，正在等待或处理本地同步'
  if (syncWanted.value) return '已开启，云盘资源入库后会自动同步'
  return '关闭后只保留云盘资源，本地文件会被清理'
})

const filteredSeries = computed(() => {
  const text = keyword.value.toLowerCase()
  return dashboard.series.filter(item => {
    const matched = !text || `${item.title_cn} ${item.bangumi_id}`.toLowerCase().includes(text)
    if (!matched) return false
    if (seriesFilter.value === '待配置') return !item.bangumi_id || !item.group_count || !item.resolution_count
    if (seriesFilter.value === '已入云盘') return Number(item.cloud_asset_count || 0) > 0
    if (seriesFilter.value === '已同步') return Number(item.local_asset_count || 0) > 0
    if (seriesFilter.value === '失败') return dashboard.tasks.some(t => t.series_id === item.id && t.status === 'failed')
    return true
  })
})

function taskTag(status) {
  if (status === 'failed') return 'danger'
  if (status === 'completed' || status === 'submitted' || status === 'synced') return 'success'
  if (status === 'running') return 'warning'
  return 'info'
}

function queueState(queue) {
  if (Number(queue.failed || 0) > 0) return '失败'
  if (Number(queue.running || 0) > 0) return '运行中'
  if (Number(queue.pending || 0) > 0) return '待处理'
  return '空闲'
}

function priorityCanResolve(item, field, priority = []) {
  if (!Array.isArray(priority) || !priority.length) return false
  const source = {
    subtitle_group: item.subtitle_groups,
    resolution: item.resolutions,
    language: item.languages
  }[field]
  const values = splitCandidateValues(source)
  if (new Set(values).size <= 1) return true
  return Boolean(priorityPick([...new Set(values)], priority))
}

function subtitleLanguageCanResolve(item) {
  const values = [...new Set(splitCandidateValues(item.languages))]
  if (values.length <= 1) return true
  return Boolean(subtitleLanguagePick(values, settings.language_priority, settings.secondary_language_priority))
}

function splitCandidateValues(value) {
  return String(value || '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)
}

function priorityPick(values, priority = []) {
  for (const preferred of priority) {
    const preferredLower = String(preferred).toLowerCase()
    const exact = values.filter(value => String(value).toLowerCase() === preferredLower)
    if (exact.length === 1) return exact[0]
    const matched = values.filter(value => {
      const valueText = String(value)
      const valueLower = valueText.toLowerCase()
      if (valueLower === preferredLower || valueLower.includes(preferredLower)) return true
      if (['简体', '简中'].includes(preferred) && valueText.startsWith('简')) return true
      if (['繁体', '繁中'].includes(preferred) && valueText.startsWith('繁')) return true
      if (['日语', '日文'].includes(preferred) && valueText.includes('日')) return true
      if (['英语', '英文'].includes(preferred) && valueText.includes('英')) return true
      return false
    })
    if (matched.length === 1) return matched[0]
    if (matched.length > 1) {
      const exact = matched.filter(value => String(value).toLowerCase() === preferredLower)
      if (exact.length === 1) return exact[0]
      return ''
    }
  }
  return ''
}

function subtitleLanguageTokens(value) {
  const text = String(value || '')
  const tokens = []
  if (text.startsWith('简') || text.includes('简体') || text.includes('简中')) tokens.push('简体')
  if (text.startsWith('繁') || text.includes('繁体') || text.includes('繁中')) tokens.push('繁体')
  if (text.includes('日')) tokens.push('日语')
  if (text.includes('英')) tokens.push('英语')
  if (text === '中文' && !tokens.length) tokens.push('中文')
  return tokens
}

function languageMatches(token, preferred) {
  if (!token || !preferred) return false
  if (token === preferred) return true
  if (['简体', '简中'].includes(preferred)) return token.startsWith('简')
  if (['繁体', '繁中'].includes(preferred)) return token.startsWith('繁')
  if (['日语', '日文'].includes(preferred)) return token.includes('日')
  if (['英语', '英文'].includes(preferred)) return token.includes('英')
  return false
}

function rankSubtitleLanguages(values, priority = [], index = 0) {
  if (!Array.isArray(priority) || !priority.length) return values
  for (const preferred of priority) {
    const matched = values.filter(value => languageMatches(subtitleLanguageTokens(value)[index], preferred))
    if (matched.length) return matched
  }
  return values
}

function subtitleLanguagePick(values, primary = [], secondary = []) {
  let candidates = rankSubtitleLanguages(values, primary, 0)
  if (candidates.length === 1) return candidates[0]
  candidates = rankSubtitleLanguages(candidates, secondary, 1)
  return candidates.length === 1 ? candidates[0] : ''
}

function progressOf(item) {
  const total = Number(item.episode_count || item.release_count || 1)
  return Math.min(100, Math.round(Number(item.downloaded_count || 0) / total * 100))
}

async function reload() {
  if (loading.value) return
  loading.value = true
  try {
    Object.assign(dashboard, await getDashboard())
    Object.assign(settings, await getSettings())
    if (view.value === 'settings') await reloadDiagnostics()
  } finally {
    loading.value = false
  }
}

async function reloadDiagnostics() {
  Object.assign(diagnostics, await getDiagnostics())
}

function stopAutoRefresh() {
  if (refreshTimer) {
    window.clearInterval(refreshTimer)
    refreshTimer = null
  }
}

function startAutoRefresh() {
  stopAutoRefresh()
  if (!autoRefresh.value) return
  refreshTimer = window.setInterval(() => {
    if (view.value === 'settings' || seriesDrawer.value) return
    reload()
  }, refreshInterval.value)
}

async function runAction(path) {
  try {
    const result = await postAction(path)
    if (result.status === 'skipped') {
      ElMessage.warning(result.message || '没有可执行任务')
    } else if (result.status === 'running') {
      ElMessage.warning(result.message || '任务正在执行')
    } else {
      ElMessage.success(result.message || '操作已提交')
    }
    setTimeout(reload, 800)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

async function saveAllSettings() {
  savingSettings.value = true
  try {
    await saveSettings(settings)
    ElMessage.success('设置已保存')
    await reload()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  } finally {
    savingSettings.value = false
  }
}

function apiErrorMessage(error) {
  return error?.response?.data?.detail || error?.response?.data?.message || error?.message || '请求失败'
}

async function openSeries(id) {
  selectedSeries.value = await getSeries(id)
  seriesDrawer.value = true
}

async function saveCurrentSeries() {
  await saveSeries(selectedSeries.value.series.id, selectedSeries.value.series)
  ElMessage.success('番剧设置已保存')
  await reload()
}

async function toggleSeriesSync(enabled) {
  const action = enabled ? 'sync' : 'sync/cancel'
  const result = await postAction(`/series/${selectedSeries.value.series.id}/${action}`)
  if (result.status === 'skipped') {
    ElMessage.warning(result.message || '没有可执行任务')
  } else {
    ElMessage.success(result.message || '同步状态已更新')
  }
  await reload()
}

async function deleteCurrentSeries() {
  const id = selectedSeries.value?.series?.id
  if (!id) return
  const result = await deleteAction(`/series/${id}`)
  if (result.status === 'not_found') {
    ElMessage.warning(result.message || '番剧不存在')
  } else {
    ElMessage.success(result.message || '已删除')
  }
  seriesDrawer.value = false
  selectedSeries.value = null
  await reload()
}

const PriorityList = {
  props: ['modelValue', 'title', 'placeholder'],
  emits: ['update:modelValue'],
  components: { draggable },
  data() {
    return { input: '' }
  },
  computed: {
    items: {
      get() { return this.modelValue || [] },
      set(value) { this.$emit('update:modelValue', value) }
    }
  },
  methods: {
    add() {
      const value = this.input.trim()
      if (!value) return
      this.items = [...this.items, value]
      this.input = ''
    },
    remove(index) {
      this.items = this.items.filter((_, i) => i !== index)
    }
  },
  template: `
    <div class="priority-card">
      <div class="priority-title">{{ title }}</div>
      <draggable v-model="items" item-key="name" handle=".drag-handle" class="priority-list">
        <template #item="{ element, index }">
          <div class="priority-item">
            <span class="drag-handle">⋮⋮</span>
            <span class="rank">{{ index + 1 }}</span>
            <span>{{ element }}</span>
            <button type="button" @click="remove(index)">×</button>
          </div>
        </template>
      </draggable>
      <div class="priority-add">
        <el-input v-model="input" :placeholder="placeholder" @keyup.enter="add" />
        <el-button @click="add">添加</el-button>
      </div>
    </div>
  `
}

watch([autoRefresh, refreshInterval], startAutoRefresh)
watch(seriesDrawer, startAutoRefresh)
watch(view, value => {
  if (value === 'settings') reloadDiagnostics().catch(error => ElMessage.error(apiErrorMessage(error)))
})

onMounted(async () => {
  await reload()
  startAutoRefresh()
})

onUnmounted(stopAutoRefresh)
</script>
