import { ElMessage } from 'element-plus'
import { listTextFromJson } from './viewHelpers'

function mediaTypeToView(type) {
  if (type === 'movie') return 'movies'
  if (type === 'tv') return 'tv'
  return 'library'
}

function blankSearchSourceForm() {
  return {
    name: '',
    kind: 'mikan',
    base_url: '',
    api_key: '',
    categories: '',
    timeout_seconds: 20,
    rate_limit_seconds: 0,
    enabled: true,
  }
}

function draftEpisodeItem(kind, item, index) {
  const episode = Number(item.episode_number || 0) || index + 1
  const ref = String(item.ref || '').trim()
  return {
    id: `discovery-${kind}-${Date.now()}-${index}-${Math.random().toString(16).slice(2)}`,
    source_mode: 'link',
    episode_number: episode,
    title: item.title || ref,
    file_name: item.file_name || '',
    source_ref: kind === 'video' ? ref : '',
    local_path: '',
    subtitle_ref: kind === 'subtitle' ? ref : '',
    subtitle_path: '',
  }
}

export function createDiscoveryActions(app, deps) {
  const { deleteAction, getAction, postAction, putAction, openMediaWizard, apiErrorMessage } = deps

  function resetSearchSourceForm() {
    app.searchSourceEditingId = 0
    Object.assign(app.searchSourceForm, blankSearchSourceForm())
  }

  function openSearchSourceDialog(item = null) {
    if (item?.id) editSearchSource(item)
    else resetSearchSourceForm()
    app.searchSourceDialogOpen = true
  }

  async function loadSearchSources() {
    app.searchSourcesLoading = true
    try {
      const data = await getAction('/search-sources')
      app.searchSources = data.items || []
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    } finally {
      app.searchSourcesLoading = false
    }
  }

  function editSearchSource(item) {
    app.searchSourceEditingId = Number(item.id || 0)
    Object.assign(app.searchSourceForm, {
      name: item.name || '',
      kind: item.kind || 'mikan',
      base_url: item.base_url || '',
      api_key: item.api_key || '',
      categories: item.categories || '',
      timeout_seconds: Number(item.timeout_seconds || 20),
      rate_limit_seconds: Number(item.rate_limit_seconds || 0),
      enabled: Boolean(Number(item.enabled ?? 1)),
    })
    app.searchSourceDialogOpen = true
  }

  async function saveSearchSource() {
    const payload = {
      ...app.searchSourceForm,
      timeout_seconds: Number(app.searchSourceForm.timeout_seconds || 20),
      rate_limit_seconds: Number(app.searchSourceForm.rate_limit_seconds || 0),
    }
    if (!payload.name.trim()) {
      ElMessage.warning('请填写搜索源名称')
      return
    }
    try {
      if (app.searchSourceEditingId) {
        await putAction(`/search-sources/${app.searchSourceEditingId}`, payload)
        ElMessage.success('搜索源已更新')
      } else {
        await postAction('/search-sources', payload)
        ElMessage.success('搜索源已添加')
      }
      resetSearchSourceForm()
      app.searchSourceDialogOpen = false
      await loadSearchSources()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function saveSearchSourceOrder() {
    try {
      const ids = (app.searchSources || []).map(item => Number(item.id || 0)).filter(Boolean)
      const data = await putAction('/search-sources/order', { ids })
      app.searchSources = data.items || app.searchSources
      ElMessage.success('搜索源优先级已保存')
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
      await loadSearchSources()
    }
  }

  async function deleteSearchSource(id) {
    try {
      await deleteAction(`/search-sources/${id}`)
      ElMessage.success('搜索源已删除')
      if (Number(app.searchSourceEditingId || 0) === Number(id || 0)) resetSearchSourceForm()
      await loadSearchSources()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function testSearchSource(item) {
    try {
      const result = await postAction(`/search-sources/${item.id}/test`)
      if (result.status === 'ok') ElMessage.success(`连接成功，返回 ${result.items || 0} 条`)
      else ElMessage.error(result.error || '搜索源测试失败')
      await loadSearchSources()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function runDiscoverySearch() {
    const keyword = String(app.discoveryState.keyword || '').trim()
    if (!keyword) {
      ElMessage.warning('请输入搜索关键词')
      return
    }
    app.discoveryState.loading = true
    app.discoveryState.backfill_entry_id = 0
    try {
      const data = await postAction('/discovery/search', {
        keyword,
        media_type: app.discoveryState.media_type || 'anime',
        year: Number(app.discoveryState.year || 0),
        season: app.discoveryState.season || '',
        source_ids: app.discoveryState.source_ids || [],
      })
      app.discoveryState.search = data.search || {}
      app.discoveryState.items = data.items || []
      ElMessage.success(`发现 ${app.discoveryState.items.length} 个候选`)
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    } finally {
      app.discoveryState.loading = false
    }
  }

  async function openDiscoveryCollectDraft(result) {
    const resultId = Number(result?.id || 0)
    if (!resultId) return
    try {
      const draft = await postAction(`/discovery/results/${resultId}/collect-draft`)
      const entry = draft.entry || {}
      app.view = mediaTypeToView(entry.media_type || 'anime')
      openMediaWizard('collect')
      app.mediaWizardStep = 1
      Object.assign(app.mediaWizardDraft, {
        title: entry.title || '',
        bangumi_id: entry.bangumi_id || '',
        tmdb_id: entry.tmdb_id || '',
        year: Number(entry.year || 0),
        month: Number(entry.month || 0),
        release_month: entry.year && entry.month ? `${entry.year}-${String(entry.month).padStart(2, '0')}` : '',
        region: entry.media_type === 'anime' ? 'jp' : '',
        poster_url: entry.poster_url || '',
        summary: entry.summary || '',
        tags_text: listTextFromJson(entry.tags_json || '[]').replace(/\n/g, '，'),
        genres_text: '',
      })
      app.mediaWizardResourceItems = (draft.resources || []).map((item, index) => draftEpisodeItem('video', item, index))
      app.mediaWizardSubtitleItems = (draft.subtitles || []).map((item, index) => draftEpisodeItem('subtitle', item, index))
      app.mediaWizardDraft.resources_text = app.mediaWizardResourceItems.map(item => item.source_ref).filter(Boolean).join('\n')
      app.mediaWizardDraft.subtitles_text = app.mediaWizardSubtitleItems.map(item => item.subtitle_ref).filter(Boolean).join('\n')
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function searchBackfillForCurrentEntry() {
    const entryId = Number(app.selectedEntry?.id || 0)
    if (!entryId) return
    app.discoveryState.loading = true
    try {
      const data = await postAction(`/entries/${entryId}/backfill/search`)
      app.discoveryState.keyword = data.search?.keyword || app.selectedEntry?.title_root || app.selectedEntry?.display_title || ''
      app.discoveryState.media_type = app.selectedEntry?.media_type || 'anime'
      app.discoveryState.search = data.search || {}
      app.discoveryState.items = data.items || []
      app.discoveryState.backfill_entry_id = entryId
      app.discoveryState.best_result_id = Number(data.best_result_id || 0)
      app.entryDrawerOpen = false
      app.view = 'discovery'
      ElMessage.success(`已生成补全候选 ${app.discoveryState.items.length} 个`)
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    } finally {
      app.discoveryState.loading = false
    }
  }

  async function applyBackfillResult(result) {
    const entryId = Number(app.discoveryState.backfill_entry_id || 0)
    const resultId = Number(result?.id || 0)
    if (!entryId || !resultId) return
    try {
      const payload = {
        search_id: Number(app.discoveryState.search?.id || 0),
        result_id: resultId,
        resource_ids: [],
        auto_download: true,
      }
      const data = await postAction(`/entries/${entryId}/backfill/apply`, payload)
      ElMessage.success(`已应用 ${data.applied || 0} 集，跳过 ${data.skipped || 0} 集`)
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  return {
    applyBackfillResult,
    deleteSearchSource,
    editSearchSource,
    loadSearchSources,
    openDiscoveryCollectDraft,
    openSearchSourceDialog,
    resetSearchSourceForm,
    runDiscoverySearch,
    saveSearchSource,
    saveSearchSourceOrder,
    searchBackfillForCurrentEntry,
    testSearchSource,
  }
}
