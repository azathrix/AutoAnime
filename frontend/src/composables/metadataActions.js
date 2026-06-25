import { ElMessage } from 'element-plus'

export function createMetadataActions(app, deps) {
  const { postAction, apiErrorMessage } = deps

  function clearEntryEditForm() {
    Object.assign(app.entryEditForm, {
      title_cn: '',
      bangumi_id: '',
      tmdb_id: '',
      bangumi_score: 0,
      tmdb_score: 0,
      year: 0,
      month: 0,
      release_month: '',
      season_number: 1,
      episode_offset: 0,
      media_type: app.selectedEntryMediaType || app.currentMediaType || 'anime',
      region: 'jp',
      title_romaji: '',
      title_raw: '',
      poster_url: '',
      summary: '',
      tags_text: '',
      genres_text: '',
    })
    ElMessage.info('已清空编辑表单，保存后才会写入')
  }

  async function refreshEntryMetadata(item = null, domain = '', mediaType = '') {
    const entry = item || app.selectedEntry || {}
    const entryId = Number(entry.id || entry.entry_id || 0)
    if (!entryId) return
    const apiMediaType = mediaType
      || app.selectedEntryMediaType
      || (app.entryMediaType ? app.entryMediaType(entry) : '')
      || app.currentMediaType
      || 'anime'
    try {
      await postAction(`/media/${apiMediaType}/${entryId}/metadata/fetch`, {
        bangumi_id: entry.bangumi_id || '',
        tmdb_id: entry.tmdb_id || '',
      })
      ElMessage.success('元数据已刷新')
      if (app.selectedEntry?.id && Number(app.selectedEntry.id) === entryId) {
        await app.openEntry(entryId, domain || app.selectedEntryDomain, apiMediaType)
      }
      await app.reload()
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  return { clearEntryEditForm, refreshEntryMetadata }
}
