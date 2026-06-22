<script>
import { appContextComponent } from '../composables/appContext'

export default appContextComponent()
</script>

<template>
      <section v-if="view === 'logs'" class="logs-page">
        <div class="logs-layout">
          <el-card class="console-card log-page-card">
            <template #header>
              <div class="card-header-row">
                <div>
                  <strong>服务日志</strong>
                  <span>查看、搜索和导出运行日志</span>
                </div>
                <el-tag :type="logsBadgeType">{{ logsBadgeText }}</el-tag>
              </div>
            </template>
            <div class="detail-summary-grid">
              <div><span>最近错误</span><strong>{{ dashboard.console_overview?.recent_error_count || 0 }}</strong></div>
              <div><span>最近警告</span><strong>{{ dashboard.console_overview?.recent_warn_count || 0 }}</strong></div>
              <div><span>显示行数</span><strong>{{ filteredServerLogs.length || 0 }}</strong></div>
              <div><span>筛选</span><strong>{{ logKeyword || '全部' }}</strong></div>
            </div>
            <div class="log-console">
              <div class="log-toolbar">
                <el-input v-model="logKeyword" clearable placeholder="搜索日志" />
                <el-button plain @click="exportLogs">导出日志</el-button>
                <el-button plain @click="runAction('/logs/clear')">清空日志</el-button>
              </div>
              <pre class="server-log">{{ filteredServerLogText }}</pre>
            </div>
          </el-card>

          <el-card class="console-card maintenance-card">
            <template #header>
              <div class="card-header-row">
                <div>
                  <strong>维护</strong>
                  <span>手动触发、失败重试和数据清理</span>
                </div>
              </div>
            </template>
            <div class="detail-summary-grid maintenance-summary-grid">
              <div><span>待处理任务</span><strong>{{ dashboard.console_overview?.pending_task_count || 0 }}</strong></div>
              <div><span>失败任务</span><strong>{{ dashboard.console_overview?.failed_task_count || 0 }}</strong></div>
              <div><span>等待重试</span><strong>{{ dashboard.console_overview?.waiting_retry_count || 0 }}</strong></div>
              <div><span>运行队列</span><strong>{{ dashboard.console_overview?.running_queue_count || 0 }}</strong></div>
            </div>
            <div class="maintenance-actions maintenance-pane">
              <el-button type="primary" plain @click="runAction('/tasks/process?force=true')">立即处理任务队列</el-button>
              <el-button :icon="Refresh" @click="runAction('/tasks/poll')">刷新下载状态</el-button>
              <el-button type="warning" @click="runAction('/tasks/retry-failed')">重试失败任务</el-button>
              <el-popconfirm title="会清空番剧、候选、任务、下载记录、本地资源记录和日志。确定？" @confirm="runAction('/system/clear-data')">
                <template #reference>
                  <el-button type="danger" plain>清除所有数据</el-button>
                </template>
              </el-popconfirm>
            </div>
          </el-card>
        </div>
      </section>

</template>

