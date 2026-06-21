<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">A</div>
        <div>
          <strong class="brand-wordmark">Auto<span>Anime</span></strong>
          <span>Media automation</span>
        </div>
      </div>
      <nav>
        <div class="nav-caption">媒体</div>
        <button :class="{ active: view === 'dashboard' }" @click="view = 'dashboard'"><el-icon><DataBoard /></el-icon> 控制台</button>
        <button :class="{ active: view === 'seasonal' }" @click="view = 'seasonal'"><el-icon><Collection /></el-icon> 新番</button>
        <button :class="{ active: view === 'calendar' }" @click="view = 'calendar'"><el-icon><Calendar /></el-icon> 日历</button>
        <button :class="{ active: view === 'library' }" @click="view = 'library'"><el-icon><Collection /></el-icon> 番剧</button>
        <button :class="{ active: view === 'import' }" @click="view = 'import'"><el-icon><Search /></el-icon> 导入</button>
        <div class="nav-caption">系统</div>
        <button :class="{ active: view === 'settings' }" @click="view = 'settings'"><el-icon><Setting /></el-icon> 设置</button>
      </nav>
      <div class="sidebar-status">
        <span class="sidebar-avatar">A</span>
        <div>
          <strong>{{ liveConnected ? '动态连接' : '本地服务' }}</strong>
          <small>{{ dashboard.console_overview?.pending_task_count || 0 }} 个待处理任务</small>
        </div>
      </div>
    </aside>

    <main class="main">
      <header class="hero">
        <div>
          <p class="eyebrow">Mikan · Downloader · Local</p>
          <h1>{{ pageTitle }}</h1>
          <p class="hero-sub">RSS 扫描负责写入源头任务，后续处理由各队列自动推进。<span class="build-version">v{{ appVersion }} · {{ appBuild }}</span></p>
        </div>
        <div class="hero-actions">
          <el-switch
            v-model="autoRefresh"
            inline-prompt
            active-text="实时"
            inactive-text="手动"
          />
          <el-tag :type="liveConnected ? 'success' : 'info'">{{ liveConnected ? '动态连接' : '轮询刷新' }}</el-tag>
          <el-select v-model="refreshInterval" class="refresh-select" :disabled="!autoRefresh">
            <el-option label="3 秒" :value="3000" />
            <el-option label="5 秒" :value="5000" />
            <el-option label="10 秒" :value="10000" />
            <el-option label="30 秒" :value="30000" />
          </el-select>
          <el-button :icon="Refresh" @click="reload" :loading="loading">刷新状态</el-button>
          <el-button v-if="view === 'dashboard' || view === 'seasonal'" type="primary" :icon="Search" :disabled="scanRunning" @click="runAction('/scan')">扫描全部</el-button>
        </div>
      </header>

      <section v-if="view === 'dashboard'" class="content-grid">
        <div class="metric-card">
          <span>新番条目</span>
          <strong>{{ dashboard.seasonal_items.length }}</strong>
        </div>
        <div class="metric-card">
          <span>下载产物</span>
          <strong>{{ downloadArtifactTotal }}</strong>
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

        <el-card class="span-4 console-card episode-job-card">
          <template #header>
            <div class="card-header-row">
              <div>
                <strong>按集运行态</strong>
                <span>以每一集为单位汇总元数据、选集、下载、本地整理和 NFO</span>
              </div>
              <el-tag type="info">{{ episodeJobRows.length }} 条</el-tag>
            </div>
          </template>
          <el-table :data="episodeJobRows" height="300" empty-text="暂无集数任务">
            <el-table-column prop="status" label="状态" width="110">
              <template #default="{ row }"><el-tag :type="taskTag(row.status)">{{ taskStatusText(row) }}</el-tag></template>
            </el-table-column>
            <el-table-column prop="display_title" label="番剧" min-width="220" show-overflow-tooltip />
            <el-table-column prop="episode_number" label="集" width="70" />
            <el-table-column prop="stage_label" label="阶段" width="120" />
            <el-table-column label="规格" width="190" show-overflow-tooltip>
              <template #default="{ row }">
                {{ [row.subtitle_group, row.resolution, row.language, subtitleFormatText(row.subtitle_format)].filter(Boolean).join(' · ') || '-' }}
              </template>
            </el-table-column>
            <el-table-column prop="reason" label="说明" min-width="260" show-overflow-tooltip />
            <el-table-column label="操作" width="96">
              <template #default="{ row }">
                <el-button v-if="row.entry_id" size="small" plain @click="openQueueEntry(row)">打开</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card class="span-4 console-card console-workbench-card">
          <div class="console-workbench">
            <aside class="console-nav">
              <div class="console-nav-tabs">
                <button :class="{ active: consoleNavMode === '队列' }" @click="consoleNavMode = '队列'">队列</button>
                <button :class="{ active: consoleNavMode === '定时任务' }" @click="consoleNavMode = '定时任务'">定时任务</button>
              </div>
              <div v-if="consoleNavMode === '队列'" class="console-nav-toolbar">
                <el-segmented v-model="queueVisibilityMode" :options="['活跃', '全部']" size="small" />
              </div>
              <div v-if="consoleNavMode === '队列'" class="console-nav-list">
                <button
                  v-for="section in queueListSections"
                  :key="section.key"
                  class="console-nav-item"
                  :class="{ active: selectedConsoleSection === section.key }"
                  v-show="shouldShowConsoleSection(section)"
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
                  <el-tag v-else-if="section.kind === 'logs'" size="small" :type="logsBadgeType">
                    {{ logsBadgeText }}
                  </el-tag>
                </button>
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
                <p class="queue-note queue-detail-note">{{ selectedQueue.state_reason || queuePendingHint(selectedQueue) }}</p>
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
                  <el-table-column
                    v-if="selectedQueue.key === 'processor'"
                    prop="processor_key"
                    label="处理器"
                    width="160"
                    show-overflow-tooltip
                  />
                  <el-table-column
                    v-if="selectedQueue.key === 'processor'"
                    prop="step_key"
                    label="步骤"
                    width="160"
                    show-overflow-tooltip
                  />
                  <el-table-column
                    v-if="selectedQueue.key === 'processor'"
                    prop="subject_type"
                    label="对象类型"
                    width="120"
                    show-overflow-tooltip
                  />
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
                    <el-button plain @click="exportLogs">导出日志</el-button>
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
                  <el-button type="primary" plain @click="runAction('/tasks/process?force=true')">立即处理下载队列</el-button>
                  <el-button :icon="Refresh" @click="runAction('/tasks/poll')">刷新下载任务</el-button>
                  <el-button type="warning" @click="runAction('/tasks/retry-failed')">重试失败任务</el-button>
                  <el-popconfirm title="会清空番剧、候选、任务、下载产物、本地整理记录和日志。确定？" @confirm="runAction('/system/clear-data')">
                    <template #reference>
                      <el-button type="danger" plain>清除所有数据</el-button>
                    </template>
                  </el-popconfirm>
                </div>
              </template>
            </section>
          </div>
        </el-card>

        <div class="span-4 dashboard-bottom-grid">
          <el-card class="console-card utility-card">
            <el-tabs v-model="utilityTab">
              <el-tab-pane label="日志" name="logs">
                <div class="log-console compact-log-console">
                  <div class="log-toolbar">
                    <el-input v-model="logKeyword" clearable placeholder="搜索日志" />
                    <el-button plain @click="exportLogs">导出日志</el-button>
                    <el-button plain @click="runAction('/logs/clear')">清空日志</el-button>
                  </div>
                  <pre class="server-log">{{ filteredServerLogText }}</pre>
                </div>
              </el-tab-pane>
              <el-tab-pane label="维护" name="maintenance">
                <div class="maintenance-actions maintenance-pane compact-maintenance-pane">
                  <el-button type="primary" :icon="Search" :disabled="scanRunning" @click="runAction('/scan')">扫描全部</el-button>
                  <el-button type="primary" plain @click="runAction('/tasks/process?force=true')">立即处理下载队列</el-button>
                  <el-button :icon="Refresh" @click="runAction('/tasks/poll')">刷新下载任务</el-button>
                  <el-button type="warning" @click="runAction('/tasks/retry-failed')">重试失败任务</el-button>
                  <el-popconfirm title="会清空番剧、候选、任务、下载产物、本地整理记录和日志。确定？" @confirm="runAction('/system/clear-data')">
                    <template #reference>
                      <el-button type="danger" plain>清除所有数据</el-button>
                    </template>
                  </el-popconfirm>
                </div>
              </el-tab-pane>
            </el-tabs>
          </el-card>
        </div>
      </section>

      <section v-if="view === 'seasonal'" class="library seasonal-page">
        <div class="toolbar">
          <el-input v-model="keyword" clearable placeholder="搜索新番条目、Bangumi ID、标题" />
          <el-segmented v-model="seriesFilter" :options="['全部', '可观看', '处理中', '需处理', '未缓存']" />
        </div>
        <div class="anime-grid catalog-card-grid">
          <article v-for="item in filteredSeries" :key="item.id" class="anime-card catalog-card" @click="openEntry(item.id, 'seasonal')">
            <div class="cover poster-cover">
              <img v-if="item.poster_url" :src="item.poster_url" />
              <span v-else>{{ item.display_title?.slice(0, 2) || item.title_cn?.slice(0, 2) || 'AN' }}</span>
            </div>
            <div class="anime-body">
              <h3>{{ item.work_display_title || item.entry_display_title || item.display_title || item.title_cn }}</h3>
              <p>{{ item.entry_scope_label || item.entry_secondary_title || item.bangumi_id || 'Season 01' }}</p>
              <div class="tagline">
                <el-tag size="small" :type="runtimeTag(item)">{{ runtimeLabel(item) }}</el-tag>
                <el-tag size="small" type="info">{{ item.release_count }} 发布</el-tag>
                <el-tag size="small" type="success">本地 {{ item.local_asset_count || 0 }}</el-tag>
                <el-tag size="small">{{ item.entry_badge_text || item.entry_kind || 'season' }}</el-tag>
              </div>
              <p v-if="runtimeSummary(item)" class="queue-note">{{ runtimeSummary(item) }}</p>
              <el-progress :percentage="runtimeProgress(item)" :show-text="false" />
            </div>
          </article>
        </div>
      </section>

      <section v-if="view === 'calendar'" class="calendar-page">
        <div class="toolbar calendar-toolbar">
          <el-date-picker
            v-model="calendarWeek"
            type="week"
            format="[第] ww [周] YYYY"
            value-format="YYYY-MM-DD"
            placeholder="选择周"
          />
          <el-button-group>
            <el-button @click="shiftCalendarWeek(-1)">上一周</el-button>
            <el-button @click="setCalendarThisWeek">本周</el-button>
            <el-button @click="shiftCalendarWeek(1)">下一周</el-button>
          </el-button-group>
        </div>
        <div class="week-calendar-grid">
          <section v-for="day in weekDays" :key="day.key" class="week-day-column" :class="{ today: day.isToday }">
            <header>
              <strong>{{ day.label }}</strong>
              <span>{{ day.dateLabel }}</span>
            </header>
            <article
              v-for="item in day.items"
              :key="`${day.key}-${item.entry_id}-${item.episode_number}-${item.updated_at}`"
              class="calendar-entry-card"
              @click="openEntry(item.entry_id, 'seasonal')"
            >
              <div class="calendar-entry-cover">
                <img v-if="item.poster_url" :src="item.poster_url" />
                <span v-else>{{ (item.work_display_title || item.entry_display_title || item.display_title || 'AN').slice(0, 2) }}</span>
              </div>
              <div class="calendar-entry-meta">
                <strong>{{ item.work_display_title || item.entry_display_title || item.display_title }}</strong>
                <span>{{ item.entry_scope_label || item.entry_secondary_title || '-' }}</span>
              </div>
              <div class="calendar-entry-tags">
                <el-tag size="small" type="primary">第 {{ item.episode_number || '?' }} 集</el-tag>
                <el-tag size="small" :type="item.synced ? 'success' : 'warning'">{{ item.synced ? '已同步' : '已更新' }}</el-tag>
              </div>
            </article>
            <div v-if="!day.items.length" class="calendar-empty">无更新</div>
          </section>
        </div>
      </section>

      <section v-if="view === 'library'" class="library">
        <div class="toolbar library-toolbar">
          <el-input v-model="keyword" clearable placeholder="搜索番剧库条目、Bangumi ID、标题" />
          <el-segmented v-model="seriesFilter" :options="['全部', '可观看', '处理中', '需处理', '未缓存']" />
          <el-select v-model="libraryYearFilter" clearable placeholder="年份" class="compact-select">
            <el-option v-for="year in libraryYearOptions" :key="year" :label="`${year} 年`" :value="year" />
          </el-select>
          <el-button plain @click="runAction('/library/import')">导入现有资源到番剧库</el-button>
        </div>
        <div class="filter-board">
          <div class="filter-row">
            <span>媒体库</span>
            <button :class="{ active: !libraryLibraryFilter }" @click="libraryLibraryFilter = ''">全部</button>
            <button
              v-for="item in libraryLibraryOptions"
              :key="item.id"
              :class="{ active: Number(libraryLibraryFilter || 0) === Number(item.id) }"
              @click="libraryLibraryFilter = Number(item.id)"
            >{{ item.name }}</button>
          </div>
          <div class="filter-row">
            <span>类型</span>
            <button :class="{ active: !libraryMediaTypeFilter }" @click="libraryMediaTypeFilter = ''">全部</button>
            <button
              v-for="type in libraryMediaTypeOptions"
              :key="type"
              :class="{ active: libraryMediaTypeFilter === type }"
              @click="libraryMediaTypeFilter = type"
            >{{ mediaTypeLabel(type) }}</button>
          </div>
          <div class="filter-row">
            <span>地区</span>
            <button :class="{ active: !libraryRegionFilter }" @click="libraryRegionFilter = ''">全部</button>
            <button
              v-for="region in libraryRegionOptions"
              :key="region"
              :class="{ active: libraryRegionFilter === region }"
              @click="libraryRegionFilter = region"
            >{{ regionLabel(region) }}</button>
          </div>
          <div class="filter-row">
            <span>季度</span>
            <button :class="{ active: !libraryScopeFilter }" @click="libraryScopeFilter = ''">全部</button>
            <button
              v-for="scope in libraryScopeOptions"
              :key="scope"
              :class="{ active: libraryScopeFilter === scope }"
              @click="libraryScopeFilter = scope"
            >{{ scope }}</button>
          </div>
          <div class="filter-row">
            <span>标签</span>
            <button :class="{ active: !libraryTagFilters.length }" @click="libraryTagFilters = []">全部</button>
            <button
              v-for="tag in libraryTagOptions"
              :key="tag"
              :class="{ active: libraryTagFilters.includes(tag) }"
              @click="toggleLibraryTag(tag)"
            >{{ tag }}</button>
          </div>
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
        <div class="library-work-grid library-catalog-grid">
          <section v-for="work in libraryWorks" :key="work.key" class="library-work-card">
            <header class="library-work-header anime-card catalog-card" @click="toggleWorkExpanded(work.key)">
              <div class="cover work-cover">
                <img v-if="work.poster_url" :src="work.poster_url" />
                <span v-else>{{ work.work_title?.slice(0, 2) || 'AN' }}</span>
              </div>
              <div class="anime-body">
                <h3>{{ work.work_title || '未命名作品' }}</h3>
                <p>{{ work.entry_count }} 个条目 · {{ work.year_label || '年份未知' }}</p>
                <div class="tagline">
                  <el-tag size="small" :type="watchStatusTag(work)">{{ work.watch_status_label || '未缓存' }}</el-tag>
                  <el-tag size="small">{{ work.media_type_label }}</el-tag>
                  <el-tag size="small" type="info">{{ work.library_label }}</el-tag>
                  <el-tag size="small" type="info">{{ work.release_count }} 发布</el-tag>
                  <el-tag size="small" type="success">本地 {{ work.local_asset_count }}</el-tag>
                  <el-tag v-if="work.entry_count > 1" size="small">{{ isWorkExpanded(work.key) ? '收起合集' : '展开合集' }}</el-tag>
                </div>
                <el-progress :percentage="libraryProgressOf(work)" :show-text="false" />
              </div>
            </header>
            <div v-show="isWorkExpanded(work.key)" class="library-entry-list catalog-card-grid">
              <article v-for="item in work.entries" :key="item.id" class="anime-card catalog-card library-entry-card" @click.stop="openEntry(item.id, 'library')">
                <div class="cover poster-cover">
                  <img v-if="item.poster_url" :src="item.poster_url" />
                  <span v-else>{{ item.display_title?.slice(0, 2) || item.title_cn?.slice(0, 2) || 'AN' }}</span>
                </div>
                <div class="anime-body">
                  <h3>{{ item.work_display_title || item.entry_display_title || item.display_title || item.title_cn }}</h3>
                  <p>{{ item.entry_scope_label || item.entry_secondary_title || 'Season 01' }} · Bangumi: {{ item.bangumi_id || '未关联' }}</p>
                  <div class="tagline">
                    <el-tag size="small" :type="runtimeTag(item)">{{ runtimeLabel(item) }}</el-tag>
                    <el-tag size="small">{{ mediaTypeLabel(item.media_type) }}</el-tag>
                    <el-tag size="small" type="info">{{ regionLabel(item.region) }}</el-tag>
                    <el-tag size="small" type="info">{{ item.release_count }} 发布</el-tag>
                    <el-tag size="small" type="success">本地 {{ item.local_asset_count || 0 }}</el-tag>
                    <el-tag size="small">{{ item.entry_badge_text || item.entry_kind || 'season' }}</el-tag>
                  </div>
                  <div v-if="entryTags(item).length" class="mini-tag-row">
                    <span v-for="tag in entryTags(item).slice(0, 4)" :key="tag">{{ tag }}</span>
                  </div>
                  <p v-if="runtimeSummary(item)" class="queue-note">{{ runtimeSummary(item) }}</p>
                  <el-progress :percentage="runtimeProgress(item)" :show-text="false" />
                </div>
              </article>
            </div>
          </section>
        </div>
      </section>

      <section v-if="view === 'import'" class="import-page">
        <el-card class="import-panel">
          <template #header>
            <div class="card-header-row">
              <div>
                <strong>导入向导</strong>
                <span>解析候选后写入番剧库，本地文件直接成为本地资源，磁链可立即进入下载器</span>
              </div>
            </div>
          </template>
          <el-tabs v-model="importTab">
            <el-tab-pane label="本地目录" name="local">
              <div class="import-form-row">
                <el-input v-model="localImportPath" placeholder="/volume1/media/anime 或 单个视频文件路径" />
                <el-button type="primary" :loading="importLoading" @click="previewLocalImport">预览本地文件</el-button>
              </div>
            </el-tab-pane>
            <el-tab-pane label="磁链 / 种子" name="torrent">
              <div class="import-form-grid">
                <el-input v-model="torrentImport.title" placeholder="发布标题，例如 [LoliHouse] Test - 01 [1080p][简繁内封字幕]" />
                <el-input v-model="torrentImport.magnet" placeholder="magnet 链接" />
                <el-input v-model="torrentImport.torrent_url" placeholder="torrent URL，可选" />
                <el-button type="primary" :loading="importLoading" @click="previewTorrentImport">预览磁链</el-button>
              </div>
            </el-tab-pane>
          </el-tabs>
        </el-card>
        <el-card class="import-panel">
          <template #header>
            <div class="card-header-row">
              <div>
                <strong>候选资源</strong>
                <span>确认标题和媒体库后正式入库</span>
              </div>
              <div class="card-actions">
                <el-tag type="info">{{ importCandidates.length }} 条</el-tag>
                <el-button type="primary" :disabled="!importCandidates.length" :loading="importLoading" @click="commitImport">正式导入</el-button>
              </div>
            </div>
          </template>
          <div class="import-commit-panel">
            <el-form :model="importCommit" label-position="top">
              <div class="import-form-grid">
                <el-form-item label="入库标题">
                  <el-input v-model="importCommit.title_cn" placeholder="留空则使用解析标题" />
                </el-form-item>
                <el-form-item label="媒体库">
                  <el-select v-model="importCommit.target_library_id" placeholder="选择媒体库">
                    <el-option
                      v-for="library in dashboard.media_libraries"
                      :key="library.id"
                      :label="`${library.name} · ${library.root_path}`"
                      :value="Number(library.id)"
                    />
                  </el-select>
                </el-form-item>
                <el-form-item label="类型">
                  <el-select v-model="importCommit.media_type">
                    <el-option label="动画" value="anime" />
                    <el-option label="电影" value="movie" />
                    <el-option label="剧集" value="tv" />
                  </el-select>
                </el-form-item>
                <el-form-item label="地区">
                  <el-select v-model="importCommit.region">
                    <el-option label="日本" value="jp" />
                    <el-option label="中国" value="cn" />
                    <el-option label="欧美" value="us" />
                    <el-option label="韩国" value="kr" />
                    <el-option label="其他" value="other" />
                  </el-select>
                </el-form-item>
                <el-form-item label="年份">
                  <el-input-number v-model="importCommit.year" :min="0" :max="2100" />
                </el-form-item>
                <el-form-item label="季">
                  <el-input-number v-model="importCommit.season_number" :min="1" :max="99" />
                </el-form-item>
                <el-form-item label="Bangumi ID">
                  <el-input v-model="importCommit.bangumi_id" placeholder="可选" />
                </el-form-item>
                <el-form-item label="TMDB ID">
                  <el-input v-model="importCommit.tmdb_id" placeholder="可选" />
                </el-form-item>
              </div>
              <div class="import-options-row">
                <el-checkbox v-model="importCommit.generate_nfo">本地导入后生成 NFO</el-checkbox>
                <el-checkbox v-model="importCommit.start_download">磁链导入后立即下载</el-checkbox>
              </div>
            </el-form>
          </div>
          <el-table :data="importCandidates" height="520" empty-text="暂无候选，先选择一种导入方式预览">
            <el-table-column prop="source_type" label="来源" width="90" />
            <el-table-column prop="series_title" label="标题" min-width="220" show-overflow-tooltip />
            <el-table-column prop="episode_number" label="集" width="70" />
            <el-table-column prop="subtitle_group" label="字幕组" width="130" show-overflow-tooltip />
            <el-table-column prop="resolution" label="分辨率" width="100" />
            <el-table-column prop="language" label="语言" width="90" />
            <el-table-column label="字幕形式" width="100">
              <template #default="{ row }">{{ subtitleFormatText(row.subtitle_format) }}</template>
            </el-table-column>
            <el-table-column prop="source_uri" label="路径 / 链接" min-width="300" show-overflow-tooltip />
          </el-table>
        </el-card>
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
                  <el-form-item label="唯一匹配自动下载"><el-switch v-model="settings.auto_download_unique" /></el-form-item>
                  <el-form-item label="按优先级选择"><el-switch v-model="settings.auto_download_by_priority" /></el-form-item>
                </div>
                <div class="priority-layout">
                  <PriorityList title="字幕组优先级" v-model="settings.subtitle_priority" placeholder="添加字幕组" />
                  <PriorityList title="分辨率优先级" v-model="settings.resolution_priority" placeholder="添加分辨率" />
                  <PriorityList title="主字幕语言优先级" v-model="settings.language_priority" placeholder="添加主字幕语言" />
                  <PriorityList title="副字幕语言优先级" v-model="settings.secondary_language_priority" placeholder="添加副字幕语言" />
                </div>
              </el-tab-pane>
              <el-tab-pane label="下载器">
                <el-form-item label="下载器执行方式">
                  <el-radio-group v-model="settings.download_backend">
                    <el-radio-button label="rclone">rclone 命令</el-radio-button>
                    <el-radio-button label="api">PikPak API</el-radio-button>
                    <el-radio-button label="local">本地测试</el-radio-button>
                  </el-radio-group>
                </el-form-item>
                <div class="form-row" v-if="settings.download_backend === 'rclone'">
                  <el-form-item label="rclone 命令"><el-input v-model="settings.rclone_command" placeholder="rclone" /></el-form-item>
                  <el-form-item label="rclone remote"><el-input v-model="settings.rclone_remote" placeholder="pikpak" /></el-form-item>
                </div>
                <el-form-item v-if="settings.download_backend === 'rclone'" label="rclone 配置文件"><el-input v-model="settings.rclone_config_path" placeholder="/data/rclone/rclone.conf" /></el-form-item>
                <el-form-item v-if="settings.download_backend === 'local'" label="本地测试下载器目录"><el-input v-model="settings.local_downloader_root" placeholder="/data/local-downloader" /></el-form-item>
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
                  <el-form-item label="下载器远端目录"><el-input v-model="settings.library_root" /></el-form-item>
                <el-form-item label="默认本地媒体根目录"><el-input v-model="settings.local_library_root" placeholder="/media/autoanime" /></el-form-item>
                </div>
                <div class="media-library-settings">
                  <div v-for="library in dashboard.media_libraries" :key="library.id" class="media-library-setting-row">
                    <div>
                      <strong>{{ library.name }}</strong>
                      <span>{{ library.media_type }} · {{ library.download_strategy }} · {{ Number(library.enabled || 0) ? '启用' : '停用' }}</span>
                    </div>
                    <code>{{ library.root_path }}</code>
                    <el-button type="danger" link @click="deleteMediaLibrary(library.id)">删除</el-button>
                  </div>
                </div>
                <div class="media-library-editor">
                  <el-input v-model="mediaLibraryForm.name" placeholder="媒体库名称，例如：国漫 / 电影 / 美剧" />
                  <el-select v-model="mediaLibraryForm.media_type" placeholder="类型">
                    <el-option label="动画" value="anime" />
                    <el-option label="电影" value="movie" />
                    <el-option label="剧集" value="tv" />
                    <el-option label="其他" value="other" />
                  </el-select>
                  <el-input v-model="mediaLibraryForm.root_path" placeholder="/media/autoanime/Library" />
                  <el-button type="primary" @click="saveMediaLibrary">新增媒体库</el-button>
                </div>
                <el-form-item label="追更自动下载到本地"><el-switch v-model="settings.auto_sync_following" /></el-form-item>
                <el-form-item label="NFO 输出目录"><el-input v-model="settings.nfo_output_root" placeholder="留空；同步后默认写入本地媒体库" /></el-form-item>
                <el-form-item label="作品目录模板"><el-input v-model="settings.work_dir_template" /></el-form-item>
                <el-form-item label="季目录模板"><el-input v-model="settings.season_dir_template" /></el-form-item>
                <el-form-item label="单集名模板"><el-input v-model="settings.episode_name_template" /></el-form-item>
              </el-tab-pane>
              <el-tab-pane label="系统">
                <div class="diagnostics-grid">
                  <div><span>数据库</span><strong>{{ diagnostics.db_path || '-' }}</strong></div>
                  <div><span>数据目录可写</span><strong>{{ diagnostics.data_dir_writable ? '是' : '否' }}</strong></div>
                  <div><span>数据库大小</span><strong>{{ diagnostics.db_size || 0 }} bytes</strong></div>
                  <div><span>作品 / 条目 / 发布</span><strong>{{ diagnostics.tables?.works || 0 }} / {{ diagnostics.tables?.entries || 0 }} / {{ diagnostics.tables?.releases || 0 }}</strong></div>
                  <div><span>下载产物 / 本地 / 整理规则</span><strong>{{ diagnostics.tables?.download_artifacts || 0 }} / {{ diagnostics.tables?.local_assets || 0 }} / {{ diagnostics.tables?.sync_rules || 0 }}</strong></div>
                  <div><span>旧 series 表</span><strong>{{ diagnostics.tables?.legacy_series || 0 }}</strong></div>
                </div>
                <el-button :icon="Refresh" @click="reloadDiagnostics">刷新诊断</el-button>
              </el-tab-pane>
            </el-tabs>
            <div class="form-actions"><el-button type="primary" size="large" :loading="savingSettings" @click="saveAllSettings">保存设置</el-button></div>
          </el-form>
        </el-card>
      </section>
    </main>

    <nav class="mobile-nav" aria-label="移动端导航">
      <button :class="{ active: view === 'dashboard' }" @click="view = 'dashboard'"><el-icon><DataBoard /></el-icon><b>控制台</b></button>
      <button :class="{ active: view === 'seasonal' }" @click="view = 'seasonal'"><el-icon><Collection /></el-icon><b>新番</b></button>
      <button :class="{ active: view === 'calendar' }" @click="view = 'calendar'"><el-icon><Calendar /></el-icon><b>日历</b></button>
      <button :class="{ active: view === 'library' }" @click="view = 'library'"><el-icon><Collection /></el-icon><b>番剧</b></button>
      <button :class="{ active: view === 'import' }" @click="view = 'import'"><el-icon><Search /></el-icon><b>导入</b></button>
      <button :class="{ active: view === 'settings' }" @click="view = 'settings'"><el-icon><Setting /></el-icon><b>设置</b></button>
    </nav>

    <el-drawer v-model="entryDrawerOpen" size="720px" :title="selectedEntryDetail?.entry?.title_cn || (selectedEntryDomain === 'library' ? '番剧库条目' : '番剧设置')">
      <template v-if="selectedEntryDetail?.entry">
        <el-alert
          type="info"
          show-icon
          :closable="false"
          :title="selectedEntryDomain === 'library' ? '这里处理番剧库条目本身；后续会补独立的补番/导入能力。' : '这里只处理规则和冲突；下载与本地整理由后台任务自动推进。'"
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
              <el-form-item label="自动下载">
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
        <div class="sync-panel">
          <div>
            <strong>本地整理</strong>
            <span>{{ syncSummary }}</span>
          </div>
          <el-switch :model-value="syncWanted" @change="toggleEntrySync" />
        </div>
        <div class="drawer-actions">
          <el-button type="primary" @click="saveCurrentEntry">保存</el-button>
          <el-button plain @click="runEntryAction('metadata')">刷新元数据</el-button>
          <el-button plain @click="runEntryAction('nfo')">生成 NFO</el-button>
          <el-button plain @click="runEntryAction('backfill')">补全条目</el-button>
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
              <el-table-column label="选中" width="80">
                <template #default="{ row }">
                  <el-tag v-if="row.selected" size="small" type="success">是</el-tag>
                  <span v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column prop="subtitle_group" label="字幕组" width="140" />
              <el-table-column prop="resolution" label="分辨率" width="100" />
              <el-table-column prop="language" label="语言" width="90" />
              <el-table-column prop="subtitle_format" label="字幕形式" width="100">
                <template #default="{ row }">{{ subtitleFormatText(row.subtitle_format) }}</template>
              </el-table-column>
              <el-table-column prop="guid" label="GUID" min-width="220" show-overflow-tooltip />
              <el-table-column prop="title" label="发布标题" min-width="260" show-overflow-tooltip />
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="下载任务">
            <el-table :data="selectedEntryDetail.tasks" height="320">
              <el-table-column prop="status" label="状态" width="110">
                <template #default="{ row }"><el-tag :type="taskTag(row.status)">{{ taskStatusText(row) }}</el-tag></template>
              </el-table-column>
              <el-table-column prop="target_dir" label="目标目录" min-width="220" show-overflow-tooltip />
              <el-table-column prop="submission_id" label="下载任务" min-width="180" show-overflow-tooltip />
              <el-table-column prop="provider_file_id" label="文件 ID" min-width="180" show-overflow-tooltip />
              <el-table-column prop="last_error" label="错误" min-width="220" show-overflow-tooltip />
              <el-table-column label="下次处理" width="130">
                <template #default="{ row }">{{ row.waiting_retry ? formatCountdown(row.retry_seconds) : '-' }}</template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="下载产物">
            <el-table :data="selectedEntryDetail.download_artifacts" height="320">
              <el-table-column prop="episode_number" label="集" width="70" />
              <el-table-column prop="provider" label="下载器" width="100" />
              <el-table-column prop="remote_path" label="远端路径" min-width="260" show-overflow-tooltip />
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
import { Calendar, Collection, DataBoard, Refresh, Search, Setting } from '@element-plus/icons-vue'
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
const liveConnected = ref(false)
const selectedQueueDomainFilter = ref('全部')
const queueVisibilityMode = ref('活跃')
const consoleNavMode = ref('队列')
const utilityTab = ref('logs')
const calendarWeek = ref('')
const expandedWorkKeys = ref(new Set())
let refreshTimer = null
let dashboardStream = null
let streamRetryTimer = null
const keyword = ref('')
const seriesFilter = ref('全部')
const libraryYearFilter = ref('')
const libraryScopeFilter = ref('')
const libraryMediaTypeFilter = ref('')
const libraryRegionFilter = ref('')
const libraryLibraryFilter = ref('')
const libraryTagFilters = ref([])
const importTab = ref('local')
const localImportPath = ref('')
const importLoading = ref(false)
const importCandidates = ref([])
const importCommit = reactive({
  title_cn: '',
  bangumi_id: '',
  tmdb_id: '',
  year: 0,
  season_number: 1,
  media_type: 'anime',
  region: 'jp',
  target_library_id: 0,
  start_download: true,
  generate_nfo: true,
})
const torrentImport = reactive({
  title: '',
  magnet: '',
  torrent_url: '',
  page_url: ''
})
const entryDrawerOpen = ref(false)
const selectedEntryDetail = ref(null)
const selectedEntryDomain = ref('seasonal')
const dashboard = reactive({
  seasonal_items: [],
  library_items: [],
  media_libraries: [],
  library_summary: {},
  seasonal_sync_calendar: [],
  seasonal_update_calendar: [],
  sync_rules: [],
  operations: [],
  scheduled_jobs: [],
  scheduled_runs: [],
  server_logs: [],
  episode_jobs: [],
  queue_summary: [],
  queue_details: {},
  console_sections: [],
  console_overview: {},
})
const settings = reactive({})
const diagnostics = reactive({ tables: {} })
const mediaLibraryForm = reactive({
  name: '',
  media_type: 'anime',
  root_path: '',
})

const pageTitle = computed(() => ({
  dashboard: '控制台',
  seasonal: '新番',
  calendar: '日历',
  library: '番剧',
  import: '导入',
  settings: '设置中心'
}[view.value]))

const seasonalRows = computed(() => dashboard.seasonal_items || [])
const libraryRows = computed(() => dashboard.library_items || [])
const libraryLibraryOptions = computed(() => {
  const used = new Set(libraryRows.value.map(item => Number(item.target_library_id || 0)).filter(Boolean))
  return (dashboard.media_libraries || []).filter(item => used.has(Number(item.id || 0)))
})
const libraryMediaTypeOptions = computed(() => {
  const values = new Set()
  for (const item of libraryRows.value) {
    if (item.media_type) values.add(item.media_type)
  }
  return Array.from(values).sort((a, b) => mediaTypeLabel(a).localeCompare(mediaTypeLabel(b)))
})
const libraryRegionOptions = computed(() => {
  const values = new Set()
  for (const item of libraryRows.value) {
    if (item.region) values.add(item.region)
  }
  return Array.from(values).sort((a, b) => regionLabel(a).localeCompare(regionLabel(b)))
})
const libraryYearOptions = computed(() => {
  const values = new Set()
  for (const item of libraryRows.value) {
    const year = Number(item.year || 0)
    if (year > 0) values.add(year)
  }
  return Array.from(values).sort((a, b) => b - a)
})
const libraryScopeOptions = computed(() => {
  const values = new Set()
  for (const item of libraryRows.value) {
    const scope = item.entry_scope_label || item.entry_badge_text || ''
    if (scope) values.add(scope)
  }
  return Array.from(values).sort((a, b) => String(a).localeCompare(String(b)))
})
const libraryTagOptions = computed(() => {
  const counts = new Map()
  for (const item of libraryRows.value) {
    for (const tag of entryTags(item)) {
      counts.set(tag, (counts.get(tag) || 0) + 1)
    }
  }
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 36)
    .map(item => item[0])
})
const activeDetailRows = computed(() => selectedEntryDomain.value === 'library' ? libraryRows.value : seasonalRows.value)
const downloadArtifactTotal = computed(() => seasonalRows.value.reduce((sum, item) => sum + Number(item.download_artifact_count || 0), 0))
const localAssetTotal = computed(() => seasonalRows.value.reduce((sum, item) => sum + Number(item.local_asset_count || 0), 0))
const episodeJobRows = computed(() => (dashboard.episode_jobs || []).slice(0, 20))
const episodeJobsByEntry = computed(() => {
  const groups = new Map()
  for (const job of dashboard.episode_jobs || []) {
    const key = Number(job.entry_id || 0)
    if (!key) continue
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(job)
  }
  for (const rows of groups.values()) {
    rows.sort((a, b) => Number(b.episode_number || 0) - Number(a.episode_number || 0))
  }
  return groups
})
const seasonalCalendarCards = computed(() => dashboard.seasonal_sync_calendar || [])
const weekStart = computed(() => startOfWeek(calendarWeek.value ? new Date(calendarWeek.value) : new Date()))
const weekDays = computed(() => {
  const labels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
  return Array.from({ length: 7 }, (_, index) => {
    const date = addDays(weekStart.value, index)
    const key = formatDateKey(date)
    return {
      key,
      label: labels[index],
      dateLabel: `${date.getMonth() + 1}/${date.getDate()}`,
      isToday: key === formatDateKey(new Date()),
      items: seasonalCalendarCards.value.filter(item => formatDateKey(new Date(item.updated_at || item.synced_at || 0)) === key)
    }
  })
})
const scanOperation = computed(() => dashboard.operations.find(op => op.name === '扫描全部' && op.status === 'running'))
const queueMap = computed(() => Object.fromEntries((dashboard.queue_summary || []).map(item => [item.key, item])))
const queueConsoleSections = computed(() => (dashboard.console_sections || []).filter(section => {
  if (section.kind === 'group') return true
  return section.kind === 'queue' && shouldShowConsoleSection(section)
}))
const queueListSections = computed(() => queueConsoleSections.value.filter(section => section.kind === 'queue'))
const scheduledConsoleSections = computed(() => (dashboard.console_sections || []).filter(section => section.kind === 'scheduled'))
const visibleConsoleSections = computed(() => (dashboard.console_sections || []).filter(section => shouldShowConsoleSection(section)))
const selectedSectionMeta = computed(() => {
  const source = consoleNavMode.value === '定时任务' ? scheduledConsoleSections.value : queueListSections.value
  return source.find(item => item.key === selectedConsoleSection.value) || null
})
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
  if (Number(stats.local_asset_count || 0) > 0) return `本地可观看 ${stats.local_asset_count} 集`
  if (syncWanted.value && Number(stats.download_artifact_count || 0) > 0) return '已加入本地下载整理队列'
  if (syncWanted.value) return '已开启，下载完成后会整理到本地媒体库'
  return '关闭后不会自动整理到本地媒体库'
})

const filteredSeries = computed(() => {
  const text = keyword.value.toLowerCase()
  const source = view.value === 'library' ? libraryRows.value : seasonalRows.value
  return source.filter(item => {
    const runtime = entryRuntime(item)
    const matched = !text || `${item.entry_display_title || item.display_title || item.title_cn} ${item.work_display_title || item.work_title || item.title_root || ''} ${item.entry_scope_label || ''} ${item.bangumi_id}`.toLowerCase().includes(text)
    if (!matched) return false
    if (view.value === 'library') {
      if (libraryLibraryFilter.value && Number(item.target_library_id || 0) !== Number(libraryLibraryFilter.value)) return false
      if (libraryMediaTypeFilter.value && String(item.media_type || '') !== String(libraryMediaTypeFilter.value)) return false
      if (libraryRegionFilter.value && String(item.region || '') !== String(libraryRegionFilter.value)) return false
      if (libraryYearFilter.value && Number(item.year || 0) !== Number(libraryYearFilter.value)) return false
      if (libraryScopeFilter.value && String(item.entry_scope_label || item.entry_badge_text || '') !== String(libraryScopeFilter.value)) return false
      if (libraryTagFilters.value.length) {
        const tags = entryTags(item)
        if (!libraryTagFilters.value.every(tag => tags.includes(tag))) return false
      }
    }
    if (seriesFilter.value === '可观看') return runtime.ready_count > 0
    if (seriesFilter.value === '处理中') return ['running', 'pending', 'waiting'].includes(runtime.status) && runtime.ready_count <= 0
    if (seriesFilter.value === '需处理') return runtime.status === 'failed' || !item.bangumi_id || Boolean(item.has_failed_task)
    if (seriesFilter.value === '未缓存') return runtime.ready_count <= 0
    return true
  })
})

const libraryWorks = computed(() => {
  const groups = new Map()
  for (const item of filteredSeries.value) {
    const key = `${item.work_id || 0}:${item.work_display_title || item.work_title || item.title_root || item.display_title || item.title_cn || 'work'}`
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        work_id: item.work_id || 0,
        work_title: item.work_display_title || item.work_title || item.title_root || item.display_title || item.title_cn || '未命名作品',
        poster_url: item.poster_url || '',
        year_label: item.year ? `${item.year}` : '',
        library_label: libraryName(item.target_library_id),
        media_type_label: mediaTypeLabel(item.media_type),
        regions: new Set(),
        media_types: new Set(),
        tags: new Set(),
        entry_count: 0,
        release_count: 0,
        download_artifact_count: 0,
        local_asset_count: 0,
        watch_status: 'unavailable',
        watch_status_label: '未缓存',
        entries: []
      })
    }
    const group = groups.get(key)
    const runtime = entryRuntime(item)
    group.entry_count += 1
    group.release_count += Number(item.release_count || 0)
    group.download_artifact_count += Number(item.download_artifact_count || 0)
    group.local_asset_count += Math.max(Number(item.local_asset_count || 0), runtime.ready_count)
    if (item.region) group.regions.add(item.region)
    if (item.media_type) group.media_types.add(item.media_type)
    for (const tag of entryTags(item)) group.tags.add(tag)
    if (!group.library_label) group.library_label = libraryName(item.target_library_id)
    group.media_type_label = Array.from(group.media_types).map(mediaTypeLabel).join(' / ') || group.media_type_label
    if (runtime.status === 'failed') {
      group.watch_status = 'warning'
      group.watch_status_label = '需处理'
    } else if (group.local_asset_count > 0 && group.watch_status !== 'warning') {
      group.watch_status = 'ready'
      group.watch_status_label = `可观看 ${group.local_asset_count} 集`
    } else if (['running', 'waiting', 'pending'].includes(runtime.status) || group.download_artifact_count > 0 || group.release_count > 0) {
      group.watch_status = 'processing'
      group.watch_status_label = runtime.label || '处理中'
    }
    if (!group.poster_url && item.poster_url) group.poster_url = item.poster_url
    if (!group.year_label && item.year) group.year_label = `${item.year}`
    group.entries.push(item)
  }
  return Array.from(groups.values()).map(group => ({
    ...group,
    regions: Array.from(group.regions),
    media_types: Array.from(group.media_types),
    tags: Array.from(group.tags).slice(0, 8),
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

function watchStatusTag(item) {
  const status = String(item?.watch_status || '')
  if (status === 'ready') return 'success'
  if (status === 'warning') return 'danger'
  if (status === 'processing') return 'warning'
  return 'info'
}

function mediaTypeLabel(value) {
  const key = String(value || 'anime')
  return {
    anime: '动画',
    movie: '电影',
    tv: '剧集',
    ova: 'OVA',
  }[key] || key
}

function regionLabel(value) {
  const key = String(value || '')
  return {
    jp: '日本',
    cn: '中国',
    us: '欧美',
    kr: '韩国',
    other: '其他',
  }[key] || key || '未知'
}

function libraryName(id) {
  const row = (dashboard.media_libraries || []).find(item => Number(item.id || 0) === Number(id || 0))
  return row?.name || ''
}

function parseJsonArray(value) {
  if (!value) return []
  if (Array.isArray(value)) return value.filter(Boolean).map(item => String(item))
  try {
    const parsed = JSON.parse(String(value))
    return Array.isArray(parsed) ? parsed.filter(Boolean).map(item => String(item)) : []
  } catch {
    return []
  }
}

function entryTags(item) {
  const tags = [...parseJsonArray(item?.genres_json), ...parseJsonArray(item?.tags_json)]
  return Array.from(new Set(tags.map(tag => tag.trim()).filter(Boolean)))
}

function toggleLibraryTag(tag) {
  const next = new Set(libraryTagFilters.value)
  if (next.has(tag)) next.delete(tag)
  else next.add(tag)
  libraryTagFilters.value = Array.from(next)
}

function entryRuntime(item) {
  const entryId = Number(item?.id || item?.entry_id || 0)
  const jobs = episodeJobsByEntry.value.get(entryId) || []
  const latest = jobs[0] || null
  const readyCount = jobs.filter(job => job.stage === 'done' || job.status === 'completed').length
  const failed = jobs.find(job => job.status === 'failed')
  const active = jobs.find(job => ['running', 'waiting', 'pending'].includes(String(job.status || '')))
  const status = failed ? 'failed' : active ? String(active.status || 'pending') : readyCount > 0 ? 'completed' : 'idle'
  const label = failed
    ? '需处理'
    : active
      ? (active.stage_label || '处理中')
      : readyCount > 0
        ? `可观看 ${readyCount} 集`
        : '未缓存'
  const reason = failed?.reason || active?.reason || latest?.reason || ''
  return {
    jobs,
    latest,
    ready_count: readyCount,
    total_count: jobs.length,
    status,
    label,
    reason,
  }
}

function runtimeTag(item) {
  const status = entryRuntime(item).status
  if (status === 'failed') return 'danger'
  if (status === 'running' || status === 'waiting' || status === 'pending') return 'warning'
  if (status === 'completed') return 'success'
  return 'info'
}

function runtimeLabel(item) {
  return entryRuntime(item).label
}

function runtimeSummary(item) {
  const runtime = entryRuntime(item)
  if (runtime.latest?.episode_number) {
    const prefix = `第 ${runtime.latest.episode_number} 集`
    return runtime.reason ? `${prefix} · ${runtime.reason}` : `${prefix} · ${runtime.label}`
  }
  return seasonalStatusSummary(item)
}

function runtimeProgress(item) {
  const runtime = entryRuntime(item)
  if (runtime.total_count > 0) {
    return Math.min(100, Math.round(runtime.ready_count / runtime.total_count * 100))
  }
  return progressOf(item)
}

function isQueueActive(queue) {
  if (!queue) return false
  if (queue.system_queue) return false
  return Number(queue.pending || 0) > 0
    || Number(queue.running || 0) > 0
    || Number(queue.failed || 0) > 0
    || Number(queue.waiting || 0) > 0
    || ['running', 'debouncing', 'rerun_pending', 'cooldown', 'ready', 'failed'].includes(String(queue.queue_state || ''))
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
  if (row?.status === 'waiting') return '等待重试'
  if (row?.status === 'pending' && row?.waiting_retry) return '等待重试'
  if (row?.status === 'pending') return '待处理'
  if (row?.status === 'failed') return '失败'
  if (row?.status === 'superseded') return '已替代'
  return row?.status || ''
}

function subtitleFormatText(value) {
  if (value === 'embedded') return '内嵌'
  if (value === 'external') return '外挂'
  return '-'
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
  if (queue.queue_state === 'ready' || Number(queue.pending || 0) > 0) return '待调度'
  return '空闲'
}

function queuePendingHint(queue) {
  const key = String(queue?.key || '')
  if (key === 'rss') return '这里只显示最近的 RSS 候选；后续 Mikan、元数据、选集、下载到本地都由任务链自动推进。'
  if (key === 'download') return '待处理表示已选中发布，等待下载器提交、轮询完成并整理到本地媒体库。'
  if (key === 'local_sync') return '待处理表示已有下载产物，等待补整理到本地媒体库。'
  if (key === 'selection') return '待处理表示元数据已完成，等待按规则自动选择发布。'
  if (key === 'processor') return '这里显示流水线统一处理器任务，扫描后可直接看每条数据卡在 RSS、匹配、元数据、整合、下载还是 NFO。'
  if (key === 'backfill') return '待处理表示番剧已入库，等待去 Mikan 番组页补抓历史条目。'
  if (key === 'metadata') return '待处理表示已拿到 Bangumi 线索，等待补全正式元数据。'
  if (key === 'mikan_match') return '待处理表示 RSS 候选已入队，等待解析对应的 Mikan/Bangumi 关联。'
  return '任务已入队，等待调度执行。'
}

function shouldShowConsoleSection(section) {
  if (!section) return false
  if (section.kind === 'group' || section.kind === 'logs' || section.kind === 'maintenance') return true
  if (section.kind === 'scheduled') return queueVisibilityMode.value === '全部'
  if (section.kind !== 'queue') return false
  if (queueVisibilityMode.value === '全部') return true
  return isQueueActive(queueMap.value[section.queue_key])
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
  return Math.min(100, Math.round(Number(item.local_asset_count || item.download_artifact_count || 0) / total * 100))
}

function isWorkExpanded(key) {
  return expandedWorkKeys.value.has(key)
}

function toggleWorkExpanded(key) {
  const next = new Set(expandedWorkKeys.value)
  if (next.has(key)) {
    next.delete(key)
  } else {
    next.add(key)
  }
  expandedWorkKeys.value = next
}

function startOfWeek(date) {
  const base = new Date(date)
  const day = base.getDay() || 7
  base.setHours(0, 0, 0, 0)
  base.setDate(base.getDate() - day + 1)
  return base
}

function addDays(date, days) {
  const next = new Date(date)
  next.setDate(next.getDate() + days)
  return next
}

function formatDateKey(date) {
  if (Number.isNaN(date.getTime())) return ''
  const y = date.getFullYear()
  const m = `${date.getMonth() + 1}`.padStart(2, '0')
  const d = `${date.getDate()}`.padStart(2, '0')
  return `${y}-${m}-${d}`
}

function setCalendarThisWeek() {
  calendarWeek.value = formatDateKey(startOfWeek(new Date()))
}

function shiftCalendarWeek(delta) {
  calendarWeek.value = formatDateKey(addDays(weekStart.value, delta * 7))
}

function applyDashboard(nextDashboard) {
  Object.assign(dashboard, nextDashboard || {})
  ensureImportDefaults()
  const source = consoleNavMode.value === '定时任务' ? scheduledConsoleSections.value : queueListSections.value
  if (!source.some(item => item.key === selectedConsoleSection.value)) {
    const fallback = source[0] || queueListSections.value[0]
    selectedConsoleSection.value = fallback?.key || 'queue:mikan_match'
  }
}

function ensureImportDefaults() {
  if (Number(importCommit.target_library_id || 0) > 0) return
  const library = (dashboard.media_libraries || []).find(item => item.key === 'anime_library')
    || (dashboard.media_libraries || []).find(item => item.media_type === 'anime')
    || (dashboard.media_libraries || [])[0]
  if (library) importCommit.target_library_id = Number(library.id || 0)
}

function applyImportCandidateDefaults() {
  const first = importCandidates.value[0] || {}
  if (!importCommit.title_cn && first.series_title) importCommit.title_cn = first.series_title
  if (!Number(importCommit.year || 0) && Number(first.year || 0) > 0) importCommit.year = Number(first.year || 0)
}

async function previewLocalImport() {
  if (!localImportPath.value.trim()) {
    ElMessage.warning('请输入本地目录或文件路径')
    return
  }
  importLoading.value = true
  try {
    const result = await postAction('/import/local/preview', {
      root_path: localImportPath.value.trim(),
      limit: 300
    })
    importCandidates.value = result.items || []
    applyImportCandidateDefaults()
    if (result.status === 'not_found') {
      ElMessage.warning(result.message || '路径不存在')
    } else {
      ElMessage.success(`解析到 ${importCandidates.value.length} 个候选`)
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  } finally {
    importLoading.value = false
  }
}

async function previewTorrentImport() {
  importLoading.value = true
  try {
    const result = await postAction('/import/torrent/preview', {
      title: torrentImport.title,
      magnet: torrentImport.magnet,
      torrent_url: torrentImport.torrent_url,
      page_url: torrentImport.page_url
    })
    importCandidates.value = result.item && Object.keys(result.item).length ? [result.item] : []
    applyImportCandidateDefaults()
    if (result.status === 'invalid') {
      ElMessage.warning(result.message || '资源链接无效')
    } else {
      ElMessage.success('解析完成')
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  } finally {
    importLoading.value = false
  }
}

async function commitImport() {
  if (!importCandidates.value.length) {
    ElMessage.warning('没有可导入候选')
    return
  }
  importLoading.value = true
  try {
    const first = importCandidates.value[0] || {}
    const isTorrent = first.source_type === 'torrent'
    const payload = {
      items: importCandidates.value,
      item: first,
      title_cn: importCommit.title_cn,
      bangumi_id: importCommit.bangumi_id,
      tmdb_id: importCommit.tmdb_id,
      year: Number(importCommit.year || 0),
      season_number: Number(importCommit.season_number || 1),
      media_type: importCommit.media_type,
      region: importCommit.region,
      target_library_id: Number(importCommit.target_library_id || 0),
      start_download: Boolean(importCommit.start_download),
      generate_nfo: Boolean(importCommit.generate_nfo),
    }
    const result = await postAction(isTorrent ? '/import/torrent/commit' : '/import/local/commit', payload)
    if (result.status === 'invalid') {
      ElMessage.warning(result.message || '导入参数无效')
      return
    }
    const count = isTorrent ? 1 : Number(result.imported_count || 0)
    ElMessage.success(`已导入 ${count} 条资源`)
    await reload()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  } finally {
    importLoading.value = false
  }
}

async function reload() {
  if (loading.value) return
  loading.value = true
  try {
    applyDashboard(await getDashboard())
    Object.assign(settings, await getSettings())
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

function stopDashboardStream() {
  if (dashboardStream) {
    dashboardStream.close()
    dashboardStream = null
  }
  if (streamRetryTimer) {
    window.clearTimeout(streamRetryTimer)
    streamRetryTimer = null
  }
  liveConnected.value = false
}

function startDashboardStream() {
  stopDashboardStream()
  if (!autoRefresh.value || !window.EventSource) {
    startAutoRefresh()
    return
  }
  dashboardStream = new EventSource('/api/dashboard/stream')
  dashboardStream.onopen = () => {
    liveConnected.value = true
    stopAutoRefresh()
  }
  dashboardStream.onmessage = event => {
    try {
      applyDashboard(JSON.parse(event.data))
    } catch {
      liveConnected.value = false
    }
  }
  dashboardStream.onerror = () => {
    stopDashboardStream()
    startAutoRefresh()
    streamRetryTimer = window.setTimeout(startDashboardStream, 5000)
  }
}

function startAutoRefresh() {
  stopAutoRefresh()
  if (!autoRefresh.value || liveConnected.value) return
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

function exportLogs() {
  const text = filteredServerLogText.value || ''
  if (!text.trim()) {
    ElMessage.warning('没有可导出的日志')
    return
  }
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-')
  const blob = new Blob([`${text}\n`], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `autoanime-log-${timestamp}.txt`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
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

async function saveMediaLibrary() {
  if (!mediaLibraryForm.name.trim() || !mediaLibraryForm.root_path.trim()) {
    ElMessage.warning('媒体库名称和目录不能为空')
    return
  }
  try {
    const result = await postAction('/media-libraries', {
      name: mediaLibraryForm.name.trim(),
      media_type: mediaLibraryForm.media_type,
      root_path: mediaLibraryForm.root_path.trim(),
      download_strategy: 'download',
      metadata_provider_priority: 'bangumi,tmdb,manual',
      enabled: true,
    })
    ElMessage.success(result.message || '媒体库已保存')
    mediaLibraryForm.name = ''
    mediaLibraryForm.root_path = ''
    await reload()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

async function deleteMediaLibrary(id) {
  try {
    const result = await deleteAction(`/media-libraries/${id}`)
    ElMessage.success(result.message || '媒体库已删除')
    await reload()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
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

watch([autoRefresh, refreshInterval], () => {
  if (autoRefresh.value) {
    startDashboardStream()
  } else {
    stopDashboardStream()
    stopAutoRefresh()
  }
})
watch(entryDrawerOpen, startAutoRefresh)
watch(selectedConsoleSection, () => {
  selectedQueueDomainFilter.value = '全部'
})
watch(queueVisibilityMode, () => {
  if (!queueListSections.value.some(item => item.key === selectedConsoleSection.value)) {
    const fallback = queueListSections.value[0]
    selectedConsoleSection.value = fallback?.key || 'queue:mikan_match'
  }
})
watch(consoleNavMode, value => {
  if (value === '定时任务') {
    selectedConsoleSection.value = scheduledConsoleSections.value[0]?.key || selectedConsoleSection.value
    return
  }
  if (!queueListSections.value.some(item => item.key === selectedConsoleSection.value)) {
    const fallback = queueListSections.value[0]
    selectedConsoleSection.value = fallback?.key || 'queue:mikan_match'
  }
})
watch(view, value => {
  if (value === 'settings') reloadDiagnostics().catch(error => ElMessage.error(apiErrorMessage(error)))
})

onMounted(async () => {
  setCalendarThisWeek()
  await reload()
  startDashboardStream()
})

onUnmounted(() => {
  stopDashboardStream()
  stopAutoRefresh()
})
</script>



