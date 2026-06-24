import { ElMessage } from 'element-plus'

export function createFileBrowserActions(app, deps) {
  const { getAction, apiErrorMessage } = deps

  async function browseServerFiles(path = '') {
    app.fileBrowser.loading = true
    try {
      const query = path ? `?path=${encodeURIComponent(path)}` : ''
      const result = await getAction(`/files/browse${query}`)
      app.fileBrowser.current = result.current || ''
      app.fileBrowser.parent = result.parent || ''
      app.fileBrowser.items = result.items || []
    } catch (error) {
      ElMessage.error(apiErrorMessage(error))
    } finally {
      app.fileBrowser.loading = false
    }
  }

  async function openServerFileBrowser(mode = 'video') {
    app.fileBrowser.mode = mode
    app.fileBrowser.open = true
    await browseServerFiles(app.fileBrowser.current || '')
  }

  function selectServerFile(item) {
    if (!item) return
    if (item.kind === 'directory') {
      browseServerFiles(item.path)
      return
    }
    if (app.fileBrowser.mode === 'subtitle') {
      if (item.kind !== 'subtitle') {
        ElMessage.warning('请选择字幕文件')
        return
      }
      app.episodeResourceForm.subtitle_path = item.path
    } else {
      if (item.kind !== 'video') {
        ElMessage.warning('请选择视频文件')
        return
      }
      app.episodeResourceForm.local_path = item.path
    }
    app.fileBrowser.open = false
  }

  return { browseServerFiles, openServerFileBrowser, selectServerFile }
}
