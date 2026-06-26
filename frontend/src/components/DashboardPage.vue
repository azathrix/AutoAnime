<script>
import { appContextComponent } from '../composables/appContext'

export default appContextComponent()
</script>

<template>
  <section v-if="view === 'dashboard'" class="mochi-dashboard">
    <div class="dashboard-command-grid">
      <article class="command-card scanner-command">
        <span>扫描器</span>
        <strong>{{ scannerStatusText }}</strong>
        <p>RSS 扫描、同步 Bangumi ID、写入集数资源。</p>
        <div class="command-card-footer">
          <el-tag :type="dashboard.scanner_status?.status === 'failed' ? 'danger' : (dashboard.scanner_status?.status === 'running' ? 'warning' : 'success')">
            {{ dashboard.scanner_status?.status || 'idle' }}
          </el-tag>
          <el-button type="primary" :disabled="scanRunning" @click="runAction('/scanner/run')">扫描 RSS</el-button>
        </div>
      </article>
      <article class="command-card">
        <span>下载任务</span>
        <strong>{{ dashboard.download_overview?.active || 0 }}</strong>
        <p>{{ dashboard.download_overview?.failed ? `${dashboard.download_overview.failed} 个失败任务待处理` : '下载器与本地整理运行状态' }}</p>
        <div class="command-card-footer">
          <el-button plain @click="openProcessorSettings">并发设置</el-button>
          <el-button plain @click="clearCompletedDownloadTasks">清除已完成</el-button>
        </div>
      </article>
      <article class="command-card">
        <span>本地媒体</span>
        <strong>{{ watchableTotal }}</strong>
        <p>已可观看条目和本地资源状态。</p>
        <div class="command-card-footer">
          <el-tag type="success">本地 {{ localAssetTotal }}</el-tag>
          <el-button plain @click="view = 'library'">打开媒体库</el-button>
        </div>
      </article>
      <article class="command-card danger-lite">
        <span>告警</span>
        <strong>{{ dashboard.download_overview?.failed || logsData.console_overview?.recent_error_count || 0 }}</strong>
        <p>下载失败、最近错误或需要人工处理的任务。</p>
        <div class="command-card-footer">
          <el-button plain @click="view = 'logs'">查看日志</el-button>
          <el-popconfirm title="取消全部下载任务？" @confirm="cancelAllDownloads">
            <template #reference>
              <el-button type="danger" plain>取消全部</el-button>
            </template>
          </el-popconfirm>
        </div>
      </article>
    </div>

    <section class="dashboard-main-grid">
      <div class="mochi-panel download-workshop">
        <header class="mochi-panel-head">
          <div>
            <strong>下载工坊</strong>
            <span>云存储、下载中、整理完成会在这里推进</span>
          </div>
          <el-tag type="warning">活动 {{ dashboard.download_overview?.active || 0 }}</el-tag>
        </header>
        <div class="download-task-cards">
          <article v-for="row in filteredConsoleTasks.filter(item => item.source === 'download').slice(0, 8)" :key="row.id" class="download-task-card" :class="row.status">
            <div class="task-card-main">
              <el-tag size="small" :type="taskTag(row.status)">{{ row.status_text || taskStatusText(row) }}</el-tag>
              <strong>{{ row.title || row.type_name || '下载任务' }}</strong>
              <span>{{ row.progress_text || row.message || row.updated_at || '-' }}</span>
            </div>
            <el-progress
              :percentage="Number(row.progress || 0)"
              :status="row.status === 'failed' ? 'exception' : (row.status === 'completed' ? 'success' : undefined)"
            />
            <div class="task-card-actions">
              <el-button v-if="row.entry_id" size="small" plain @click="openQueueEntry(row)">打开</el-button>
              <el-button
                v-if="['pending','submitting','remote_downloading','remote_completed','local_copying','running','submitted','downloading'].includes(row.status)"
                size="small"
                type="danger"
                plain
                @click="cancelDownloadTask({ id: row.raw_id })"
              >取消</el-button>
              <el-button v-if="['failed','cancelled'].includes(row.status)" size="small" type="primary" plain @click="retryDownloadTask({ id: row.raw_id })">重试</el-button>
            </div>
          </article>
          <el-empty v-if="!filteredConsoleTasks.filter(item => item.source === 'download').length" description="暂无下载任务" />
        </div>
      </div>

      <aside class="mochi-panel task-type-panel">
        <header class="mochi-panel-head">
          <div>
            <strong>任务类型</strong>
            <span>选择类型查看下方流水</span>
          </div>
        </header>
        <div class="task-type-pills">
          <button :class="{ active: !selectedTaskType }" @click="selectedTaskType = ''">
            <span>全部</span><b>{{ (dashboard.tasks || []).length }}</b>
          </button>
          <button
            v-for="item in taskTypeRows"
            :key="item.type"
            :class="{ active: selectedTaskType === item.type }"
            @click="selectedTaskType = item.type"
          >
            <span>{{ item.name }}</span>
            <b>{{ item.running || item.pending || item.failed || item.total || 0 }}</b>
          </button>
        </div>
      </aside>
    </section>

    <section class="mochi-panel task-timeline-panel">
      <header class="mochi-panel-head">
        <div>
          <strong>任务流水</strong>
          <span>最近扫描、元数据、下载、本地状态和缓存任务</span>
        </div>
      </header>
      <div class="task-timeline">
        <article v-for="row in filteredConsoleTasks.slice(0, 12)" :key="row.id" class="task-timeline-item" :class="row.status">
          <i></i>
          <div>
            <strong>{{ row.title || row.type_name || row.type }}</strong>
            <span>{{ row.type_name || row.type }} · {{ row.updated_at || '-' }}</span>
          </div>
          <el-progress :percentage="Number(row.progress || 0)" />
          <div class="task-timeline-actions">
            <el-button v-if="row.entry_id" size="small" plain @click="openQueueEntry(row)">打开</el-button>
            <el-button v-if="row.source !== 'operation' && ['pending','running','waiting'].includes(row.status)" size="small" plain @click="pauseGenericTask(row)">暂停</el-button>
            <el-button v-if="row.source !== 'operation' && row.status === 'paused'" size="small" type="primary" plain @click="resumeGenericTask(row)">继续</el-button>
            <el-button v-if="row.source === 'download' && ['failed','cancelled'].includes(row.status)" size="small" type="primary" plain @click="retryDownloadTask({ id: row.raw_id })">重试</el-button>
          </div>
        </article>
        <el-empty v-if="!filteredConsoleTasks.length" description="暂无任务" />
      </div>
    </section>
  </section>
</template>
