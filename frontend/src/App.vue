<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">A</div>
        <div>
          <strong>AutoAnime</strong>
          <span>Media automation</span>
        </div>
      </div>
      <nav>
        <button :class="{ active: view === 'dashboard' }" @click="view = 'dashboard'"><el-icon><DataBoard /></el-icon> 控制台</button>
        <button :class="{ active: view === 'library' }" @click="view = 'library'"><el-icon><Collection /></el-icon> 番剧库</button>
        <button :class="{ active: view === 'settings' }" @click="view = 'settings'"><el-icon><Setting /></el-icon> 设置</button>
      </nav>
    </aside>

    <main class="main">
      <header class="hero">
        <div>
          <p class="eyebrow">Mikan · PikPak · Local</p>
          <h1>{{ pageTitle }}</h1>
          <p class="hero-sub">RSS 扫描负责写入源头任务，后续处理由各队列自动推进。<span class="build-version">v{{ appVersion }} · {{ appBuild }}</span></p>
        </div>
        <div class="hero-actions">
          <el-switch
            v-model="autoRefresh"
            inline-prompt
            active-text="自动"
            inactive-text="手动"
          />
          <el-select v-model="refreshInterval" class="refresh-select" :disabled="!autoRefresh">
            <el-option label="3 秒" :value="3000" />
            <el-option label="5 秒" :value="5000" />
            <el-option label="10 秒" :value="10000" />
            <el-option label="30 秒" :value="30000" />
          </el-select>
          <el-button :icon="Refresh" @click="reload" :loading="loading">刷新状态</el-button>
          <el-button v-if="view === 'dashboard'" type="primary" :icon="Search" :disabled="scanRunning" @click="runAction('/scan')">扫描全部</el-button>
        </div>
      </header>

      <section v-if="view === 'dashboard'" class="content-grid">
        <div class="metric-card">
          <span>新番条目</span>
          <strong>{{ dashboard.seasonal_items.length }}</strong>
        </div>
        <div class="metric-card">
          <span>云盘资源</span>
          <strong>{{ cloudAssetTotal }}</strong>
        </div>
        <div class="metric-card">
          <span>本地资源</span>
          <strong>{{ localAssetTotal }}</strong>
        </div>
        <div class="metric-card">
          <span>待处理</span>
          <strong>{{ dashboard.console_overview?.pending_task_count || 0 }}</strong>
        </div>

        <el-card class="span-4 console-card">
          <template #header>最近 7 天已同步新番</template>
          <el-table :data="dashboard.seasonal_sync_calendar || []" height="240" class="candidate-table">
            <el-table-column prop="work_display_title" label="作品" min-width="180" show-overflow-tooltip />
            <el-table-column prop="entry_display_title" label="条目" min-width="220" show-overflow-tooltip />
            <el-table-column prop="episode_number" label="集" width="70" />
            <el-table-column prop="synced_at" label="同步时间" width="220" />
            <el-table-column prop="local_path" label="本地路径" min-width="320" show-overflow-tooltip />
          </el-table>
        </el-card>

        <el-card class="span-4 console-card">
          <template #header>本季条目</template>
          <div class="anime-grid">
            <article v-for="item in filteredSeries" :key="item.id" class="anime-card" @click="openEntry(item.id, 'seasonal')">
              <div class="cover">
                <img v-if="item.poster_url" :src="item.poster_url" />
                <span v-else>{{ item.display_title?.slice(0, 2) || item.title_cn?.slice(0, 2) || 'AN' }}</span>
              </div>
              <div class="anime-body">
                <h3>{{ item.entry_display_title || item.display_title || item.title_cn }}</h3>
                <p>{{ item.entry_secondary_title || item.work_display_title || item.bangumi_id || '未关联' }}</p>
                <div class="tagline">
                  <el-tag size="small" type="info">{{ item.release_count }} 发布</el-tag>
                  <el-tag size="small" type="warning">云盘 {{ item.cloud_asset_count || 0 }}</el-tag>
                  <el-tag size="small" type="success">本地 {{ item.local_asset_count || 0 }}</el-tag>
                  <el-tag size="small">{{ item.entry_badge_text || item.entry_kind || 'season' }}</el-tag>
                </div>
                <p v-if="seasonalStatusSummary(item)" class="queue-note">{{ seasonalStatusSummary(item) }}</p>
                <el-progress :percentage="progressOf(item)" :show-text="false" />
              </div>
            </article>
          </div>
        </el-card>

        <el-card class="span-4 console-card">
          <template #header>系统概览</template>
          <div v-if="scanOperation" class="scan-progress">
            <div>
              <strong>{{ scanOperation.name }}</strong>
              <span>{{ scanOperation.message || '正在执行' }}</span>
            </div>
            <el-progress :percentage="scanProgress" :status="scanOperation.status === 'failed' ? 'exception' : undefined" />
          </div>
          <div class="detail-summary-grid console-overview-grid">
            <div><span>队列总数</span><strong>{{ dashboard.console_overview?.queue_total || 0 }}</strong></div>
            <div><span>运行队列</span><strong>{{ dashboard.console_overview?.running_queue_count || 0 }}</strong></div>
            <div><span>待处理任务</span><strong>{{ dashboard.console_overview?.pending_task_count || 0 }}</strong></div>
            <div><span>失败任务</span><strong>{{ dashboard.console_overview?.failed_task_count || 0 }}</strong></div>
            <div><span>等待重试</span><strong>{{ dashboard.console_overview?.waiting_retry_count || 0 }}</strong></div>
            <div><span>运行操作</span><strong>{{ dashboard.console_overview?.running_operation_count || 0 }}</strong></div>
            <div><span>最近警告</span><strong>{{ dashboard.console_overview?.recent_warn_count || 0 }}</strong></div>
            <div><span>最近错误</span><strong>{{ dashboard.console_overview?.recent_error_count || 0 }}</strong></div>
          </div>
          <p class="muted" v-if="(dashboard.console_overview?.active_queue_names || []).length">
            活跃队列：{{ dashboard.console_overview.active_queue_names.join(' / ') }}
          </p>
          <div class="queue-grid">
            <div v-for="queue in dashboard.queue_summary" :key="queue.key" class="queue-card">
              <div class="queue-title">
                <strong>{{ queue.name }}</strong>
                <el-tag size="small" :type="queueTag(queue)">
                  {{ queueState(queue) }}
                </el-tag>
              </div>
              <p>{{ queue.description }}</p>
              <p class="queue-note">{{ queue.state_reason || queuePendingHint(queue) }}</p>
              <p v-if="queue.state_detail" class="queue-note queue-detail-note">{{ queue.state_detail }}</p>
              <div class="queue-counts">
                <span>待处理 <b>{{ queue.pending }}</b></span>
                <span>运行中 <b>{{ queue.running }}</b></span>
                <span>失败 <b>{{ queue.failed }}</b></span>
              </div>
            </div>
          </div>
        </el-card>

        <el-card class="span-4 console-card console-workbench-card">
          <div class="console-workbench">
            <aside class="console-nav">
              <div v-for="section in dashboard.console_sections || []" :key="section.key">
                <div v-if="section.kind === 'group'" class="console-nav-group">{{ section.name }}</div>
                <button
                  v-else
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
                  <el-tag v-else-if="section.kind === 'operations'" size="small" :type="operationsBadgeType">
                    {{ operationsBadgeText }}
                  </el-tag>
                  <el-tag v-else-if="section.kind === 'logs'" size="small" :type="logsBadgeType">
                    {{ logsBadgeText }}
                  </el-tag>
                </button>
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
                    <el-segmented
                      v-if="selectedQueueDomainOptions.length > 1"
                      v-model="selectedQueueDomainFilter"
                      :options="selectedQueueDomainOptions"
                      size="small"
                    />
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
                <el-table :data="selectedQueueItems" height="520" class="candidate-table" empty-text="当前队列没有任务明细">
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
                  <el-table-column prop="display_title" label="对象" min-width="240" show-overflow-tooltip />
                  <el-table-column prop="episode_number" label="集" width="70" />
                  <el-table-column prop="display_reason" label="说明" min-width="260" show-overflow-tooltip />
                  <el-table-column prop="attempts" label="尝试" width="80" />
                  <el-table-column prop="updated_at" label="更新时间" width="190" show-overflow-tooltip />
                  <el-table-column prop="last_error" label="错误" min-width="280" show-overflow-tooltip />
                  <el-table-column label="进度" width="140">
                    <template #default="{ row }">
                      <span v-if="Number(row.progress || 0) > 0 || row.progress_text">
                        {{ Number(row.progress || 0) > 0 ? `${row.progress}%` : '-' }}
                        <span v-if="row.progress_text"> · {{ row.progress_text }}</span>
                      </span>
                      <span v-else>-</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="等待" width="120">
                    <template #default="{ row }">{{ row.waiting_retry ? formatCountdown(row.retry_seconds) : '-' }}</template>
                  </el-table-column>
                  <el-table-column label="操作" width="96">
                    <template #default="{ row }">
                      <el-button v-if="row.entry_id" size="small" plain @click="openQueueEntry(row)">打开</el-button>
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
                  </div>
                </div>
                <div class="detail-summary-grid">
                  <div><span>间隔</span><strong>{{ selectedScheduledJob.interval_minutes || 0 }} 分钟</strong></div>
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

              <template v-else-if="selectedConsoleSection === 'operations'">
                <div class="detail-header">
                  <div>
                    <h3>运行操作</h3>
                    <p>手动触发任务和系统长操作</p>
                  </div>
                  <div class="detail-tags">
                    <el-tag :type="operationsBadgeType">{{ operationsBadgeText }}</el-tag>
                    <el-button v-if="dashboard.operations.length" plain @click="runAction('/operations/clear')">清空已结束操作</el-button>
                  </div>
                </div>
                <div class="detail-summary-grid">
                  <div><span>运行中</span><strong>{{ dashboard.console_overview?.running_operation_count || 0 }}</strong></div>
                  <div><span>失败</span><strong>{{ dashboard.console_overview?.failed_operation_count || 0 }}</strong></div>
                  <div><span>总数</span><strong>{{ dashboard.operations.length || 0 }}</strong></div>
                  <div><span>说明</span><strong>只保留运行中和失败项</strong></div>
                </div>
                <div class="operation-list operation-list-full">
                  <div v-if="!dashboard.operations.length" class="operation-item">
                    <el-tag type="success">idle</el-tag>
                    <div>
                      <strong>当前没有运行中的操作</strong>
                      <span>手动操作完成后会自动从这里移除。</span>
                    </div>
                  </div>
                  <div v-for="op in dashboard.operations" :key="op.id" class="operation-item">
                    <el-tag :type="taskTag(op.status)">{{ op.status }}</el-tag>
                    <div>
                      <strong>{{ op.name }}</strong>
                      <span>{{ op.message || '处理中' }}</span>
                    </div>
                  </div>
                </div>
              </template>

              <template v-else-if="selectedConsoleSection === 'logs'">
                <div class="detail-header">
                  <div>
                    <h3>服务日志</h3>
                    <p>直接读取服务端日志文件</p>
                  </div>
                  <div class="detail-tags">
                    <el-tag :type="logsBadgeType">{{ logsBadgeText }}</el-tag>
                  </div>
                </div>
                <div class="detail-summary-grid">
                  <div><span>最近错误</span><strong>{{ dashboard.console_overview?.recent_error_count || 0 }}</strong></div>
                  <div><span>最近警告</span><strong>{{ dashboard.console_overview?.recent_warn_count || 0 }}</strong></div>
                  <div><span>显示行数</span><strong>{{ filteredServerLogs.length || 0 }}</strong></div>
                  <div><span>筛选</span><strong>{{ logKeyword || '全部' }}</strong></div>
                </div>
                <div class="log-console">
                  <div class="log-toolbar">
                    <el-input v-model="logKeyword" clearable placeholder="搜索日志" />
                    <el-button plain @click="runAction('/logs/clear')">清空日志</el-button>
                  </div>
                  <pre class="server-log">{{ filteredServerLogText }}</pre>
                </div>
              </template>

              <template v-else-if="selectedConsoleSection === 'maintenance'">
                <div class="detail-header">
                  <div>
                    <h3>维护</h3>
                    <p>手动触发、失败重试和数据清理</p>
                  </div>
                </div>
                <div class="detail-summary-grid">
                  <div><span>待处理任务</span><strong>{{ dashboard.console_overview?.pending_task_count || 0 }}</strong></div>
                  <div><span>失败任务</span><strong>{{ dashboard.console_overview?.failed_task_count || 0 }}</strong></div>
                  <div><span>等待重试</span><strong>{{ dashboard.console_overview?.waiting_retry_count || 0 }}</strong></div>
                  <div><span>运行队列</span><strong>{{ dashboard.console_overview?.running_queue_count || 0 }}</strong></div>
                </div>
                <div class="maintenance-actions maintenance-pane">
                  <el-button type="primary" :icon="Search" :disabled="scanRunning" @click="runAction('/scan')">扫描全部</el-button>
                  <el-button type="primary" plain @click="runAction('/tasks/process?force=true')">立即处理云盘队列</el-button>
                  <el-button :icon="Refresh" @click="runAction('/tasks/poll')">刷新 PikPak 状态</el-button>
                  <el-button type="warning" @click="runAction('/tasks/retry-failed')">重试失败任务</el-button>
                  <el-popconfirm title="会清空番剧、候选、任务、云盘资源、本地同步记录和日志。确定？" @confirm="runAction('/system/clear-data')">
                    <template #reference>
                      <el-button type="danger" plain>清除所有数据</el-button>
                    </template>
                  </el-popconfirm>
                </div>
              </template>
            </section>
          </div>
        </el-card>
      </section>

      <section v-if="view === 'library'" class="library">
        <div class="toolbar">
          <el-input v-model="keyword" clearable placeholder="搜索番剧库条目、Bangumi ID、标题" />
          <el-segmented v-model="seriesFilter" :options="['全部', '待配置', '已入云盘', '已同步', '失败']" />
          <el-button plain @click="runAction('/library/import')">导入云盘到番剧库</el-button>
        </div>
        <div class="library-summary-grid">
          <div class="metric-card">
            <span>作品数</span>
            <strong>{{ dashboard.library_summary?.work_count || 0 }}</strong>
          </div>
          <div class="metric-card">
            <span>条目数</span>
            <strong>{{ dashboard.library_summary?.entry_count || 0 }}</strong>
          </div>
          <div class="metric-card">
            <span>待关联</span>
            <strong>{{ dashboard.library_summary?.unmatched_count || 0 }}</strong>
          </div>
          <div class="metric-card">
            <span>失败条目</span>
            <strong>{{ dashboard.library_summary?.failed_entry_count || 0 }}</strong>
          </div>
        </div>
        <div class="library-work-grid">
          <section v-for="work in libraryWorks" :key="work.work_id || work.work_title" class="library-work-card">
            <header class="library-work-header">
              <div>
                <h3>{{ work.work_title || '未命名作品' }}</h3>
                <p>{{ work.entry_count }} 个条目 · 云盘 {{ work.cloud_asset_count }} · 本地 {{ work.local_asset_count }}</p>
              </div>
            </header>
            <div class="library-entry-list">
              <article v-for="item in work.entries" :key="item.id" class="library-entry-row" @click="openEntry(item.id, 'library')">
                <div class="cover small">
                  <img v-if="item.poster_url" :src="item.poster_url" />
                  <span v-else>{{ item.display_title?.slice(0, 2) || item.title_cn?.slice(0, 2) || 'AN' }}</span>
                </div>
                <div class="anime-body">
                  <h3>{{ item.entry_display_title || item.display_title || item.title_cn }}</h3>
                  <p>Bangumi: {{ item.bangumi_id || '未关联' }}</p>
                  <div class="tagline">
                    <el-tag size="small" type="info">{{ item.release_count }} 发布</el-tag>
                    <el-tag size="small" type="warning">云盘 {{ item.cloud_asset_count || 0 }}</el-tag>
                    <el-tag size="small" type="success">本地 {{ item.local_asset_count || 0 }}</el-tag>
                    <el-tag size="small">{{ item.entry_badge_text || item.entry_kind || 'season' }}</el-tag>
                  </div>
                  <el-progress :percentage="libraryProgressOf(item)" :show-text="false" />
                </div>
              </article>
            </div>
          </section>
        </div>
      </section>

      <section v-if="view === 'settings'">
        <el-card>
          <template #header>全局设置</template>
          <el-form :model="settings" label-position="top" class="settings-form">
            <el-tabs>
              <el-tab-pane label="来源">
                <el-alert
                  type="info"
                  show-icon
                  :closable="false"
                  title="保存设置会自动重排选集、补全、同步等后续任务；要立即触发新一轮源头扫描，请回到控制台点击“扫描全部”。"
                  class="settings-alert"
                />
                <el-form-item label="Mikan RSS"><el-input v-model="settings.rss_url" /></el-form-item>
                <el-form-item label="RSS 代理"><el-input v-model="settings.rss_proxy" placeholder="http://NAS_IP:20171" /></el-form-item>
                <div class="form-row">
                  <el-form-item label="自动扫描"><el-switch v-model="settings.auto_scan" /></el-form-item>
                  <el-form-item label="扫描间隔"><el-input-number v-model="settings.scan_interval_minutes" :min="1" /></el-form-item>
                  <el-form-item label="默认补全">
                    <el-select v-model="settings.default_backfill">
                      <el-option label="不补全" value="none" />
                      <el-option label="补全本季" value="season" />
                      <el-option label="补全全部" value="all" />
                    </el-select>
                  </el-form-item>
                </div>
              </el-tab-pane>
              <el-tab-pane label="自动选择">
                <div class="form-row">
                  <el-form-item label="唯一匹配自动入云盘"><el-switch v-model="settings.auto_download_unique" /></el-form-item>
                  <el-form-item label="按优先级选择"><el-switch v-model="settings.auto_download_by_priority" /></el-form-item>
                </div>
                <div class="priority-layout">
                  <PriorityList title="字幕组优先级" v-model="settings.subtitle_priority" placeholder="添加字幕组" />
                  <PriorityList title="分辨率优先级" v-model="settings.resolution_priority" placeholder="添加分辨率" />
                  <PriorityList title="主字幕语言优先级" v-model="settings.language_priority" placeholder="添加主字幕语言" />
                  <PriorityList title="副字幕语言优先级" v-model="settings.secondary_language_priority" placeholder="添加副字幕语言" />
                </div>
              </el-tab-pane>
              <el-tab-pane label="PikPak">
                <el-form-item label="云盘执行方式">
                  <el-radio-group v-model="settings.cloud_transfer_backend">
                    <el-radio-button label="rclone">rclone 命令</el-radio-button>
                    <el-radio-button label="api">PikPak API</el-radio-button>
                  </el-radio-group>
                </el-form-item>
                <div class="form-row" v-if="settings.cloud_transfer_backend === 'rclone'">
                  <el-form-item label="rclone 命令"><el-input v-model="settings.rclone_command" placeholder="rclone" /></el-form-item>
                  <el-form-item label="rclone remote"><el-input v-model="settings.rclone_remote" placeholder="pikpak" /></el-form-item>
                </div>
                <el-form-item v-if="settings.cloud_transfer_backend === 'rclone'" label="rclone 配置文件"><el-input v-model="settings.rclone_config_path" placeholder="/data/rclone/rclone.conf" /></el-form-item>
                <el-form-item label="认证方式">
                  <el-radio-group v-model="settings.pikpak_auth_mode">
                    <el-radio-button label="token">Access + Refresh Token</el-radio-button>
                    <el-radio-button label="password">账号密码</el-radio-button>
                  </el-radio-group>
                </el-form-item>
                <div class="form-row">
                  <el-form-item label="Access Token"><el-input v-model="settings.pikpak_access_token" type="password" show-password /></el-form-item>
                  <el-form-item label="Refresh Token"><el-input v-model="settings.pikpak_refresh_token" type="password" show-password /></el-form-item>
                </div>
                <div class="form-row">
                  <el-form-item label="用户名"><el-input v-model="settings.pikpak_username" /></el-form-item>
                  <el-form-item label="密码"><el-input v-model="settings.pikpak_password" type="password" show-password /></el-form-item>
                </div>
                <el-form-item label="PikPak 代理"><el-input v-model="settings.pikpak_proxy" placeholder="通常留空" /></el-form-item>
              </el-tab-pane>
              <el-tab-pane label="媒体库">
                <el-alert
                  type="info"
                  show-icon
                  :closable="false"
                  title="建议外层目录只保留作品总名，季信息放在 Season 目录层，例如：作品名 (2026) [bangumi-123456] / Season 01。"
                  class="settings-alert"
                />
                <div class="form-row">
                  <el-form-item label="云盘库根目录"><el-input v-model="settings.library_root" /></el-form-item>
                <el-form-item label="本地同步目录"><el-input v-model="settings.local_library_root" placeholder="/media/pikpak-anime" /></el-form-item>
                </div>
                <el-form-item label="追更自动同步"><el-switch v-model="settings.auto_sync_following" /></el-form-item>
                <el-form-item label="NFO 输出目录"><el-input v-model="settings.nfo_output_root" placeholder="留空；同步后默认写入本地媒体库" /></el-form-item>
                <el-form-item label="番剧目录模板"><el-input v-model="settings.series_dir_template" /></el-form-item>
                <el-form-item label="季目录模板"><el-input v-model="settings.season_dir_template" /></el-form-item>
                <el-form-item label="单集名模板"><el-input v-model="settings.episode_name_template" /></el-form-item>
              </el-tab-pane>
              <el-tab-pane label="系统">
                <div class="diagnostics-grid">
                  <div><span>数据库</span><strong>{{ diagnostics.db_path || '-' }}</strong></div>
                  <div><span>数据目录可写</span><strong>{{ diagnostics.data_dir_writable ? '是' : '否' }}</strong></div>
                  <div><span>数据库大小</span><strong>{{ diagnostics.db_size || 0 }} bytes</strong></div>
                  <div><span>番剧 / 发布 / 云盘</span><strong>{{ diagnostics.tables?.series || 0 }} / {{ diagnostics.tables?.releases || 0 }} / {{ diagnostics.tables?.cloud_assets || 0 }}</strong></div>
                </div>
                <el-button :icon="Refresh" @click="reloadDiagnostics">刷新诊断</el-button>
              </el-tab-pane>
            </el-tabs>
            <div class="form-actions"><el-button type="primary" size="large" :loading="savingSettings" @click="saveAllSettings">保存设置</el-button></div>
          </el-form>
        </el-card>
      </section>
    </main>

    <el-drawer v-model="entryDrawerOpen" size="720px" :title="selectedEntryDetail?.entry?.title_cn || (selectedEntryDomain === 'library' ? '番剧库条目' : '番剧设置')">
      <template v-if="selectedEntryDetail?.entry">
        <el-alert
          type="info"
          show-icon
          :closable="false"
          :title="selectedEntryDomain === 'library' ? '这里处理番剧库条目本身；后续会补独立的补番/导入能力。' : '这里只处理规则和冲突；云盘入库与本地同步由后台任务自动推进。'"
          class="settings-alert"
        />
        <el-form :model="selectedEntry" label-position="top">
          <div class="form-row">
            <el-form-item label="中文标题"><el-input v-model="selectedEntry.title_cn" /></el-form-item>
            <el-form-item label="年份"><el-input-number v-model="selectedEntry.year" /></el-form-item>
          </div>
          <div class="form-row">
            <el-form-item label="Bangumi ID"><el-input v-model="selectedEntry.bangumi_id" /></el-form-item>
          </div>
          <template v-if="selectedEntryDomain === 'seasonal'">
            <div class="form-row">
              <el-form-item label="字幕组">
                <el-select v-model="selectedEntry.selected_group" clearable>
                  <el-option v-for="g in selectedEntryDetail.groups" :key="g" :label="g" :value="g" />
                </el-select>
              </el-form-item>
              <el-form-item label="分辨率">
                <el-select v-model="selectedEntry.selected_resolution" clearable>
                  <el-option v-for="r in selectedEntryDetail.resolutions" :key="r" :label="r" :value="r" />
                </el-select>
              </el-form-item>
            </div>
            <div class="form-row">
              <el-form-item label="自动入云盘">
                <el-select v-model="selectedEntry.auto_download">
                  <el-option label="跟随全局" value="inherit" />
                  <el-option label="开启" value="on" />
                  <el-option label="关闭" value="off" />
                </el-select>
              </el-form-item>
              <el-form-item label="补全">
                <el-select v-model="selectedEntry.backfill_mode">
                  <el-option label="跟随全局" value="inherit" />
                  <el-option label="不补全" value="none" />
                  <el-option label="补全本季" value="season" />
                  <el-option label="补全全部" value="all" />
                </el-select>
              </el-form-item>
            </div>
          </template>
        </el-form>
        <div class="sync-panel" v-if="selectedEntryDomain === 'seasonal'">
          <div>
            <strong>本地同步</strong>
            <span>{{ syncSummary }}</span>
          </div>
          <el-switch :model-value="syncWanted" @change="toggleEntrySync" />
        </div>
        <div class="drawer-actions">
          <el-button type="primary" @click="saveCurrentEntry">保存</el-button>
          <el-button plain @click="runEntryAction('metadata')">刷新元数据</el-button>
          <el-button plain @click="runEntryAction('nfo')">生成 NFO</el-button>
          <el-button v-if="selectedEntryDomain === 'library'" plain @click="runEntryAction('backfill')">补全条目</el-button>
          <el-popconfirm title="只从列表隐藏这个误识别条目，保留关联记录。确定隐藏？" @confirm="deleteCurrentEntry">
            <template #reference>
              <el-button type="danger" plain>{{ selectedEntryDomain === 'library' ? '隐藏条目' : '隐藏误识别' }}</el-button>
            </template>
          </el-popconfirm>
        </div>
        <el-divider />
        <el-tabs>
          <el-tab-pane label="RSS 发布">
            <el-table :data="selectedEntryDetail.releases" height="320">
              <el-table-column prop="episode_number" label="集" width="70" />
              <el-table-column prop="subtitle_group" label="字幕组" width="140" />
              <el-table-column prop="resolution" label="分辨率" width="100" />
              <el-table-column prop="language" label="语言" width="90" />
              <el-table-column prop="guid" label="GUID" min-width="220" show-overflow-tooltip />
              <el-table-column prop="title" label="发布标题" min-width="260" show-overflow-tooltip />
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="云盘任务">
            <el-table :data="selectedEntryDetail.tasks" height="320">
              <el-table-column prop="status" label="状态" width="110">
                <template #default="{ row }"><el-tag :type="taskTag(row.status)">{{ taskStatusText(row) }}</el-tag></template>
              </el-table-column>
              <el-table-column prop="target_dir" label="目标目录" min-width="220" show-overflow-tooltip />
              <el-table-column prop="pikpak_task_id" label="PikPak 任务" min-width="180" show-overflow-tooltip />
              <el-table-column prop="pikpak_file_id" label="文件 ID" min-width="180" show-overflow-tooltip />
              <el-table-column prop="last_error" label="错误" min-width="220" show-overflow-tooltip />
              <el-table-column label="下次处理" width="130">
                <template #default="{ row }">{{ row.waiting_retry ? formatCountdown(row.retry_seconds) : '-' }}</template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="云盘资源">
            <el-table :data="selectedEntryDetail.cloud_assets" height="320">
              <el-table-column prop="episode_number" label="集" width="70" />
              <el-table-column prop="provider" label="云盘" width="100" />
              <el-table-column prop="cloud_path" label="云盘路径" min-width="260" show-overflow-tooltip />
              <el-table-column prop="provider_file_id" label="文件 ID" min-width="180" show-overflow-tooltip />
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="本地资源">
            <el-table :data="selectedEntryDetail.local_assets" height="320">
              <el-table-column prop="episode_number" label="集" width="70" />
              <el-table-column prop="status" label="状态" width="110" />
              <el-table-column prop="local_path" label="本地路径" min-width="260" show-overflow-tooltip />
              <el-table-column prop="nfo_status" label="NFO" width="110" />
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import draggable from 'vuedraggable'
import { ElMessage } from 'element-plus'
import { Collection, DataBoard, Refresh, Search, Setting } from '@element-plus/icons-vue'
import { deleteAction, getDashboard, getDiagnostics, getLibraryItem, getSeasonalItem, getSettings, postAction, saveLibraryItem, saveSeasonalItem, saveSettings } from './api'
import { APP_BUILD, APP_VERSION } from './version'

const view = ref('dashboard')
const appVersion = APP_VERSION
const appBuild = APP_BUILD
const selectedConsoleSection = ref('queue:mikan_match')
const logKeyword = ref('')
const loading = ref(false)
const savingSettings = ref(false)
const autoRefresh = ref(true)
const refreshInterval = ref(5000)
const selectedQueueDomainFilter = ref('全部')
let refreshTimer = null
const keyword = ref('')
const seriesFilter = ref('全部')
const entryDrawerOpen = ref(false)
const selectedEntryDetail = ref(null)
const selectedEntryDomain = ref('seasonal')
const dashboard = reactive({
  seasonal_items: [],
  library_items: [],
  library_summary: {},
  seasonal_sync_calendar: [],
  sync_rules: [],
  operations: [],
  scheduled_jobs: [],
  scheduled_runs: [],
  server_logs: [],
  queue_summary: [],
  queue_details: {},
  console_sections: [],
  console_overview: {},
})
const settings = reactive({})
const diagnostics = reactive({ tables: {} })

const pageTitle = computed(() => ({
  dashboard: '控制台',
  library: '番剧库',
  settings: '设置中心'
}[view.value]))

const seasonalRows = computed(() => dashboard.seasonal_items || [])
const libraryRows = computed(() => dashboard.library_items || [])
const activeDetailRows = computed(() => selectedEntryDomain.value === 'library' ? libraryRows.value : seasonalRows.value)
const cloudAssetTotal = computed(() => seasonalRows.value.reduce((sum, item) => sum + Number(item.cloud_asset_count || 0), 0))
const localAssetTotal = computed(() => seasonalRows.value.reduce((sum, item) => sum + Number(item.local_asset_count || 0), 0))
const scanOperation = computed(() => dashboard.operations.find(op => op.name === '扫描全部' && op.status === 'running'))
const queueMap = computed(() => Object.fromEntries((dashboard.queue_summary || []).map(item => [item.key, item])))
const selectedSectionMeta = computed(() => (dashboard.console_sections || []).find(item => item.key === selectedConsoleSection.value) || null)
const selectedQueue = computed(() => {
  const section = selectedSectionMeta.value
  if (!section || section.kind !== 'queue') return null
  return queueMap.value[section.queue_key] || null
})
const selectedQueueItems = computed(() => {
  const section = selectedSectionMeta.value
  if (!section || section.kind !== 'queue') return []
  const items = dashboard.queue_details?.[section.queue_key]?.items || []
  if (selectedQueueDomainFilter.value === '全部') return items
  if (selectedQueueDomainFilter.value === '新番') return items.filter(item => (item.domain_kind || 'seasonal') !== 'library')
  if (selectedQueueDomainFilter.value === '番剧库') return items.filter(item => item.domain_kind === 'library')
  return items
})
const selectedQueueDomainOptions = computed(() => {
  const section = selectedSectionMeta.value
  if (!section || section.kind !== 'queue') return ['全部']
  const items = dashboard.queue_details?.[section.queue_key]?.items || []
  const hasLibrary = items.some(item => item.domain_kind === 'library')
  const hasSeasonal = items.some(item => (item.domain_kind || 'seasonal') !== 'library')
  const options = ['全部']
  if (hasSeasonal) options.push('新番')
  if (hasLibrary) options.push('番剧库')
  return options
})
const selectedQueueAction = computed(() => {
  const queue = selectedQueue.value
  if (!queue) return ''
  return `/queues/${queue.key}/trigger`
})
const selectedScheduledJob = computed(() => {
  const section = selectedSectionMeta.value
  if (!section || section.kind !== 'scheduled') return null
  return (dashboard.scheduled_jobs || []).find(item => item.job_key === section.job_key) || null
})
const selectedScheduledRuns = computed(() => {
  const section = selectedSectionMeta.value
  if (!section || section.kind !== 'scheduled') return []
  return (dashboard.scheduled_runs || []).filter(item => item.job_key === section.job_key)
})
const scanRunning = computed(() => Boolean(scanOperation.value))
const scanProgress = computed(() => {
  const message = scanOperation.value?.message || ''
  const match = message.match(/(\d+)\/(\d+)/)
  if (!match) return scanRunning.value ? 12 : 0
  return Math.min(95, Math.round(Number(match[1]) / Number(match[2]) * 100))
})
const filteredServerLogs = computed(() => {
  const keyword = logKeyword.value.trim().toLowerCase()
  const rows = dashboard.server_logs || []
  if (!keyword) return rows
  return rows.filter(line => String(line || '').toLowerCase().includes(keyword))
})
const filteredServerLogText = computed(() => filteredServerLogs.value.join('\n'))
const operationsBadgeText = computed(() => {
  const running = Number(dashboard.console_overview?.running_operation_count || 0)
  const failed = Number(dashboard.console_overview?.failed_operation_count || 0)
  if (failed > 0) return `${failed} 失败`
  if (running > 0) return `${running} 运行`
  return '空闲'
})
const operationsBadgeType = computed(() => {
  const failed = Number(dashboard.console_overview?.failed_operation_count || 0)
  const running = Number(dashboard.console_overview?.running_operation_count || 0)
  if (failed > 0) return 'danger'
  if (running > 0) return 'warning'
  return 'success'
})
const logsBadgeText = computed(() => {
  const errors = Number(dashboard.console_overview?.recent_error_count || 0)
  const warns = Number(dashboard.console_overview?.recent_warn_count || 0)
  if (errors > 0) return `${errors} 错误`
  if (warns > 0) return `${warns} 警告`
  return '正常'
})
const logsBadgeType = computed(() => {
  const errors = Number(dashboard.console_overview?.recent_error_count || 0)
  const warns = Number(dashboard.console_overview?.recent_warn_count || 0)
  if (errors > 0) return 'danger'
  if (warns > 0) return 'warning'
  return 'success'
})
const selectedEntry = computed(() => selectedEntryDetail.value?.entry || null)
const selectedEntryStats = computed(() => {
  const id = selectedEntry.value?.id
  return activeDetailRows.value.find(item => item.id === id) || {}
})
const selectedSyncRule = computed(() => {
  const id = selectedEntry.value?.id
  return dashboard.sync_rules.find(item => item.entry_id === id) || {}
})
const syncWanted = computed(() => Boolean(selectedSyncRule.value.sync_enabled))
const syncSummary = computed(() => {
  const stats = selectedEntryStats.value
  if (Number(stats.local_asset_count || 0) > 0) return `已同步 ${stats.local_asset_count} 集到本地`
  if (syncWanted.value && Number(stats.cloud_asset_count || 0) > 0) return '已开启，正在等待或处理本地同步'
  if (syncWanted.value) return '已开启，云盘资源入库后会自动同步'
  return '关闭后只保留云盘资源，本地文件会被清理'
})

const filteredSeries = computed(() => {
  const text = keyword.value.toLowerCase()
  const source = view.value === 'library' ? libraryRows.value : seasonalRows.value
  return source.filter(item => {
    const matched = !text || `${item.entry_display_title || item.display_title || item.title_cn} ${item.work_display_title || item.work_title || item.title_root || ''} ${item.entry_scope_label || ''} ${item.bangumi_id}`.toLowerCase().includes(text)
    if (!matched) return false
    if (view.value === 'library') return true
    if (seriesFilter.value === '待配置') return !item.bangumi_id || !item.group_count || !item.resolution_count
    if (seriesFilter.value === '已入云盘') return Number(item.cloud_asset_count || 0) > 0
    if (seriesFilter.value === '已同步') return Number(item.local_asset_count || 0) > 0
    if (seriesFilter.value === '失败') return Boolean(item.has_failed_task)
    return true
  })
})

const libraryWorks = computed(() => {
  const groups = new Map()
  for (const item of filteredSeries.value) {
    const key = `${item.work_id || 0}:${item.work_display_title || item.work_title || item.title_root || item.display_title || item.title_cn || 'work'}`
    if (!groups.has(key)) {
      groups.set(key, {
        work_id: item.work_id || 0,
        work_title: item.work_display_title || item.work_title || item.title_root || item.display_title || item.title_cn || '未命名作品',
        entry_count: 0,
        cloud_asset_count: 0,
        local_asset_count: 0,
        entries: []
      })
    }
    const group = groups.get(key)
    group.entry_count += 1
    group.cloud_asset_count += Number(item.cloud_asset_count || 0)
    group.local_asset_count += Number(item.local_asset_count || 0)
    group.entries.push(item)
  }
  return Array.from(groups.values()).map(group => ({
    ...group,
    entries: group.entries.sort((a, b) => String(a.display_title || '').localeCompare(String(b.display_title || '')))
  }))
})

function taskTag(status) {
  if (status === 'failed') return 'danger'
  if (status === 'superseded') return 'info'
  if (status === 'completed' || status === 'submitted' || status === 'synced') return 'success'
  if (status === 'running') return 'warning'
  return 'info'
}

function queueTag(queue) {
  if (!queue) return 'info'
  if (Number(queue.failed || 0) > 0) return 'danger'
  if (queue.queue_state === 'running' || Number(queue.running || 0) > 0) return 'warning'
  if (queue.queue_state === 'debouncing' || queue.queue_state === 'cooldown' || Number(queue.waiting || 0) > 0) return 'info'
  if (Number(queue.pending || 0) > 0) return 'primary'
  return 'success'
}

function queueBadge(queue) {
  if (!queue) return '-'
  if (Number(queue.failed || 0) > 0) return `${queue.failed} 失败`
  if (Number(queue.running || 0) > 0) return `${queue.running} 运行`
  if (Number(queue.pending || 0) > 0) return `${queue.pending} 待处理`
  return '空闲'
}

function scheduledBadgeText(jobKey) {
  const job = (dashboard.scheduled_jobs || []).find(item => item.job_key === jobKey)
  if (!job) return '-'
  if (job.last_status === 'failed') return '失败'
  if (job.last_status === 'running') return '运行'
  const minutes = Number(job.interval_minutes || 0)
  return minutes > 0 ? `${minutes} 分` : '已配置'
}

function scheduledBadgeType(jobKey) {
  const job = (dashboard.scheduled_jobs || []).find(item => item.job_key === jobKey)
  if (!job) return 'info'
  if (job.last_status === 'failed') return 'danger'
  if (job.last_status === 'running') return 'warning'
  return 'success'
}

function taskStatusText(row) {
  if (row?.status === 'completed') return '已完成'
  if (row?.status === 'synced') return '已同步'
  if (row?.status === 'submitted') return '已提交'
  if (row?.status === 'running') return '处理中'
  if (row?.status === 'pending' && row?.waiting_retry) return '等待重试'
  if (row?.status === 'pending') return '待处理'
  if (row?.status === 'failed') return '失败'
  if (row?.status === 'superseded') return '已替代'
  return row?.status || ''
}

function formatCountdown(seconds) {
  const value = Math.max(0, Number(seconds || 0))
  if (!value) return '即将执行'
  const minutes = Math.floor(value / 60)
  const rest = value % 60
  if (minutes <= 0) return `${rest} 秒`
  return `${minutes} 分 ${rest} 秒`
}

function queueState(queue) {
  if (queue.queue_state === 'failed' || Number(queue.failed || 0) > 0) return '失败'
  if (queue.queue_state === 'running' || Number(queue.running || 0) > 0) return '运行中'
  if (queue.queue_state === 'debouncing') return '聚合中'
  if (queue.queue_state === 'rerun_pending') return '待重跑'
  if (queue.queue_state === 'cooldown' || Number(queue.waiting || 0) > 0) return '等待重试'
  if (queue.queue_state === 'ready' || Number(queue.pending || 0) > 0) return '待处理'
  return '空闲'
}

function queuePendingHint(queue) {
  const key = String(queue?.key || '')
  if (key === 'rss') return '这里只显示最近的 RSS 候选；后续 Mikan、元数据、选集、云盘和同步都由任务链自动推进。'
  if (key === 'cloud_assets') return '待处理表示已发现完成的云盘任务，等待登记成正式云盘资源。'
  if (key === 'sync') return '待处理表示云盘资源已就绪，等待进入本地同步。'
  if (key === 'selection') return '待处理表示元数据已完成，等待按规则自动选择发布。'
  if (key === 'backfill') return '待处理表示番剧已入库，等待去 Mikan 番组页补抓历史条目。'
  if (key === 'cloud') return '待处理表示已选中发布，等待提交到 PikPak。'
  if (key === 'metadata') return '待处理表示已拿到 Bangumi 线索，等待补全正式元数据。'
  if (key === 'mikan_match') return '待处理表示 RSS 候选已入队，等待解析对应的 Mikan/Bangumi 关联。'
  return '任务已入队，等待调度执行。'
}

function seasonalStatusSummary(item) {
  return String(item?.status_summary || '')
}

function progressOf(item) {
  const total = Number(item.episode_count || item.release_count || 1)
  return Math.min(100, Math.round(Number(item.downloaded_count || 0) / total * 100))
}

function libraryProgressOf(item) {
  const total = Number(item.release_count || item.episode_count || 1)
  return Math.min(100, Math.round(Number(item.local_asset_count || item.cloud_asset_count || 0) / total * 100))
}

async function reload() {
  if (loading.value) return
  loading.value = true
  try {
    Object.assign(dashboard, await getDashboard())
    Object.assign(settings, await getSettings())
    if (!(dashboard.console_sections || []).some(item => item.key === selectedConsoleSection.value)) {
      selectedConsoleSection.value = 'queue:mikan_match'
    }
    if (view.value === 'settings') await reloadDiagnostics()
  } finally {
    loading.value = false
  }
}

async function reloadDiagnostics() {
  Object.assign(diagnostics, await getDiagnostics())
}

function stopAutoRefresh() {
  if (refreshTimer) {
    window.clearInterval(refreshTimer)
    refreshTimer = null
  }
}

function startAutoRefresh() {
  stopAutoRefresh()
  if (!autoRefresh.value) return
  refreshTimer = window.setInterval(() => {
    if (view.value === 'settings' || entryDrawerOpen.value) return
    reload()
  }, refreshInterval.value)
}

async function runAction(path) {
  try {
    const result = await postAction(path)
    if (result.status === 'skipped') {
      ElMessage.warning(result.message || '没有可执行任务')
    } else if (result.status === 'running') {
      ElMessage.warning(result.message || '任务正在执行')
    } else {
      ElMessage.success(result.message || '操作已提交')
    }
    setTimeout(reload, 800)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

async function saveAllSettings() {
  savingSettings.value = true
  try {
    await saveSettings(settings)
    ElMessage.success('设置已保存')
    await reload()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  } finally {
    savingSettings.value = false
  }
}

function apiErrorMessage(error) {
  return error?.response?.data?.detail || error?.response?.data?.message || error?.message || '请求失败'
}

async function openEntry(id, domain = 'seasonal') {
  selectedEntryDomain.value = domain
  selectedEntryDetail.value = domain === 'library' ? await getLibraryItem(id) : await getSeasonalItem(id)
  entryDrawerOpen.value = true
}

async function openQueueEntry(row) {
  const entryId = Number(row?.entry_id || 0)
  if (!entryId) return
  const domain = row?.domain_kind === 'library' ? 'library' : 'seasonal'
  await openEntry(entryId, domain)
}

async function saveCurrentEntry() {
  const payload = selectedEntry.value
  if (!payload) return
  if (selectedEntryDomain.value === 'library') {
    await saveLibraryItem(payload.id, payload)
  } else {
    await saveSeasonalItem(payload.id, payload)
  }
  ElMessage.success(selectedEntryDomain.value === 'library' ? '番剧库条目已保存' : '番剧设置已保存')
  await reload()
}

async function toggleEntrySync(enabled) {
  const base = selectedEntryDomain.value === 'library' ? '/library' : '/seasonal'
  const action = enabled ? 'sync' : 'sync/cancel'
  const entryId = selectedEntry.value?.id
  if (!entryId) return
  const result = await postAction(`${base}/${entryId}/${action}`)
  if (result.status === 'skipped') {
    ElMessage.warning(result.message || '没有可执行任务')
  } else {
    ElMessage.success(result.message || '同步状态已更新')
  }
  await reload()
}

async function runEntryAction(action) {
  const base = selectedEntryDomain.value === 'library' ? '/library' : '/seasonal'
  try {
    const entryId = selectedEntry.value?.id
    if (!entryId) return
    const result = await postAction(`${base}/${entryId}/${action}`)
    ElMessage.success(result.message || (action === 'metadata' ? '元数据任务已启动' : 'NFO 已生成'))
    await reload()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

async function deleteCurrentEntry() {
  const id = selectedEntry.value?.id
  if (!id) return
  const base = selectedEntryDomain.value === 'library' ? '/library' : '/seasonal'
  const result = await deleteAction(`${base}/${id}`)
  if (result.status === 'not_found' || result.status === 'invalid_domain') {
    ElMessage.warning(result.message || '番剧不存在')
  } else {
    ElMessage.success(result.message || '已删除')
  }
  entryDrawerOpen.value = false
  selectedEntryDetail.value = null
  selectedEntryDomain.value = 'seasonal'
  await reload()
}

const PriorityList = {
  props: ['modelValue', 'title', 'placeholder'],
  emits: ['update:modelValue'],
  components: { draggable },
  data() {
    return { input: '' }
  },
  computed: {
    items: {
      get() { return this.modelValue || [] },
      set(value) { this.$emit('update:modelValue', value) }
    }
  },
  methods: {
    add() {
      const value = this.input.trim()
      if (!value) return
      this.items = [...this.items, value]
      this.input = ''
    },
    remove(index) {
      this.items = this.items.filter((_, i) => i !== index)
    }
  },
  template: `
    <div class="priority-card">
      <div class="priority-title">{{ title }}</div>
      <draggable v-model="items" item-key="name" handle=".drag-handle" class="priority-list">
        <template #item="{ element, index }">
          <div class="priority-item">
            <span class="drag-handle">⋮⋮</span>
            <span class="rank">{{ index + 1 }}</span>
            <span>{{ element }}</span>
            <button type="button" @click="remove(index)">×</button>
          </div>
        </template>
      </draggable>
      <div class="priority-add">
        <el-input v-model="input" :placeholder="placeholder" @keyup.enter="add" />
        <el-button @click="add">添加</el-button>
      </div>
    </div>
  `
}

watch([autoRefresh, refreshInterval], startAutoRefresh)
watch(entryDrawerOpen, startAutoRefresh)
watch(selectedConsoleSection, () => {
  selectedQueueDomainFilter.value = '全部'
})
watch(view, value => {
  if (value === 'settings') reloadDiagnostics().catch(error => ElMessage.error(apiErrorMessage(error)))
})

onMounted(async () => {
  await reload()
  startAutoRefresh()
})

onUnmounted(stopAutoRefresh)
</script>
