import { ElMessage } from 'element-plus'
import {
  entryTitle,
  errorMessage,
  inferEpisodeFromText,
  isValidResourceReference,
  isValidSubtitleReference,
  jsonFromListText,
  listTextFromJson,
  mediaTypeLabel,
  numberFromInput,
  regionLabel,
  resourceReferenceKind,
  sourceModeText,
  splitTextLines,
  titleFromResourceSeed,
} from './viewHelpers'

export function createAppActions(app, deps) {
  const {
    deleteAction,
    getAction,
    getDiagnostics,
    getMediaItem,
    getSettings,
    postAction,
    putAction,
    saveMediaItem,
    saveSettings,
    uploadFile,
  } = deps
  let metadataProgressTimer = null

  async function runAction(path) {
    try {
      const result = await postAction(path)
      ElMessage.success(result?.message || '操作已提交')
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  function syncScheduledJobForm(job = app.selectedScheduledJob) {
    app.scheduledJobForm.enabled = Boolean(Number(job?.enabled ?? 1))
    app.scheduledJobForm.interval_minutes = Math.max(1, Number(job?.interval_minutes || 1))
  }

  function openScheduledSettings() {
    syncScheduledJobForm()
    app.scheduledSettingsDialogOpen = true
  }

  async function saveScheduledJob() {
    const job = app.selectedScheduledJob
    if (!job?.job_key) return
    try {
      await putAction(`/scheduled-jobs/${job.job_key}`, {
        enabled: Boolean(app.scheduledJobForm.enabled),
        interval_minutes: Number(app.scheduledJobForm.interval_minutes || 1),
      })
      ElMessage.success('定时任务设置已保存')
      app.scheduledSettingsDialogOpen = false
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  function openProcessorSettings() {
    app.processorSettingsForm.download_concurrency = Number(app.settings.download_concurrency || 2)
    app.processorSettingsDialogOpen = true
  }

  async function saveProcessorSettings() {
    try {
      const value = Math.max(1, Math.min(12, Number(app.processorSettingsForm.download_concurrency || 2)))
      await putAction('/processors/download/settings', { download_concurrency: value })
      app.settings.download_concurrency = value
      app.processorSettingsDialogOpen = false
      ElMessage.success('下载处理器设置已保存')
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function loadRssSubscriptions() {
    app.rssLoading = true
    try {
      const data = await getAction('/rss-subscriptions')
      app.rssSubscriptions = data.items || []
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    } finally {
      app.rssLoading = false
    }
  }

  function resetRssForm() {
    app.rssEditingId = 0
    app.rssForm.name = ''
    app.rssForm.url = ''
    app.rssForm.kind = 'mikan'
    app.rssForm.enabled = true
  }

  async function openRssDialog() {
    resetRssForm()
    app.rssDialogOpen = true
    await loadRssSubscriptions()
  }

  function editRssSubscription(item) {
    app.rssEditingId = Number(item.id || 0)
    app.rssForm.name = item.name || ''
    app.rssForm.url = item.url || ''
    app.rssForm.kind = item.kind || 'mikan'
    app.rssForm.enabled = Boolean(Number(item.enabled ?? 1))
  }

  async function saveRssSubscription() {
    const payload = {
      name: app.rssForm.name,
      url: app.rssForm.url,
      kind: app.rssForm.kind,
      enabled: Boolean(app.rssForm.enabled),
    }
    if (!payload.url.trim()) {
      ElMessage.warning('请填写 RSS 地址')
      return
    }
    try {
      if (app.rssEditingId) {
        await putAction(`/rss-subscriptions/${app.rssEditingId}`, payload)
        ElMessage.success('RSS 订阅已更新')
      } else {
        await postAction('/rss-subscriptions', payload)
        ElMessage.success('RSS 订阅已添加')
      }
      resetRssForm()
      await loadRssSubscriptions()
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function deleteRssSubscription(id) {
    try {
      await deleteAction(`/rss-subscriptions/${id}`)
      ElMessage.success('RSS 订阅已删除')
      await loadRssSubscriptions()
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  function openEpisodeResourceEditor(row) {
    app.episodeResourceForm.episode_id = row.episode_id || 0
    app.episodeResourceForm.resource_id = row.resource_id || 0
    app.episodeResourceForm.subtitle_id = row.subtitle_id || 0
    app.episodeResourceForm.episode_number = row.episode_number || ''
    app.episodeResourceForm.title = row.resource_title || ''
    app.episodeResourceForm.subtitle_group = row.subtitle_group || ''
    app.episodeResourceForm.resolution = row.resolution || ''
    app.episodeResourceForm.language = row.language || ''
    app.episodeResourceForm.subtitle_format = row.subtitle_format || ''
    app.episodeResourceForm.subtitle_path = row.subtitle_file || ''
    app.episodeResourceForm.subtitle_url = row.subtitle_url || ''
    app.episodeResourceForm.subtitle_file_name = row.subtitle_file_name || ''
    app.episodeResourceDialogOpen = true
  }

  async function saveEpisodeResource() {
    try {
      const episodeId = Number(app.episodeResourceForm.episode_id || 0)
      if (!episodeId) return
      await putAction(`/episodes/${episodeId}/resource`, {
        resource_id: Number(app.episodeResourceForm.resource_id || 0),
        title: app.episodeResourceForm.title,
        subtitle_group: app.episodeResourceForm.subtitle_group,
        resolution: app.episodeResourceForm.resolution,
        language: app.episodeResourceForm.language,
        subtitle_format: app.episodeResourceForm.subtitle_format,
        selected: true,
      })
      await putAction(`/episodes/${episodeId}/subtitle`, {
        subtitle_id: Number(app.episodeResourceForm.subtitle_id || 0),
        language: app.episodeResourceForm.language,
        subtitle_format: app.episodeResourceForm.subtitle_format,
        subtitle_path: app.episodeResourceForm.subtitle_path,
        subtitle_url: app.episodeResourceForm.subtitle_url,
        file_name: app.episodeResourceForm.subtitle_file_name,
        selected: true,
      })
      ElMessage.success('集数资源已保存')
      app.episodeResourceDialogOpen = false
      if (app.selectedEntry?.id) {
        await app.openEntry(app.selectedEntry.id, app.selectedEntryDomain, app.selectedEntryMediaType)
      }
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function deleteEpisodeResource(row) {
    try {
      const resourceId = Number(row?.resource_id || 0)
      if (!resourceId) return
      await deleteAction(`/episode-resources/${resourceId}`)
      ElMessage.success('集数资源已删除')
      if (app.selectedEntry?.id) {
        await app.openEntry(app.selectedEntry.id, app.selectedEntryDomain, app.selectedEntryMediaType)
      }
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function refreshEpisodeResource(row) {
    try {
      const episodeId = Number(row?.episode_id || 0)
      if (!episodeId) return
      await postAction(`/episodes/${episodeId}/refresh`)
      ElMessage.success('集数状态已刷新')
      if (app.selectedEntry?.id) {
        await app.openEntry(app.selectedEntry.id, app.selectedEntryDomain, app.selectedEntryMediaType)
      }
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function downloadEpisodeResource(row) {
    try {
      const episodeId = Number(row?.episode_id || 0)
      if (!episodeId) return
      await postAction(`/episodes/${episodeId}/download`)
      ElMessage.success('已加入下载队列')
      if (app.selectedEntry?.id) {
        await app.openEntry(app.selectedEntry.id, app.selectedEntryDomain, app.selectedEntryMediaType)
      }
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function cancelEpisodeDownload(row) {
    try {
      const episodeId = Number(row?.episode_id || 0)
      if (!episodeId) return
      await postAction(`/episodes/${episodeId}/download/cancel`)
      ElMessage.success('已取消该集下载')
      if (app.selectedEntry?.id) {
        await app.openEntry(app.selectedEntry.id, app.selectedEntryDomain, app.selectedEntryMediaType)
      }
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function cancelQueueDownload(row) {
    try {
      const entryId = Number(row?.entry_id || 0)
      const episodeNumber = Number(row?.episode_number || 0)
      if (!entryId || !episodeNumber) return
      const result = await postAction('/downloads/cancel', {
        entry_id: entryId,
        episode_number: episodeNumber,
      })
      ElMessage.success(result?.message || '已取消该集下载')
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function cancelAllDownloads() {
    try {
      const result = await postAction('/downloads/cancel-all')
      ElMessage.success(result?.message || '已取消全部下载任务')
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function pauseEpisodeDownload(row) {
    try {
      const episodeId = Number(row?.episode_id || 0)
      if (!episodeId) return
      await postAction(`/episodes/${episodeId}/download/pause`)
      ElMessage.success('已暂停该集下载')
      if (app.selectedEntry?.id) {
        await app.openEntry(app.selectedEntry.id, app.selectedEntryDomain, app.selectedEntryMediaType)
      }
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function downloadCurrentEntryResources() {
    try {
      const entryId = Number(app.selectedEntry?.id || 0)
      if (!entryId) return
      const result = await postAction(`/entries/${entryId}/download`)
      ElMessage.success(result.message || '已提交批量下载')
      await app.openEntry(entryId, app.selectedEntryDomain, app.selectedEntryMediaType)
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function saveBatchSubtitles() {
    try {
      const entryId = Number(app.selectedEntry?.id || 0)
      if (!entryId) return
      const text = [app.batchSubtitleForm.subtitles_text, ...(app.batchSubtitleForm.file_names || [])].filter(Boolean).join('\n')
      await postAction(`/entries/${entryId}/subtitles/batch`, {
        subtitles_text: text,
        file_names: app.batchSubtitleForm.file_names || [],
        subtitle_format: app.batchSubtitleForm.subtitle_format,
        language: app.batchSubtitleForm.language,
      })
      ElMessage.success('字幕批量配置已保存')
      app.batchSubtitleDialogOpen = false
      await app.openEntry(entryId, app.selectedEntryDomain, app.selectedEntryMediaType)
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function commitEpisodeImport() {
    try {
      const entryId = Number(app.selectedEntry?.id || 0)
      if (!entryId) return
      if (app.episodeImportInvalidCount > 0) {
        ElMessage.warning('请先处理无法识别的资源或字幕')
        return
      }
      await postAction(`/entries/${entryId}/resources/import`, {
        resources_text: app.episodeImportForm.resources_text,
        subtitles_text: app.episodeImportForm.subtitles_text,
        subtitle_format: app.episodeImportForm.subtitle_format,
        language: app.episodeImportForm.language,
      })
      ElMessage.success('集数资源已导入')
      app.episodeImportDialogOpen = false
      await app.openEntry(entryId, app.selectedEntryDomain, app.selectedEntryMediaType)
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  function openMediaWizard(mode = 'collect') {
    app.mediaWizardMode = mode
    app.mediaWizardStep = 0
    app.mediaWizardSeed = ''
    app.mediaWizardCandidates = []
    app.mediaWizardFiles = []
    Object.assign(app.mediaWizardDraft, {
      source_mode: 'link',
      title: '',
      bangumi_id: '',
      tmdb_id: '',
      year: 0,
      month: 0,
      season_number: 1,
      region: app.currentMediaType === 'anime' ? 'jp' : '',
      poster_url: '',
      summary: '',
      tags_text: '',
      genres_text: '',
      episode_number: 0,
      resource_title: '',
      source_ref: '',
      resources_text: '',
      subtitles_text: '',
      subtitle_format: '',
      language: '',
    })
    app.mediaWizardOpen = true
  }

  async function advanceMediaWizard() {
    if (app.mediaWizardStep === 0) {
      const seed = app.mediaWizardSeed.trim()
      if (seed && isValidResourceReference(seed)) {
        app.mediaWizardDraft.source_mode = 'link'
        app.mediaWizardDraft.resources_text = seed
        app.mediaWizardDraft.source_ref = seed
      }
      const titleSeed = titleFromResourceSeed(seed || app.mediaWizardDraft.resources_text || app.mediaWizardFiles?.[0]?.name || '')
      if (titleSeed && !app.mediaWizardDraft.title) {
        app.mediaWizardDraft.title = titleSeed
      }
    }
    if (app.mediaWizardStep === 1 && !app.mediaWizardDraft.title.trim()) {
      ElMessage.warning('请先填写作品标题')
      return
    }
    if (app.mediaWizardStep === 2) {
      if (app.mediaWizardDraft.source_mode === 'link' && !app.mediaWizardResourceRows.length) {
        ElMessage.warning('请至少填写一条资源链接，或返回选择只登记作品')
        return
      }
      if (app.mediaWizardInvalidResourceCount || app.mediaWizardInvalidSubtitleCount) {
        ElMessage.warning('请先修正无法识别的资源或字幕')
        return
      }
    }
    app.mediaWizardStep = Math.min(3, app.mediaWizardStep + 1)
  }

  function applyMetadataToWizard(item) {
    app.mediaWizardDraft.title = item.title || item.original_title || app.mediaWizardDraft.title
    app.mediaWizardDraft.year = item.year || app.mediaWizardDraft.year
    app.mediaWizardDraft.month = item.month || app.mediaWizardDraft.month
    app.mediaWizardDraft.region = item.region || app.mediaWizardDraft.region
    app.mediaWizardDraft.poster_url = item.poster_url || app.mediaWizardDraft.poster_url
    app.mediaWizardDraft.summary = item.summary || app.mediaWizardDraft.summary
    if (item.tags_json || item.tags) app.mediaWizardDraft.tags_text = listTextFromJson(item.tags_json || item.tags)
    if (item.genres_json || item.genres) app.mediaWizardDraft.genres_text = listTextFromJson(item.genres_json || item.genres)
    if (item.provider === 'bangumi') app.mediaWizardDraft.bangumi_id = String(item.id || '')
    if (item.provider === 'tmdb') app.mediaWizardDraft.tmdb_id = String(item.id || '')
  }

  function applyMetadataToEntryEdit(item) {
    app.entryEditForm.title_cn = item.title || item.original_title || app.entryEditForm.title_cn
    app.entryEditForm.title_raw = item.original_title || app.entryEditForm.title_raw
    app.entryEditForm.year = item.year || app.entryEditForm.year
    app.entryEditForm.month = item.month || app.entryEditForm.month
    app.entryEditForm.region = item.region || app.entryEditForm.region
    app.entryEditForm.poster_url = item.poster_url || app.entryEditForm.poster_url
    app.entryEditForm.summary = item.summary || app.entryEditForm.summary
    if (item.tags_json || item.tags) app.entryEditForm.tags_text = listTextFromJson(item.tags_json || item.tags)
    if (item.genres_json || item.genres) app.entryEditForm.genres_text = listTextFromJson(item.genres_json || item.genres)
    if (item.provider === 'bangumi') app.entryEditForm.bangumi_id = String(item.id || '')
    if (item.provider === 'tmdb') app.entryEditForm.tmdb_id = String(item.id || '')
  }

  function applyMetadataSearchItem(item) {
    if (app.metadataSearchTarget === 'entry') applyMetadataToEntryEdit(item)
    else applyMetadataToWizard(item)
    app.metadataSearchDialogOpen = false
    ElMessage.success('已填入作品信息')
  }

  async function searchWizardMetadata(provider, keyword, autoApply = false) {
    const text = String(keyword || '').trim()
    if (!text) return
    try {
      const result = await getAction(`/metadata/search?provider=${encodeURIComponent(provider)}&keyword=${encodeURIComponent(text)}`)
      const candidates = result.items || []
      if (provider === 'bangumi') app.mediaWizardCandidates = candidates
      if (autoApply && candidates.length === 1) applyMetadataToWizard(candidates[0])
    } catch (error) {
      if (!autoApply) ElMessage.error(apiErrorMessage(error))
    }
  }

  async function openMetadataSearch(provider = 'bangumi', target = 'wizard') {
    app.metadataSearchProvider = provider
    app.metadataSearchTarget = target
    app.metadataSearchKeyword = target === 'entry'
      ? (app.entryEditForm.title_cn || app.entryEditForm.title_raw || '')
      : (app.mediaWizardDraft.title || titleFromResourceSeed(app.mediaWizardSeed || app.mediaWizardDraft.resources_text || '') || '')
    app.metadataSearchResults = { bangumi: [], tmdb: [] }
    app.metadataSearchDialogOpen = true
    if (app.metadataSearchKeyword) await runMetadataSearch()
  }

  async function runMetadataSearch() {
    app.metadataSearchLoading = true
    try {
      const keyword = encodeURIComponent(app.metadataSearchKeyword)
      const [bangumiResult, tmdbResult] = await Promise.allSettled([
        getAction(`/metadata/search?provider=bangumi&keyword=${keyword}`),
        getAction(`/metadata/search?provider=tmdb&keyword=${keyword}`),
      ])
      app.metadataSearchResults = {
        bangumi: bangumiResult.status === 'fulfilled' ? (bangumiResult.value.items || []) : [],
        tmdb: tmdbResult.status === 'fulfilled' ? (tmdbResult.value.items || []) : [],
      }
      if (bangumiResult.status === 'rejected' && tmdbResult.status === 'rejected') {
        ElMessage.error(apiErrorMessage(bangumiResult.reason))
      }
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    } finally {
      app.metadataSearchLoading = false
    }
  }

  async function uploadMediaWizardFiles() {
    const uploaded = []
    for (const file of app.mediaWizardFiles) {
      const raw = file.raw || file
      if (!raw) continue
      uploaded.push(await uploadFile('/uploads/local', raw))
    }
    return uploaded
  }

  async function commitMediaWizard() {
    app.mediaWizardSaving = true
    try {
      const resourcesText = app.mediaWizardDraft.resources_text || app.mediaWizardDraft.source_ref || ''
      const firstResource = splitTextLines(resourcesText)[0] || ''
      const sourceRef = firstResource || app.mediaWizardDraft.source_ref || app.mediaWizardSeed || ''
      const payload = {
        mode: app.mediaWizardDraft.source_mode,
        title: app.mediaWizardDraft.title,
        bangumi_id: app.mediaWizardDraft.bangumi_id,
        tmdb_id: app.mediaWizardDraft.tmdb_id,
        year: numberFromInput(app.mediaWizardDraft.year, 0),
        month: numberFromInput(app.mediaWizardDraft.month, 0),
        season_number: numberFromInput(app.mediaWizardDraft.season_number, 1),
        region: app.mediaWizardDraft.region || (app.currentMediaType === 'anime' ? 'jp' : ''),
        episode_number: inferEpisodeFromText(firstResource || app.mediaWizardDraft.resource_title, numberFromInput(app.mediaWizardDraft.episode_number, 1)),
        resource_title: app.mediaWizardDraft.resource_title || firstResource,
        source_ref: sourceRef,
        subtitle_group: app.mediaWizardDraft.subtitle_group || '',
        resolution: app.mediaWizardDraft.resolution || '',
        language: app.mediaWizardDraft.language || '',
        subtitle_format: app.mediaWizardDraft.subtitle_format || '',
        subtitle_path: app.mediaWizardDraft.subtitle_path || '',
        subtitle_url: splitTextLines(app.mediaWizardDraft.subtitles_text || app.mediaWizardDraft.subtitle_url || '')[0] || '',
        subtitle_file_name: app.mediaWizardDraft.subtitle_file_name || '',
        poster_url: app.mediaWizardDraft.poster_url || '',
        summary: app.mediaWizardDraft.summary || '',
        tags_json: jsonFromListText(app.mediaWizardDraft.tags_text || ''),
        genres_json: jsonFromListText(app.mediaWizardDraft.genres_text || ''),
      }
      const created = await postAction(`/media/${app.currentMediaType}`, payload)
      const entryId = Number(created.entry?.id || created.detail?.entry?.id || 0)
      if (entryId && splitTextLines(resourcesText).length) {
        await postAction(`/entries/${entryId}/resources/import`, {
          resources_text: resourcesText,
          subtitles_text: app.mediaWizardDraft.subtitles_text || '',
          subtitle_format: app.mediaWizardDraft.subtitle_format || '',
          language: app.mediaWizardDraft.language || '',
        })
      }
      if (entryId && app.mediaWizardDraft.source_mode === 'local' && app.mediaWizardFiles.length) {
        const uploaded = await uploadMediaWizardFiles()
        await postAction(`/entries/${entryId}/uploads/import`, {
          uploads: uploaded,
          subtitle_format: app.mediaWizardDraft.subtitle_format || '',
          language: app.mediaWizardDraft.language || '',
        })
      }
      ElMessage.success('媒体已收录')
      app.mediaWizardOpen = false
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    } finally {
      app.mediaWizardSaving = false
    }
  }

  function exportLogs() {
    const blob = new Blob([app.filteredServerLogText || ''], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `anitrack-logs-${new Date().toISOString().replace(/[:.]/g, '-')}.txt`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  }

  async function saveAllSettings() {
    app.savingSettings = true
    try {
      normalizeSettingsShape()
      await saveSettings(app.settings)
      ElMessage.success('设置已保存')
      await Object.assign(app.settings, await getSettings())
      normalizeSettingsShape()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    } finally {
      app.savingSettings = false
    }
  }

  function apiErrorMessage(error) {
    return errorMessage(error)
  }

  async function openEntry(id, domain = 'seasonal', mediaType = '') {
    try {
      app.selectedEntryDomain = domain
      app.selectedEntryMediaType = mediaType || (domain === 'library' ? app.currentMediaType : 'anime')
      const apiMediaType = app.selectedEntryMediaType || 'anime'
      app.selectedEntryDetail = await getMediaItem(apiMediaType, id)
      app.entryDrawerOpen = true
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function openQueueEntry(row) {
    if (!row?.entry_id) return
    const domain = row.domain_kind === 'library' ? 'library' : 'seasonal'
    await openEntry(row.entry_id, domain, row.media_type || '')
  }

  function stopMetadataProgress() {
    if (metadataProgressTimer) window.clearInterval(metadataProgressTimer)
    metadataProgressTimer = null
  }

  function startMetadataProgress() {
    stopMetadataProgress()
    app.metadataFetchProgress = 8
    metadataProgressTimer = window.setInterval(() => {
      app.metadataFetchProgress = Math.min(92, app.metadataFetchProgress + 7)
    }, 280)
  }

  function openEntryEditDialog() {
    const entry = app.selectedEntry || {}
    Object.assign(app.entryEditForm, {
      title_cn: entry.title_cn || entry.display_title || '',
      bangumi_id: entry.bangumi_id || '',
      tmdb_id: entry.tmdb_id || '',
      year: Number(entry.year || 0),
      month: Number(entry.month || 0),
      season_number: Number(entry.season_number || 1),
      media_type: entry.media_type || 'anime',
      region: entry.region || 'jp',
      title_romaji: entry.title_romaji || '',
      title_raw: entry.title_raw || '',
      poster_url: entry.poster_url || '',
      summary: entry.summary || '',
      tags_text: listTextFromJson(entry.tags_json),
      genres_text: listTextFromJson(entry.genres_json),
    })
    app.entryEditDialogOpen = true
  }

  function entryEditPayload() {
    return {
      title_cn: app.entryEditForm.title_cn,
      bangumi_id: app.entryEditForm.bangumi_id,
      tmdb_id: app.entryEditForm.tmdb_id,
      year: Number(app.entryEditForm.year || 0),
      month: Number(app.entryEditForm.month || 0),
      season_number: Number(app.entryEditForm.season_number || 1),
      media_type: app.entryEditForm.media_type,
      region: app.entryEditForm.region,
      title_romaji: app.entryEditForm.title_romaji,
      title_raw: app.entryEditForm.title_raw,
      poster_url: app.entryEditForm.poster_url,
      summary: app.entryEditForm.summary,
      tags_json: jsonFromListText(app.entryEditForm.tags_text),
      genres_json: jsonFromListText(app.entryEditForm.genres_text),
    }
  }

  async function fetchEntryMetadata() {
    const entryId = Number(app.selectedEntry?.id || 0)
    if (!entryId) return
    startMetadataProgress()
    app.metadataFetching = true
    try {
      const provider = app.entryEditForm.bangumi_id ? 'bangumi' : (app.entryEditForm.tmdb_id ? 'tmdb' : 'bangumi')
      const mediaType = app.selectedEntryMediaType || 'anime'
      const result = await postAction(`/media/${mediaType}/${entryId}/metadata/fetch`, {
        provider,
        bangumi_id: app.entryEditForm.bangumi_id,
        tmdb_id: app.entryEditForm.tmdb_id,
      })
      const entry = result.entry || result
      Object.assign(app.entryEditForm, {
        title_cn: entry.title_cn || app.entryEditForm.title_cn,
        title_romaji: entry.title_romaji || app.entryEditForm.title_romaji,
        title_raw: entry.title_raw || app.entryEditForm.title_raw,
        poster_url: entry.poster_url || app.entryEditForm.poster_url,
        summary: entry.summary || app.entryEditForm.summary,
        year: entry.year || app.entryEditForm.year,
        month: entry.month || app.entryEditForm.month,
        tags_text: entry.tags_json ? listTextFromJson(entry.tags_json) : app.entryEditForm.tags_text,
        genres_text: entry.genres_json ? listTextFromJson(entry.genres_json) : app.entryEditForm.genres_text,
      })
      app.metadataFetchProgress = 100
      ElMessage.success('信息已填入，请确认后保存')
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    } finally {
      app.metadataFetching = false
      window.setTimeout(stopMetadataProgress, 600)
    }
  }

  async function saveEntryEditForm() {
    const entryId = Number(app.selectedEntry?.id || 0)
    if (!entryId) return
    try {
      app.selectedEntryDetail = await saveMediaItem(app.selectedEntryMediaType || 'anime', entryId, entryEditPayload())
      app.entryEditDialogOpen = false
      ElMessage.success('作品信息已保存')
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  function normalizeSettingsShape() {
    app.settings.subtitle_priority = Array.isArray(app.settings.subtitle_priority) ? app.settings.subtitle_priority : []
    app.settings.resolution_priority = Array.isArray(app.settings.resolution_priority) ? app.settings.resolution_priority : []
    app.settings.language_priority = Array.isArray(app.settings.language_priority) ? app.settings.language_priority : []
    app.settings.secondary_language_priority = Array.isArray(app.settings.secondary_language_priority) ? app.settings.secondary_language_priority : []
    app.settings.movie_quality_priority = Array.isArray(app.settings.movie_quality_priority) ? app.settings.movie_quality_priority : []
    app.settings.movie_source_priority = Array.isArray(app.settings.movie_source_priority) ? app.settings.movie_source_priority : []
    app.settings.movie_subtitle_priority = Array.isArray(app.settings.movie_subtitle_priority) ? app.settings.movie_subtitle_priority : []
    app.settings.tv_quality_priority = Array.isArray(app.settings.tv_quality_priority) ? app.settings.tv_quality_priority : []
    app.settings.tv_source_priority = Array.isArray(app.settings.tv_source_priority) ? app.settings.tv_source_priority : []
    app.settings.tv_subtitle_priority = Array.isArray(app.settings.tv_subtitle_priority) ? app.settings.tv_subtitle_priority : []
    app.settings.downloaders = Array.isArray(app.settings.downloaders) ? app.settings.downloaders : []
    app.settings.movie_name_template = app.settings.movie_name_template || '{title_base}/{title_base}'
    app.settings.tv_name_template = app.settings.tv_name_template || '{title_base}/Season {season:02d}/{title_base} - S{season:02d}E{episode:02d}'
    app.settings.episode_name_template = app.settings.episode_name_template || '{title_cn} - S{season:02d}E{episode:02d} - {episode_title}'
  }

  function resetSelectionRules(type) {
    normalizeSettingsShape()
    if (type === 'movie') {
      app.settings.movie_quality_priority = ['2160p', '1080p', '720p']
      app.settings.movie_source_priority = ['BluRay', 'WEB-DL', 'WebRip', 'HDTV']
      app.settings.movie_subtitle_priority = ['简繁', '简体', '繁体', '双语', '中字']
      ElMessage.success('已重置电影自动选集规则，保存设置后生效')
      return
    }
    if (type === 'tv') {
      app.settings.tv_quality_priority = ['2160p', '1080p', '720p']
      app.settings.tv_source_priority = ['WEB-DL', 'WebRip', 'HDTV']
      app.settings.tv_subtitle_priority = ['简繁', '简体', '繁体', '双语', '中字']
      ElMessage.success('已重置电视剧自动选集规则，保存设置后生效')
      return
    }
    app.settings.subtitle_priority = ['LoliHouse', '喵萌奶茶屋', '猎户压制部', '百冬练习组']
    app.settings.resolution_priority = ['2160p', '1080p', '720p']
    app.settings.language_priority = ['简繁', '简体', '繁体']
    app.settings.secondary_language_priority = ['内封', '内嵌', '外挂']
    ElMessage.success('已重置动画自动选集规则，保存设置后生效')
  }

  function addDownloader() {
    normalizeSettingsShape()
    app.settings.downloaders = [
      ...app.settings.downloaders,
      {
        id: `downloader-${Date.now()}`,
        name: 'PikPak',
        type: 'pikpak_rclone',
        remote_dir: '/Temp',
        rclone_remote: 'pikpak',
        rclone_config_path: '/data/rclone/rclone.conf',
        rclone_command: 'rclone',
        rpc_url: '',
        token: '',
        auth_mode: 'token',
        username: '',
        password: '',
        access_token: '',
        refresh_token: '',
        proxy: '',
        enabled: true,
        max_attempts: 3,
      },
    ]
  }

  function removeDownloader(index) {
    normalizeSettingsShape()
    app.settings.downloaders = app.settings.downloaders.filter((_, i) => i !== index)
  }

  async function archiveCurrentEntry() {
    if (app.selectedEntryDomain !== 'seasonal') return
    const id = app.selectedEntry?.id
    if (!id) return
    const result = await deleteAction(`/seasonal/${id}`)
    if (result.status === 'not_found' || result.status === 'invalid_domain') {
      ElMessage.warning(result.message || '条目不存在')
    } else {
      ElMessage.success('已归档，新番页不再显示')
    }
    app.entryDrawerOpen = false
    app.selectedEntryDetail = null
    app.selectedEntryDomain = 'seasonal'
    await app.reload()
  }

  return {
    addDownloader,
    advanceMediaWizard,
    apiErrorMessage,
    applyMetadataSearchItem,
    applyMetadataToWizard,
    archiveCurrentEntry,
    cancelAllDownloads,
    cancelEpisodeDownload,
    cancelQueueDownload,
    commitEpisodeImport,
    commitMediaWizard,
    deleteEpisodeResource,
    deleteRssSubscription,
    downloadCurrentEntryResources,
    downloadEpisodeResource,
    editRssSubscription,
    entryEditPayload,
    exportLogs,
    fetchEntryMetadata,
    loadRssSubscriptions,
    normalizeSettingsShape,
    openEntry,
    openEntryEditDialog,
    openEpisodeResourceEditor,
    openMediaWizard,
    openMetadataSearch,
    openProcessorSettings,
    openQueueEntry,
    openRssDialog,
    openScheduledSettings,
    pauseEpisodeDownload,
    refreshEpisodeResource,
    removeDownloader,
    resetRssForm,
    resetSelectionRules,
    runAction,
    runMetadataSearch,
    saveAllSettings,
    saveBatchSubtitles,
    saveEntryEditForm,
    saveEpisodeResource,
    saveProcessorSettings,
    saveRssSubscription,
    saveScheduledJob,
    searchWizardMetadata,
    startMetadataProgress,
    stopMetadataProgress,
    syncScheduledJobForm,
    uploadMediaWizardFiles,
  }
}
