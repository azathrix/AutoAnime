<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <img class="brand-logo" src="/anitrack-logo.png" alt="AniTrack logo" />
        <div>
          <strong class="brand-wordmark">AniTrack</strong>
        </div>
      </div>
      <nav>
        <div class="nav-caption">媒体</div>
        <button :class="{ active: view === 'seasonal' }" @click="view = 'seasonal'"><el-icon><Collection /></el-icon> 新番</button>
        <button :class="{ active: view === 'calendar' }" @click="view = 'calendar'"><el-icon><Calendar /></el-icon> 日历</button>
        <button :class="{ active: view === 'library' }" @click="view = 'library'"><el-icon><Collection /></el-icon> 番剧</button>
        <button :class="{ active: view === 'movies' }" @click="view = 'movies'"><el-icon><Collection /></el-icon> 电影</button>
        <button :class="{ active: view === 'tv' }" @click="view = 'tv'"><el-icon><Collection /></el-icon> 电视剧</button>
        <div class="nav-caption">系统</div>
        <button :class="{ active: view === 'dashboard' }" @click="view = 'dashboard'"><el-icon><DataBoard /></el-icon> 控制台</button>
        <button :class="{ active: view === 'logs' }" @click="view = 'logs'"><el-icon><Document /></el-icon> 日志</button>
        <button :class="{ active: view === 'settings' }" @click="view = 'settings'"><el-icon><Setting /></el-icon> 设置</button>
      </nav>
    </aside>

    <main class="main">
      <header class="hero">
        <div>
          <p class="eyebrow">RSS · Downloader · Media Library</p>
          <h1>{{ pageTitle }}</h1>
          <p class="hero-sub">扫描订阅、自动选集并整理到本地媒体库。<span class="build-version">v{{ appVersion }} · {{ appBuild }}</span></p>
        </div>
      </header>

      <DashboardPage />
      <LogsPage />
      <SeasonalPage />
      <CalendarPage />
      <MediaCatalogPage />
      <SettingsPage />
    </main>

    <nav class="mobile-nav" aria-label="移动端导航">
      <button :class="{ active: view === 'dashboard' }" @click="view = 'dashboard'"><el-icon><DataBoard /></el-icon><b>控制台</b></button>
      <button :class="{ active: view === 'seasonal' }" @click="view = 'seasonal'"><el-icon><Collection /></el-icon><b>新番</b></button>
      <button :class="{ active: view === 'calendar' }" @click="view = 'calendar'"><el-icon><Calendar /></el-icon><b>日历</b></button>
      <button :class="{ active: view === 'library' }" @click="view = 'library'"><el-icon><Collection /></el-icon><b>番剧</b></button>
      <button :class="{ active: view === 'movies' }" @click="view = 'movies'"><el-icon><Collection /></el-icon><b>电影</b></button>
      <button :class="{ active: view === 'tv' }" @click="view = 'tv'"><el-icon><Collection /></el-icon><b>剧集</b></button>
      <button :class="{ active: view === 'logs' }" @click="view = 'logs'"><el-icon><Document /></el-icon><b>日志</b></button>
      <button :class="{ active: view === 'settings' }" @click="view = 'settings'"><el-icon><Setting /></el-icon><b>设置</b></button>
    </nav>

    <EntryDrawer />
    <EntryDialogs />
  </div>
</template>

<script setup>
import { computed, isRef, onMounted, onUnmounted, provide, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Calendar, Collection, DataBoard, Document, Refresh, Search, Setting } from '@element-plus/icons-vue'
import { deleteAction, getAction, getDashboard, getDiagnostics, getMediaItem, getSettings, postAction, putAction, saveMediaItem, saveSettings } from './api'
import { APP_BUILD, APP_VERSION } from './version'
import { createAppActions } from './composables/appActions'
import {
  addDays,
  cardInitials,
  cardSubtitle,
  catalogTags,
  entryMediaType,
  entryTags,
  entryTitle,
  episodeCanCancel,
  episodeDownloadTag,
  episodeDownloadText,
  errorMessage,
  formatDateKey,
  inferEpisodeFromText,
  isValidResourceReference,
  isValidSubtitleReference,
  jsonFromListText,
  listTextFromJson,
  mediaTypeLabel,
  normalizedSeasonLabel,
  numberFromInput,
  parseDateValue,
  regionLabel,
  resourceReferenceKind,
  sourceModeText,
  splitTextLines,
  startOfWeek,
  subtitleFormatText,
  taskStatusText,
  taskTag,
  titleFromResourceSeed,
  watchableCount,
} from './composables/viewHelpers'
import DashboardPage from './components/DashboardPage.vue'
import LogsPage from './components/LogsPage.vue'
import SeasonalPage from './components/SeasonalPage.vue'
import CalendarPage from './components/CalendarPage.vue'
import MediaCatalogPage from './components/MediaCatalogPage.vue'
import SettingsPage from './components/SettingsPage.vue'
import EntryDrawer from './components/EntryDrawer.vue'
import EntryDialogs from './components/EntryDialogs.vue'

const validViews = new Set(['dashboard', 'seasonal', 'calendar', 'library', 'movies', 'tv', 'logs', 'settings'])
function initialView() {
  const saved = window.localStorage.getItem('anitrack:view') || ''
  return validViews.has(saved) ? saved : 'seasonal'
}

const view = ref(initialView())
const appVersion = APP_VERSION
const appBuild = APP_BUILD
const selectedConsoleSection = ref('')
const selectedTaskType = ref('')
const scheduleEditingId = ref(0)
const logKeyword = ref('')
const loading = ref(false)
const savingSettings = ref(false)
const liveConnected = ref(false)
const calendarWeek = ref('')
const advancedFilterOpen = ref(false)
const rssDialogOpen = ref(false)
const rssLoading = ref(false)
const rssEditingId = ref(0)
const processorSettingsDialogOpen = ref(false)
const scheduledSettingsDialogOpen = ref(false)
const mediaWizardOpen = ref(false)
const mediaWizardMode = ref('import')
const mediaWizardStep = ref(0)
const mediaWizardSeed = ref('')
const mediaWizardSaving = ref(false)
const mediaWizardCandidates = ref([])
const mediaWizardResourceItems = ref([])
const mediaWizardSubtitleItems = ref([])
const metadataSearchDialogOpen = ref(false)
const metadataSearchProvider = ref('bangumi')
const metadataSearchKeyword = ref('')
const metadataSearchLoading = ref(false)
const metadataSearchResults = ref({ bangumi: [], tmdb: [] })
const metadataSearchTarget = ref('wizard')
const metadataSelectedBangumi = ref(null)
const metadataSelectedTmdb = ref(null)
const episodeResourceDialogOpen = ref(false)
const entryEditDialogOpen = ref(false)
const batchSubtitleDialogOpen = ref(false)
const episodeImportDialogOpen = ref(false)
const batchSubtitleStep = ref(0)
const episodeImportStep = ref(0)
const metadataFetching = ref(false)
const metadataFetchProgress = ref(0)
let dashboardStream = null
let streamRetryTimer = null
const keyword = ref('')
const libraryYearFilter = ref('')
const libraryMonthFilter = ref('')
const libraryScopeFilter = ref('')
const libraryMediaTypeFilter = ref('')
const libraryRegionFilter = ref('')
const libraryTagFilters = ref([])
const entryDrawerOpen = ref(false)
const selectedEntryDetail = ref(null)
const selectedEntryDomain = ref('seasonal')
const selectedEntryMediaType = ref('anime')
const expandedResourceKeys = ref([])
const expandedDownloadTaskKeys = ref([])
const dashboard = reactive({
  seasonal_items: [],
  library_items: [],
  seasonal_sync_calendar: [],
  seasonal_update_calendar: [],
  operations: [],
  scheduled_jobs: [],
  schedules: [],
  scheduled_runs: [],
  server_logs: [],
  queue_summary: [],
  queue_details: {},
  console_sections: [],
  console_overview: {},
  scanner_status: {},
  download_tasks: [],
  download_overview: {},
  tasks: [],
  task_overview: [],
})
const settings = reactive({})
const diagnostics = reactive({ tables: {} })
const rssForm = reactive({
  name: '',
  url: '',
  kind: 'mikan',
  enabled: true,
})
const rssSubscriptions = ref([])
const episodeResourceForm = reactive({
  episode_id: 0,
  resource_id: 0,
  subtitle_id: 0,
  episode_number: '',
  title: '',
  source_type: 'manual',
  source_ref: '',
  subtitle_group: '',
  resolution: '',
  language: '',
  subtitle_format: '',
  subtitle_path: '',
  subtitle_url: '',
  subtitle_file_name: '',
  local_path: '',
  resources_text: '',
  subtitles_text: '',
})
const entryEditForm = reactive({
  title_cn: '',
  bangumi_id: '',
  tmdb_id: '',
  bangumi_score: 0,
  tmdb_score: 0,
  year: 0,
  month: 0,
  release_month: '',
  season_number: 1,
  media_type: 'anime',
  region: 'jp',
  title_romaji: '',
  title_raw: '',
  poster_url: '',
  summary: '',
  tags_text: '',
  genres_text: '',
})
const batchSubtitleForm = reactive({
  subtitles_text: '',
  file_names: [],
  subtitle_format: 'external',
  language: '',
})
const episodeImportForm = reactive({
  source_mode: 'link',
  resources_text: '',
  subtitles_text: '',
  subtitle_format: 'external',
  language: '',
})
const episodeImportLocalItems = ref([])
const mediaWizardDraft = reactive({
  source_mode: 'link',
  title: '',
  bangumi_id: '',
  tmdb_id: '',
  bangumi_score: 0,
  tmdb_score: 0,
  year: 0,
  month: 0,
  release_month: '',
  season_number: 1,
  region: '',
  poster_url: '',
  summary: '',
  tags_text: '',
  genres_text: '',
  episode_number: 0,
  resource_title: '',
  source_ref: '',
  subtitle_group: '',
  resolution: '',
  language: '',
  subtitle_format: '',
  subtitle_path: '',
  subtitle_url: '',
  subtitle_file_name: '',
  resource_input: '',
  subtitle_input: '',
})
const scheduledJobForm = reactive({
  enabled: true,
  interval_minutes: 1,
})
const processorSettingsForm = reactive({
  download_concurrency: 2,
})
const fileBrowser = reactive({
  open: false,
  mode: 'video',
  current: '',
  parent: '',
  items: [],
  loading: false,
})
const appContextBindings = {}

function exposeAppContext(bindings) {
  Object.assign(appContextBindings, bindings)
}

const appContext = new Proxy(appContextBindings, {
  get(target, key) {
    if (typeof key !== 'string') return undefined
    const value = target[key]
    return isRef(value) ? value.value : value
  },
  set(target, key, value) {
    if (typeof key !== 'string') return false
    const current = target[key]
    if (isRef(current)) {
      current.value = value
      return true
    }
    target[key] = value
    return true
  },
  has(target, key) {
    return typeof key === 'string' && key in target
  },
  ownKeys(target) {
    return Reflect.ownKeys(target)
  },
  getOwnPropertyDescriptor(target, key) {
    if (key in target) return { configurable: true, enumerable: true }
    return undefined
  },
})

const pageTitle = computed(() => ({
  dashboard: '控制台',
  seasonal: '新番',
  calendar: '日历',
  library: '番剧',
  movies: '电影',
  tv: '电视剧',
  logs: '日志与维护',
  settings: '设置中心'
}[view.value]))

const seasonalRows = computed(() => dashboard.seasonal_items || [])
const libraryRows = computed(() => dashboard.library_items || [])
const isMediaCatalogView = computed(() => ['library', 'movies', 'tv'].includes(view.value))
const currentMediaType = computed(() => ({
  library: 'anime',
  movies: 'movie',
  tv: 'tv',
}[view.value] || 'anime'))
const currentMediaPageTitle = computed(() => ({
  library: '番剧',
  movies: '电影',
  tv: '电视剧',
}[view.value] || '媒体'))
const currentCatalogSourceRows = computed(() => {
  if (!isMediaCatalogView.value) return seasonalRows.value
  return libraryRows.value.filter(item => belongsToCurrentMediaPage(item))
})
const currentYearOptions = computed(() => {
  const values = new Set()
  for (const item of currentCatalogSourceRows.value) {
    const year = Number(item.year || 0)
    if (year > 0) values.add(year)
  }
  return Array.from(values).sort((a, b) => b - a)
})
const currentMonthOptions = computed(() => {
  const values = new Set()
  for (const item of currentCatalogSourceRows.value) {
    const month = Number(item.month || 0)
    if (month >= 1 && month <= 12) values.add(month)
  }
  return Array.from(values).sort((a, b) => a - b)
})
const currentMediaTypeOptions = computed(() => {
  const values = new Set()
  for (const item of currentCatalogSourceRows.value) {
    const type = entryMediaType(item)
    if (type) values.add(type)
  }
  return Array.from(values).sort((a, b) => mediaTypeLabel(a).localeCompare(mediaTypeLabel(b)))
})
const currentRegionOptions = computed(() => {
  const values = new Set()
  for (const item of currentCatalogSourceRows.value) {
    if (item.region) values.add(item.region)
  }
  return Array.from(values).sort((a, b) => regionLabel(a).localeCompare(regionLabel(b)))
})
const currentScopeOptions = computed(() => {
  const values = new Set()
  for (const item of currentCatalogSourceRows.value) {
    const scope = normalizedSeasonLabel(item)
    if (scope) values.add(scope)
  }
  return Array.from(values).sort((a, b) => String(a).localeCompare(String(b)))
})
const currentTagOptions = computed(() => {
  const counts = new Map()
  for (const item of currentCatalogSourceRows.value) {
    for (const tag of catalogTags(item)) {
      counts.set(tag, (counts.get(tag) || 0) + 1)
    }
  }
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 36)
    .map(item => item[0])
})
const activeDetailRows = computed(() => selectedEntryDomain.value === 'library' ? libraryRows.value : seasonalRows.value)
const localAssetTotal = computed(() => seasonalRows.value.reduce((sum, item) => sum + Number(item.local_asset_count || 0), 0))
const watchableTotal = computed(() => localAssetTotal.value)
const seasonalCalendarCards = computed(() => dashboard.seasonal_sync_calendar || [])
const weekStart = computed(() => startOfWeek(calendarWeek.value ? new Date(calendarWeek.value) : new Date()))
const weekDays = computed(() => {
  const labels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
  return Array.from({ length: 7 }, (_, index) => {
    const date = addDays(weekStart.value, index)
    const key = formatDateKey(date)
    const itemsByEntry = new Map()
    for (const item of seasonalCalendarCards.value) {
      const itemDate = formatDateKey(new Date(item.updated_at || item.synced_at || 0))
      if (itemDate !== key) continue
      const entryId = Number(item.entry_id || 0)
      const old = itemsByEntry.get(entryId)
      if (!old || Number(item.episode_number || 0) > Number(old.episode_number || 0)) {
        itemsByEntry.set(entryId, item)
      }
    }
    return {
      key,
      label: labels[index],
      dateLabel: `${date.getMonth() + 1}/${date.getDate()}`,
      isToday: key === formatDateKey(new Date()),
      items: Array.from(itemsByEntry.values()).sort((a, b) => Number(b.episode_number || 0) - Number(a.episode_number || 0))
    }
  })
})
const scheduledConsoleSections = computed(() => (dashboard.console_sections || []).filter(section => section.kind === 'scheduled'))
const selectedSectionMeta = computed(() => {
  return scheduledConsoleSections.value.find(item => item.key === selectedConsoleSection.value) || null
})
const selectedScheduledJob = computed(() => {
  if (Number(scheduleEditingId.value || 0) > 0) {
    return (dashboard.schedules || []).find(item => Number(item.id || 0) === Number(scheduleEditingId.value || 0)) || null
  }
  const section = selectedSectionMeta.value
  if (!section || section.kind !== 'scheduled') return null
  return (dashboard.scheduled_jobs || []).find(item => item.job_key === section.job_key) || null
})
const taskTypeDefaults = [
  { type: 'rss_scan', name: 'RSS 扫描' },
  { type: 'metadata', name: '刷新元数据' },
  { type: 'download', name: '下载任务' },
  { type: 'cache', name: '缓存清理' },
  { type: 'local_status', name: '本地状态' },
  { type: 'runtime', name: '后台任务' },
]
const taskTypeRows = computed(() => {
  const overview = new Map((dashboard.task_overview || []).map(item => [String(item.type || ''), item]))
  return taskTypeDefaults.map(item => ({
    ...item,
    ...(overview.get(item.type) || {}),
    total: Number(overview.get(item.type)?.total || 0),
    running: Number(overview.get(item.type)?.running || 0),
    pending: Number(overview.get(item.type)?.pending || 0),
    failed: Number(overview.get(item.type)?.failed || 0),
  }))
})
const filteredConsoleTasks = computed(() => {
  const selected = String(selectedTaskType.value || '')
  const rows = dashboard.tasks || []
  return selected ? rows.filter(item => String(item.type || '') === selected) : rows
})
const scanRunning = computed(() => String(dashboard.scanner_status?.status || '') === 'running')
const scannerStatusText = computed(() => {
  const status = String(dashboard.scanner_status?.status || 'idle')
  if (status === 'running') return '扫描中'
  if (status === 'failed') return '失败'
  if (status === 'completed') return '完成'
  return '空闲'
})
const filteredServerLogs = computed(() => {
  const keyword = logKeyword.value.trim().toLowerCase()
  const rows = dashboard.server_logs || []
  if (!keyword) return rows
  return rows.filter(line => String(line || '').toLowerCase().includes(keyword))
})
const filteredServerLogText = computed(() => filteredServerLogs.value.join('\n'))
const logsBadgeText = computed(() => {
  const errors = Number(dashboard.console_overview?.recent_error_count || 0)
  const warns = Number(dashboard.console_overview?.recent_warn_count || 0)
  if (errors > 0) return `${errors} 错误`
  if (warns > 0) return `${warns} 警告`
  return '正常'
})
const logsBadgeType = computed(() => {
  const errors = Number(dashboard.console_overview?.recent_error_count || 0)
  const warns = Number(dashboard.console_overview?.recent_warn_count || 0)
  if (errors > 0) return 'danger'
  if (warns > 0) return 'warning'
  return 'success'
})
const selectedEntry = computed(() => selectedEntryDetail.value?.entry || null)
const selectedEntryStats = computed(() => {
  const id = selectedEntry.value?.id
  return activeDetailRows.value.find(item => item.id === id) || {}
})
const mediaWizardTitle = computed(() => {
  return `收录${currentMediaPageTitle.value}`
})
const entryResourceRows = computed(() => {
  const detail = selectedEntryDetail.value || {}
  const rows = []
  for (const item of detail.episodes || []) {
    const episode = Number(item.episode_number || 0)
    if (episode <= 0) continue
    rows.push({
      key: `episode:${Number(item.id || episode)}`,
      episode_id: Number(item.id || 0),
      resource_id: 0,
      subtitle_id: 0,
      episode_number: episode || '-',
      release_id: item.release_id || 0,
      resource_title: item.source_title || item.resource_ref || '-',
      display_name: `${entryTitle(selectedEntry.value)} - S${String(selectedEntry.value?.season_number || 1).padStart(2, '0')}E${String(episode).padStart(2, '0')} - 第 ${String(episode).padStart(2, '0')} 话`,
      source_type: item.source_type || 'magnet',
      source_ref: item.resource_ref || '',
      torrent_url: item.resource_ref && String(item.resource_ref).startsWith('http') ? item.resource_ref : '',
      magnet: item.resource_ref && String(item.resource_ref).startsWith('magnet:') ? item.resource_ref : '',
      subtitle_group: item.subtitle_group || '-',
      resolution: item.resolution || '-',
      language: item.language || '-',
      subtitle_format: item.subtitle_format || '',
      subtitle_file: item.subtitle_path || '-',
      subtitle_url: item.subtitle_ref || '',
      subtitle_file_name: '',
      downloaded: Boolean(item.watchable),
      local_path: item.watchable ? (item.local_asset_path || item.local_path || '') : '',
      status: item.status || '',
      download_status: item.download_status || '',
      download_progress: Number(item.download_progress || 0),
      download_progress_text: item.download_progress_text || '',
      download_job_id: Number(item.download_job_id || item.last_download_job_id || 0),
      download_error: item.download_error || '',
      download_retry_after: item.download_retry_after || '',
      local_asset_id: Number(item.local_asset_id || 0),
      nfo_status: '',
      selected: true,
    })
  }
  return rows.sort((a, b) => Number(a.episode_number || 0) - Number(b.episode_number || 0))
})
const batchSubtitlePreviewRows = computed(() => {
  return [
    ...splitTextLines(batchSubtitleForm.subtitles_text),
    ...batchSubtitleForm.file_names,
  ].map((text, index) => {
    const valid = isValidSubtitleReference(text)
    return {
      key: `subtitle:${index}:${text}`,
      text,
      episode: inferEpisodeFromText(text, index + 1),
      valid,
      reason: valid ? '' : '格式无效',
    }
  })
})
const batchSubtitleInvalidRows = computed(() => batchSubtitlePreviewRows.value.filter(item => !item.valid))
const batchSubtitleCanAdvance = computed(() => {
  if (batchSubtitleStep.value === 0) return batchSubtitlePreviewRows.value.length > 0
  if (batchSubtitleStep.value === 1) return batchSubtitlePreviewRows.value.length > 0 && batchSubtitleInvalidRows.value.length === 0
  return true
})
const batchSubtitleCanSave = computed(() => batchSubtitlePreviewRows.value.length > 0 && batchSubtitleInvalidRows.value.length === 0)
const episodeImportLinkRows = computed(() => splitTextLines(episodeImportForm.resources_text).map((text, index) => {
  const valid = isValidResourceReference(text)
  return {
    key: `resource:${index}:${text}`,
    text,
    episode: inferEpisodeFromText(text, index + 1),
    valid,
    kind: resourceReferenceKind(text),
    reason: valid ? '' : '不是下载链接',
    source_type: 'manual',
    source_ref: text,
    local_path: '',
  }
}))
const episodeImportLocalRows = computed(() => episodeImportLocalItems.value.map((item, index) => {
  const text = item.path || item.name || ''
  return {
    key: item.id || `local-resource:${index}:${text}`,
    text,
    episode: Number(item.episode_number || 0) || inferEpisodeFromText(text, index + 1),
    valid: Boolean(text),
    kind: '本地文件',
    reason: text ? '' : '未选择文件',
    source_type: 'local',
    source_ref: '',
    local_path: text,
  }
}))
const episodeImportResourceRows = computed(() => [...episodeImportLinkRows.value, ...episodeImportLocalRows.value])
const episodeImportSubtitleRows = computed(() => splitTextLines(episodeImportForm.subtitles_text).map((text, index) => {
  const valid = isValidSubtitleReference(text)
  return {
    key: `episode-subtitle:${index}:${text}`,
    text,
    episode: inferEpisodeFromText(text, index + 1),
    valid,
    reason: valid ? '' : '格式无效',
  }
}))
const episodeImportInvalidCount = computed(() => {
  return episodeImportResourceRows.value.filter(item => !item.valid).length
    + episodeImportSubtitleRows.value.filter(item => !item.valid).length
})
const episodeImportCanAdvance = computed(() => {
  if (episodeImportStep.value === 0) {
    return episodeImportResourceRows.value.length > 0 && episodeImportResourceRows.value.every(item => item.valid)
  }
  if (episodeImportStep.value === 1) {
    return episodeImportSubtitleRows.value.every(item => item.valid)
  }
  return true
})
const episodeImportCanSave = computed(() => episodeImportResourceRows.value.length > 0 && episodeImportInvalidCount.value === 0)
const mediaWizardResourceRows = computed(() => mediaWizardResourceItems.value.map((item, index) => {
  const text = item.source_ref || item.file_name || ''
  const valid = isValidResourceReference(text)
  return {
    ...item,
    key: item.id || `wizard-resource:${index}:${text}`,
    text,
    episode: Number(item.episode_number || 0) || inferEpisodeFromText(text, currentMediaType.value === 'movie' ? 1 : index + 1),
    valid,
    kind: resourceReferenceKind(text),
    reason: valid ? '' : '不是可用资源',
  }
}))
const mediaWizardSubtitleRows = computed(() => mediaWizardSubtitleItems.value.map((item, index) => {
  const text = item.subtitle_ref || item.file_name || ''
  const valid = isValidSubtitleReference(text)
  return {
    ...item,
    key: item.id || `wizard-subtitle:${index}:${text}`,
    text,
    episode: Number(item.episode_number || 0) || inferEpisodeFromText(text, currentMediaType.value === 'movie' ? 1 : index + 1),
    valid,
    reason: valid ? '' : '格式无效',
  }
}))
const mediaWizardInvalidResourceCount = computed(() => mediaWizardResourceRows.value.filter(item => !item.valid).length)
const mediaWizardInvalidSubtitleCount = computed(() => mediaWizardSubtitleRows.value.filter(item => !item.valid).length)

const filteredSeries = computed(() => {
  const text = keyword.value.toLowerCase()
  const source = currentCatalogSourceRows.value
  return source.filter(item => {
    const matched = !text || `${item.entry_display_title || item.display_title || item.title_cn} ${item.work_display_title || item.work_title || item.title_root || ''} ${item.entry_scope_label || ''} ${item.bangumi_id || ''} ${item.tmdb_id || ''}`.toLowerCase().includes(text)
    if (!matched) return false
    if (libraryMediaTypeFilter.value && entryMediaType(item) !== String(libraryMediaTypeFilter.value)) return false
    if (libraryRegionFilter.value && String(item.region || '') !== String(libraryRegionFilter.value)) return false
    if (libraryYearFilter.value && Number(item.year || 0) !== Number(libraryYearFilter.value)) return false
    if (libraryMonthFilter.value && Number(item.month || 0) !== Number(libraryMonthFilter.value)) return false
    if (libraryScopeFilter.value && normalizedSeasonLabel(item) !== String(libraryScopeFilter.value)) return false
    if (libraryTagFilters.value.length) {
      const tags = catalogTags(item)
      if (!libraryTagFilters.value.every(tag => tags.includes(tag))) return false
    }
    return true
  })
})

function belongsToCurrentMediaPage(item) {
  const type = entryMediaType(item)
  if (view.value === 'movies') return type === 'movie'
  if (view.value === 'tv') return type === 'tv'
  return type === 'anime'
}

function hasRecentUpdate(item) {
  const entryId = Number(item?.id || item?.entry_id || 0)
  const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000
  return [...(dashboard.seasonal_update_calendar || []), ...(dashboard.seasonal_sync_calendar || [])].some(row => {
    if (Number(row.entry_id || 0) !== entryId) return false
    return parseDateValue(row.updated_at || row.synced_at || row.published_at) >= cutoff
  })
}

function toggleLibraryTag(tag) {
  const next = new Set(libraryTagFilters.value)
  if (next.has(tag)) next.delete(tag)
  else next.add(tag)
  libraryTagFilters.value = Array.from(next)
}

function scheduledBadgeText(jobKey) {
  const job = (dashboard.scheduled_jobs || []).find(item => item.job_key === jobKey)
  if (!job) return '-'
  if (!Number(job.enabled ?? 1)) return '关闭'
  if (job.last_status === 'failed') return '失败'
  if (job.last_status === 'running') return '运行'
  const minutes = Number(job.interval_minutes || 0)
  return minutes > 0 ? `${minutes} 分` : '已配置'
}

function metadataScores(item) {
  const scores = []
  const bangumiScore = Number(item?.bangumi_score || 0)
  const tmdbScore = Number(item?.tmdb_score || 0)
  if (bangumiScore > 0) scores.push({ key: 'bangumi', label: `Bangumi ${bangumiScore.toFixed(1)}` })
  if (tmdbScore > 0) scores.push({ key: 'tmdb', label: `TMDB ${tmdbScore.toFixed(1)}` })
  return scores
}

function scheduledBadgeType(jobKey) {
  const job = (dashboard.scheduled_jobs || []).find(item => item.job_key === jobKey)
  if (!job) return 'info'
  if (!Number(job.enabled ?? 1)) return 'info'
  if (job.last_status === 'failed') return 'danger'
  if (job.last_status === 'running') return 'warning'
  return 'success'
}

function openBatchSubtitleDialog() {
  batchSubtitleForm.subtitles_text = ''
  batchSubtitleForm.file_names = []
  batchSubtitleForm.subtitle_format = 'external'
  batchSubtitleForm.language = ''
  batchSubtitleStep.value = 0
  batchSubtitleDialogOpen.value = true
}

function openEpisodeImportDialog() {
  episodeImportForm.source_mode = 'link'
  episodeImportForm.resources_text = ''
  episodeImportForm.subtitles_text = ''
  episodeImportForm.subtitle_format = 'external'
  episodeImportForm.language = ''
  episodeImportLocalItems.value = []
  episodeImportStep.value = 0
  episodeImportDialogOpen.value = true
}

function setCalendarThisWeek() {
  calendarWeek.value = formatDateKey(startOfWeek(new Date()))
}

function shiftCalendarWeek(delta) {
  calendarWeek.value = formatDateKey(addDays(weekStart.value, delta * 7))
}

function applyDashboard(nextDashboard) {
  Object.assign(dashboard, nextDashboard || {})
  if (!scheduledConsoleSections.value.some(item => item.key === selectedConsoleSection.value)) {
    selectedConsoleSection.value = ''
  }
  if (selectedTaskType.value && !taskTypeRows.value.some(item => item.type === selectedTaskType.value)) {
    selectedTaskType.value = ''
  }
}

async function reload() {
  if (loading.value) return
  loading.value = true
  try {
    applyDashboard(await getDashboard())
    Object.assign(settings, await getSettings())
    normalizeSettingsShape()
    if (view.value === 'settings') await reloadDiagnostics()
  } finally {
    loading.value = false
  }
}

async function reloadDiagnostics() {
  Object.assign(diagnostics, await getDiagnostics())
}

function stopDashboardStream() {
  if (dashboardStream) {
    dashboardStream.close()
    dashboardStream = null
  }
  if (streamRetryTimer) {
    window.clearTimeout(streamRetryTimer)
    streamRetryTimer = null
  }
  liveConnected.value = false
}

function startDashboardStream() {
  stopDashboardStream()
  if (!window.EventSource) return
  dashboardStream = new EventSource('/api/dashboard/stream')
  dashboardStream.onopen = () => {
    liveConnected.value = true
  }
  dashboardStream.onmessage = event => {
    try {
      applyDashboard(JSON.parse(event.data))
    } catch {
      liveConnected.value = false
    }
  }
  dashboardStream.onerror = () => {
    stopDashboardStream()
    streamRetryTimer = window.setTimeout(startDashboardStream, 5000)
  }
}

exposeAppContext({
  Calendar,
  Collection,
  DataBoard,
  Document,
  Refresh,
  Search,
  Setting,
  activeDetailRows,
  addDays,
  advancedFilterOpen,
  appBuild,
  appVersion,
  batchSubtitleCanAdvance,
  batchSubtitleCanSave,
  batchSubtitleDialogOpen,
  batchSubtitleForm,
  batchSubtitleInvalidRows,
  batchSubtitlePreviewRows,
  batchSubtitleStep,
  belongsToCurrentMediaPage,
  calendarWeek,
  cardInitials,
  cardSubtitle,
  catalogTags,
  currentCatalogSourceRows,
  currentMediaPageTitle,
  currentMediaType,
  currentMediaTypeOptions,
  currentMonthOptions,
  currentRegionOptions,
  currentScopeOptions,
  currentTagOptions,
  currentYearOptions,
  dashboard,
  diagnostics,
  entryDrawerOpen,
  entryEditDialogOpen,
  entryEditForm,
  entryMediaType,
  entryResourceRows,
  entryTags,
  entryTitle,
  episodeCanCancel,
  episodeDownloadTag,
  episodeDownloadText,
  episodeImportCanAdvance,
  episodeImportCanSave,
  episodeImportDialogOpen,
  episodeImportForm,
  episodeImportInvalidCount,
  episodeImportLocalItems,
  episodeImportResourceRows,
  episodeImportStep,
  episodeImportSubtitleRows,
  episodeResourceDialogOpen,
  episodeResourceForm,
  errorMessage,
  expandedDownloadTaskKeys,
  expandedResourceKeys,
  fileBrowser,
  filteredSeries,
  filteredConsoleTasks,
  filteredServerLogs,
  filteredServerLogText,
  formatDateKey,
  hasRecentUpdate,
  inferEpisodeFromText,
  isMediaCatalogView,
  isValidResourceReference,
  isValidSubtitleReference,
  jsonFromListText,
  keyword,
  libraryMediaTypeFilter,
  libraryMonthFilter,
  libraryRegionFilter,
  libraryRows,
  libraryScopeFilter,
  libraryTagFilters,
  libraryYearFilter,
  listTextFromJson,
  liveConnected,
  loading,
  localAssetTotal,
  logKeyword,
  logsBadgeText,
  logsBadgeType,
  mediaTypeLabel,
  mediaWizardCandidates,
  mediaWizardDraft,
  mediaWizardInvalidResourceCount,
  mediaWizardInvalidSubtitleCount,
  mediaWizardMode,
  mediaWizardOpen,
  mediaWizardResourceItems,
  mediaWizardResourceRows,
  mediaWizardSaving,
  mediaWizardStep,
  mediaWizardSubtitleItems,
  mediaWizardSubtitleRows,
  mediaWizardTitle,
  metadataFetchProgress,
  metadataFetching,
  metadataScores,
  metadataSearchDialogOpen,
  metadataSearchKeyword,
  metadataSearchLoading,
  metadataSearchProvider,
  metadataSearchResults,
  metadataSearchTarget,
  metadataSelectedBangumi,
  metadataSelectedTmdb,
  normalizedSeasonLabel,
  numberFromInput,
  openBatchSubtitleDialog,
  openEpisodeImportDialog,
  pageTitle,
  parseDateValue,
  processorSettingsDialogOpen,
  processorSettingsForm,
  regionLabel,
  reload,
  reloadDiagnostics,
  resourceReferenceKind,
  rssDialogOpen,
  rssEditingId,
  rssForm,
  rssLoading,
  rssSubscriptions,
  savingSettings,
  scanRunning,
  scannerStatusText,
  scheduledBadgeText,
  scheduledBadgeType,
  scheduledConsoleSections,
  scheduledJobForm,
  scheduledSettingsDialogOpen,
  scheduleEditingId,
  seasonalCalendarCards,
  seasonalRows,
  selectedConsoleSection,
  selectedEntry,
  selectedEntryDetail,
  selectedEntryDomain,
  selectedEntryMediaType,
  selectedEntryStats,
  selectedScheduledJob,
  selectedSectionMeta,
  selectedTaskType,
  settings,
  shiftCalendarWeek,
  sourceModeText,
  splitTextLines,
  startOfWeek,
  subtitleFormatText,
  taskStatusText,
  taskTag,
  taskTypeRows,
  titleFromResourceSeed,
  toggleLibraryTag,
  view,
  watchableCount,
  watchableTotal,
  weekDays,
  weekStart,
})

provide('appContext', appContext)

const {
  addDownloader, addMediaWizardResourceLines, addMediaWizardSubtitleLines, advanceMediaWizard, apiErrorMessage, applyMetadataToWizard,
  browseServerFiles,
  archiveCurrentEntry, backfillCurrentEntrySeason, cancelAllDownloads, cancelDownloadTask, cancelEpisodeDownload, cancelQueueDownload, clearCompletedDownloadTasks, clearEntryEditForm,
  cancelGenericTask, clearGenericTask, pauseGenericTask, resumeGenericTask,
  commitEpisodeImport, commitMediaWizard,
  deleteCurrentEntry, deleteDownloadTask, deleteEpisodeResource, deleteRssSubscription, downloadCurrentEntryResources, downloadEpisodeResource,
  editRssSubscription, entryEditPayload, exportLogs, fetchEntryMetadata, loadRssSubscriptions, normalizeSettingsShape, openEntry,
  openEntryEditDialog, openEpisodeResourceEditor, openMediaWizard, openMetadataSearch, openProcessorSettings, openQueueEntry, openRssDialog, openServerFileBrowser,
  openScheduledSettings, migrateEpisodeModel, organizeAllLocalFiles, organizeCurrentEntryLocalFiles, refreshAllLocalStatus, refreshCurrentEntryLocalStatus, refreshEntryMetadata, repairLocalPaths, retryDownloadTask, refreshEpisodeResource, removeDownloader, removeMediaWizardResourceItem,
  removeMediaWizardSubtitleItem, resetRssForm, resetSelectionRules, runAction, runMetadataSearch, saveAllSettings, saveBatchSubtitles,
  saveEntryEditForm, saveEpisodeResource, saveProcessorSettings, saveRssSubscription, saveScheduledJob, searchWizardMetadata, selectServerFile, setCurrentEntryFollowing,
  confirmMetadataMatch, selectedMetadataCandidate, selectMetadataCandidate, skipMetadataProvider, toggleEntryResourceRow,
  startMetadataProgress, stopMetadataProgress, syncScheduledJobForm, triggerSchedule,
} = createAppActions(appContext, {
  deleteAction,
  getAction,
  getDiagnostics,
  getMediaItem,
  getSettings,
  postAction,
  putAction,
  saveMediaItem,
  saveSettings,
})

exposeAppContext({
  addDownloader, addMediaWizardResourceLines, addMediaWizardSubtitleLines, advanceMediaWizard, apiErrorMessage, applyMetadataToWizard,
  browseServerFiles,
  archiveCurrentEntry, backfillCurrentEntrySeason, cancelAllDownloads, cancelDownloadTask, cancelEpisodeDownload, cancelQueueDownload, clearCompletedDownloadTasks, clearEntryEditForm,
  cancelGenericTask, clearGenericTask, pauseGenericTask, resumeGenericTask,
  commitEpisodeImport, commitMediaWizard,
  deleteCurrentEntry, deleteDownloadTask, deleteEpisodeResource, deleteRssSubscription, downloadCurrentEntryResources, downloadEpisodeResource,
  editRssSubscription, entryEditPayload, exportLogs, fetchEntryMetadata, loadRssSubscriptions, normalizeSettingsShape, openEntry,
  openEntryEditDialog, openEpisodeResourceEditor, openMediaWizard, openMetadataSearch, openProcessorSettings, openQueueEntry, openRssDialog, openServerFileBrowser,
  openScheduledSettings, migrateEpisodeModel, organizeAllLocalFiles, organizeCurrentEntryLocalFiles, refreshAllLocalStatus, refreshCurrentEntryLocalStatus, refreshEntryMetadata, repairLocalPaths, retryDownloadTask, refreshEpisodeResource, removeDownloader, removeMediaWizardResourceItem,
  removeMediaWizardSubtitleItem, resetRssForm, resetSelectionRules, runAction, runMetadataSearch, saveAllSettings, saveBatchSubtitles,
  saveEntryEditForm, saveEpisodeResource, saveProcessorSettings, saveRssSubscription, saveScheduledJob, searchWizardMetadata, selectServerFile, setCurrentEntryFollowing,
  confirmMetadataMatch, selectedMetadataCandidate, selectMetadataCandidate, skipMetadataProvider, toggleEntryResourceRow,
  startMetadataProgress, stopMetadataProgress, syncScheduledJobForm, triggerSchedule,
})

watch(selectedScheduledJob, job => {
  syncScheduledJobForm(job)
})
watch(view, value => {
  if (validViews.has(value)) window.localStorage.setItem('anitrack:view', value)
})
watch(entryEditDialogOpen, value => {
  if (!value) {
    stopMetadataProgress()
    metadataFetching.value = false
    metadataFetchProgress.value = 0
  }
})
watch(view, value => {
  if (['seasonal', 'library', 'movies', 'tv'].includes(value)) {
    libraryYearFilter.value = ''
    libraryMonthFilter.value = ''
    libraryScopeFilter.value = ''
    libraryMediaTypeFilter.value = ''
    libraryRegionFilter.value = ''
    libraryTagFilters.value = []
    advancedFilterOpen.value = false
  }
  if (value === 'settings') reloadDiagnostics().catch(error => ElMessage.error(apiErrorMessage(error)))
})

onMounted(async () => {
  setCalendarThisWeek()
  await reload()
  startDashboardStream()
})

onUnmounted(() => {
  stopDashboardStream()
  stopMetadataProgress()
})
</script>


