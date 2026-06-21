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
        <button :class="{ active: view === 'movies' }" @click="view = 'movies'"><el-icon><Collection /></el-icon> 电影</button>
        <button :class="{ active: view === 'tv' }" @click="view = 'tv'"><el-icon><Collection /></el-icon> 电视剧</button>
        <div class="nav-caption">系统</div>
        <button :class="{ active: view === 'logs' }" @click="view = 'logs'"><el-icon><Document /></el-icon> 日志</button>
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
          <p class="hero-sub">RSS 扫描写入源头任务，处理队列实时推进。<span class="build-version">v{{ appVersion }} · {{ appBuild }}</span></p>
        </div>
        <div class="hero-actions">
          <el-switch
            v-model="autoRefresh"
            inline-prompt
            active-text="实时"
            inactive-text="手动"
          />
          <el-tag :type="liveConnected ? 'success' : 'info'">{{ liveConnected ? '动态连接' : '轮询刷新' }}</el-tag>
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
                  <div><span>启用</span><strong>{{ Number(selectedScheduledJob.enabled ?? 1) ? '是' : '否' }}</strong></div>
                  <div><span>防抖</span><strong>{{ selectedScheduledJob.debounce_seconds || 0 }} 秒</strong></div>
                  <div><span>最近状态</span><strong>{{ selectedScheduledJob.last_status || '-' }}</strong></div>
                  <div><span>最近执行</span><strong>{{ selectedScheduledJob.latest_run?.started_at || '-' }}</strong></div>
                </div>
                <div class="scheduled-config-panel">
                  <el-form label-position="top">
                    <div class="form-row">
                      <el-form-item label="启用定时任务"><el-switch v-model="scheduledJobForm.enabled" /></el-form-item>
                      <el-form-item label="执行间隔（分钟）"><el-input-number v-model="scheduledJobForm.interval_minutes" :min="1" /></el-form-item>
                    </div>
                    <el-button type="primary" plain @click="saveScheduledJob">保存定时配置</el-button>
                  </el-form>
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
          </el-card>
        </div>
      </section>

      <section v-if="view === 'seasonal'" class="library seasonal-page media-page">
        <div class="toolbar media-toolbar">
          <el-input v-model="keyword" clearable placeholder="搜索新番条目、Bangumi ID、标题" />
          <div class="toolbar-spacer"></div>
          <el-button plain @click="advancedFilterOpen = !advancedFilterOpen">{{ advancedFilterOpen ? '收起筛选' : '高级筛选' }}</el-button>
          <el-button type="primary" @click="openRssDialog">添加 RSS 订阅</el-button>
        </div>
        <div v-if="advancedFilterOpen" class="filter-board">
          <div class="filter-row">
            <span>年份</span>
            <button :class="{ active: !libraryYearFilter }" @click="libraryYearFilter = ''">全部</button>
            <button
              v-for="year in currentYearOptions"
              :key="year"
              :class="{ active: Number(libraryYearFilter || 0) === Number(year) }"
              @click="libraryYearFilter = Number(year)"
            >{{ year }}</button>
          </div>
          <div class="filter-row">
            <span>类型</span>
            <button :class="{ active: !libraryMediaTypeFilter }" @click="libraryMediaTypeFilter = ''">全部</button>
            <button
              v-for="type in currentMediaTypeOptions"
              :key="type"
              :class="{ active: libraryMediaTypeFilter === type }"
              @click="libraryMediaTypeFilter = type"
            >{{ mediaTypeLabel(type) }}</button>
          </div>
          <div class="filter-row">
            <span>地区</span>
            <button :class="{ active: !libraryRegionFilter }" @click="libraryRegionFilter = ''">全部</button>
            <button
              v-for="region in currentRegionOptions"
              :key="region"
              :class="{ active: libraryRegionFilter === region }"
              @click="libraryRegionFilter = region"
            >{{ regionLabel(region) }}</button>
          </div>
          <div class="filter-row">
            <span>季度</span>
            <button :class="{ active: !libraryScopeFilter }" @click="libraryScopeFilter = ''">全部</button>
            <button
              v-for="scope in currentScopeOptions"
              :key="scope"
              :class="{ active: libraryScopeFilter === scope }"
              @click="libraryScopeFilter = scope"
            >{{ scope }}</button>
          </div>
          <div class="filter-row">
            <span>标签</span>
            <button :class="{ active: !libraryTagFilters.length }" @click="libraryTagFilters = []">全部</button>
            <button
              v-for="tag in currentTagOptions"
              :key="tag"
              :class="{ active: libraryTagFilters.includes(tag) }"
              @click="toggleLibraryTag(tag)"
            >{{ tag }}</button>
          </div>
        </div>
        <div class="anime-grid catalog-card-grid">
          <article v-for="item in filteredSeries" :key="item.id" class="anime-card catalog-card" @click="openEntry(item.id, 'seasonal')">
            <div class="cover poster-cover">
              <img v-if="item.poster_url" :src="item.poster_url" />
              <span v-else>{{ item.display_title?.slice(0, 2) || item.title_cn?.slice(0, 2) || 'AN' }}</span>
            </div>
            <div class="anime-body">
              <h3>{{ entryTitle(item) }}</h3>
              <p>{{ item.entry_scope_label || item.entry_secondary_title || item.bangumi_id || 'Season 01' }}</p>
              <div class="tagline">
                <el-tag size="small" type="success">可观看 {{ watchableCount(item) }} 集</el-tag>
                <el-tag v-if="hasRecentUpdate(item)" size="small" type="primary">已更新</el-tag>
              </div>
              <div v-if="entryTags(item).length" class="mini-tag-row">
                <span v-for="tag in entryTags(item).slice(0, 3)" :key="tag">{{ tag }}</span>
              </div>
            </div>
          </article>
          <el-empty v-if="!filteredSeries.length" description="没有匹配的新番条目" />
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

      <section v-if="isMediaCatalogView" class="library media-page">
        <div class="toolbar media-toolbar">
          <el-input v-model="keyword" clearable :placeholder="`搜索${currentMediaPageTitle}、Bangumi ID、TMDB ID、标题`" />
          <div class="toolbar-spacer"></div>
          <el-button plain @click="advancedFilterOpen = !advancedFilterOpen">{{ advancedFilterOpen ? '收起筛选' : '高级筛选' }}</el-button>
          <el-button plain @click="openMediaWizard('import')">导入</el-button>
          <el-button type="primary" @click="openMediaWizard('add')">添加</el-button>
        </div>
        <div v-if="advancedFilterOpen" class="filter-board">
          <div class="filter-row">
            <span>年份</span>
            <button :class="{ active: !libraryYearFilter }" @click="libraryYearFilter = ''">全部</button>
            <button
              v-for="year in currentYearOptions"
              :key="year"
              :class="{ active: Number(libraryYearFilter || 0) === Number(year) }"
              @click="libraryYearFilter = Number(year)"
            >{{ year }}</button>
          </div>
          <div class="filter-row">
            <span>类型</span>
            <button :class="{ active: !libraryMediaTypeFilter }" @click="libraryMediaTypeFilter = ''">全部</button>
            <button
              v-for="type in currentMediaTypeOptions"
              :key="type"
              :class="{ active: libraryMediaTypeFilter === type }"
              @click="libraryMediaTypeFilter = type"
            >{{ mediaTypeLabel(type) }}</button>
          </div>
          <div class="filter-row">
            <span>地区</span>
            <button :class="{ active: !libraryRegionFilter }" @click="libraryRegionFilter = ''">全部</button>
            <button
              v-for="region in currentRegionOptions"
              :key="region"
              :class="{ active: libraryRegionFilter === region }"
              @click="libraryRegionFilter = region"
            >{{ regionLabel(region) }}</button>
          </div>
          <div class="filter-row">
            <span>季度</span>
            <button :class="{ active: !libraryScopeFilter }" @click="libraryScopeFilter = ''">全部</button>
            <button
              v-for="scope in currentScopeOptions"
              :key="scope"
              :class="{ active: libraryScopeFilter === scope }"
              @click="libraryScopeFilter = scope"
            >{{ scope }}</button>
          </div>
          <div class="filter-row">
            <span>标签</span>
            <button :class="{ active: !libraryTagFilters.length }" @click="libraryTagFilters = []">全部</button>
            <button
              v-for="tag in currentTagOptions"
              :key="tag"
              :class="{ active: libraryTagFilters.includes(tag) }"
              @click="toggleLibraryTag(tag)"
            >{{ tag }}</button>
          </div>
        </div>
        <div class="anime-grid catalog-card-grid">
          <article v-for="item in filteredSeries" :key="item.id" class="anime-card catalog-card" @click="openEntry(item.id, 'library')">
            <div class="cover poster-cover">
              <img v-if="item.poster_url" :src="item.poster_url" />
              <span v-else>{{ entryTitle(item).slice(0, 2) || 'AN' }}</span>
            </div>
            <div class="anime-body">
              <h3>{{ entryTitle(item) }}</h3>
              <p>{{ item.entry_scope_label || item.entry_secondary_title || item.bangumi_id || 'Season 01' }}</p>
              <div class="tagline">
                <el-tag size="small" type="success">可观看 {{ watchableCount(item) }} 集</el-tag>
                <el-tag v-if="hasRecentUpdate(item)" size="small" type="primary">已更新</el-tag>
              </div>
              <div v-if="entryTags(item).length" class="mini-tag-row">
                <span v-for="tag in entryTags(item).slice(0, 3)" :key="tag">{{ tag }}</span>
              </div>
            </div>
          </article>
          <el-empty v-if="!filteredSeries.length" :description="`没有匹配的${currentMediaPageTitle}`" />
        </div>
      </section>

      <section v-if="view === 'settings'">
        <el-card>
          <template #header>全局设置</template>
          <el-form :model="settings" label-position="top" class="settings-form">
            <el-tabs>
              <el-tab-pane label="基础">
                <el-alert
                  type="info"
                  show-icon
                  :closable="false"
                  title="RSS 订阅入口在新番页；这里保留代理、补全、NFO 和命名规则等全局行为。"
                  class="settings-alert"
                />
                <el-form-item label="RSS 代理"><el-input v-model="settings.rss_proxy" placeholder="http://NAS_IP:20171" /></el-form-item>
                <div class="form-row">
                  <el-form-item label="补全本季"><el-switch v-model="settings.backfill_current_season" /></el-form-item>
                  <el-form-item label="自动生成 NFO"><el-switch v-model="settings.auto_generate_nfo" /></el-form-item>
                </div>
              </el-tab-pane>
              <el-tab-pane label="自动选择">
                <el-tabs tab-position="left" class="nested-settings-tabs">
                  <el-tab-pane label="动画">
                    <div class="priority-layout">
                      <PriorityList title="字幕组优先级" v-model="settings.subtitle_priority" placeholder="添加字幕组" />
                      <PriorityList title="分辨率优先级" v-model="settings.resolution_priority" placeholder="添加分辨率" />
                      <PriorityList title="主字幕语言优先级" v-model="settings.language_priority" placeholder="添加主字幕语言" />
                      <PriorityList title="副字幕语言优先级" v-model="settings.secondary_language_priority" placeholder="添加副字幕语言" />
                    </div>
                  </el-tab-pane>
                  <el-tab-pane label="电影">
                    <div class="selection-rule-grid">
                      <el-form-item label="优先画质"><el-input v-model="settings.movie_quality_priority" placeholder="2160p, 1080p, 720p" /></el-form-item>
                      <el-form-item label="优先来源"><el-input v-model="settings.movie_source_priority" placeholder="BluRay, WEB-DL, WebRip" /></el-form-item>
                      <el-form-item label="优先字幕"><el-input v-model="settings.movie_subtitle_priority" placeholder="简体, 繁体, 双语" /></el-form-item>
                    </div>
                  </el-tab-pane>
                  <el-tab-pane label="电视剧">
                    <div class="selection-rule-grid">
                      <el-form-item label="优先画质"><el-input v-model="settings.tv_quality_priority" placeholder="2160p, 1080p, 720p" /></el-form-item>
                      <el-form-item label="优先来源"><el-input v-model="settings.tv_source_priority" placeholder="WEB-DL, WebRip, HDTV" /></el-form-item>
                      <el-form-item label="优先字幕"><el-input v-model="settings.tv_subtitle_priority" placeholder="简体, 繁体, 双语" /></el-form-item>
                    </div>
                  </el-tab-pane>
                </el-tabs>
              </el-tab-pane>
              <el-tab-pane label="下载器">
                <div class="downloader-settings">
                  <draggable v-model="settings.downloaders" item-key="id" handle=".drag-handle" class="downloader-list">
                    <template #item="{ element, index }">
                      <div class="downloader-row">
                        <span class="drag-handle">⋮⋮</span>
                        <span class="rank">{{ index + 1 }}</span>
                        <el-select v-model="element.type" class="downloader-type">
                          <el-option label="PikPak rclone" value="pikpak_rclone" />
                          <el-option label="PikPak API" value="pikpak_api" />
                          <el-option label="aria2" value="aria2" />
                          <el-option label="qBittorrent" value="qb" />
                        </el-select>
                        <el-input v-model="element.name" placeholder="名称" />
                        <el-input v-model="element.remote_dir" placeholder="远端目录 / 临时目录" />
                        <el-input
                          v-if="['aria2', 'qb'].includes(element.type)"
                          v-model="element.rpc_url"
                          placeholder="RPC / Web UI 地址"
                        />
                        <el-input
                          v-if="element.type === 'aria2'"
                          v-model="element.token"
                          placeholder="aria2 token"
                          show-password
                        />
                        <template v-if="element.type === 'qb'">
                          <el-input v-model="element.username" placeholder="qB 用户名" />
                          <el-input v-model="element.password" placeholder="qB 密码" show-password />
                        </template>
                        <el-switch v-model="element.enabled" />
                        <el-button type="danger" link @click="removeDownloader(index)">删除</el-button>
                      </div>
                    </template>
                  </draggable>
                  <el-button plain @click="addDownloader">添加下载器</el-button>
                </div>
              </el-tab-pane>
              <el-tab-pane label="媒体库">
                <el-alert
                  type="info"
                  show-icon
                  :closable="false"
                  title="媒体根目录固定为容器内 media，系统会自动使用 media/anime、media/movies、media/tv。"
                  class="settings-alert"
                />
                <div class="media-root-grid">
                  <div><span>动画</span><code>media/anime</code></div>
                  <div><span>电影</span><code>media/movies</code></div>
                  <div><span>电视剧</span><code>media/tv</code></div>
                </div>
                <el-form-item label="动画命名模板"><el-input v-model="settings.episode_name_template" /></el-form-item>
                <el-form-item label="电影命名模板"><el-input v-model="settings.movie_name_template" /></el-form-item>
                <el-form-item label="电视剧命名模板"><el-input v-model="settings.tv_name_template" /></el-form-item>
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
      <button :class="{ active: view === 'movies' }" @click="view = 'movies'"><el-icon><Collection /></el-icon><b>电影</b></button>
      <button :class="{ active: view === 'tv' }" @click="view = 'tv'"><el-icon><Collection /></el-icon><b>剧集</b></button>
      <button :class="{ active: view === 'logs' }" @click="view = 'logs'"><el-icon><Document /></el-icon><b>日志</b></button>
      <button :class="{ active: view === 'settings' }" @click="view = 'settings'"><el-icon><Setting /></el-icon><b>设置</b></button>
    </nav>

    <el-drawer v-model="entryDrawerOpen" size="760px" :title="entryTitle(selectedEntryDetail?.entry) || (selectedEntryDomain === 'library' ? '媒体条目' : '新番设置')">
      <template v-if="selectedEntryDetail?.entry">
        <el-tabs class="entry-detail-tabs">
          <el-tab-pane label="信息">
            <div class="entry-info-head">
              <div class="cover poster-cover">
                <img v-if="selectedEntry.poster_url" :src="selectedEntry.poster_url" />
                <span v-else>{{ entryTitle(selectedEntry).slice(0, 2) || 'AN' }}</span>
              </div>
              <div>
                <h2>{{ entryTitle(selectedEntry) }}</h2>
                <p>{{ selectedEntry.entry_scope_label || selectedEntry.entry_secondary_title || selectedEntry.display_title || '-' }}</p>
                <div class="tagline">
                  <el-tag size="small">{{ mediaTypeLabel(selectedEntry.media_type) }}</el-tag>
                  <el-tag size="small" type="info">{{ regionLabel(selectedEntry.region) }}</el-tag>
                  <el-tag size="small" type="success">可观看 {{ watchableCount(selectedEntryStats) }} 集</el-tag>
                  <el-tag v-if="selectedEntryDomain === 'seasonal'" size="small" type="primary">追番中</el-tag>
                </div>
              </div>
            </div>
            <el-form :model="selectedEntry" label-position="top">
              <div class="form-row">
                <el-form-item label="中文标题"><el-input v-model="selectedEntry.title_cn" /></el-form-item>
                <el-form-item label="年份"><el-input-number v-model="selectedEntry.year" /></el-form-item>
              </div>
              <div class="form-row">
                <el-form-item label="Bangumi ID"><el-input v-model="selectedEntry.bangumi_id" /></el-form-item>
                <el-form-item label="TMDB ID"><el-input v-model="selectedEntry.tmdb_id" /></el-form-item>
              </div>
              <div class="form-row">
                <el-form-item label="媒体类型">
                  <el-select v-model="selectedEntry.media_type">
                    <el-option label="动画" value="anime" />
                    <el-option label="电影" value="movie" />
                    <el-option label="电视剧" value="tv" />
                  </el-select>
                </el-form-item>
                <el-form-item label="国家 / 地区">
                  <el-select v-model="selectedEntry.region" clearable>
                    <el-option label="日本" value="jp" />
                    <el-option label="中国" value="cn" />
                    <el-option label="欧美" value="us" />
                    <el-option label="韩国" value="kr" />
                    <el-option label="其他" value="other" />
                  </el-select>
                </el-form-item>
              </div>
            </el-form>
            <el-descriptions :column="2" border class="entry-meta-descriptions">
              <el-descriptions-item label="Bangumi ID">{{ selectedEntry.bangumi_id || '-' }}</el-descriptions-item>
              <el-descriptions-item label="TMDB ID">{{ selectedEntry.tmdb_id || '-' }}</el-descriptions-item>
              <el-descriptions-item label="年份 / 月份">{{ selectedEntry.year || '-' }} / {{ selectedEntry.month || '-' }}</el-descriptions-item>
              <el-descriptions-item label="追番状态">{{ selectedEntryDomain === 'seasonal' ? '追番中' : '普通媒体库条目' }}</el-descriptions-item>
              <el-descriptions-item label="别名" :span="2">{{ selectedEntry.aliases || selectedEntry.alias || '-' }}</el-descriptions-item>
              <el-descriptions-item label="标签" :span="2">
                <div class="mini-tag-row">
                  <span v-for="tag in entryTags(selectedEntry)" :key="tag">{{ tag }}</span>
                  <em v-if="!entryTags(selectedEntry).length">-</em>
                </div>
              </el-descriptions-item>
            </el-descriptions>
            <div class="drawer-actions">
              <el-button type="primary" @click="saveCurrentEntry">保存信息</el-button>
              <el-popconfirm
                v-if="selectedEntryDomain === 'seasonal'"
                title="归档后新番页不再显示，番剧库仍会保留该动画条目。确定归档？"
                @confirm="archiveCurrentEntry"
              >
                <template #reference>
                  <el-button plain>归档</el-button>
                </template>
              </el-popconfirm>
            </div>
          </el-tab-pane>
          <el-tab-pane label="集数资源">
            <el-table :data="entryResourceRows" height="520" empty-text="暂无集数资源">
              <el-table-column prop="episode_number" label="集" width="70" />
              <el-table-column prop="resource_title" label="当前选中资源" min-width="260" show-overflow-tooltip />
              <el-table-column prop="subtitle_group" label="字幕组" width="140" show-overflow-tooltip />
              <el-table-column prop="resolution" label="分辨率" width="100" />
              <el-table-column prop="language" label="语言" width="100" />
              <el-table-column label="字幕类型" width="110">
                <template #default="{ row }">{{ subtitleFormatText(row.subtitle_format) }}</template>
              </el-table-column>
              <el-table-column prop="subtitle_file" label="字幕文件" min-width="180" show-overflow-tooltip />
              <el-table-column label="已下载" width="90">
                <template #default="{ row }">
                  <el-tag :type="row.downloaded ? 'success' : 'info'" size="small">{{ row.downloaded ? '是' : '否' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="local_path" label="本地文件路径" min-width="260" show-overflow-tooltip />
              <el-table-column label="操作" width="110" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" plain @click="openEpisodeResourceEditor(row)">配置</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>

    <el-dialog v-model="episodeResourceDialogOpen" title="配置集数资源" width="620px">
      <el-form :model="episodeResourceForm" label-position="top">
        <div class="form-row">
          <el-form-item label="集数"><el-input v-model="episodeResourceForm.episode_number" disabled /></el-form-item>
          <el-form-item label="分辨率"><el-input v-model="episodeResourceForm.resolution" placeholder="1080p" /></el-form-item>
        </div>
        <el-form-item label="当前资源标题"><el-input v-model="episodeResourceForm.title" /></el-form-item>
        <div class="form-row">
          <el-form-item label="字幕组"><el-input v-model="episodeResourceForm.subtitle_group" /></el-form-item>
          <el-form-item label="语言"><el-input v-model="episodeResourceForm.language" /></el-form-item>
        </div>
        <div class="form-row">
          <el-form-item label="字幕类型">
            <el-select v-model="episodeResourceForm.subtitle_format" clearable>
              <el-option label="内嵌" value="embedded" />
              <el-option label="内封" value="muxed" />
              <el-option label="外挂" value="external" />
            </el-select>
          </el-form-item>
          <el-form-item label="是否内嵌"><el-switch v-model="episodeResourceForm.embedded" /></el-form-item>
        </div>
        <el-form-item label="外挂字幕文件"><el-input v-model="episodeResourceForm.subtitle_path" placeholder="留空表示无外挂字幕" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="episodeResourceDialogOpen = false">取消</el-button>
        <el-button type="primary" @click="saveEpisodeResource">保存配置</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="rssDialogOpen" title="RSS 订阅" width="760px">
      <div class="rss-dialog-layout">
        <el-form :model="rssForm" label-position="top" class="rss-form">
          <el-form-item label="订阅名称"><el-input v-model="rssForm.name" placeholder="例如：Mikan 追番" /></el-form-item>
          <el-form-item label="RSS 地址"><el-input v-model="rssForm.url" placeholder="https://mikanani.me/RSS/..." /></el-form-item>
          <div class="form-row">
            <el-form-item label="订阅类型">
              <el-select v-model="rssForm.kind">
                <el-option label="Mikan" value="mikan" />
              </el-select>
            </el-form-item>
            <el-form-item label="启用"><el-switch v-model="rssForm.enabled" /></el-form-item>
          </div>
        </el-form>
        <div class="rss-subscription-list" v-loading="rssLoading">
          <div v-for="item in rssSubscriptions" :key="item.id" class="rss-subscription-row">
            <div>
              <strong>{{ item.name || 'Mikan RSS' }}</strong>
              <span>{{ item.kind }} · {{ Number(item.enabled || 0) ? '启用' : '停用' }}</span>
              <code>{{ item.url }}</code>
            </div>
            <div class="rss-row-actions">
              <el-button size="small" plain @click="editRssSubscription(item)">编辑</el-button>
              <el-button size="small" type="danger" plain @click="deleteRssSubscription(item.id)">删除</el-button>
            </div>
          </div>
          <el-empty v-if="!rssSubscriptions.length && !rssLoading" description="暂无 RSS 订阅" />
        </div>
      </div>
      <template #footer>
        <el-button v-if="rssEditingId" plain @click="resetRssForm">新增模式</el-button>
        <el-button @click="rssDialogOpen = false">取消</el-button>
        <el-button type="primary" @click="saveRssSubscription">{{ rssEditingId ? '保存修改' : '保存订阅' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="mediaWizardOpen" :title="mediaWizardTitle" width="760px">
      <el-steps :active="mediaWizardStep" finish-status="success" align-center>
        <el-step title="选择来源" />
        <el-step title="匹配作品" />
        <el-step title="集数与字幕" />
        <el-step title="收录" />
      </el-steps>
      <div class="wizard-panel">
        <template v-if="mediaWizardStep === 0">
          <el-upload drag action="#" :auto-upload="false" multiple>
            <p>选择本地目录或文件</p>
            <small>选择要收录的媒体文件，系统按集数与字幕整理。</small>
          </el-upload>
          <el-input v-if="mediaWizardMode === 'add'" v-model="mediaWizardSeed" placeholder="Bangumi ID / TMDB ID / 作品名 / 磁力链接" />
        </template>
        <template v-else-if="mediaWizardStep === 1">
          <div class="wizard-form-grid">
            <el-input v-model="mediaWizardDraft.title" placeholder="作品标题" />
            <el-input v-model="mediaWizardDraft.bangumi_id" placeholder="Bangumi ID" />
            <el-input v-model="mediaWizardDraft.tmdb_id" placeholder="TMDB ID" />
            <el-input-number v-model="mediaWizardDraft.year" :min="0" placeholder="年份" />
          </div>
        </template>
        <template v-else-if="mediaWizardStep === 2">
          <div class="wizard-form-grid">
            <el-input-number v-model="mediaWizardDraft.episode_number" :min="0" placeholder="集数" />
            <el-input v-model="mediaWizardDraft.resource_title" placeholder="资源标题 / 文件名" />
            <el-input v-model="mediaWizardDraft.subtitle_group" placeholder="字幕组" />
            <el-select v-model="mediaWizardDraft.subtitle_format" clearable placeholder="字幕类型">
              <el-option label="内嵌" value="embedded" />
              <el-option label="内封" value="muxed" />
              <el-option label="外挂" value="external" />
            </el-select>
          </div>
        </template>
        <template v-else>
          <div class="wizard-confirm-panel">
            <strong>{{ mediaWizardDraft.title || currentMediaPageTitle }}</strong>
            <span>{{ mediaTypeLabel(currentMediaType) }} · {{ mediaWizardDraft.year || '年份未知' }}</span>
            <span>第 {{ mediaWizardDraft.episode_number || '?' }} 集 · {{ mediaWizardDraft.resource_title || '未填写资源' }}</span>
          </div>
        </template>
      </div>
      <template #footer>
        <el-button @click="mediaWizardOpen = false">关闭</el-button>
        <el-button :disabled="mediaWizardStep <= 0" @click="mediaWizardStep -= 1">上一步</el-button>
        <el-button type="primary" :disabled="mediaWizardStep >= 3" @click="mediaWizardStep += 1">下一步</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import draggable from 'vuedraggable'
import { ElMessage } from 'element-plus'
import { Calendar, Collection, DataBoard, Document, Refresh, Search, Setting } from '@element-plus/icons-vue'
import { deleteAction, getAction, getDashboard, getDiagnostics, getLibraryItem, getSeasonalItem, getSettings, postAction, putAction, saveLibraryItem, saveSeasonalItem, saveSettings } from './api'
import { APP_BUILD, APP_VERSION } from './version'

const view = ref('dashboard')
const appVersion = APP_VERSION
const appBuild = APP_BUILD
const selectedConsoleSection = ref('')
const logKeyword = ref('')
const loading = ref(false)
const savingSettings = ref(false)
const autoRefresh = ref(true)
const liveConnected = ref(false)
const consoleNavMode = ref('队列')
const calendarWeek = ref('')
const advancedFilterOpen = ref(false)
const rssDialogOpen = ref(false)
const rssLoading = ref(false)
const rssEditingId = ref(0)
const mediaWizardOpen = ref(false)
const mediaWizardMode = ref('import')
const mediaWizardStep = ref(0)
const mediaWizardSeed = ref('')
const episodeResourceDialogOpen = ref(false)
let refreshTimer = null
let dashboardStream = null
let streamRetryTimer = null
const keyword = ref('')
const libraryYearFilter = ref('')
const libraryScopeFilter = ref('')
const libraryMediaTypeFilter = ref('')
const libraryRegionFilter = ref('')
const libraryTagFilters = ref([])
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
const rssForm = reactive({
  name: '',
  url: '',
  kind: 'mikan',
  enabled: true,
})
const rssSubscriptions = ref([])
const episodeResourceForm = reactive({
  episode_id: 0,
  resource_id: 0,
  subtitle_id: 0,
  episode_number: '',
  title: '',
  subtitle_group: '',
  resolution: '',
  language: '',
  subtitle_format: '',
  subtitle_path: '',
  embedded: false,
})
const mediaWizardDraft = reactive({
  title: '',
  bangumi_id: '',
  tmdb_id: '',
  year: 0,
  episode_number: 0,
  resource_title: '',
  subtitle_group: '',
  subtitle_format: '',
})
const scheduledJobForm = reactive({
  enabled: true,
  interval_minutes: 1,
})

const pageTitle = computed(() => ({
  dashboard: '控制台',
  seasonal: '新番',
  calendar: '日历',
  library: '番剧',
  movies: '电影',
  tv: '电视剧',
  logs: '日志与维护',
  settings: '设置中心'
}[view.value]))

const seasonalRows = computed(() => dashboard.seasonal_items || [])
const libraryRows = computed(() => dashboard.library_items || [])
const isMediaCatalogView = computed(() => ['library', 'movies', 'tv'].includes(view.value))
const currentMediaType = computed(() => ({
  library: 'anime',
  movies: 'movie',
  tv: 'tv',
}[view.value] || 'anime'))
const currentMediaPageTitle = computed(() => ({
  library: '番剧',
  movies: '电影',
  tv: '电视剧',
}[view.value] || '媒体'))
const currentCatalogSourceRows = computed(() => {
  if (!isMediaCatalogView.value) return seasonalRows.value
  return libraryRows.value.filter(item => belongsToCurrentMediaPage(item))
})
const currentYearOptions = computed(() => {
  const values = new Set()
  for (const item of currentCatalogSourceRows.value) {
    const year = Number(item.year || 0)
    if (year > 0) values.add(year)
  }
  return Array.from(values).sort((a, b) => b - a)
})
const currentMediaTypeOptions = computed(() => {
  const values = new Set()
  for (const item of currentCatalogSourceRows.value) {
    const type = entryMediaType(item)
    if (type) values.add(type)
  }
  return Array.from(values).sort((a, b) => mediaTypeLabel(a).localeCompare(mediaTypeLabel(b)))
})
const currentRegionOptions = computed(() => {
  const values = new Set()
  for (const item of currentCatalogSourceRows.value) {
    if (item.region) values.add(item.region)
  }
  return Array.from(values).sort((a, b) => regionLabel(a).localeCompare(regionLabel(b)))
})
const currentScopeOptions = computed(() => {
  const values = new Set()
  for (const item of currentCatalogSourceRows.value) {
    const scope = item.entry_scope_label || item.entry_badge_text || ''
    if (scope) values.add(scope)
  }
  return Array.from(values).sort((a, b) => String(a).localeCompare(String(b)))
})
const currentTagOptions = computed(() => {
  const counts = new Map()
  for (const item of currentCatalogSourceRows.value) {
    for (const tag of entryTags(item)) {
      counts.set(tag, (counts.get(tag) || 0) + 1)
    }
  }
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 36)
    .map(item => item[0])
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
  return section.kind === 'queue'
}))
const queueListSections = computed(() => queueConsoleSections.value.filter(section => section.kind === 'queue'))
const scheduledConsoleSections = computed(() => (dashboard.console_sections || []).filter(section => section.kind === 'scheduled'))
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
  return dashboard.queue_details?.[section.queue_key]?.items || []
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
const mediaWizardTitle = computed(() => {
  const action = mediaWizardMode.value === 'add' ? '添加' : '导入'
  return `${action}${currentMediaPageTitle.value}`
})
const entryResourceRows = computed(() => {
  const detail = selectedEntryDetail.value || {}
  const rows = new Map()
  const episodeIds = new Map()
  for (const episode of detail.episodes || []) {
    episodeIds.set(Number(episode.episode_number || 0), Number(episode.id || 0))
  }
  for (const resource of detail.episode_resources || []) {
    const episode = Number(resource.episode_number || 0)
    const key = episode > 0 ? `episode:${episode}` : `resource:${resource.id}`
    rows.set(key, {
      key,
      episode_id: Number(resource.episode_id || episodeIds.get(episode) || 0),
      resource_id: Number(resource.id || 0),
      subtitle_id: 0,
      episode_number: episode || '-',
      release_id: resource.release_id || 0,
      resource_title: resource.title || resource.source_ref || '-',
      subtitle_group: resource.subtitle_group || '-',
      resolution: resource.resolution || '-',
      language: resource.language || '-',
      subtitle_format: resource.subtitle_format || '',
      subtitle_file: '-',
      downloaded: Boolean(resource.downloaded) || Boolean(resource.local_path),
      local_path: resource.local_path || '',
      selected: Boolean(resource.selected),
    })
  }
  for (const subtitle of detail.episode_subtitles || []) {
    const episode = Number(subtitle.episode_number || 0)
    const key = episode > 0 ? `episode:${episode}` : `subtitle:${subtitle.id}`
    const row = rows.get(key)
    if (!row) continue
    row.subtitle_id = Number(subtitle.id || 0)
    row.subtitle_file = subtitle.subtitle_path || row.subtitle_file
    row.subtitle_format = subtitle.subtitle_format || row.subtitle_format
    row.language = subtitle.language || row.language
  }
  for (const release of detail.releases || []) {
    const episode = Number(release.episode_number || 0)
    const key = episode > 0 ? `episode:${episode}` : `release:${release.id}`
    const previous = rows.get(key)
    if (previous && (previous.selected || !release.selected)) continue
    rows.set(key, {
      key,
      episode_id: Number(episodeIds.get(episode) || 0),
      resource_id: 0,
      subtitle_id: 0,
      episode_number: episode || '-',
      release_id: release.id,
      resource_title: release.title || release.guid || '-',
      subtitle_group: release.subtitle_group || '-',
      resolution: release.resolution || '-',
      language: release.language || '-',
      subtitle_format: release.subtitle_format || '',
      subtitle_file: '-',
      downloaded: false,
      local_path: '',
      selected: Boolean(release.selected),
    })
  }
  for (const artifact of detail.download_artifacts || []) {
    const episode = Number(artifact.episode_number || 0)
    const key = episode > 0 ? `episode:${episode}` : `artifact:${artifact.id}`
    const row = rows.get(key) || {
      key,
      episode_id: Number(episodeIds.get(episode) || 0),
      resource_id: 0,
      subtitle_id: 0,
      episode_number: episode || '-',
      resource_title: artifact.remote_path || artifact.provider_file_id || '-',
      subtitle_group: '-',
      resolution: '-',
      language: '-',
      subtitle_format: '',
      subtitle_file: '-',
      downloaded: false,
      local_path: '',
    }
    row.resource_title = row.resource_title === '-' ? (artifact.remote_path || artifact.provider_file_id || '-') : row.resource_title
    rows.set(key, row)
  }
  for (const asset of detail.local_assets || []) {
    const episode = Number(asset.episode_number || 0)
    const key = episode > 0 ? `episode:${episode}` : `local:${asset.id}`
    const row = rows.get(key) || {
      key,
      episode_id: Number(episodeIds.get(episode) || 0),
      resource_id: 0,
      subtitle_id: 0,
      episode_number: episode || '-',
      resource_title: asset.local_path || '-',
      subtitle_group: '-',
      resolution: '-',
      language: '-',
      subtitle_format: '',
      subtitle_file: '-',
      downloaded: false,
      local_path: '',
    }
    row.downloaded = String(asset.status || '').toLowerCase() === 'synced' || Boolean(asset.local_path)
    row.local_path = asset.local_path || row.local_path || ''
    if (asset.subtitle_path) row.subtitle_file = asset.subtitle_path
    rows.set(key, row)
  }
  return Array.from(rows.values()).sort((a, b) => Number(a.episode_number || 0) - Number(b.episode_number || 0))
})

const filteredSeries = computed(() => {
  const text = keyword.value.toLowerCase()
  const source = currentCatalogSourceRows.value
  return source.filter(item => {
    const matched = !text || `${item.entry_display_title || item.display_title || item.title_cn} ${item.work_display_title || item.work_title || item.title_root || ''} ${item.entry_scope_label || ''} ${item.bangumi_id || ''} ${item.tmdb_id || ''}`.toLowerCase().includes(text)
    if (!matched) return false
    if (libraryMediaTypeFilter.value && entryMediaType(item) !== String(libraryMediaTypeFilter.value)) return false
    if (libraryRegionFilter.value && String(item.region || '') !== String(libraryRegionFilter.value)) return false
    if (libraryYearFilter.value && Number(item.year || 0) !== Number(libraryYearFilter.value)) return false
    if (libraryScopeFilter.value && String(item.entry_scope_label || item.entry_badge_text || '') !== String(libraryScopeFilter.value)) return false
    if (libraryTagFilters.value.length) {
      const tags = entryTags(item)
      if (!libraryTagFilters.value.every(tag => tags.includes(tag))) return false
    }
    return true
  })
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

function entryMediaType(item) {
  const value = String(item?.media_type || 'anime').toLowerCase()
  if (value === 'movie' || value === 'film') return 'movie'
  if (value === 'tv' || value === 'series' || value === 'drama') return 'tv'
  return 'anime'
}

function belongsToCurrentMediaPage(item) {
  const type = entryMediaType(item)
  if (view.value === 'movies') return type === 'movie'
  if (view.value === 'tv') return type === 'tv'
  return type === 'anime'
}

function entryTitle(item) {
  if (!item) return ''
  return item.work_display_title
    || item.entry_display_title
    || item.display_title
    || item.title_cn
    || item.work_title
    || item.title_root
    || '未命名条目'
}

function watchableCount(item) {
  if (!item) return 0
  const runtimeReady = entryRuntime(item).ready_count
  return Math.max(
    Number(item.local_asset_count || 0),
    Number(item.downloaded_count || 0),
    Number(runtimeReady || 0),
  )
}

function parseDateValue(value) {
  if (!value) return 0
  const time = new Date(value).getTime()
  return Number.isFinite(time) ? time : 0
}

function hasRecentUpdate(item) {
  const entryId = Number(item?.id || item?.entry_id || 0)
  const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000
  const direct = [
    item?.updated_at,
    item?.synced_at,
    item?.latest_release_at,
    item?.last_release_at,
    item?.published_at,
  ].some(value => parseDateValue(value) >= cutoff)
  if (direct) return true
  return [...(dashboard.seasonal_update_calendar || []), ...(dashboard.seasonal_sync_calendar || [])].some(row => {
    if (Number(row.entry_id || 0) !== entryId) return false
    return parseDateValue(row.updated_at || row.synced_at || row.published_at) >= cutoff
  })
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
  if (!Number(job.enabled ?? 1)) return '关闭'
  if (job.last_status === 'failed') return '失败'
  if (job.last_status === 'running') return '运行'
  const minutes = Number(job.interval_minutes || 0)
  return minutes > 0 ? `${minutes} 分` : '已配置'
}

function scheduledBadgeType(jobKey) {
  const job = (dashboard.scheduled_jobs || []).find(item => item.job_key === jobKey)
  if (!job) return 'info'
  if (!Number(job.enabled ?? 1)) return 'info'
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
  const key = String(value || '').toLowerCase()
  if (key === 'embedded' || key === 'hardsub' || key === 'burned') return '内嵌'
  if (key === 'muxed' || key === 'softsub' || key === 'internal') return '内封'
  if (key === 'external' || key === 'sidecar') return '外挂'
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
  if (queue.queue_state === 'ready' || Number(queue.pending || 0) > 0) return '可执行'
  return '空闲'
}

function queuePendingHint(queue) {
  const key = String(queue?.key || '')
  if (key === 'rss') return '这里只显示最近的 RSS 候选；Mikan、元数据、选集、下载到本地由任务链推进。'
  if (key === 'download') return '待处理表示已选中发布，等待下载器提交、轮询完成并整理到本地媒体库。'
  if (key === 'local_sync') return '待处理表示已有下载产物，等待补整理到本地媒体库。'
  if (key === 'selection') return '待处理表示元数据已完成，等待按规则自动选择发布。'
  if (key === 'processor') return '这里显示流水线统一处理器任务，扫描后可直接看每条数据卡在 RSS、匹配、元数据、整合、下载还是 NFO。'
  if (key === 'backfill') return '待处理表示番剧已入库，等待去 Mikan 番组页补抓历史条目。'
  if (key === 'metadata') return '待处理表示已拿到 Bangumi 线索，等待补全正式元数据。'
  if (key === 'mikan_match') return '待处理表示 RSS 候选已入队，等待解析对应的 Mikan/Bangumi 关联。'
  return '任务已入队，等待执行。'
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
  const source = consoleNavMode.value === '定时任务' ? scheduledConsoleSections.value : queueListSections.value
  if (!source.some(item => item.key === selectedConsoleSection.value)) {
    selectedConsoleSection.value = ''
  }
}

async function reload() {
  if (loading.value) return
  loading.value = true
  try {
    applyDashboard(await getDashboard())
    Object.assign(settings, await getSettings())
    normalizeSettingsShape()
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
  }, 5000)
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

function syncScheduledJobForm(job = selectedScheduledJob.value) {
  scheduledJobForm.enabled = Boolean(Number(job?.enabled ?? 1))
  scheduledJobForm.interval_minutes = Math.max(1, Number(job?.interval_minutes || 1))
}

async function saveScheduledJob() {
  const job = selectedScheduledJob.value
  if (!job?.job_key) return
  try {
    await putAction(`/scheduled-jobs/${job.job_key}`, {
      enabled: scheduledJobForm.enabled,
      interval_minutes: scheduledJobForm.interval_minutes,
    })
    ElMessage.success('定时配置已保存')
    await reload()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

async function loadRssSubscriptions() {
  rssLoading.value = true
  try {
    const result = await getAction('/rss-subscriptions')
    rssSubscriptions.value = result.items || []
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  } finally {
    rssLoading.value = false
  }
}

function resetRssForm() {
  rssEditingId.value = 0
  rssForm.name = 'Mikan RSS'
  rssForm.url = settings.rss_url || ''
  rssForm.kind = 'mikan'
  rssForm.enabled = true
}

async function openRssDialog() {
  resetRssForm()
  rssDialogOpen.value = true
  await loadRssSubscriptions()
}

function editRssSubscription(item) {
  rssEditingId.value = Number(item.id || 0)
  rssForm.name = item.name || 'Mikan RSS'
  rssForm.url = item.url || ''
  rssForm.kind = item.kind || 'mikan'
  rssForm.enabled = Boolean(Number(item.enabled ?? 1))
}

async function saveRssSubscription() {
  if (!rssForm.url.trim()) {
    ElMessage.warning('RSS 地址不能为空')
    return
  }
  try {
    const payload = {
      name: rssForm.name.trim() || 'Mikan RSS',
      url: rssForm.url.trim(),
      kind: rssForm.kind || 'mikan',
      enabled: rssForm.enabled,
    }
    if (rssEditingId.value) {
      await putAction(`/rss-subscriptions/${rssEditingId.value}`, payload)
    } else {
      await postAction('/rss-subscriptions', payload)
    }
    ElMessage.success('RSS 订阅已保存')
    resetRssForm()
    await loadRssSubscriptions()
    await reload()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

async function deleteRssSubscription(id) {
  try {
    await deleteAction(`/rss-subscriptions/${id}`)
    ElMessage.success('RSS 订阅已删除')
    if (rssEditingId.value === Number(id)) resetRssForm()
    await loadRssSubscriptions()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

function openEpisodeResourceEditor(row) {
  if (!Number(row.episode_id || 0)) {
    ElMessage.warning('该资源还没有可编辑的集数记录')
    return
  }
  episodeResourceForm.episode_id = Number(row.episode_id || 0)
  episodeResourceForm.resource_id = Number(row.resource_id || 0)
  episodeResourceForm.subtitle_id = Number(row.subtitle_id || 0)
  episodeResourceForm.episode_number = String(row.episode_number || '')
  episodeResourceForm.title = row.resource_title === '-' ? '' : String(row.resource_title || '')
  episodeResourceForm.subtitle_group = row.subtitle_group === '-' ? '' : String(row.subtitle_group || '')
  episodeResourceForm.resolution = row.resolution === '-' ? '' : String(row.resolution || '')
  episodeResourceForm.language = row.language === '-' ? '' : String(row.language || '')
  episodeResourceForm.subtitle_format = String(row.subtitle_format || '')
  episodeResourceForm.subtitle_path = row.subtitle_file === '-' ? '' : String(row.subtitle_file || '')
  episodeResourceForm.embedded = ['embedded', 'hardsub', 'burned'].includes(String(row.subtitle_format || '').toLowerCase())
  episodeResourceDialogOpen.value = true
}

async function saveEpisodeResource() {
  const episodeId = Number(episodeResourceForm.episode_id || 0)
  if (!episodeId) {
    ElMessage.warning('缺少集数记录，无法保存')
    return
  }
  try {
    await putAction(`/episodes/${episodeId}/resource`, {
      resource_id: episodeResourceForm.resource_id,
      title: episodeResourceForm.title,
      subtitle_group: episodeResourceForm.subtitle_group,
      resolution: episodeResourceForm.resolution,
      language: episodeResourceForm.language,
      subtitle_format: episodeResourceForm.subtitle_format,
      selected: true,
    })
    if (episodeResourceForm.subtitle_path || episodeResourceForm.subtitle_id) {
      await putAction(`/episodes/${episodeId}/subtitle`, {
        subtitle_id: episodeResourceForm.subtitle_id,
        language: episodeResourceForm.language,
        subtitle_format: episodeResourceForm.subtitle_format,
        subtitle_path: episodeResourceForm.subtitle_path,
        embedded: episodeResourceForm.embedded,
        selected: true,
      })
    }
    ElMessage.success('集数资源已保存')
    episodeResourceDialogOpen.value = false
    if (selectedEntry.value?.id) {
      await openEntry(selectedEntry.value.id, selectedEntryDomain.value)
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

function openMediaWizard(mode) {
  mediaWizardMode.value = mode
  mediaWizardStep.value = 0
  mediaWizardSeed.value = ''
  mediaWizardDraft.title = ''
  mediaWizardDraft.bangumi_id = ''
  mediaWizardDraft.tmdb_id = ''
  mediaWizardDraft.year = 0
  mediaWizardDraft.episode_number = 0
  mediaWizardDraft.resource_title = ''
  mediaWizardDraft.subtitle_group = ''
  mediaWizardDraft.subtitle_format = ''
  mediaWizardOpen.value = true
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
    normalizeSettingsShape()
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

function normalizeSettingsShape() {
  if (!Array.isArray(settings.downloaders)) settings.downloaders = []
  settings.backfill_current_season = Boolean(settings.backfill_current_season)
  settings.auto_generate_nfo = settings.auto_generate_nfo !== false
  settings.movie_name_template = settings.movie_name_template || '{title_cn} ({year})/{title_cn} ({year})'
  settings.tv_name_template = settings.tv_name_template || '{title_cn} ({year})/Season {season:02d}/{title_cn} - S{season:02d}E{episode:02d}'
  settings.episode_name_template = settings.episode_name_template || '{title_cn} - S{season:02d}E{episode:02d} - {episode_title}'
}

function addDownloader() {
  normalizeSettingsShape()
  settings.downloaders = [
    ...settings.downloaders,
    {
      id: `downloader-${Date.now()}`,
      name: 'PikPak',
      type: 'pikpak_rclone',
      remote_dir: '/Temp',
      rpc_url: '',
      token: '',
      username: '',
      password: '',
      enabled: true,
      max_attempts: 3,
    },
  ]
}

function removeDownloader(index) {
  normalizeSettingsShape()
  settings.downloaders = settings.downloaders.filter((_, i) => i !== index)
}

async function archiveCurrentEntry() {
  if (selectedEntryDomain.value !== 'seasonal') return
  const id = selectedEntry.value?.id
  if (!id) return
  const result = await deleteAction(`/seasonal/${id}`)
  if (result.status === 'not_found' || result.status === 'invalid_domain') {
    ElMessage.warning(result.message || '条目不存在')
  } else {
    ElMessage.success('已归档，新番页不再显示')
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

watch(autoRefresh, () => {
  if (autoRefresh.value) {
    startDashboardStream()
  } else {
    stopDashboardStream()
    stopAutoRefresh()
  }
})
watch(entryDrawerOpen, startAutoRefresh)
watch(consoleNavMode, value => {
  selectedConsoleSection.value = ''
})
watch(selectedScheduledJob, job => {
  syncScheduledJobForm(job)
})
watch(view, value => {
  if (['seasonal', 'library', 'movies', 'tv'].includes(value)) {
    libraryYearFilter.value = ''
    libraryScopeFilter.value = ''
    libraryMediaTypeFilter.value = ''
    libraryRegionFilter.value = ''
    libraryTagFilters.value = []
    advancedFilterOpen.value = false
  }
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



