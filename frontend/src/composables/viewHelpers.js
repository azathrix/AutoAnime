export function taskTag(status) {
  if (status === 'failed') return 'danger'
  if (status === 'superseded') return 'info'
  if (status === 'completed' || status === 'submitted' || status === 'synced') return 'success'
  if (status === 'running') return 'warning'
  return 'info'
}

export function queueTag(queue) {
  if (!queue) return 'info'
  if (Number(queue.failed || 0) > 0) return 'danger'
  if (queue.queue_state === 'running' || Number(queue.running || 0) > 0) return 'warning'
  if (queue.queue_state === 'debouncing' || queue.queue_state === 'cooldown' || Number(queue.waiting || 0) > 0) return 'info'
  if (Number(queue.pending || 0) > 0) return 'primary'
  return 'success'
}

export function errorMessage(error) {
  return error?.response?.data?.detail || error?.response?.data?.message || error?.message || '请求失败'
}

export function mediaTypeLabel(value) {
  const key = String(value || 'anime')
  return { anime: '动画', movie: '电影', tv: '剧集', ova: 'OVA' }[key] || key
}

export function regionLabel(value) {
  const key = String(value || '')
  return { jp: '日本', cn: '中国', us: '欧美', kr: '韩国', other: '其他' }[key] || key || '未知'
}

export function sourceModeText(value) {
  const key = String(value || 'link')
  return {
    local: '本地文件',
    link: '磁链 / 下载链接',
    metadata: '只登记作品',
    collect: '收录',
    add: '添加',
    import: '导入',
  }[key] || key
}

export function numberFromInput(value, fallback = 0) {
  const matches = String(value ?? '').match(/\d+/g)
  if (!matches?.length) return fallback
  const parsed = Number.parseInt(matches[matches.length - 1], 10)
  return Number.isFinite(parsed) ? parsed : fallback
}

export function splitTextLines(value) {
  return String(value || '').split(/\r?\n/).map(item => item.trim()).filter(Boolean)
}

export function inferEpisodeFromText(value, fallback = 1) {
  const text = String(value || '')
  const patterns = [/S\d{1,2}E(\d{1,4})/i, /(?:第|EP|E|episode)[\s._-]*(\d{1,4})/i, /[\s._\-[【(](\d{1,4})[\])】)\s._-]/]
  for (const pattern of patterns) {
    const match = text.match(pattern)
    if (match) {
      const valueNumber = Number.parseInt(match[1], 10)
      if (Number.isFinite(valueNumber) && valueNumber > 0) return valueNumber
    }
  }
  return Math.max(1, fallback)
}

export function isValidResourceReference(value) {
  const text = String(value || '').trim().toLowerCase()
  if (!text || /\s/.test(text.replace(/^magnet:\?xt=[^&]+/i, ''))) return false
  return text.startsWith('magnet:?') || text.startsWith('http://') || text.startsWith('https://') || text.startsWith('ftp://') || text.startsWith('thunder://') || text.startsWith('ed2k://')
}

export function resourceReferenceKind(value) {
  const text = String(value || '').trim().toLowerCase()
  if (text.startsWith('magnet:?')) return '磁链'
  if (text.endsWith('.torrent')) return '种子'
  if (text.startsWith('http://') || text.startsWith('https://')) return '下载链接'
  if (text.startsWith('ftp://')) return 'FTP'
  if (text.startsWith('thunder://')) return '迅雷'
  if (text.startsWith('ed2k://')) return 'ED2K'
  return '资源'
}

export function isValidSubtitleReference(value) {
  const text = String(value || '').trim().toLowerCase()
  if (!text) return false
  if (text.startsWith('http://') || text.startsWith('https://')) return true
  return /\.(ass|srt|ssa|vtt|sup|sub)(\?.*)?$/.test(text)
}

export function entryMediaType(item) {
  const value = String(item?.media_type || 'anime').toLowerCase()
  if (value === 'movie' || value === 'film') return 'movie'
  if (value === 'tv' || value === 'series' || value === 'drama') return 'tv'
  return 'anime'
}

export function entryTitle(item) {
  if (!item) return ''
  return item.work_display_title || item.entry_display_title || item.display_title || item.title_cn || item.work_title || item.title_root || '未命名条目'
}

export function cardSubtitle(item) {
  if (!item) return 'Season 01'
  return item.entry_scope_label || item.entry_secondary_title || item.bangumi_id || item.tmdb_id || 'Season 01'
}

export function cardInitials(item) {
  return entryTitle(item).slice(0, 2) || 'AN'
}

export function watchableCount(item) {
  if (!item) return 0
  return Number(item.local_asset_count || 0)
}

export function parseDateValue(value) {
  if (!value) return 0
  const time = new Date(value).getTime()
  return Number.isFinite(time) ? time : 0
}

export function parseJsonArray(value) {
  if (!value) return []
  if (Array.isArray(value)) return value.filter(Boolean).map(item => String(item))
  try {
    const parsed = JSON.parse(String(value))
    return Array.isArray(parsed) ? parsed.filter(Boolean).map(item => String(item)) : []
  } catch {
    return []
  }
}

export function entryTags(item) {
  const tags = [...parseJsonArray(item?.genres_json), ...parseJsonArray(item?.tags_json)]
  return Array.from(new Set(tags.map(tag => tag.trim()).filter(Boolean)))
}

export function listTextFromJson(value) {
  return parseJsonArray(value).join('\n')
}

export function jsonFromListText(value) {
  const items = String(value || '').replace(/,/g, '\n').split('\n').map(item => item.trim()).filter(Boolean)
  return JSON.stringify(Array.from(new Set(items)))
}

export function isQueueActive(queue) {
  if (!queue) return false
  if (queue.system_queue) return false
  return Number(queue.pending || 0) > 0 || Number(queue.running || 0) > 0 || Number(queue.failed || 0) > 0 || Number(queue.waiting || 0) > 0 || ['running', 'debouncing', 'rerun_pending', 'cooldown', 'ready', 'failed'].includes(String(queue.queue_state || ''))
}

export function queueBadge(queue) {
  if (!queue) return '-'
  if (Number(queue.failed || 0) > 0) return `${queue.failed} 失败`
  if (Number(queue.running || 0) > 0) return `${queue.running} 运行`
  if (Number(queue.pending || 0) > 0) return `${queue.pending} 待处理`
  return '空闲'
}

export function taskStatusText(row) {
  if (row?.status === 'completed') return '已完成'
  if (row?.status === 'synced') return '已整理'
  if (row?.status === 'submitted') return '已提交'
  if (row?.status === 'running') return '处理中'
  if (row?.status === 'waiting') return '等待重试'
  if (row?.status === 'pending' && row?.waiting_retry) return '等待重试'
  if (row?.status === 'pending') return '待处理'
  if (row?.status === 'failed') return '失败'
  if (row?.status === 'superseded') return '已替代'
  return row?.status || ''
}

export function queueTaskProgressText(row) {
  const parts = []
  if (Number(row?.progress || 0) > 0) parts.push(`${Number(row.progress)}%`)
  if (row?.progress_text) parts.push(String(row.progress_text))
  if (row?.message && row.message !== row.progress_text) parts.push(String(row.message))
  return parts.length ? parts.join(' · ') : '-'
}

export function subtitleFormatText(value) {
  const key = String(value || '').toLowerCase()
  if (key === 'embedded' || key === 'hardsub' || key === 'burned') return '内嵌'
  if (key === 'muxed' || key === 'softsub' || key === 'internal') return '内封'
  if (key === 'external' || key === 'sidecar') return '外挂'
  return '-'
}

export function episodeDownloadState(row) {
  if (row?.downloaded) return 'downloaded'
  return String(row?.download_status || row?.status || '').toLowerCase()
}

export function episodeDownloadText(row) {
  const state = episodeDownloadState(row)
  const progress = Number(row?.download_progress || 0)
  if (progress > 0 && progress < 100 && ['queued', 'pending', 'running', 'submitted', 'downloading'].includes(state)) {
    return `下载中 ${progress}%`
  }
  return {
    downloaded: '可观看',
    synced: '可观看',
    queued: '排队中',
    pending: '排队中',
    running: '下载中',
    submitted: '下载中',
    downloading: '下载中',
    remote_completed: '整理中',
    paused: '已暂停',
    cancelled: '已取消',
    failed: '失败',
    available: '未下载',
  }[state] || '未下载'
}

export function episodeDownloadTag(row) {
  const state = episodeDownloadState(row)
  if (['downloaded', 'synced'].includes(state)) return 'success'
  if (['queued', 'pending', 'running', 'submitted', 'downloading', 'remote_completed'].includes(state)) return 'warning'
  if (state === 'failed') return 'danger'
  if (state === 'cancelled' || state === 'paused') return 'info'
  return 'info'
}

export function episodeCanCancel(row) {
  return ['queued', 'pending', 'running', 'submitted', 'downloading', 'failed', 'paused'].includes(episodeDownloadState(row))
}

export function episodeCanPause(row) {
  return ['queued', 'pending', 'running', 'submitted', 'downloading'].includes(episodeDownloadState(row))
}

export function formatCountdown(seconds) {
  const value = Math.max(0, Number(seconds || 0))
  if (!value) return '即将执行'
  const minutes = Math.floor(value / 60)
  const rest = value % 60
  if (minutes <= 0) return `${rest} 秒`
  return `${minutes} 分 ${rest} 秒`
}

export function queueState(queue) {
  if (queue.queue_state === 'failed' || Number(queue.failed || 0) > 0) return '失败'
  if (queue.queue_state === 'running' || Number(queue.running || 0) > 0) return '运行中'
  if (queue.queue_state === 'debouncing') return '聚合中'
  if (queue.queue_state === 'rerun_pending') return '待重跑'
  if (queue.queue_state === 'cooldown' || Number(queue.waiting || 0) > 0) return '等待重试'
  if (queue.queue_state === 'ready' || Number(queue.pending || 0) > 0) return '可执行'
  return '空闲'
}

export function queuePendingHint(queue) {
  const key = String(queue?.key || '')
  if (key === 'rss') return '这里只显示最近的 RSS 候选；Mikan、元数据、选集、下载到本地由任务链推进。'
  if (key === 'download') return '待处理表示已选中发布，等待下载器完成并整理到本地媒体库。'
  if (key === 'local_sync') return '待处理表示下载已完成，等待整理到本地媒体库。'
  if (key === 'selection') return '待处理表示元数据已完成，等待按规则自动选择发布。'
  if (key === 'processor') return '这里显示流水线统一处理器任务，扫描后可直接看每条数据卡在 RSS、匹配、元数据、整合还是下载。'
  if (key === 'backfill') return '待处理表示番剧已入库，等待去 Mikan 番组页补抓历史条目。'
  if (key === 'metadata') return '待处理表示已拿到 Bangumi 线索，等待补全正式元数据。'
  if (key === 'mikan_match') return '待处理表示 RSS 候选已入队，等待解析对应的 Mikan/Bangumi 关联。'
  return '任务已入队，等待执行。'
}

export function startOfWeek(date) {
  const base = new Date(date)
  const day = base.getDay() || 7
  base.setHours(0, 0, 0, 0)
  base.setDate(base.getDate() - day + 1)
  return base
}

export function addDays(date, days) {
  const next = new Date(date)
  next.setDate(next.getDate() + days)
  return next
}

export function formatDateKey(date) {
  if (Number.isNaN(date.getTime())) return ''
  const y = date.getFullYear()
  const m = `${date.getMonth() + 1}`.padStart(2, '0')
  const d = `${date.getDate()}`.padStart(2, '0')
  return `${y}-${m}-${d}`
}
