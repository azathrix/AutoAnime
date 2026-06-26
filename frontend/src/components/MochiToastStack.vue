<script setup>
import { computed, inject } from 'vue'

const app = inject('appContext') || {}

const toasts = computed(() => {
  const rows = []
  const scanner = app.dashboard?.scanner_status || {}
  if (scanner.status && scanner.status !== 'idle') {
    rows.push({
      key: `scanner-${scanner.updated_at || scanner.status}`,
      tone: scanner.status === 'failed' ? 'danger' : scanner.status === 'running' ? 'warning' : 'success',
      title: scanner.status === 'failed' ? '扫描失败' : scanner.status === 'running' ? 'RSS 扫描中' : '扫描完成',
      text: scanner.message || scanner.stage || scanner.updated_at || '扫描器状态已更新',
    })
  }

  const failedDownloads = Number(app.dashboard?.download_overview?.failed || 0)
  if (failedDownloads > 0) {
    rows.push({
      key: `download-failed-${failedDownloads}`,
      tone: 'danger',
      title: '下载任务异常',
      text: `${failedDownloads} 个任务需要处理`,
    })
  }

  const activeDownloads = Number(app.dashboard?.download_overview?.active || 0)
  if (activeDownloads > 0) {
    rows.push({
      key: `download-active-${activeDownloads}`,
      tone: 'info',
      title: '下载进行中',
      text: `${activeDownloads} 个任务正在推进`,
    })
  }

  return rows.slice(0, 3)
})
</script>

<template>
  <div class="mochi-toast-stack" aria-live="polite">
    <div v-for="item in toasts" :key="item.key" class="mochi-toast" :class="item.tone">
      <strong>{{ item.title }}</strong>
      <span>{{ item.text }}</span>
    </div>
  </div>
</template>
