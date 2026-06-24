<script>
import { appContextComponent } from '../composables/appContext'

export default appContextComponent()
</script>

<template>
  <section v-if="view === 'dashboard'" class="content-grid">
    <div class="metric-card">
      <span>新番条目</span>
      <strong>{{ dashboard.seasonal_items.length }}</strong>
    </div>
    <div class="metric-card">
      <span>可观看</span>
      <strong>{{ watchableTotal }}</strong>
    </div>
    <div class="metric-card">
      <span>本地资源</span>
      <strong>{{ localAssetTotal }}</strong>
    </div>
    <div class="metric-card">
      <span>下载任务</span>
      <strong>{{ dashboard.download_overview?.active || 0 }}</strong>
    </div>

    <el-card class="span-4 console-card scanner-card">
      <template #header>
        <div class="card-header-row">
          <div>
            <strong>扫描器</strong>
            <span>RSS 扫描、同步 Bangumi ID、写入集数资源并创建下载任务</span>
          </div>
          <div class="detail-tags">
            <el-tag :type="dashboard.scanner_status?.status === 'failed' ? 'danger' : (dashboard.scanner_status?.status === 'running' ? 'warning' : 'success')">
              {{ dashboard.scanner_status?.message || '空闲' }}
            </el-tag>
            <el-button type="primary" plain @click="runAction('/scanner/run')">扫描 RSS</el-button>
          </div>
        </div>
      </template>
      <div class="detail-summary-grid">
        <div><span>当前状态</span><strong>{{ dashboard.scanner_status?.status || 'idle' }}</strong></div>
        <div><span>最近操作</span><strong>{{ dashboard.scanner_status?.operation_id || '-' }}</strong></div>
        <div><span>更新时间</span><strong>{{ dashboard.scanner_status?.updated_at || '-' }}</strong></div>
        <div><span>运行中</span><strong>{{ dashboard.console_overview?.running_operation_count || 0 }}</strong></div>
      </div>
    </el-card>

    <el-card class="span-4 console-card task-console-card">
      <template #header>
        <div class="card-header-row">
          <div>
            <strong>任务</strong>
            <span>扫描、元数据、下载、本地状态和缓存清理</span>
          </div>
          <div class="detail-tags">
            <el-tag type="warning">下载中 {{ dashboard.download_overview?.active || 0 }}</el-tag>
            <el-tag v-if="dashboard.download_overview?.failed" type="danger">失败 {{ dashboard.download_overview.failed }}</el-tag>
            <el-button size="small" plain @click="openProcessorSettings">设置</el-button>
            <el-button size="small" type="primary" plain @click="clearCompletedDownloadTasks">清除已完成</el-button>
            <el-popconfirm title="取消全部下载任务？" @confirm="cancelAllDownloads">
              <template #reference>
                <el-button size="small" type="danger" plain>取消全部</el-button>
              </template>
            </el-popconfirm>
          </div>
        </div>
      </template>
      <div class="task-console-layout">
        <aside class="task-type-list">
          <button :class="{ active: !selectedTaskType }" @click="selectedTaskType = ''">
            <span>全部任务</span>
            <el-tag size="small">{{ (dashboard.tasks || []).length }}</el-tag>
          </button>
          <button
            v-for="item in taskTypeRows"
            :key="item.type"
            :class="{ active: selectedTaskType === item.type }"
            @click="selectedTaskType = item.type"
          >
            <span>{{ item.name }}</span>
            <el-tag size="small" :type="item.failed ? 'danger' : (item.running ? 'warning' : (item.pending ? 'info' : 'success'))">
              {{ item.running ? `${item.running} 运行` : (item.pending ? `${item.pending} 待处理` : (item.failed ? `${item.failed} 失败` : item.total)) }}
            </el-tag>
          </button>
        </aside>
        <el-table
          :data="filteredConsoleTasks"
          row-key="id"
          height="560"
          class="candidate-table task-table"
          empty-text="暂无任务"
        >
          <el-table-column label="状态" width="110">
            <template #default="{ row }"><el-tag :type="taskTag(row.status)">{{ row.status_text || taskStatusText(row) }}</el-tag></template>
          </el-table-column>
          <el-table-column prop="type_name" label="类型" width="120" />
          <el-table-column prop="title" label="任务" min-width="220" show-overflow-tooltip />
          <el-table-column label="进度" min-width="220" show-overflow-tooltip>
            <template #default="{ row }">
              <el-progress
                v-if="Number(row.progress || 0) > 0"
                :percentage="Number(row.progress || 0)"
                :status="row.status === 'failed' ? 'exception' : (row.status === 'completed' ? 'success' : undefined)"
              />
              <span v-else>{{ row.message || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="更新时间" width="190" show-overflow-tooltip>
            <template #default="{ row }">{{ row.updated_at || '-' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="240" fixed="right">
            <template #default="{ row }">
              <el-button v-if="row.entry_id" size="small" plain @click="openQueueEntry(row)">打开</el-button>
              <el-button v-if="row.source !== 'operation' && ['pending','running','waiting'].includes(row.status)" size="small" type="danger" plain @click="cancelGenericTask(row)">取消</el-button>
              <el-button v-if="row.source !== 'operation' && ['pending','running','waiting'].includes(row.status)" size="small" plain @click="pauseGenericTask(row)">暂停</el-button>
              <el-button v-if="row.source !== 'operation' && row.status === 'paused'" size="small" type="primary" plain @click="resumeGenericTask(row)">继续</el-button>
              <el-button v-if="row.source === 'download' && ['failed','cancelled'].includes(row.status)" size="small" type="primary" plain @click="retryDownloadTask({ id: row.raw_id })">重试</el-button>
              <el-popconfirm v-if="['completed','failed','cancelled','skipped'].includes(row.status)" title="清理这条任务记录？" @confirm="clearGenericTask(row)">
                <template #reference>
                  <el-button size="small" plain>清理</el-button>
                </template>
              </el-popconfirm>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>
  </section>
</template>
