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
          <span>待处理</span>
          <strong>{{ dashboard.console_overview?.pending_task_count || 0 }}</strong>
        </div>

        <el-card v-if="scanOperation" class="span-4 console-card">
          <div class="scan-progress">
            <div>
              <strong>{{ scanOperation.name }}</strong>
              <span>{{ scanOperation.message || '正在执行' }}</span>
            </div>
            <el-progress :percentage="scanProgress" :status="scanOperation.status === 'failed' ? 'exception' : undefined" />
          </div>
        </el-card>

        <el-card class="span-4 console-card console-workbench-card">
          <div class="console-workbench">
            <aside class="console-nav">
              <div class="console-nav-tabs">
                <button :class="{ active: consoleNavMode === '队列' }" @click="consoleNavMode = '队列'">队列</button>
                <button :class="{ active: consoleNavMode === '定时任务' }" @click="consoleNavMode = '定时任务'">定时任务</button>
              </div>
              <div v-if="consoleNavMode === '队列'" class="console-nav-list">
                <button
                  v-for="section in queueListSections"
                  :key="section.key"
                  class="console-nav-item"
                  :class="{ active: selectedConsoleSection === section.key }"
                  @click="selectedConsoleSection = section.key"
                >
                  <span>{{ section.name }}</span>
                  <el-tag
                    v-if="section.kind === 'queue' && queueMap[section.queue_key]"
                    size="small"
                    :type="queueTag(queueMap[section.queue_key])"
                  >
                    {{ queueBadge(queueMap[section.queue_key]) }}
                  </el-tag>
                  <el-tag v-else-if="section.kind === 'scheduled'" size="small" :type="scheduledBadgeType(section.job_key)">
                    {{ scheduledBadgeText(section.job_key) }}
                  </el-tag>
                </button>
                <div v-if="!queueListSections.length" class="console-nav-empty">暂无队列</div>
              </div>
              <div v-else class="console-nav-list">
                <button
                  v-for="section in scheduledConsoleSections"
                  :key="section.key"
                  class="console-nav-item"
                  :class="{ active: selectedConsoleSection === section.key }"
                  @click="selectedConsoleSection = section.key"
                >
                  <span>{{ section.name }}</span>
                  <el-tag size="small" :type="scheduledBadgeType(section.job_key)">
                    {{ scheduledBadgeText(section.job_key) }}
                  </el-tag>
                </button>
                <div v-if="!scheduledConsoleSections.length" class="console-nav-empty">暂无定时任务</div>
              </div>
            </aside>

            <section class="console-detail">
              <template v-if="selectedQueue">
                <div class="detail-header">
                  <div>
                    <h3>{{ selectedQueue.name }}</h3>
                    <p>{{ selectedQueue.description }}</p>
                  </div>
                  <div class="detail-tags">
                    <el-tag :type="queueTag(selectedQueue)">{{ queueState(selectedQueue) }}</el-tag>
                    <el-tag v-if="selectedQueue.waiting" type="warning">重试 {{ selectedQueue.waiting }}</el-tag>
                    <el-button v-if="selectedQueue.key === 'download'" size="small" plain @click="openProcessorSettings">设置</el-button>
                    <el-popconfirm v-if="selectedQueue.key === 'download'" title="取消全部下载任务？" @confirm="cancelAllDownloads">
                      <template #reference>
                        <el-button size="small" type="danger" plain>取消全部</el-button>
                      </template>
                    </el-popconfirm>
                    <el-button v-if="selectedQueueAction" size="small" plain @click="runAction(selectedQueueAction)">立即执行该队列</el-button>
                  </div>
                </div>
                <div class="detail-summary-grid">
                  <div><span>当前状态</span><strong>{{ selectedQueue.state_reason || '-' }}</strong></div>
                  <div><span>待处理</span><strong>{{ selectedQueue.pending || 0 }}</strong></div>
                  <div><span>运行中</span><strong>{{ selectedQueue.running || 0 }}</strong></div>
                  <div><span>失败</span><strong>{{ selectedQueue.failed || 0 }}</strong></div>
                </div>
                <div class="detail-summary-grid queue-monitor-grid">
                  <div><span>聚合中</span><strong>{{ selectedQueue.queue_state === 'debouncing' ? '是' : '否' }}</strong></div>
                  <div><span>等待重试</span><strong>{{ selectedQueue.waiting || 0 }}</strong></div>
                  <div><span>状态细节</span><strong>{{ selectedQueue.state_detail || '-' }}</strong></div>
                  <div><span>运行队列</span><strong>{{ selectedQueue.runtime_queue_key || selectedQueue.key || '-' }}</strong></div>
                </div>
                <p class="queue-note queue-detail-note">{{ selectedQueue.state_reason || queuePendingHint(selectedQueue) }}</p>
                <el-table :data="selectedQueueItems" height="520" class="candidate-table queue-task-table" empty-text="当前队列没有任务明细">
                  <el-table-column type="expand" width="44">
                    <template #default="{ row }">
                      <div class="queue-task-expand">
                        <section>
                          <strong>任务信息</strong>
                          <div><span>处理器</span><code>{{ row.processor_key || '-' }}</code></div>
                          <div><span>步骤</span><code>{{ row.step_key || '-' }}</code></div>
                          <div><span>对象类型</span><code>{{ row.subject_type || '-' }}</code></div>
                          <div><span>更新时间</span><code>{{ row.updated_at || '-' }}</code></div>
                        </section>
                        <section>
                          <strong>执行状态</strong>
                          <div><span>尝试</span><code>{{ row.attempts || 0 }}</code></div>
                          <div><span>等待</span><code>{{ row.waiting_retry ? formatCountdown(row.retry_seconds) : '-' }}</code></div>
                          <div><span>进度</span><code>{{ queueTaskProgressText(row) }}</code></div>
                          <div><span>错误</span><code>{{ row.last_error || '-' }}</code></div>
                        </section>
                      </div>
                    </template>
                  </el-table-column>
                  <el-table-column prop="status" label="状态" width="110">
                    <template #default="{ row }"><el-tag :type="taskTag(row.status)">{{ taskStatusText(row) }}</el-tag></template>
                  </el-table-column>
                  <el-table-column label="域" width="90">
                    <template #default="{ row }">
                      <el-tag size="small" :type="row.domain_kind === 'library' ? 'warning' : 'success'">
                        {{ row.domain_kind === 'library' ? '番剧库' : '新番' }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column prop="display_title" label="对象" min-width="300" show-overflow-tooltip />
                  <el-table-column prop="episode_number" label="集" width="70" />
                  <el-table-column label="说明" min-width="240" show-overflow-tooltip>
                    <template #default="{ row }">
                      {{ row.display_reason || row.progress_text || row.message || '-' }}
                    </template>
                  </el-table-column>
                  <el-table-column label="等待" width="120">
                    <template #default="{ row }">{{ row.waiting_retry ? formatCountdown(row.retry_seconds) : '-' }}</template>
                  </el-table-column>
                  <el-table-column label="操作" width="150">
                    <template #default="{ row }">
                      <el-button v-if="row.entry_id" size="small" plain @click="openQueueEntry(row)">打开</el-button>
                      <el-popconfirm v-if="queueTaskCanCancel(row)" title="取消该集下载任务？" @confirm="cancelQueueDownload(row)">
                        <template #reference>
                          <el-button size="small" type="danger" plain>取消</el-button>
                        </template>
                      </el-popconfirm>
                    </template>
                  </el-table-column>
                </el-table>
              </template>

              <template v-else-if="selectedScheduledJob">
                <div class="detail-header">
                  <div>
                    <h3>{{ selectedScheduledJob.job_key }}</h3>
                    <p>定时任务与调度状态</p>
                  </div>
                  <div class="detail-tags">
                    <el-tag :type="taskTag(selectedScheduledJob.last_status || 'idle')">{{ selectedScheduledJob.last_status || 'idle' }}</el-tag>
                    <el-button size="small" plain @click="openScheduledSettings">设置</el-button>
                  </div>
                </div>
                <div class="detail-summary-grid">
                  <div><span>间隔</span><strong>{{ selectedScheduledJob.interval_minutes || 0 }} 分钟</strong></div>
                  <div><span>启用</span><strong>{{ Number(selectedScheduledJob.enabled ?? 1) ? '是' : '否' }}</strong></div>
                  <div><span>防抖</span><strong>{{ selectedScheduledJob.debounce_seconds || 0 }} 秒</strong></div>
                  <div><span>最近状态</span><strong>{{ selectedScheduledJob.last_status || '-' }}</strong></div>
                  <div><span>最近执行</span><strong>{{ selectedScheduledJob.latest_run?.started_at || '-' }}</strong></div>
                </div>
                <el-table :data="selectedScheduledRuns" height="520" class="candidate-table">
                  <el-table-column prop="status" label="状态" width="110">
                    <template #default="{ row }"><el-tag :type="taskTag(row.status)">{{ row.status }}</el-tag></template>
                  </el-table-column>
                  <el-table-column prop="trigger_source" label="来源" width="120" />
                  <el-table-column prop="message" label="说明" min-width="320" show-overflow-tooltip />
                  <el-table-column prop="started_at" label="开始时间" width="220" />
                  <el-table-column prop="finished_at" label="结束时间" width="220" />
                </el-table>
              </template>

              <template v-else>
                <div class="console-empty-state">
                  <h3>选择一个队列查看详情</h3>
                  <p>左侧显示当前运行时队列和定时任务。默认不自动选中，避免空队列时右侧误判为卡住。</p>
                </div>
              </template>
            </section>
          </div>
        </el-card>
      </section>

</template>

