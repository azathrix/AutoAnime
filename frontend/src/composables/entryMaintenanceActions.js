import { ElMessage } from 'element-plus'

export function createEntryMaintenanceActions(app, deps) {
  const { postAction, apiErrorMessage } = deps

  async function reloadEntry(entryId) {
    await app.openEntry(entryId, app.selectedEntryDomain, app.selectedEntryMediaType)
    await app.reload()
  }

  async function refreshCurrentEntryLocalStatus() {
    try {
      const entryId = Number(app.selectedEntry?.id || 0)
      if (!entryId) return
      const result = await postAction(`/entries/${entryId}/refresh-local-status`)
      ElMessage.success(result?.message || '本地状态已刷新')
      await reloadEntry(entryId)
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function organizeCurrentEntryLocalFiles() {
    try {
      const entryId = Number(app.selectedEntry?.id || 0)
      if (!entryId) return
      const result = await postAction(`/entries/${entryId}/organize-local-files`)
      ElMessage.success(result?.message || '本地资源已整理')
      await reloadEntry(entryId)
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    }
  }

  async function backfillCurrentEntrySeason() {
    try {
      const entryId = Number(app.selectedEntry?.id || 0)
      if (!entryId) return
      const result = await postAction(`/entries/${entryId}/backfill-current-season`)
      ElMessage.success(result?.message || '已加入补全本季任务')
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

  async function organizeAllLocalFiles() {
    try {
      const result = await postAction('/maintenance/organize-local-files')
      ElMessage.success(result?.message || '全部本地资源已整理')
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
    backfillCurrentEntrySeason,
    migrateEpisodeModel,
    organizeAllLocalFiles,
    organizeCurrentEntryLocalFiles,
    refreshAllLocalStatus,
    refreshCurrentEntryLocalStatus,
    repairLocalPaths,
  }
}
