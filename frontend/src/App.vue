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
        <button :class="{ active: view === 'library' }" @click="view = 'library'"><el-icon><Collection /></el-icon> 新番库</button>
        <button :class="{ active: view === 'tasks' }" @click="view = 'tasks'"><el-icon><List /></el-icon> 云盘队列</button>
        <button :class="{ active: view === 'calendar' }" @click="view = 'calendar'"><el-icon><Calendar /></el-icon> 日历</button>
        <button :class="{ active: view === 'settings' }" @click="view = 'settings'"><el-icon><Setting /></el-icon> 设置</button>
      </nav>
    </aside>

    <main class="main">
      <header class="hero">
        <div>
          <p class="eyebrow">PikPak · Jellyfin · Bangumi</p>
          <h1>{{ pageTitle }}</h1>
          <p class="hero-sub">追番、补全、整理和媒体库元数据的统一入口。</p>
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
          <el-button type="primary" :icon="Search" @click="runAction('/scan')">扫描 RSS</el-button>
          <el-button :icon="Refresh" @click="reload" :loading="loading">刷新状态</el-button>
          <el-button type="warning" @click="runAction('/tasks/retry-failed')">重试失败</el-button>
        </div>
      </header>

      <section v-if="view === 'dashboard'" class="content-grid">
        <div class="metric-card">
          <span>番剧</span>
          <strong>{{ dashboard.series.length }}</strong>
        </div>
        <div class="metric-card">
          <span>任务</span>
          <strong>{{ dashboard.tasks.length }}</strong>
        </div>
        <div class="metric-card">
          <span>已提交</span>
          <strong>{{ submittedCount }}</strong>
        </div>
        <div class="metric-card">
          <span>失败</span>
          <strong>{{ failedCount }}</strong>
        </div>

        <el-card class="span-2">
          <template #header>
            <div class="card-header">
              <span>当前队列</span>
              <el-button size="small" :icon="Refresh" @click="runAction('/tasks/poll')">刷新状态</el-button>
            </div>
          </template>
          <el-empty v-if="!dashboard.active_tasks.length" description="当前没有待处理任务" />
          <div v-else class="active-queue">
            <div v-for="task in dashboard.active_tasks" :key="task.id" class="active-task">
              <el-tag :type="taskTag(task.status)">{{ task.status }}</el-tag>
              <div>
                <strong>{{ task.title_cn }}</strong>
                <span>第 {{ task.episode_number }} 集 · {{ task.subtitle_group }} · {{ task.resolution }}</span>
                <small v-if="task.last_error">{{ task.last_error }}</small>
              </div>
            </div>
          </div>
        </el-card>

        <el-card class="span-2">
          <template #header>功能板块</template>
          <div class="module-grid">
            <div><b>新番追更</b><span>已启用：Mikan RSS -> PikPak</span></div>
            <div><b>老番补全</b><span>待接入搜索源</span></div>
            <div><b>电影下载</b><span>预留 TMDB 工作流</span></div>
            <div><b>美剧追番</b><span>预留剧集工作流</span></div>
          </div>
        </el-card>

        <el-card class="span-2">
          <template #header>最近日志</template>
          <el-timeline>
            <el-timeline-item v-for="log in dashboard.logs.slice(0, 8)" :key="log.id" :timestamp="log.created_at">
              <el-tag size="small" :type="log.level === 'error' ? 'danger' : log.level === 'warn' ? 'warning' : 'success'">{{ log.level }}</el-tag>
              <span class="log-message">{{ log.message }}</span>
            </el-timeline-item>
          </el-timeline>
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

      <section v-if="view === 'tasks'">
        <el-card>
          <template #header>
            <div class="card-header">
              <span>云盘队列</span>
              <el-button :icon="Refresh" @click="runAction('/tasks/poll')">刷新状态</el-button>
              <el-button @click="runAction('/cloud/scan')">扫描云盘库</el-button>
              <el-button type="success" :icon="VideoPlay" @click="runAction('/tasks/process')">处理云盘任务</el-button>
            </div>
          </template>
          <el-table :data="dashboard.tasks" height="620">
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
        </el-card>
        <el-card class="task-card">
          <template #header>
            <div class="card-header">
              <span>本地同步队列</span>
              <el-button :icon="Refresh" @click="runAction('/sync/tasks/process')">处理同步</el-button>
            </div>
          </template>
          <el-table :data="dashboard.sync_tasks" height="360">
            <el-table-column prop="status" label="状态" width="120">
              <template #default="{ row }"><el-tag :type="taskTag(row.status)">{{ row.status }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="title_cn" label="番剧" min-width="180" />
            <el-table-column prop="source_path" label="云盘路径" min-width="260" />
            <el-table-column prop="target_path" label="本地路径" min-width="260" />
            <el-table-column prop="last_error" label="错误" min-width="220" />
          </el-table>
        </el-card>
      </section>

      <section v-if="view === 'calendar'">
        <el-card>
          <template #header>追番日历</template>
          <el-empty v-if="!dashboard.calendar.length" description="Bangumi 元数据接入后会显示放送日历" />
          <el-table v-else :data="dashboard.calendar">
            <el-table-column prop="air_date" label="日期" width="160" />
            <el-table-column prop="title_cn" label="番剧" />
            <el-table-column prop="episode_number" label="集" width="80" />
            <el-table-column prop="status" label="状态" width="120" />
          </el-table>
        </el-card>
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
                  title="保存设置只会更新规则；要让规则作用到已扫描内容，请点击顶部“扫描 RSS”。失败任务需要点击“重试失败”。"
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
                  <PriorityList title="语言优先级" v-model="settings.language_priority" placeholder="添加语言" />
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
            </el-tabs>
            <div class="form-actions"><el-button type="primary" size="large" @click="saveAllSettings">保存设置</el-button></div>
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
          title="番剧设置保存后只更新规则；云盘入库一般由扫描自动处理，只有需要手动补救时才点“存入云盘”。"
          class="settings-alert"
        />
        <el-form :model="selectedSeries.series" label-position="top">
          <div class="form-row">
            <el-form-item label="中文标题"><el-input v-model="selectedSeries.series.title_cn" /></el-form-item>
            <el-form-item label="年份"><el-input-number v-model="selectedSeries.series.year" /></el-form-item>
          </div>
          <div class="form-row">
            <el-form-item label="Bangumi ID"><el-input v-model="selectedSeries.series.bangumi_id" /></el-form-item>
            <el-form-item label="TMDB ID"><el-input v-model="selectedSeries.series.tmdb_id" /></el-form-item>
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
          <el-button v-if="!seriesHasCloud" @click="runSeriesAction('download')">存入云盘</el-button>
          <el-button @click="runSeriesAction('metadata')">刷新元数据</el-button>
          <el-button @click="runSeriesAction('nfo')">生成 NFO</el-button>
          <el-popconfirm title="只删除本应用里的误识别条目，不删除云盘文件。确定删除？" @confirm="deleteCurrentSeries">
            <template #reference>
              <el-button type="danger" plain>删除误识别</el-button>
            </template>
          </el-popconfirm>
        </div>
        <el-divider />
        <el-table :data="selectedSeries.releases" height="360">
          <el-table-column prop="episode_number" label="集" width="70" />
          <el-table-column prop="subtitle_group" label="字幕组" width="140" />
          <el-table-column prop="resolution" label="分辨率" width="100" />
          <el-table-column prop="language" label="语言" width="90" />
          <el-table-column prop="title" label="发布标题" />
          <el-table-column label="操作" width="100">
            <template #default="{ row }"><el-button size="small" @click="downloadRelease(row.id)">存云盘</el-button></template>
          </el-table-column>
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import draggable from 'vuedraggable'
import { ElMessage } from 'element-plus'
import { Calendar, Collection, DataBoard, List, Refresh, Search, Setting, VideoPlay } from '@element-plus/icons-vue'
import { deleteAction, getDashboard, getSeries, getSettings, postAction, saveSeries, saveSettings } from './api'

const view = ref('dashboard')
const loading = ref(false)
const autoRefresh = ref(true)
const refreshInterval = ref(5000)
let refreshTimer = null
const keyword = ref('')
const seriesFilter = ref('全部')
const seriesDrawer = ref(false)
const selectedSeries = ref(null)
const dashboard = reactive({
  series: [],
  tasks: [],
  sync_tasks: [],
  sync_rules: [],
  cloud_assets: [],
  logs: [],
  calendar: [],
  active_tasks: [],
  task_counts: {}
})
const settings = reactive({})

const pageTitle = computed(() => ({
  dashboard: '控制台',
  library: '新番库',
  tasks: '云盘队列',
  calendar: '追番日历',
  settings: '设置中心'
}[view.value]))

const submittedCount = computed(() => dashboard.tasks.filter(t => ['submitted', 'completed'].includes(t.status)).length)
const failedCount = computed(() => dashboard.tasks.filter(t => t.status === 'failed').length)
const seriesHasCloud = computed(() => {
  const id = selectedSeries.value?.series?.id
  return Boolean(id && dashboard.cloud_assets.some(item => item.series_id === id && item.status === 'available'))
})
const seriesHasLocal = computed(() => {
  const id = selectedSeries.value?.series?.id
  return Boolean(id && dashboard.series.some(item => item.id === id && Number(item.local_asset_count || 0) > 0))
})
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
  } finally {
    loading.value = false
  }
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
  const result = await postAction(path)
  if (result.status === 'skipped') {
    ElMessage.warning(result.message || '没有可执行任务')
  } else {
    ElMessage.success(result.message || '操作已提交')
  }
  setTimeout(reload, 800)
}

async function saveAllSettings() {
  await saveSettings(settings)
  ElMessage.success('设置已保存')
  await reload()
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

async function runSeriesAction(action) {
  const result = await postAction(`/series/${selectedSeries.value.series.id}/${action}`)
  if (result.status === 'skipped') {
    ElMessage.warning(result.message || '没有可执行任务')
  } else {
    ElMessage.success(result.message || '操作已提交')
  }
  setTimeout(reload, 800)
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

async function downloadRelease(id) {
  await postAction(`/releases/${id}/download`)
  ElMessage.success('已加入云盘队列')
  setTimeout(reload, 800)
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

onMounted(async () => {
  await reload()
  startAutoRefresh()
})

onUnmounted(stopAutoRefresh)
</script>
