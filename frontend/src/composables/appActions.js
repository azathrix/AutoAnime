import { ElMessage } from 'element-plus'
import { entryTitle, errorMessage, inferEpisodeFromText, isValidResourceReference, isValidSubtitleReference, jsonFromListText, listTextFromJson, numberFromInput, splitTextLines, titleFromResourceSeed } from './viewHelpers'
import { createFileBrowserActions } from './fileBrowserActions'
import { createMetadataActions } from './metadataActions'

export function createAppActions(app, deps) {
  const { deleteAction, getAction, getDiagnostics, getMediaItem, getSettings, postAction, putAction, saveMediaItem, saveSettings } = deps
  let metadataProgressTimer = null
  const { clearEntryEditForm, refreshEntryMetadata } = createMetadataActions(app, {
    postAction,
    apiErrorMessage,
  })
  const { browseServerFiles, openServerFileBrowser, selectServerFile } = createFileBrowserActions(app, {
    getAction,
    apiErrorMessage,
  })

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
    app.episodeResourceForm.source_type = row.source_type === 'upload' ? 'manual' : (row.source_type || 'manual')
    app.episodeResourceForm.source_ref = row.source_ref || row.magnet || row.torrent_url || ''
    app.episodeResourceForm.subtitle_group = row.subtitle_group || ''
    app.episodeResourceForm.resolution = row.resolution || ''
    app.episodeResourceForm.language = row.language || ''
    app.episodeResourceForm.subtitle_format = row.subtitle_format || ''
    app.episodeResourceForm.subtitle_path = row.subtitle_file || ''
    app.episodeResourceForm.subtitle_url = row.subtitle_url || ''
    app.episodeResourceForm.subtitle_file_name = row.subtitle_file_name || ''
    app.episodeResourceForm.local_path = row.local_path || ''
    app.episodeResourceDialogOpen = true
  }

  async function saveEpisodeResource() {
    try {
      const episodeId = Number(app.episodeResourceForm.episode_id || 0)
      if (!episodeId) return
      await putAction(`/episodes/${episodeId}`, {
        source_title: app.episodeResourceForm.title,
        resource_ref: app.episodeResourceForm.source_ref,
        subtitle_group: app.episodeResourceForm.subtitle_group,
        resolution: app.episodeResourceForm.resolution,
        language: app.episodeResourceForm.language,
        subtitle_format: app.episodeResourceForm.subtitle_format,
        local_path: app.episodeResourceForm.local_path || '',
        subtitle_path: app.episodeResourceForm.subtitle_path,
        subtitle_ref: app.episodeResourceForm.subtitle_url || app.episodeResourceForm.subtitle_file_name || '',
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

  function toggleEntryResourceRow(row, column) {
    const property = String(column?.property || column?.type || '')
    if (property === 'selection') return
    const key = row?.key
    if (!key) return
    const current = app.expandedResourceKeys || []
    app.expandedResourceKeys = current.includes(key)
      ? current.filter(item => item !== key)
      : [...current, key]
  }

  async function deleteEpisodeResource(row) {
    try {
      const resourceId = Number(row?.resource_id || 0)
      const episodeId = Number(row?.episode_id || 0)
      if (resourceId) {
        await deleteAction(`/episode-resources/${resourceId}`)
      } else if (episodeId) {
        await deleteAction(`/episodes/${episodeId}`)
      } else {
        return
      }
      ElMessage.success('集数已删除')
      if (app.selectedEntry?.id) {
        await app.openEntry(app.selectedEntry.id, app.selectedEntryDomain, app.selectedEntryMediaType)
      }
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function clearCompletedDownloadTasks() {
    try {
      const result = await postAction('/download-tasks/clear-completed')
      ElMessage.success(result?.message || '已清除完成任务')
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function refreshCurrentEntryLocalStatus() {
    try {
      const entryId = Number(app.selectedEntry?.id || 0)
      if (!entryId) return
      const result = await postAction(`/entries/${entryId}/refresh-local-status`)
      ElMessage.success(result?.message || '本地状态已刷新')
      await app.openEntry(entryId, app.selectedEntryDomain, app.selectedEntryMediaType)
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function refreshAllLocalStatus() {
    try {
      const result = await postAction('/maintenance/refresh-local-status')
      ElMessage.success(result?.message || '全部本地状态已刷新')
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function migrateEpisodeModel() {
    try {
      const result = await postAction('/maintenance/migrate-episode-model')
      ElMessage.success(result?.message || '集数模型迁移完成')
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

  async function cancelDownloadTask(row) {
    try {
      const taskId = Number(row?.id || row?.download_job_id || 0)
      if (!taskId) return
      const result = await postAction(`/download-tasks/${taskId}/cancel`)
      ElMessage.success(result?.message || '下载任务已取消')
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function retryDownloadTask(row) {
    try {
      const taskId = Number(row?.id || row?.download_job_id || 0)
      if (!taskId) return
      const result = await postAction(`/download-tasks/${taskId}/retry`)
      ElMessage.success(result?.message || '下载任务已重试')
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function deleteDownloadTask(row) {
    try {
      const taskId = Number(row?.id || row?.download_job_id || 0)
      if (!taskId) return
      const result = await deleteAction(`/download-tasks/${taskId}`)
      ElMessage.success(result?.message || '下载任务已删除')
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
    app.mediaWizardResourceItems = []
    app.mediaWizardSubtitleItems = []
    Object.assign(app.mediaWizardDraft, {
      source_mode: 'link',
      title: '',
      bangumi_id: '',
      tmdb_id: '',
      year: 0,
      month: 0,
      release_month: '',
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
      resource_input: '',
      subtitle_input: '',
      subtitle_format: '',
      language: '',
    })
    app.mediaWizardOpen = true
  }

  async function advanceMediaWizard() {
    if (app.mediaWizardStep === 0) {
      addMediaWizardResourceLines(false)
      const titleSeed = titleFromResourceSeed(app.mediaWizardDraft.resource_input || app.mediaWizardDraft.resources_text || '')
      if (titleSeed && !app.mediaWizardDraft.title) {
        app.mediaWizardDraft.title = titleSeed
      }
    }
    if (app.mediaWizardStep === 1) {
      if (!app.mediaWizardDraft.title.trim()) {
        ElMessage.warning('请先填写作品标题')
        return
      }
    }
    if (app.mediaWizardStep === 2) {
      if (!addMediaWizardResourceLines(false) || !addMediaWizardSubtitleLines(false)) {
        return
      }
      if (!app.mediaWizardResourceRows.length && app.mediaWizardDraft.source_mode !== 'metadata') {
        app.mediaWizardDraft.source_mode = 'metadata'
      }
      if (app.mediaWizardInvalidResourceCount || app.mediaWizardInvalidSubtitleCount) {
        ElMessage.warning('请先修正无法识别的资源或字幕')
        return
      }
      const emptyEpisode = app.mediaWizardResourceRows.find(item => !Number(item.episode || item.episode_number || 0))
      if (emptyEpisode) {
        ElMessage.warning('请先填写资源对应集数')
        return
      }
    }
    if (app.mediaWizardStep === 3) {
      if (!addMediaWizardSubtitleLines(false)) return
      if (app.mediaWizardInvalidSubtitleCount) {
        ElMessage.warning('请先修正无法识别的字幕')
        return
      }
    }
    app.mediaWizardStep = Math.min(4, app.mediaWizardStep + 1)
  }

  function mediaWizardEpisodeFallback(index) {
    return app.currentMediaType === 'movie' ? 1 : index + 1
  }

  function addMediaWizardResourceLines(showMessage = true) {
    const lines = splitTextLines(app.mediaWizardDraft.resource_input || '')
    if (!lines.length) return true
    const invalid = lines.find(line => !isValidResourceReference(line))
    if (invalid) {
      ElMessage.warning(`资源链接格式无效: ${invalid}`)
      return false
    }
    const baseIndex = app.mediaWizardResourceItems.length
    const next = lines.map((line, index) => ({
      id: `resource-${Date.now()}-${baseIndex + index}`,
      source_mode: 'link',
      source_ref: line,
      file_name: '',
      title: titleFromResourceSeed(line) || line,
      episode_number: inferEpisodeFromText(line, mediaWizardEpisodeFallback(baseIndex + index)),
      subtitle_ref: '',
      subtitle_source_mode: 'link',
      subtitle_format: app.mediaWizardDraft.subtitle_format || '',
      language: app.mediaWizardDraft.language || '',
    }))
    app.mediaWizardResourceItems = [...app.mediaWizardResourceItems, ...next]
    app.mediaWizardDraft.resource_input = ''
    app.mediaWizardDraft.resources_text = app.mediaWizardResourceItems
      .filter(item => item.source_mode === 'link')
      .map(item => item.source_ref)
      .join('\n')
    if (showMessage) ElMessage.success(`已添加 ${next.length} 条资源`)
    return true
  }

  function addMediaWizardSubtitleLines(showMessage = true) {
    const lines = splitTextLines(app.mediaWizardDraft.subtitle_input || '')
    if (!lines.length) return true
    const invalid = lines.find(line => !isValidSubtitleReference(line))
    if (invalid) {
      ElMessage.warning(`字幕链接或文件名格式无效: ${invalid}`)
      return false
    }
    const baseIndex = app.mediaWizardSubtitleItems.length
    const next = lines.map((line, index) => ({
      id: `subtitle-${Date.now()}-${baseIndex + index}`,
      source_mode: 'link',
      subtitle_ref: line,
      file_name: '',
      episode_number: inferEpisodeFromText(line, mediaWizardEpisodeFallback(baseIndex + index)),
      subtitle_format: app.mediaWizardDraft.subtitle_format || 'external',
      language: app.mediaWizardDraft.language || '',
    }))
    app.mediaWizardSubtitleItems = [...app.mediaWizardSubtitleItems, ...next]
    app.mediaWizardDraft.subtitle_input = ''
    app.mediaWizardDraft.subtitles_text = app.mediaWizardSubtitleItems.map(item => item.subtitle_ref || item.file_name).join('\n')
    if (showMessage) ElMessage.success(`已添加 ${next.length} 条字幕`)
    return true
  }

  function removeMediaWizardResourceItem(index) {
    app.mediaWizardResourceItems = app.mediaWizardResourceItems.filter((_, itemIndex) => itemIndex !== index)
    app.mediaWizardDraft.resources_text = app.mediaWizardResourceItems
      .filter(item => item.source_mode === 'link')
      .map(item => item.source_ref)
      .join('\n')
  }

  function removeMediaWizardSubtitleItem(index) {
    app.mediaWizardSubtitleItems = app.mediaWizardSubtitleItems.filter((_, itemIndex) => itemIndex !== index)
    app.mediaWizardDraft.subtitles_text = app.mediaWizardSubtitleItems.map(item => item.subtitle_ref || item.file_name).join('\n')
  }

  function hasFieldValue(value) {
    if (value === null || value === undefined) return false
    return String(value).trim() !== '' && String(value).trim() !== '0'
  }

  function fillIfEmpty(target, key, value) {
    if (!hasFieldValue(value) || hasFieldValue(target[key])) return
    target[key] = value
  }

  function fillListIfEmpty(target, key, value) {
    if (hasFieldValue(target[key])) return
    const text = key === 'tags_text' ? tagsInputText(value) : listTextFromJson(value)
    if (text) target[key] = text
  }

  function monthFieldValue(year, month) {
    const y = Number(year || 0)
    const m = Number(month || 0)
    if (y <= 0 || m <= 0 || m > 12) return ''
    return `${y}-${String(m).padStart(2, '0')}`
  }

  function parseMonthField(value) {
    const match = String(value || '').match(/^(\d{4})-(\d{2})/)
    if (!match) return { year: 0, month: 0 }
    return { year: Number(match[1]), month: Number(match[2]) }
  }

  function tagsInputText(value) {
    return listTextFromJson(value).replace(/\n/g, '，')
  }

  function applyMetadataToWizard(item) {
    fillIfEmpty(app.mediaWizardDraft, 'title', item.title || item.original_title)
    fillIfEmpty(app.mediaWizardDraft, 'year', item.year)
    fillIfEmpty(app.mediaWizardDraft, 'month', item.month)
    fillIfEmpty(app.mediaWizardDraft, 'release_month', monthFieldValue(item.year, item.month))
    fillIfEmpty(app.mediaWizardDraft, 'region', item.region)
    fillIfEmpty(app.mediaWizardDraft, 'poster_url', item.poster_url)
    fillIfEmpty(app.mediaWizardDraft, 'summary', item.summary)
    fillIfEmpty(app.mediaWizardDraft, 'bangumi_score', item.bangumi_score)
    fillIfEmpty(app.mediaWizardDraft, 'tmdb_score', item.tmdb_score)
    fillListIfEmpty(app.mediaWizardDraft, 'tags_text', item.tags_json || item.tags)
    if (item.provider === 'bangumi') fillIfEmpty(app.mediaWizardDraft, 'bangumi_id', String(item.id || ''))
    if (item.provider === 'tmdb') fillIfEmpty(app.mediaWizardDraft, 'tmdb_id', String(item.id || ''))
  }

  function applyMetadataToEntryEdit(item) {
    fillIfEmpty(app.entryEditForm, 'title_cn', item.title || item.original_title)
    fillIfEmpty(app.entryEditForm, 'title_raw', item.original_title)
    fillIfEmpty(app.entryEditForm, 'year', item.year)
    fillIfEmpty(app.entryEditForm, 'month', item.month)
    fillIfEmpty(app.entryEditForm, 'release_month', monthFieldValue(item.year, item.month))
    fillIfEmpty(app.entryEditForm, 'region', item.region)
    fillIfEmpty(app.entryEditForm, 'poster_url', item.poster_url)
    fillIfEmpty(app.entryEditForm, 'summary', item.summary)
    fillIfEmpty(app.entryEditForm, 'bangumi_score', item.bangumi_score)
    fillIfEmpty(app.entryEditForm, 'tmdb_score', item.tmdb_score)
    fillListIfEmpty(app.entryEditForm, 'tags_text', item.tags_json || item.tags)
    if (item.provider === 'bangumi') fillIfEmpty(app.entryEditForm, 'bangumi_id', String(item.id || ''))
    if (item.provider === 'tmdb') fillIfEmpty(app.entryEditForm, 'tmdb_id', String(item.id || ''))
  }

  function selectedMetadataCandidate(provider = app.metadataSearchProvider) {
    return provider === 'tmdb' ? app.metadataSelectedTmdb : app.metadataSelectedBangumi
  }

  function selectMetadataCandidate(item) {
    if (!item) return
    if (item.provider === 'tmdb') {
      app.metadataSelectedTmdb = item
    } else {
      app.metadataSelectedBangumi = item
      app.metadataSearchProvider = 'tmdb'
    }
  }

  function skipMetadataProvider() {
    if (app.metadataSearchProvider === 'bangumi') {
      app.metadataSearchProvider = 'tmdb'
      return
    }
    confirmMetadataMatch()
  }

  function confirmMetadataMatch() {
    const selected = [app.metadataSelectedBangumi, app.metadataSelectedTmdb].filter(Boolean)
    if (!selected.length) {
      ElMessage.warning('请先选择一个匹配结果，或取消匹配')
      return
    }
    for (const item of selected) {
      if (app.metadataSearchTarget === 'entry') applyMetadataToEntryEdit(item)
      else applyMetadataToWizard(item)
    }
    app.metadataSearchDialogOpen = false
    ElMessage.success('已按空字段优先填入作品信息')
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
      : (app.mediaWizardDraft.title || titleFromResourceSeed(app.mediaWizardDraft.resource_input || app.mediaWizardSeed || app.mediaWizardDraft.resources_text || '') || '')
    app.metadataSearchResults = { bangumi: [], tmdb: [] }
    app.metadataSelectedBangumi = null
    app.metadataSelectedTmdb = null
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

  async function commitMediaWizard() {
    app.mediaWizardSaving = true
    try {
      addMediaWizardResourceLines(false)
      addMediaWizardSubtitleLines(false)
      const linkItems = app.mediaWizardResourceItems.filter(item => item.source_ref)
      const resourcesText = linkItems.map(item => item.source_ref).join('\n')
      const sourceMode = linkItems.length ? 'link' : 'metadata'
      const release = parseMonthField(app.mediaWizardDraft.release_month)
      const payload = {
        mode: sourceMode,
        title: app.mediaWizardDraft.title,
        bangumi_id: app.mediaWizardDraft.bangumi_id,
        tmdb_id: app.mediaWizardDraft.tmdb_id,
        bangumi_score: Number(app.mediaWizardDraft.bangumi_score || 0),
        tmdb_score: Number(app.mediaWizardDraft.tmdb_score || 0),
        year: release.year || numberFromInput(app.mediaWizardDraft.year, 0),
        month: release.month || numberFromInput(app.mediaWizardDraft.month, 0),
        season_number: numberFromInput(app.mediaWizardDraft.season_number, 1),
        region: app.mediaWizardDraft.region || (app.currentMediaType === 'anime' ? 'jp' : ''),
        episode_number: 0,
        resource_title: '',
        source_ref: '',
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
      if (entryId && linkItems.length) {
        await postAction(`/entries/${entryId}/resources/import`, {
          resources_text: resourcesText,
          resources: linkItems.map(item => ({
            source_ref: item.source_ref,
            episode_number: Number(item.episode_number || 0),
            title: item.title || item.source_ref,
            language: item.language || app.mediaWizardDraft.language || '',
            subtitle_format: item.subtitle_format || app.mediaWizardDraft.subtitle_format || '',
            subtitle_url: item.subtitle_ref || '',
          })),
          subtitles_text: app.mediaWizardSubtitleItems.map(item => item.subtitle_ref || item.file_name).filter(Boolean).join('\n'),
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
      bangumi_score: Number(entry.bangumi_score || 0),
      tmdb_score: Number(entry.tmdb_score || 0),
      year: Number(entry.year || 0),
      month: Number(entry.month || 0),
      release_month: monthFieldValue(entry.year, entry.month),
      season_number: Number(entry.season_number || 1),
      media_type: entry.media_type || 'anime',
      region: entry.region || 'jp',
      title_romaji: entry.title_romaji || '',
      title_raw: entry.title_raw || '',
      poster_url: entry.poster_url || '',
      summary: entry.summary || '',
      tags_text: tagsInputText(entry.tags_json),
      genres_text: listTextFromJson(entry.genres_json),
    })
    app.entryEditDialogOpen = true
  }

  function entryEditPayload() {
    const release = parseMonthField(app.entryEditForm.release_month)
    return {
      title_cn: app.entryEditForm.title_cn,
      bangumi_id: app.entryEditForm.bangumi_id,
      tmdb_id: app.entryEditForm.tmdb_id,
      bangumi_score: Number(app.entryEditForm.bangumi_score || 0),
      tmdb_score: Number(app.entryEditForm.tmdb_score || 0),
      year: release.year || Number(app.entryEditForm.year || 0),
      month: release.month || Number(app.entryEditForm.month || 0),
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
        bangumi_score: Number(entry.bangumi_score || app.entryEditForm.bangumi_score || 0),
        tmdb_score: Number(entry.tmdb_score || app.entryEditForm.tmdb_score || 0),
        year: entry.year || app.entryEditForm.year,
        month: entry.month || app.entryEditForm.month,
        release_month: monthFieldValue(entry.year || app.entryEditForm.year, entry.month || app.entryEditForm.month),
        tags_text: entry.tags_json ? tagsInputText(entry.tags_json) : app.entryEditForm.tags_text,
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
    app.settings.tv_name_template = app.settings.tv_name_template || '{title_base}/Season {season:02d}/{title_base} - S{season:02d}E{episode:02d} - 第 {episode:02d} 话'
    app.settings.episode_name_template = app.settings.episode_name_template || '{title_base} - S{season:02d}E{episode:02d} - 第 {episode:02d} 话'
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

  async function deleteCurrentEntry() {
    const id = Number(app.selectedEntry?.id || 0)
    const mediaType = app.selectedEntryMediaType || app.currentMediaType || 'anime'
    if (!id) return
    try {
      const result = await deleteAction(`/media/${mediaType}/${id}`)
      ElMessage.success(result?.message || '媒体条目已删除')
      app.entryDrawerOpen = false
      app.selectedEntryDetail = null
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function repairLocalPaths() {
    try {
      const result = await postAction('/maintenance/repair-local-paths')
      ElMessage.success(result?.message || '本地路径已修复')
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  return {
    addDownloader, addMediaWizardResourceLines, addMediaWizardSubtitleLines, advanceMediaWizard, apiErrorMessage, applyMetadataToWizard,
    archiveCurrentEntry, browseServerFiles, cancelAllDownloads, cancelDownloadTask, cancelEpisodeDownload, cancelQueueDownload, clearCompletedDownloadTasks, clearEntryEditForm,
    commitEpisodeImport, commitMediaWizard,
    deleteCurrentEntry, deleteDownloadTask, deleteEpisodeResource, deleteRssSubscription, downloadCurrentEntryResources, downloadEpisodeResource,
    editRssSubscription, entryEditPayload, exportLogs, fetchEntryMetadata, loadRssSubscriptions, normalizeSettingsShape, openEntry,
    openEntryEditDialog, openEpisodeResourceEditor, openMediaWizard, openMetadataSearch, openProcessorSettings, openQueueEntry, openRssDialog, openServerFileBrowser,
    openScheduledSettings, migrateEpisodeModel, refreshAllLocalStatus, refreshCurrentEntryLocalStatus, refreshEntryMetadata, repairLocalPaths, retryDownloadTask, refreshEpisodeResource, removeDownloader, removeMediaWizardResourceItem,
    removeMediaWizardSubtitleItem, resetRssForm, resetSelectionRules, runAction, runMetadataSearch, saveAllSettings, saveBatchSubtitles,
    saveEntryEditForm, saveEpisodeResource, saveProcessorSettings, saveRssSubscription, saveScheduledJob, searchWizardMetadata, selectServerFile,
    confirmMetadataMatch, selectedMetadataCandidate, selectMetadataCandidate, skipMetadataProvider, toggleEntryResourceRow,
    startMetadataProgress, stopMetadataProgress, syncScheduledJobForm,
  }
}
