export function taskTag(status) {
  if (status === 'failed') return 'danger'
  if (status === 'superseded') return 'info'
  if (status === 'completed' || status === 'synced') return 'success'
  if (['running', 'submitting', 'remote_downloading', 'remote_completed', 'local_copying', 'submitted', 'downloading'].includes(status)) return 'warning'
  if (status === 'cancelled') return 'info'
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
    link: '磁链 / 下载链接',
    metadata: '只登记作品',
    collect: '收录',
    add: '添加',
    import: '导入',
    manual: '磁力 / 下载链接',
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

export function titleFromResourceSeed(value) {
  let text = String(value || '').trim()
  if (!text) return ''
  try {
    const url = new URL(text)
    const name = decodeURIComponent(url.pathname.split('/').filter(Boolean).pop() || '')
    if (name) text = name
  } catch {
    const dn = text.match(/[?&]dn=([^&]+)/i)
    if (dn) text = decodeURIComponent(dn[1].replace(/\+/g, ' '))
  }
  return text
    .replace(/\.(torrent|mkv|mp4|avi|mov|wmv|flv|webm)$/i, '')
    .replace(/magnet:\?.*/i, '')
    .replace(/\[[^\]]*(1080p|2160p|720p|hevc|avc|aac|web|rip|简|繁|字幕)[^\]]*\]/gi, '')
    .replace(/【[^】]*(1080p|2160p|720p|hevc|avc|aac|web|rip|简|繁|字幕)[^】]*】/gi, '')
    .replace(/S\d{1,2}E\d{1,4}/ig, '')
    .replace(/(?:第|EP|E|episode)[\s._-]*\d{1,4}/ig, '')
    .replace(/[\s._-]+/g, ' ')
    .trim()
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
  return item.entry_display_title || item.display_title || item.title_cn || item.work_display_title || item.work_title || item.title_root || '未命名条目'
}

export function cardSubtitle(item) {
  if (!item) return '第一季'
  if (entryMediaType(item) === 'movie') return '电影'
  return normalizedSeasonLabel(item)
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

export function normalizedSeasonLabel(item) {
  const candidates = [
    item?.season_number,
    item?.entry_scope_label,
    item?.entry_badge_text,
    item?.entry_secondary_title,
  ]
  for (const value of candidates) {
    const text = String(value || '')
    const match = text.match(/(?:第\s*)?(\d{1,3})(?:\s*季|期|部|章|篇)|season\s*0*(\d{1,3})/i)
    const parsed = Number.parseInt(match?.[1] || match?.[2] || text, 10)
    if (Number.isFinite(parsed) && parsed > 0) return `第${parsed}季`
  }
  return '第一季'
}

export function catalogTags(item) {
  const titleTokens = [
    item?.entry_display_title,
    item?.display_title,
    item?.title_cn,
    item?.title_raw,
    item?.title_root,
    item?.work_title,
  ].map(value => String(value || '').trim()).filter(Boolean)
  return entryTags(item).filter(tag => {
    const text = String(tag || '').trim()
    if (!text) return false
    if (text.length > 8) return false
    if (/^\d{4}(年)?(\d{1,2}月)?$/.test(text)) return false
    if (/^\d{4}年\d{1,2}月$/.test(text)) return false
    if (/^(第?\d+季|Season\s*\d+|S\d+|TV|OVA|OAD|WEB|剧场版|电影)$/i.test(text)) return false
    if (/^(日本|中国|欧美|韩国|美国|其他|未定档)$/.test(text)) return false
    if (/^[A-Za-z0-9 ._-]{2,}$/.test(text)) return false
    if (titleTokens.some(title => title.includes(text) || text.includes(title))) return false
    return true
  })
}

export function listTextFromJson(value) {
  return parseJsonArray(value).join('\n')
}

export function jsonFromListText(value) {
  const items = String(value || '').replace(/[，,、]/g, '\n').split('\n').map(item => item.trim()).filter(Boolean)
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
  if (Number(queue.waiting || 0) > 0) return `${queue.waiting} 重试`
  return '空闲'
}

export function taskStatusText(row) {
  if (row?.status === 'completed') return '已完成'
  if (row?.status === 'synced') return '已整理'
  if (row?.status === 'submitting') return '提交下载器'
  if (row?.status === 'remote_downloading') return '云存储'
  if (row?.status === 'remote_completed') return '等待整理'
  if (row?.status === 'local_copying') return '整理中'
  if (row?.status === 'submitted') return '云存储'
  if (row?.status === 'running') return '处理中'
  if (row?.status === 'waiting') return '等待重试'
  if (row?.status === 'pending' && row?.waiting_retry) return '等待重试'
  if (row?.status === 'pending') return '待处理'
  if (row?.status === 'failed') return '失败'
  if (row?.status === 'cancelled') return '已取消'
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
  const hasResource = Boolean(row?.source_ref || row?.magnet || row?.torrent_url)
  if (progress > 0 && progress < 100 && ['downloading', 'remote_completed', 'local_copying'].includes(state)) {
    return `下载中 ${progress}%`
  }
  if (!hasResource && !row?.downloaded && ['missing', 'unknown', ''].includes(state)) return '未知'
  return {
    downloaded: '可观看',
    synced: '可观看',
    queued: '排队中',
    pending: '排队中',
    submitting: '云存储',
    running: '云存储',
    submitted: '云存储',
    remote_downloading: '云存储',
    downloading: '下载中',
    remote_completed: '下载中',
    local_copying: '下载中',
    cancelled: '已取消',
    failed: '失败',
    missing: '未知',
    unknown: '未知',
    available: '未下载',
  }[state] || (hasResource ? '未下载' : '未知')
}

export function episodeDownloadTag(row) {
  const state = episodeDownloadState(row)
  if (['downloaded', 'synced'].includes(state)) return 'success'
  if (['queued', 'pending', 'running', 'submitted', 'downloading', 'submitting', 'remote_downloading', 'remote_completed', 'local_copying'].includes(state)) return 'warning'
  if (state === 'failed') return 'danger'
  if (['missing', 'unknown'].includes(state)) return 'danger'
  if (state === 'cancelled') return 'info'
  return 'info'
}

export function episodeCanCancel(row) {
  return ['queued', 'pending', 'running', 'submitted', 'downloading', 'submitting', 'remote_downloading', 'remote_completed', 'local_copying', 'failed'].includes(episodeDownloadState(row))
}

export function episodeCanPause(row) {
  return false
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
  if (key === 'download') return '待处理表示已创建下载任务，等待进入云存储或本地下载阶段。'
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
