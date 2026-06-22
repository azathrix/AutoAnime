<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <img class="brand-logo" src="/anitrack-logo.png" alt="AniTrack logo" />
        <div>
          <strong class="brand-wordmark">AniTrack</strong>
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
    </aside>

    <main class="main">
      <header class="hero">
        <div>
          <p class="eyebrow">RSS · Downloader · Media Library</p>
          <h1>{{ pageTitle }}</h1>
          <p class="hero-sub">扫描订阅、自动选集并整理到本地媒体库。<span class="build-version">v{{ appVersion }} · {{ appBuild }}</span></p>
        </div>
      </header>

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

      <section v-if="view === 'seasonal'" class="library seasonal-page media-page">
        <div class="toolbar media-toolbar">
          <el-input v-model="keyword" clearable placeholder="搜索新番条目、Bangumi ID、标题" />
          <div class="toolbar-spacer"></div>
          <el-button plain @click="advancedFilterOpen = !advancedFilterOpen">{{ advancedFilterOpen ? '收起筛选' : '高级筛选' }}</el-button>
          <el-button type="primary" :icon="Search" :disabled="scanRunning" @click="runAction('/scan')">扫描全部</el-button>
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
            <span>月份</span>
            <button :class="{ active: !libraryMonthFilter }" @click="libraryMonthFilter = ''">全部</button>
            <button
              v-for="month in currentMonthOptions"
              :key="month"
              :class="{ active: Number(libraryMonthFilter || 0) === Number(month) }"
              @click="libraryMonthFilter = Number(month)"
            >{{ month }} 月</button>
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
              <span v-else>{{ cardInitials(item) }}</span>
            </div>
            <div class="anime-body">
              <h3>{{ entryTitle(item) }}</h3>
              <p>{{ cardSubtitle(item) }}</p>
              <div class="tagline">
                <el-tag size="small" type="success">可观看 {{ watchableCount(item) }} 集</el-tag>
                <el-tag v-if="hasRecentUpdate(item)" size="small" type="primary">已更新</el-tag>
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
                <el-tag size="small" :type="item.synced ? 'success' : 'warning'">{{ item.synced ? '已下载' : '已更新' }}</el-tag>
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
          <el-button type="primary" @click="openMediaWizard">收录{{ currentMediaPageTitle }}</el-button>
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
            <span>月份</span>
            <button :class="{ active: !libraryMonthFilter }" @click="libraryMonthFilter = ''">全部</button>
            <button
              v-for="month in currentMonthOptions"
              :key="month"
              :class="{ active: Number(libraryMonthFilter || 0) === Number(month) }"
              @click="libraryMonthFilter = Number(month)"
            >{{ month }} 月</button>
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
          <article v-for="item in filteredSeries" :key="item.id" class="anime-card catalog-card" @click="openEntry(item.id, 'library', entryMediaType(item))">
            <div class="cover poster-cover">
              <img v-if="item.poster_url" :src="item.poster_url" />
              <span v-else>{{ cardInitials(item) }}</span>
            </div>
            <div class="anime-body">
              <h3>{{ entryTitle(item) }}</h3>
              <p>{{ cardSubtitle(item) }}</p>
              <div class="tagline">
                <el-tag size="small" type="success">可观看 {{ watchableCount(item) }} 集</el-tag>
                <el-tag v-if="hasRecentUpdate(item)" size="small" type="primary">已更新</el-tag>
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
                    <div class="settings-section-toolbar">
                      <div>
                        <strong>动画自动选集</strong>
                        <span>用于新番和番剧的资源优先级</span>
                      </div>
                      <el-button plain @click="resetSelectionRules('anime')">重置动画规则</el-button>
                    </div>
                    <div class="priority-layout">
                      <PriorityList title="字幕组优先级" v-model="settings.subtitle_priority" placeholder="添加字幕组" />
                      <PriorityList title="分辨率优先级" v-model="settings.resolution_priority" placeholder="添加分辨率" />
                      <PriorityList title="主字幕语言优先级" v-model="settings.language_priority" placeholder="添加主字幕语言" />
                      <PriorityList title="副字幕语言优先级" v-model="settings.secondary_language_priority" placeholder="添加副字幕语言" />
                    </div>
                  </el-tab-pane>
                  <el-tab-pane label="电影">
                    <div class="settings-section-toolbar">
                      <div>
                        <strong>电影自动选集</strong>
                        <span>只影响电影页面和电影导入资源</span>
                      </div>
                      <el-button plain @click="resetSelectionRules('movie')">重置电影规则</el-button>
                    </div>
                    <div class="priority-layout">
                      <PriorityList title="画质优先级" v-model="settings.movie_quality_priority" placeholder="添加画质，如 2160p" />
                      <PriorityList title="来源优先级" v-model="settings.movie_source_priority" placeholder="添加来源，如 BluRay" />
                      <PriorityList title="字幕优先级" v-model="settings.movie_subtitle_priority" placeholder="添加字幕，如 简体" />
                    </div>
                  </el-tab-pane>
                  <el-tab-pane label="电视剧">
                    <div class="settings-section-toolbar">
                      <div>
                        <strong>电视剧自动选集</strong>
                        <span>只影响电视剧页面和电视剧导入资源</span>
                      </div>
                      <el-button plain @click="resetSelectionRules('tv')">重置电视剧规则</el-button>
                    </div>
                    <div class="priority-layout">
                      <PriorityList title="画质优先级" v-model="settings.tv_quality_priority" placeholder="添加画质，如 1080p" />
                      <PriorityList title="来源优先级" v-model="settings.tv_source_priority" placeholder="添加来源，如 WEB-DL" />
                      <PriorityList title="字幕优先级" v-model="settings.tv_subtitle_priority" placeholder="添加字幕，如 双语" />
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
                        <div class="downloader-fields">
                          <el-select v-model="element.type" class="downloader-type">
                            <el-option label="PikPak rclone" value="pikpak_rclone" />
                            <el-option label="PikPak API" value="pikpak_api" />
                            <el-option label="aria2" value="aria2" />
                            <el-option label="qBittorrent" value="qb" />
                          </el-select>
                          <el-input v-model="element.name" placeholder="名称" />
                          <el-input v-model="element.remote_dir" placeholder="远端目录 / 临时目录" />
                          <template v-if="element.type === 'pikpak_rclone'">
                            <el-input v-model="element.rclone_remote" placeholder="rclone remote，例如 pikpak" />
                            <el-input v-model="element.rclone_config_path" placeholder="rclone.conf 路径" />
                            <el-input v-model="element.rclone_command" placeholder="rclone 命令" />
                            <el-input v-model="element.username" placeholder="PikPak 用户名，可用于初始化 rclone" />
                            <el-input v-model="element.password" placeholder="PikPak 密码" show-password />
                          </template>
                          <template v-if="element.type === 'pikpak_api'">
                            <el-select v-model="element.auth_mode" placeholder="认证方式">
                              <el-option label="Token" value="token" />
                              <el-option label="账号密码" value="password" />
                            </el-select>
                            <template v-if="element.auth_mode === 'password'">
                              <el-input v-model="element.username" placeholder="PikPak 用户名" />
                              <el-input v-model="element.password" placeholder="PikPak 密码" show-password />
                            </template>
                            <template v-else>
                              <el-input v-model="element.access_token" placeholder="access token" show-password />
                              <el-input v-model="element.refresh_token" placeholder="refresh token" show-password />
                            </template>
                            <el-input v-model="element.proxy" placeholder="代理，可选" />
                          </template>
                          <template v-if="['aria2', 'qb'].includes(element.type)">
                            <el-input v-model="element.rpc_url" placeholder="RPC / Web UI 地址" />
                          </template>
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
                        </div>
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
                  <el-tag v-if="selectedEntryDomain === 'seasonal'" size="small" type="success">可观看 {{ watchableCount(selectedEntryStats) }} 集</el-tag>
                  <el-tag v-if="selectedEntryDomain === 'seasonal'" size="small" type="primary">追番中</el-tag>
                </div>
              </div>
            </div>
            <el-descriptions :column="2" border class="entry-meta-descriptions">
              <el-descriptions-item label="标题">{{ selectedEntry.title_cn || selectedEntry.display_title || '-' }}</el-descriptions-item>
              <el-descriptions-item label="媒体类型">{{ mediaTypeLabel(selectedEntry.media_type) }}</el-descriptions-item>
              <el-descriptions-item label="Bangumi ID">{{ selectedEntry.bangumi_id || '-' }}</el-descriptions-item>
              <el-descriptions-item label="TMDB ID">{{ selectedEntry.tmdb_id || '-' }}</el-descriptions-item>
              <el-descriptions-item label="年份 / 月份">{{ selectedEntry.year || '-' }} / {{ selectedEntry.month || '-' }}</el-descriptions-item>
              <el-descriptions-item label="国家 / 地区">{{ regionLabel(selectedEntry.region) }}</el-descriptions-item>
              <el-descriptions-item label="追番状态">{{ selectedEntryDomain === 'seasonal' ? '追番中' : '普通媒体库条目' }}</el-descriptions-item>
              <el-descriptions-item label="别名" :span="2">{{ selectedEntry.title_romaji || selectedEntry.title_raw || '-' }}</el-descriptions-item>
              <el-descriptions-item label="标签" :span="2">
                <div class="mini-tag-row">
                  <span v-for="tag in entryTags(selectedEntry)" :key="tag">{{ tag }}</span>
                  <em v-if="!entryTags(selectedEntry).length">-</em>
                </div>
              </el-descriptions-item>
              <el-descriptions-item label="简介" :span="2">{{ selectedEntry.summary || '-' }}</el-descriptions-item>
            </el-descriptions>
            <div class="drawer-actions">
              <el-button type="primary" @click="openEntryEditDialog">编辑信息</el-button>
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
            <div class="resource-toolbar">
              <el-button plain @click="refreshCurrentEntryResources">刷新全部</el-button>
              <el-button plain @click="openEpisodeImportDialog">手动导入集数</el-button>
              <el-button type="primary" @click="openBatchSubtitleDialog">字幕批量配置</el-button>
            </div>
            <el-table :data="entryResourceRows" height="620" class="episode-resource-table" empty-text="暂无集数资源">
              <el-table-column type="expand" width="44">
                <template #default="{ row }">
                  <div class="resource-expand">
                    <section class="resource-expand-section">
                      <strong>资源信息</strong>
                      <div><span>字幕组</span><code>{{ row.subtitle_group || '-' }}</code></div>
                      <div><span>分辨率</span><code>{{ row.resolution || '-' }}</code></div>
                      <div><span>语言</span><code>{{ row.language || '-' }}</code></div>
                      <div><span>字幕类型</span><code>{{ subtitleFormatText(row.subtitle_format) }}</code></div>
                      <div><span>资源链接</span><code>{{ row.source_ref || row.magnet || row.torrent_url || '-' }}</code></div>
                    </section>
                    <section class="resource-expand-section">
                      <strong>字幕与本地文件</strong>
                      <div><span>字幕链接</span><code>{{ row.subtitle_url || '-' }}</code></div>
                      <div><span>上传字幕</span><code>{{ row.subtitle_file_name || '-' }}</code></div>
                      <div><span>字幕文件路径</span><code>{{ row.subtitle_file || '-' }}</code></div>
                      <div><span>本地文件路径</span><code>{{ row.local_path || '-' }}</code></div>
                    </section>
                    <section class="resource-expand-section">
                      <strong>状态与操作</strong>
                      <div><span>资源状态</span><code>{{ row.status || '-' }}</code></div>
                      <div><span>下载状态</span><code>{{ episodeDownloadText(row) }}</code></div>
                      <div><span>下载错误</span><code>{{ row.download_error || '-' }}</code></div>
                      <div><span>NFO</span><code>{{ row.nfo_status || '-' }}</code></div>
                      <div class="resource-expand-actions">
                        <el-button size="small" plain @click="openEpisodeResourceEditor(row)">配置</el-button>
                        <el-button size="small" plain :disabled="row.downloaded || !row.release_id" @click="downloadEpisodeResource(row)">下载</el-button>
                        <el-button size="small" plain :disabled="!episodeCanPause(row)" @click="pauseEpisodeDownload(row)">暂停</el-button>
                        <el-button size="small" plain :disabled="!episodeCanCancel(row)" @click="cancelEpisodeDownload(row)">取消</el-button>
                        <el-button size="small" plain @click="refreshEpisodeResource(row)">刷新</el-button>
                      </div>
                    </section>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="episode_number" label="集" width="58" />
              <el-table-column prop="resource_title" label="当前选中资源" min-width="420" show-overflow-tooltip />
              <el-table-column label="可观看" width="94">
                <template #default="{ row }">
                  <el-tag :type="episodeDownloadTag(row)" size="small">{{ row.downloaded ? '可观看' : '未缓存' }}</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>

    <el-dialog v-model="entryEditDialogOpen" title="编辑作品信息" width="760px">
      <el-form :model="entryEditForm" label-position="top">
        <div class="form-row">
          <el-form-item label="中文标题"><el-input v-model="entryEditForm.title_cn" /></el-form-item>
          <el-form-item label="年份"><el-input-number v-model="entryEditForm.year" :min="0" /></el-form-item>
          <el-form-item label="月份"><el-input-number v-model="entryEditForm.month" :min="0" :max="12" /></el-form-item>
        </div>
        <div class="form-row">
          <el-form-item label="Bangumi ID"><el-input v-model="entryEditForm.bangumi_id" /></el-form-item>
          <el-form-item label="TMDB ID"><el-input v-model="entryEditForm.tmdb_id" /></el-form-item>
        </div>
        <div class="form-row">
          <el-form-item label="媒体类型">
            <el-select v-model="entryEditForm.media_type">
              <el-option label="动画" value="anime" />
              <el-option label="电影" value="movie" />
              <el-option label="电视剧" value="tv" />
            </el-select>
          </el-form-item>
          <el-form-item label="国家 / 地区">
            <el-select v-model="entryEditForm.region" clearable>
              <el-option label="日本" value="jp" />
              <el-option label="中国" value="cn" />
              <el-option label="欧美" value="us" />
              <el-option label="韩国" value="kr" />
              <el-option label="其他" value="other" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="别名 / 原名"><el-input v-model="entryEditForm.title_romaji" /></el-form-item>
        <el-form-item label="海报 URL"><el-input v-model="entryEditForm.poster_url" /></el-form-item>
        <el-form-item label="标签">
          <el-input v-model="entryEditForm.tags_text" type="textarea" :rows="3" placeholder="一行一个标签，或用逗号分隔" />
        </el-form-item>
        <el-form-item label="类型 / 题材">
          <el-input v-model="entryEditForm.genres_text" type="textarea" :rows="2" placeholder="一行一个类型，或用逗号分隔" />
        </el-form-item>
        <el-form-item label="简介"><el-input v-model="entryEditForm.summary" type="textarea" :rows="4" /></el-form-item>
        <el-progress v-if="metadataFetching || metadataFetchProgress" :percentage="metadataFetchProgress" :status="metadataFetchProgress >= 100 ? 'success' : undefined" />
      </el-form>
      <template #footer>
        <el-button @click="entryEditDialogOpen = false">取消</el-button>
        <el-button plain :loading="metadataFetching" @click="fetchEntryMetadata">扒信息</el-button>
        <el-button type="primary" @click="saveEntryEditForm">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="episodeResourceDialogOpen" title="配置集数资源" width="760px">
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
              <el-option label="无字幕 / 未配置" value="" />
              <el-option label="内嵌（硬字幕）" value="embedded" />
              <el-option label="内封（软字幕）" value="muxed" />
              <el-option label="外挂" value="external" />
            </el-select>
          </el-form-item>
          <el-form-item label="字幕链接">
            <el-input v-model="episodeResourceForm.subtitle_url" placeholder="https://... / magnet:? / 其它字幕下载地址" />
          </el-form-item>
        </div>
        <div class="form-row">
          <el-form-item label="上传本地字幕">
            <el-upload action="#" :auto-upload="false" :limit="1" :on-change="handleSubtitleFilePicked">
              <el-button plain>选择字幕文件</el-button>
              <template #tip>
                <div class="el-upload__tip">{{ episodeResourceForm.subtitle_file_name || '支持先记录文件名；真实上传后续接入导入器。' }}</div>
              </template>
            </el-upload>
          </el-form-item>
          <el-form-item label="本地字幕路径">
            <el-input v-model="episodeResourceForm.subtitle_path" placeholder="可选，已存在的字幕路径" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="episodeResourceDialogOpen = false">取消</el-button>
        <el-button type="primary" @click="saveEpisodeResource">保存配置</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="batchSubtitleDialogOpen" title="字幕批量配置" width="760px">
      <div class="guided-dialog">
        <el-steps :active="batchSubtitleStep" simple>
          <el-step title="提供字幕" />
          <el-step title="匹配规则" />
          <el-step title="确认写入" />
        </el-steps>
        <div v-if="batchSubtitleStep === 0" class="guided-step">
          <el-alert type="info" show-icon :closable="false" title="粘贴字幕下载链接，或选择本地字幕文件；文件名里包含集数时会自动匹配到对应集。" />
          <el-form :model="batchSubtitleForm" label-position="top">
            <el-form-item label="字幕链接 / 文件名">
              <el-input v-model="batchSubtitleForm.subtitles_text" type="textarea" :rows="8" placeholder="https://example.com/show.05.ass&#10;[Subtitle] Show - 06.srt" />
            </el-form-item>
            <el-form-item label="本地字幕文件">
              <el-upload action="#" :auto-upload="false" multiple :on-change="handleBatchSubtitlePicked">
                <el-button plain>选择字幕文件</el-button>
                <template #tip>
                  <div class="el-upload__tip">{{ batchSubtitleForm.file_names.length ? batchSubtitleForm.file_names.join('，') : '当前版本先记录文件名，真实上传由后续导入器接入。' }}</div>
                </template>
              </el-upload>
            </el-form-item>
          </el-form>
        </div>
        <div v-else-if="batchSubtitleStep === 1" class="guided-step">
          <el-alert type="warning" show-icon :closable="false" title="外挂字幕需要下载链接或字幕文件；内封/内嵌通常来自视频资源本身，不需要单独文件。" />
          <el-form :model="batchSubtitleForm" label-position="top">
            <div class="form-row">
              <el-form-item label="字幕类型">
                <el-select v-model="batchSubtitleForm.subtitle_format">
                  <el-option label="外挂" value="external" />
                  <el-option label="内封（软字幕）" value="muxed" />
                  <el-option label="内嵌（硬字幕）" value="embedded" />
                </el-select>
              </el-form-item>
              <el-form-item label="语言"><el-input v-model="batchSubtitleForm.language" placeholder="简体 / 繁体 / 双语" /></el-form-item>
            </div>
          </el-form>
          <div class="guide-preview">
            <strong>识别预览</strong>
            <div v-for="item in batchSubtitlePreviewRows" :key="item.key" :class="['guide-preview-row', { invalid: !item.valid }]">
              <span>第 {{ item.episode }} 集</span>
              <code>{{ item.text }}</code>
              <el-tag size="small" :type="item.valid ? 'success' : 'danger'">{{ item.valid ? '可导入' : item.reason }}</el-tag>
            </div>
          </div>
        </div>
        <div v-else class="guided-step">
          <el-alert
            :type="batchSubtitleInvalidRows.length ? 'error' : 'success'"
            show-icon
            :closable="false"
            :title="batchSubtitleInvalidRows.length ? `还有 ${batchSubtitleInvalidRows.length} 条字幕无法识别，请返回修改` : `准备写入 ${batchSubtitlePreviewRows.length} 条字幕配置`"
          />
          <div class="guide-summary-grid">
            <div><span>字幕类型</span><strong>{{ subtitleFormatText(batchSubtitleForm.subtitle_format) }}</strong></div>
            <div><span>语言</span><strong>{{ batchSubtitleForm.language || '未指定' }}</strong></div>
            <div><span>目标条数</span><strong>{{ batchSubtitlePreviewRows.length }}</strong></div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="batchSubtitleDialogOpen = false">取消</el-button>
        <el-button :disabled="batchSubtitleStep <= 0" @click="batchSubtitleStep -= 1">上一步</el-button>
        <el-button v-if="batchSubtitleStep < 2" type="primary" :disabled="!batchSubtitleCanAdvance" @click="batchSubtitleStep += 1">下一步</el-button>
        <el-button v-else type="primary" :disabled="!batchSubtitleCanSave" @click="saveBatchSubtitles">保存字幕配置</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="episodeImportDialogOpen" title="手动导入集数资源" width="820px">
      <div class="guided-dialog">
        <el-steps :active="episodeImportStep" simple>
          <el-step title="资源链接" />
          <el-step title="字幕配置" />
          <el-step title="确认导入" />
        </el-steps>
        <div v-if="episodeImportStep === 0" class="guided-step">
          <el-alert type="info" show-icon :closable="false" title="每行一个磁链、种子链接或下载链接。明显不是链接的内容会被拦截，避免误把备注写成资源。" />
          <el-form :model="episodeImportForm" label-position="top">
            <el-form-item label="资源链接">
              <el-input v-model="episodeImportForm.resources_text" type="textarea" :rows="9" placeholder="magnet:?xt=urn:btih:...&#10;https://example.com/show.S01E05.torrent&#10;https://example.com/download/show-06.mkv" />
            </el-form-item>
          </el-form>
          <div class="guide-preview">
            <strong>资源识别</strong>
            <div v-for="item in episodeImportResourceRows" :key="item.key" :class="['guide-preview-row', { invalid: !item.valid }]">
              <span>第 {{ item.episode }} 集</span>
              <code>{{ item.text }}</code>
              <el-tag size="small" :type="item.valid ? 'success' : 'danger'">{{ item.valid ? item.kind : item.reason }}</el-tag>
            </div>
          </div>
        </div>
        <div v-else-if="episodeImportStep === 1" class="guided-step">
          <el-alert type="warning" show-icon :closable="false" title="如果资源本身已经内封或内嵌字幕，可以只设置字幕类型；外挂字幕可以在下面批量粘贴链接或文件名。" />
          <el-form :model="episodeImportForm" label-position="top">
            <div class="form-row">
              <el-form-item label="字幕类型">
                <el-select v-model="episodeImportForm.subtitle_format">
                  <el-option label="无字幕 / 未配置" value="" />
                  <el-option label="外挂" value="external" />
                  <el-option label="内封（软字幕）" value="muxed" />
                  <el-option label="内嵌（硬字幕）" value="embedded" />
                </el-select>
              </el-form-item>
              <el-form-item label="语言"><el-input v-model="episodeImportForm.language" placeholder="简体 / 繁体 / 双语" /></el-form-item>
            </div>
            <el-form-item label="外挂字幕链接 / 文件名">
              <el-input v-model="episodeImportForm.subtitles_text" type="textarea" :rows="5" placeholder="可选，一行一个字幕链接或字幕文件名；系统按集数自动匹配" />
            </el-form-item>
          </el-form>
          <div class="guide-preview" v-if="episodeImportSubtitleRows.length">
            <strong>字幕识别</strong>
            <div v-for="item in episodeImportSubtitleRows" :key="item.key" :class="['guide-preview-row', { invalid: !item.valid }]">
              <span>第 {{ item.episode }} 集</span>
              <code>{{ item.text }}</code>
              <el-tag size="small" :type="item.valid ? 'success' : 'danger'">{{ item.valid ? '可导入' : item.reason }}</el-tag>
            </div>
          </div>
        </div>
        <div v-else class="guided-step">
          <el-alert
            :type="episodeImportInvalidCount ? 'error' : 'success'"
            show-icon
            :closable="false"
            :title="episodeImportInvalidCount ? `还有 ${episodeImportInvalidCount} 条内容无法导入，请返回修改` : `准备导入 ${episodeImportResourceRows.length} 条集数资源`"
          />
          <div class="guide-summary-grid">
            <div><span>资源条数</span><strong>{{ episodeImportResourceRows.length }}</strong></div>
            <div><span>字幕条数</span><strong>{{ episodeImportSubtitleRows.length }}</strong></div>
            <div><span>字幕类型</span><strong>{{ subtitleFormatText(episodeImportForm.subtitle_format) }}</strong></div>
            <div><span>语言</span><strong>{{ episodeImportForm.language || '未指定' }}</strong></div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="episodeImportDialogOpen = false">取消</el-button>
        <el-button :disabled="episodeImportStep <= 0" @click="episodeImportStep -= 1">上一步</el-button>
        <el-button v-if="episodeImportStep < 2" type="primary" :disabled="!episodeImportCanAdvance" @click="episodeImportStep += 1">下一步</el-button>
        <el-button v-else type="primary" :disabled="!episodeImportCanSave" @click="commitEpisodeImport">导入集数资源</el-button>
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
        <el-step title="作品信息" />
        <el-step title="集数资源" />
        <el-step title="确认收录" />
      </el-steps>
      <div class="wizard-panel">
        <template v-if="mediaWizardStep === 0">
          <div class="wizard-intro">
            <strong>选择这次收录的来源</strong>
            <span>本地文件、磁链下载和纯作品登记共用同一个入口，后续都整理成作品、集数和资源。</span>
          </div>
          <el-radio-group v-model="mediaWizardDraft.source_mode" class="wizard-source-grid">
            <el-radio-button label="local">
              <div class="wizard-source-card">
                <strong>本地文件</strong>
                <span>选择文件或目录，后续上传/整理到媒体库</span>
              </div>
            </el-radio-button>
            <el-radio-button label="link">
              <div class="wizard-source-card">
                <strong>磁链 / 下载链接</strong>
                <span>记录资源链接，并交给下载器处理</span>
              </div>
            </el-radio-button>
            <el-radio-button label="metadata">
              <div class="wizard-source-card">
                <strong>只登记作品</strong>
                <span>先建作品卡片，稍后再补集数资源</span>
              </div>
            </el-radio-button>
          </el-radio-group>
          <el-upload v-if="mediaWizardDraft.source_mode === 'local'" drag action="#" :auto-upload="false" multiple>
            <p>选择本地文件或目录</p>
            <small>第一版只记录文件信息，真实上传整理会在导入器接入后启用。</small>
          </el-upload>
          <el-form label-position="top" class="wizard-form">
            <el-form-item :label="mediaWizardDraft.source_mode === 'metadata' ? '作品线索' : '资源线索'">
              <el-input
                v-model="mediaWizardSeed"
                type="textarea"
                :rows="4"
                placeholder="可以填 Bangumi ID、TMDB ID、作品名、磁力链接或下载链接。系统会尽量自动带入后面的字段。"
              />
            </el-form-item>
          </el-form>
        </template>
        <template v-else-if="mediaWizardStep === 1">
          <div class="wizard-intro">
            <strong>确认作品信息</strong>
            <span>这里是媒体卡片的基础信息。不确定的字段可以先留空，收录后还能在详情页编辑。</span>
          </div>
          <el-form :model="mediaWizardDraft" label-position="top" class="wizard-form">
            <div class="wizard-form-grid labeled">
              <el-form-item label="作品标题"><el-input v-model="mediaWizardDraft.title" placeholder="例如 欢迎来到实力至上主义的教室 第四季" /></el-form-item>
              <el-form-item label="别名 / 原名"><el-input v-model="mediaWizardDraft.alias" placeholder="可选，例如 Youkoso Jitsuryoku..." /></el-form-item>
              <el-form-item label="Bangumi ID"><el-input v-model="mediaWizardDraft.bangumi_id" placeholder="动画优先使用 Bangumi ID" /></el-form-item>
              <el-form-item label="TMDB ID"><el-input v-model="mediaWizardDraft.tmdb_id" placeholder="电影/电视剧可填 TMDB ID" /></el-form-item>
              <el-form-item label="年份"><el-input v-model="mediaWizardDraft.year" placeholder="例如 2026" /></el-form-item>
              <el-form-item label="月份"><el-input v-model="mediaWizardDraft.month" placeholder="例如 4，未知可留空" /></el-form-item>
              <el-form-item label="季 / 篇章"><el-input v-model="mediaWizardDraft.season_number" placeholder="例如 1、2，电影可留空" /></el-form-item>
              <el-form-item label="国家 / 地区">
                <el-select v-model="mediaWizardDraft.region" clearable placeholder="可选">
                  <el-option label="日本" value="jp" />
                  <el-option label="中国" value="cn" />
                  <el-option label="欧美" value="us" />
                  <el-option label="韩国" value="kr" />
                  <el-option label="其他" value="other" />
                </el-select>
              </el-form-item>
            </div>
          </el-form>
        </template>
        <template v-else-if="mediaWizardStep === 2">
          <div class="wizard-intro">
            <strong>配置第一条集数资源</strong>
            <span>如果只是先登记作品，可以跳过这里。批量集数和字幕在详情页的“集数资源”里继续配置。</span>
          </div>
          <el-form :model="mediaWizardDraft" label-position="top" class="wizard-form">
            <div class="wizard-form-grid labeled">
              <el-form-item label="集数"><el-input v-model="mediaWizardDraft.episode_number" placeholder="例如 1、05、S01E05；不填则只收录作品" /></el-form-item>
              <el-form-item label="资源标题 / 文件名"><el-input v-model="mediaWizardDraft.resource_title" placeholder="资源发布标题或本地文件名" /></el-form-item>
              <el-form-item label="资源链接"><el-input v-model="mediaWizardDraft.source_ref" placeholder="磁力链接、下载链接或文件标识" /></el-form-item>
              <el-form-item label="字幕组"><el-input v-model="mediaWizardDraft.subtitle_group" placeholder="例如 LoliHouse" /></el-form-item>
              <el-form-item label="分辨率"><el-input v-model="mediaWizardDraft.resolution" placeholder="例如 1080p、2160p" /></el-form-item>
              <el-form-item label="语言"><el-input v-model="mediaWizardDraft.language" placeholder="例如 简繁、简体、双语" /></el-form-item>
              <el-form-item label="字幕类型">
                <el-select v-model="mediaWizardDraft.subtitle_format" clearable placeholder="可选">
                  <el-option label="内嵌（硬字幕）" value="embedded" />
                  <el-option label="内封（软字幕）" value="muxed" />
                  <el-option label="外挂" value="external" />
                </el-select>
              </el-form-item>
              <el-form-item label="字幕链接"><el-input v-model="mediaWizardDraft.subtitle_url" placeholder="外挂字幕下载链接，可选" /></el-form-item>
              <el-form-item label="上传字幕文件名"><el-input v-model="mediaWizardDraft.subtitle_file_name" placeholder="选择文件后记录文件名，可选" /></el-form-item>
              <el-form-item label="字幕路径"><el-input v-model="mediaWizardDraft.subtitle_path" placeholder="服务端整理后的字幕路径，可选" /></el-form-item>
            </div>
          </el-form>
        </template>
        <template v-else>
          <div class="wizard-confirm-panel">
            <div class="confirm-hero">
              <strong>{{ mediaWizardDraft.title || currentMediaPageTitle }}</strong>
              <span>{{ mediaTypeLabel(currentMediaType) }} · {{ mediaWizardDraft.year || '年份未知' }} · {{ regionLabel(mediaWizardDraft.region) || '地区未填' }}</span>
            </div>
            <div class="confirm-grid">
              <div><span>Bangumi</span><code>{{ mediaWizardDraft.bangumi_id || '-' }}</code></div>
              <div><span>TMDB</span><code>{{ mediaWizardDraft.tmdb_id || '-' }}</code></div>
              <div><span>首条集数</span><code>{{ mediaWizardDraft.episode_number ? `第 ${mediaWizardDraft.episode_number} 集` : '暂不配置' }}</code></div>
              <div><span>资源</span><code>{{ mediaWizardDraft.resource_title || mediaWizardDraft.source_ref || '未填写资源' }}</code></div>
              <div><span>字幕</span><code>{{ subtitleFormatText(mediaWizardDraft.subtitle_format) || '-' }} · {{ mediaWizardDraft.language || '-' }}</code></div>
              <div><span>来源</span><code>{{ sourceModeText(mediaWizardDraft.source_mode) }}</code></div>
            </div>
          </div>
        </template>
      </div>
      <template #footer>
        <el-button @click="mediaWizardOpen = false">关闭</el-button>
        <el-button :disabled="mediaWizardStep <= 0" @click="mediaWizardStep -= 1">上一步</el-button>
        <el-button v-if="mediaWizardStep < 3" type="primary" @click="advanceMediaWizard">下一步</el-button>
        <el-button v-else type="primary" :loading="mediaWizardSaving" @click="commitMediaWizard">收录</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import draggable from 'vuedraggable'
import { ElMessage } from 'element-plus'
import { Calendar, Collection, DataBoard, Document, Refresh, Search, Setting } from '@element-plus/icons-vue'
import { deleteAction, getAction, getDashboard, getDiagnostics, getMediaItem, getSettings, postAction, putAction, saveMediaItem, saveSettings } from './api'
import { APP_BUILD, APP_VERSION } from './version'

const view = ref('dashboard')
const appVersion = APP_VERSION
const appBuild = APP_BUILD
const selectedConsoleSection = ref('')
const logKeyword = ref('')
const loading = ref(false)
const savingSettings = ref(false)
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
const mediaWizardSaving = ref(false)
const episodeResourceDialogOpen = ref(false)
const entryEditDialogOpen = ref(false)
const batchSubtitleDialogOpen = ref(false)
const episodeImportDialogOpen = ref(false)
const batchSubtitleStep = ref(0)
const episodeImportStep = ref(0)
const metadataFetching = ref(false)
const metadataFetchProgress = ref(0)
let dashboardStream = null
let streamRetryTimer = null
let metadataProgressTimer = null
const keyword = ref('')
const libraryYearFilter = ref('')
const libraryMonthFilter = ref('')
const libraryScopeFilter = ref('')
const libraryMediaTypeFilter = ref('')
const libraryRegionFilter = ref('')
const libraryTagFilters = ref([])
const entryDrawerOpen = ref(false)
const selectedEntryDetail = ref(null)
const selectedEntryDomain = ref('seasonal')
const selectedEntryMediaType = ref('anime')
const dashboard = reactive({
  seasonal_items: [],
  library_items: [],
  seasonal_sync_calendar: [],
  seasonal_update_calendar: [],
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
  subtitle_url: '',
  subtitle_file_name: '',
})
const entryEditForm = reactive({
  title_cn: '',
  bangumi_id: '',
  tmdb_id: '',
  year: 0,
  month: 0,
  season_number: 1,
  media_type: 'anime',
  region: 'jp',
  title_romaji: '',
  title_raw: '',
  poster_url: '',
  summary: '',
  tags_text: '',
  genres_text: '',
})
const batchSubtitleForm = reactive({
  subtitles_text: '',
  file_names: [],
  subtitle_format: 'external',
  language: '',
})
const episodeImportForm = reactive({
  resources_text: '',
  subtitles_text: '',
  subtitle_format: 'external',
  language: '',
})
const mediaWizardDraft = reactive({
  source_mode: 'link',
  title: '',
  alias: '',
  bangumi_id: '',
  tmdb_id: '',
  year: 0,
  month: 0,
  season_number: 1,
  region: '',
  episode_number: 0,
  resource_title: '',
  source_ref: '',
  subtitle_group: '',
  resolution: '',
  language: '',
  subtitle_format: '',
  subtitle_path: '',
  subtitle_url: '',
  subtitle_file_name: '',
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
const currentMonthOptions = computed(() => {
  const values = new Set()
  for (const item of currentCatalogSourceRows.value) {
    const month = Number(item.month || 0)
    if (month >= 1 && month <= 12) values.add(month)
  }
  return Array.from(values).sort((a, b) => a - b)
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
const activeDetailRows = computed(() => selectedEntryDomain.value === 'library' ? libraryRows.value : seasonalRows.value)
const localAssetTotal = computed(() => seasonalRows.value.reduce((sum, item) => sum + Number(item.local_asset_count || 0), 0))
const watchableTotal = computed(() => localAssetTotal.value)
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
  return `收录${currentMediaPageTitle.value}`
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
      source_ref: resource.source_ref || '',
      torrent_url: resource.torrent_url || '',
      magnet: resource.magnet || '',
      subtitle_group: resource.subtitle_group || '-',
      resolution: resource.resolution || '-',
      language: resource.language || '-',
      subtitle_format: resource.subtitle_format || '',
      subtitle_file: '-',
      subtitle_url: '',
      subtitle_file_name: '',
      downloaded: Boolean(resource.downloaded) || Boolean(resource.local_path),
      local_path: resource.local_path || '',
      status: resource.status || '',
      download_status: resource.download_status || '',
      download_job_id: Number(resource.download_job_id || 0),
      download_error: resource.download_error || '',
      download_retry_after: resource.download_retry_after || '',
      local_asset_id: Number(resource.local_asset_id || 0),
      nfo_status: resource.local_nfo_status || '',
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
    row.subtitle_url = subtitle.subtitle_url || row.subtitle_url
    row.subtitle_file_name = subtitle.file_name || row.subtitle_file_name
    row.subtitle_format = subtitle.subtitle_format || row.subtitle_format
    row.language = subtitle.language || row.language
  }
  return Array.from(rows.values()).sort((a, b) => Number(a.episode_number || 0) - Number(b.episode_number || 0))
})
const batchSubtitlePreviewRows = computed(() => {
  return [
    ...splitTextLines(batchSubtitleForm.subtitles_text),
    ...batchSubtitleForm.file_names,
  ].map((text, index) => {
    const valid = isValidSubtitleReference(text)
    return {
      key: `subtitle:${index}:${text}`,
      text,
      episode: inferEpisodeFromText(text, index + 1),
      valid,
      reason: valid ? '' : '格式无效',
    }
  })
})
const batchSubtitleInvalidRows = computed(() => batchSubtitlePreviewRows.value.filter(item => !item.valid))
const batchSubtitleCanAdvance = computed(() => {
  if (batchSubtitleStep.value === 0) return batchSubtitlePreviewRows.value.length > 0
  if (batchSubtitleStep.value === 1) return batchSubtitlePreviewRows.value.length > 0 && batchSubtitleInvalidRows.value.length === 0
  return true
})
const batchSubtitleCanSave = computed(() => batchSubtitlePreviewRows.value.length > 0 && batchSubtitleInvalidRows.value.length === 0)
const episodeImportResourceRows = computed(() => splitTextLines(episodeImportForm.resources_text).map((text, index) => {
  const valid = isValidResourceReference(text)
  return {
    key: `resource:${index}:${text}`,
    text,
    episode: inferEpisodeFromText(text, index + 1),
    valid,
    kind: resourceReferenceKind(text),
    reason: valid ? '' : '不是下载链接',
  }
}))
const episodeImportSubtitleRows = computed(() => splitTextLines(episodeImportForm.subtitles_text).map((text, index) => {
  const valid = isValidSubtitleReference(text)
  return {
    key: `episode-subtitle:${index}:${text}`,
    text,
    episode: inferEpisodeFromText(text, index + 1),
    valid,
    reason: valid ? '' : '格式无效',
  }
}))
const episodeImportInvalidCount = computed(() => {
  return episodeImportResourceRows.value.filter(item => !item.valid).length
    + episodeImportSubtitleRows.value.filter(item => !item.valid).length
})
const episodeImportCanAdvance = computed(() => {
  if (episodeImportStep.value === 0) {
    return episodeImportResourceRows.value.length > 0 && episodeImportResourceRows.value.every(item => item.valid)
  }
  if (episodeImportStep.value === 1) {
    return episodeImportSubtitleRows.value.every(item => item.valid)
  }
  return true
})
const episodeImportCanSave = computed(() => episodeImportResourceRows.value.length > 0 && episodeImportInvalidCount.value === 0)

const filteredSeries = computed(() => {
  const text = keyword.value.toLowerCase()
  const source = currentCatalogSourceRows.value
  return source.filter(item => {
    const matched = !text || `${item.entry_display_title || item.display_title || item.title_cn} ${item.work_display_title || item.work_title || item.title_root || ''} ${item.entry_scope_label || ''} ${item.bangumi_id || ''} ${item.tmdb_id || ''}`.toLowerCase().includes(text)
    if (!matched) return false
    if (libraryMediaTypeFilter.value && entryMediaType(item) !== String(libraryMediaTypeFilter.value)) return false
    if (libraryRegionFilter.value && String(item.region || '') !== String(libraryRegionFilter.value)) return false
    if (libraryYearFilter.value && Number(item.year || 0) !== Number(libraryYearFilter.value)) return false
    if (libraryMonthFilter.value && Number(item.month || 0) !== Number(libraryMonthFilter.value)) return false
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

function errorMessage(error) {
  return error?.response?.data?.detail || error?.response?.data?.message || error?.message || '请求失败'
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

function sourceModeText(value) {
  const key = String(value || 'link')
  return {
    local: '本地文件',
    link: '磁链 / 下载链接',
    metadata: '只登记作品',
    collect: '收录',
    add: '添加',
    import: '导入',
  }[key] || key
}

function numberFromInput(value, fallback = 0) {
  const matches = String(value ?? '').match(/\d+/g)
  if (!matches?.length) return fallback
  const parsed = Number.parseInt(matches[matches.length - 1], 10)
  return Number.isFinite(parsed) ? parsed : fallback
}

function splitTextLines(value) {
  return String(value || '')
    .split(/\r?\n/)
    .map(item => item.trim())
    .filter(Boolean)
}

function inferEpisodeFromText(value, fallback = 1) {
  const text = String(value || '')
  const patterns = [
    /S\d{1,2}E(\d{1,4})/i,
    /(?:第|EP|E|episode)[\s._-]*(\d{1,4})/i,
    /[\s._\-[【(](\d{1,4})[\])】)\s._-]/,
  ]
  for (const pattern of patterns) {
    const match = text.match(pattern)
    if (match) {
      const valueNumber = Number.parseInt(match[1], 10)
      if (Number.isFinite(valueNumber) && valueNumber > 0) return valueNumber
    }
  }
  return Math.max(1, fallback)
}

function isValidResourceReference(value) {
  const text = String(value || '').trim().toLowerCase()
  if (!text || /\s/.test(text.replace(/^magnet:\?xt=[^&]+/i, ''))) return false
  return text.startsWith('magnet:?')
    || text.startsWith('http://')
    || text.startsWith('https://')
    || text.startsWith('ftp://')
    || text.startsWith('thunder://')
    || text.startsWith('ed2k://')
}

function resourceReferenceKind(value) {
  const text = String(value || '').trim().toLowerCase()
  if (text.startsWith('magnet:?')) return '磁链'
  if (text.endsWith('.torrent')) return '种子'
  if (text.startsWith('http://') || text.startsWith('https://')) return '下载链接'
  if (text.startsWith('ftp://')) return 'FTP'
  if (text.startsWith('thunder://')) return '迅雷'
  if (text.startsWith('ed2k://')) return 'ED2K'
  return '资源'
}

function isValidSubtitleReference(value) {
  const text = String(value || '').trim().toLowerCase()
  if (!text) return false
  if (text.startsWith('http://') || text.startsWith('https://')) return true
  return /\.(ass|srt|ssa|vtt|sup|sub)(\?.*)?$/.test(text)
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

function cardSubtitle(item) {
  if (!item) return 'Season 01'
  return item.entry_scope_label
    || item.entry_secondary_title
    || item.bangumi_id
    || item.tmdb_id
    || 'Season 01'
}

function cardInitials(item) {
  return entryTitle(item).slice(0, 2) || 'AN'
}

function watchableCount(item) {
  if (!item) return 0
  return Number(item.local_asset_count || 0)
}

function parseDateValue(value) {
  if (!value) return 0
  const time = new Date(value).getTime()
  return Number.isFinite(time) ? time : 0
}

function hasRecentUpdate(item) {
  const entryId = Number(item?.id || item?.entry_id || 0)
  const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000
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

function listTextFromJson(value) {
  return parseJsonArray(value).join('\n')
}

function jsonFromListText(value) {
  const items = String(value || '')
    .replace(/,/g, '\n')
    .split('\n')
    .map(item => item.trim())
    .filter(Boolean)
  return JSON.stringify(Array.from(new Set(items)))
}

function toggleLibraryTag(tag) {
  const next = new Set(libraryTagFilters.value)
  if (next.has(tag)) next.delete(tag)
  else next.add(tag)
  libraryTagFilters.value = Array.from(next)
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
  if (row?.status === 'synced') return '已整理'
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

function episodeDownloadState(row) {
  if (row?.downloaded) return 'downloaded'
  return String(row?.download_status || row?.status || '').toLowerCase()
}

function episodeDownloadText(row) {
  const state = episodeDownloadState(row)
  return {
    downloaded: '可观看',
    synced: '可观看',
    queued: '排队中',
    pending: '排队中',
    running: '下载中',
    submitted: '下载中',
    downloading: '下载中',
    remote_completed: '整理中',
    paused: '已暂停',
    cancelled: '已取消',
    failed: '失败',
    available: '未下载',
  }[state] || '未下载'
}

function episodeDownloadTag(row) {
  const state = episodeDownloadState(row)
  if (['downloaded', 'synced'].includes(state)) return 'success'
  if (['queued', 'pending', 'running', 'submitted', 'downloading', 'remote_completed'].includes(state)) return 'warning'
  if (state === 'failed') return 'danger'
  if (state === 'cancelled' || state === 'paused') return 'info'
  return 'info'
}

function episodeCanCancel(row) {
  return ['queued', 'pending', 'running', 'submitted', 'downloading', 'failed', 'paused'].includes(episodeDownloadState(row))
}

function episodeCanPause(row) {
  return ['queued', 'pending', 'running', 'submitted', 'downloading'].includes(episodeDownloadState(row))
}

function handleSubtitleFilePicked(file) {
  episodeResourceForm.subtitle_file_name = file?.name || file?.raw?.name || ''
  if (!episodeResourceForm.subtitle_format) episodeResourceForm.subtitle_format = 'external'
}

function openBatchSubtitleDialog() {
  batchSubtitleForm.subtitles_text = ''
  batchSubtitleForm.file_names = []
  batchSubtitleForm.subtitle_format = 'external'
  batchSubtitleForm.language = ''
  batchSubtitleStep.value = 0
  batchSubtitleDialogOpen.value = true
}

function handleBatchSubtitlePicked(file) {
  const name = file?.name || file?.raw?.name || ''
  if (name && !batchSubtitleForm.file_names.includes(name)) {
    batchSubtitleForm.file_names = [...batchSubtitleForm.file_names, name]
  }
}

function openEpisodeImportDialog() {
  episodeImportForm.resources_text = ''
  episodeImportForm.subtitles_text = ''
  episodeImportForm.subtitle_format = 'external'
  episodeImportForm.language = ''
  episodeImportStep.value = 0
  episodeImportDialogOpen.value = true
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
  if (key === 'download') return '待处理表示已选中发布，等待下载器完成并整理到本地媒体库。'
  if (key === 'local_sync') return '待处理表示下载已完成，等待整理到本地媒体库。'
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
  if (!window.EventSource) return
  dashboardStream = new EventSource('/api/dashboard/stream')
  dashboardStream.onopen = () => {
    liveConnected.value = true
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
    streamRetryTimer = window.setTimeout(startDashboardStream, 5000)
  }
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
  episodeResourceForm.subtitle_url = row.subtitle_url === '-' ? '' : String(row.subtitle_url || '')
  episodeResourceForm.subtitle_file_name = row.subtitle_file_name === '-' ? '' : String(row.subtitle_file_name || '')
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
    if (episodeResourceForm.subtitle_path || episodeResourceForm.subtitle_url || episodeResourceForm.subtitle_file_name || episodeResourceForm.subtitle_id) {
      await putAction(`/episodes/${episodeId}/subtitle`, {
        subtitle_id: episodeResourceForm.subtitle_id,
        language: episodeResourceForm.language,
        subtitle_format: episodeResourceForm.subtitle_format,
        subtitle_path: episodeResourceForm.subtitle_path,
        subtitle_url: episodeResourceForm.subtitle_url,
        file_name: episodeResourceForm.subtitle_file_name,
        selected: true,
      })
    }
    ElMessage.success('集数资源已保存')
    episodeResourceDialogOpen.value = false
    if (selectedEntry.value?.id) {
      await openEntry(selectedEntry.value.id, selectedEntryDomain.value, selectedEntryMediaType.value)
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

async function refreshEpisodeResource(row) {
  const episodeId = Number(row?.episode_id || 0)
  if (!episodeId) {
    ElMessage.warning('缺少集数记录，无法刷新')
    return
  }
  try {
    const result = await postAction(`/episodes/${episodeId}/refresh`)
    ElMessage.success(result.download_run_id ? '已刷新并重新加入下载队列' : '集数状态已刷新')
    if (selectedEntry.value?.id) {
      await openEntry(selectedEntry.value.id, selectedEntryDomain.value, selectedEntryMediaType.value)
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

async function downloadEpisodeResource(row) {
  const episodeId = Number(row?.episode_id || 0)
  if (!episodeId) {
    ElMessage.warning('缺少集数记录，无法下载')
    return
  }
  try {
    await postAction(`/episodes/${episodeId}/download`)
    ElMessage.success('已加入下载队列')
    if (selectedEntry.value?.id) {
      await openEntry(selectedEntry.value.id, selectedEntryDomain.value, selectedEntryMediaType.value)
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

async function cancelEpisodeDownload(row) {
  const episodeId = Number(row?.episode_id || 0)
  if (!episodeId) {
    ElMessage.warning('缺少集数记录，无法取消')
    return
  }
  try {
    await postAction(`/episodes/${episodeId}/download/cancel`)
    ElMessage.success('已取消该集下载')
    if (selectedEntry.value?.id) {
      await openEntry(selectedEntry.value.id, selectedEntryDomain.value, selectedEntryMediaType.value)
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

async function pauseEpisodeDownload(row) {
  const episodeId = Number(row?.episode_id || 0)
  if (!episodeId) {
    ElMessage.warning('缺少集数记录，无法暂停')
    return
  }
  try {
    await postAction(`/episodes/${episodeId}/download/pause`)
    ElMessage.success('已暂停该集下载流程')
    if (selectedEntry.value?.id) {
      await openEntry(selectedEntry.value.id, selectedEntryDomain.value, selectedEntryMediaType.value)
    }
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

async function refreshCurrentEntryResources() {
  const entry = selectedEntry.value
  if (!entry?.id) return
  try {
    const result = await postAction(`/entries/${entry.id}/refresh-resources`)
    ElMessage.success(result.download_run_ids?.length ? '已刷新并补下载缺失资源' : '集数资源状态已刷新')
    await openEntry(entry.id, selectedEntryDomain.value, selectedEntryMediaType.value)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

async function saveBatchSubtitles() {
  const entry = selectedEntry.value
  if (!entry?.id) return
  if (!batchSubtitleCanSave.value) {
    ElMessage.warning('请先补全可识别的字幕链接或字幕文件')
    return
  }
  try {
    const result = await postAction(`/entries/${entry.id}/subtitles/batch`, {
      subtitles_text: batchSubtitleForm.subtitles_text,
      file_names: batchSubtitleForm.file_names,
      subtitle_format: batchSubtitleForm.subtitle_format,
      language: batchSubtitleForm.language,
    })
    batchSubtitleDialogOpen.value = false
    selectedEntryDetail.value = result.detail
    ElMessage.success(`已配置 ${result.count || 0} 条字幕`)
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

async function commitEpisodeImport() {
  const entry = selectedEntry.value
  if (!entry?.id) return
  if (!episodeImportCanSave.value) {
    ElMessage.warning('请先补全有效的资源链接')
    return
  }
  try {
    const result = await postAction(`/entries/${entry.id}/resources/import`, {
      resources_text: episodeImportForm.resources_text,
      subtitles_text: episodeImportForm.subtitles_text,
      subtitle_format: episodeImportForm.subtitle_format,
      language: episodeImportForm.language,
    })
    episodeImportDialogOpen.value = false
    selectedEntryDetail.value = result.detail
    ElMessage.success(`已导入 ${result.count || 0} 条集数资源`)
    await reload()
  } catch (error) {
    ElMessage.error(apiErrorMessage(error))
  }
}

function openMediaWizard(mode = 'collect') {
  mediaWizardMode.value = mode
  mediaWizardStep.value = 0
  mediaWizardSeed.value = ''
  mediaWizardDraft.source_mode = 'link'
  mediaWizardDraft.title = ''
  mediaWizardDraft.alias = ''
  mediaWizardDraft.bangumi_id = ''
  mediaWizardDraft.tmdb_id = ''
  mediaWizardDraft.year = 0
  mediaWizardDraft.month = 0
  mediaWizardDraft.season_number = 1
  mediaWizardDraft.region = ''
  mediaWizardDraft.episode_number = 0
  mediaWizardDraft.resource_title = ''
  mediaWizardDraft.source_ref = ''
  mediaWizardDraft.subtitle_group = ''
  mediaWizardDraft.resolution = ''
  mediaWizardDraft.language = ''
  mediaWizardDraft.subtitle_format = ''
  mediaWizardDraft.subtitle_path = ''
  mediaWizardDraft.subtitle_url = ''
  mediaWizardDraft.subtitle_file_name = ''
  mediaWizardOpen.value = true
}

function advanceMediaWizard() {
  if (mediaWizardStep.value === 0) {
    const seed = String(mediaWizardSeed.value || '').trim()
    const firstLine = seed.split(/\r?\n/).map(item => item.trim()).filter(Boolean)[0] || ''
    const bangumiMatch = seed.match(/(?:bangumi|bgm|subject)[^\d]*(\d{2,})/i)
    const tmdbMatch = seed.match(/tmdb[^\d]*(\d{2,})/i)
    if (bangumiMatch && !mediaWizardDraft.bangumi_id) mediaWizardDraft.bangumi_id = bangumiMatch[1]
    if (tmdbMatch && !mediaWizardDraft.tmdb_id) mediaWizardDraft.tmdb_id = tmdbMatch[1]
    if (/^(magnet:|https?:\/\/)/i.test(firstLine) && !mediaWizardDraft.source_ref) {
      mediaWizardDraft.source_ref = firstLine
      if (!mediaWizardDraft.resource_title) mediaWizardDraft.resource_title = firstLine.slice(0, 80)
    } else if (firstLine && !bangumiMatch && !tmdbMatch && !mediaWizardDraft.title) {
      mediaWizardDraft.title = firstLine
    }
  }
  mediaWizardStep.value += 1
}

async function commitMediaWizard() {
  const seed = String(mediaWizardSeed.value || '').trim()
  const seedLooksLikeLink = /^(magnet:|https?:\/\/)/i.test(seed)
  const title = String(mediaWizardDraft.title || (!seedLooksLikeLink ? seed : '')).trim()
  if (!title) {
    ElMessage.warning('作品标题不能为空')
    mediaWizardStep.value = 1
    return
  }
  mediaWizardSaving.value = true
  try {
    const sourceRef = String(mediaWizardDraft.source_ref || (seedLooksLikeLink ? seed : '')).trim()
    const year = Math.max(0, numberFromInput(mediaWizardDraft.year, 0))
    const month = Math.max(0, Math.min(12, numberFromInput(mediaWizardDraft.month, 0)))
    const seasonNumber = Math.max(1, numberFromInput(mediaWizardDraft.season_number, 1))
    const episodeNumber = Math.max(0, numberFromInput(mediaWizardDraft.episode_number, 0))
    const result = await postAction(`/media/${currentMediaType.value}`, {
      mode: mediaWizardDraft.source_mode || mediaWizardMode.value,
      title,
      bangumi_id: mediaWizardDraft.bangumi_id,
      tmdb_id: mediaWizardDraft.tmdb_id,
      year,
      month,
      season_number: seasonNumber,
      region: mediaWizardDraft.region || '',
      episode_number: episodeNumber,
      resource_title: mediaWizardDraft.resource_title,
      source_ref: sourceRef,
      subtitle_group: mediaWizardDraft.subtitle_group,
      resolution: mediaWizardDraft.resolution,
      language: mediaWizardDraft.language,
      subtitle_format: mediaWizardDraft.subtitle_format,
      subtitle_path: mediaWizardDraft.subtitle_path,
      subtitle_url: mediaWizardDraft.subtitle_url,
      subtitle_file_name: mediaWizardDraft.subtitle_file_name,
    })
    ElMessage.success(result?.download_run_id ? '媒体条目已收录，下载任务已创建' : '媒体条目已收录')
    mediaWizardOpen.value = false
    await reload()
    const entryId = result?.entry?.id
    if (entryId) await openEntry(entryId, 'library', currentMediaType.value)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    mediaWizardSaving.value = false
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
  link.download = `anitrack-log-${timestamp}.txt`
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

async function openEntry(id, domain = 'seasonal', mediaType = '') {
  const type = mediaType || (domain === 'seasonal' ? 'anime' : currentMediaType.value)
  selectedEntryDomain.value = domain
  selectedEntryMediaType.value = type
  selectedEntryDetail.value = await getMediaItem(type, id)
  entryDrawerOpen.value = true
}

async function openQueueEntry(row) {
  const entryId = Number(row?.entry_id || 0)
  if (!entryId) return
  const domain = row?.domain_kind === 'library' ? 'library' : 'seasonal'
  await openEntry(entryId, domain, row?.media_type || (domain === 'seasonal' ? 'anime' : currentMediaType.value))
}

function stopMetadataProgress() {
  if (metadataProgressTimer) {
    window.clearInterval(metadataProgressTimer)
    metadataProgressTimer = null
  }
}

function startMetadataProgress() {
  stopMetadataProgress()
  metadataFetchProgress.value = 8
  metadataProgressTimer = window.setInterval(() => {
    metadataFetchProgress.value = Math.min(92, metadataFetchProgress.value + 8)
  }, 350)
}

function openEntryEditDialog() {
  const entry = selectedEntry.value
  if (!entry) return
  entryEditForm.title_cn = entry.title_cn || entry.display_title || ''
  entryEditForm.bangumi_id = entry.bangumi_id || ''
  entryEditForm.tmdb_id = entry.tmdb_id || ''
  entryEditForm.year = Number(entry.year || 0)
  entryEditForm.month = Number(entry.month || 0)
  entryEditForm.season_number = Number(entry.season_number || 1)
  entryEditForm.media_type = entryMediaType(entry)
  entryEditForm.region = entry.region || 'jp'
  entryEditForm.title_romaji = entry.title_romaji || entry.title_raw || ''
  entryEditForm.title_raw = entry.title_raw || ''
  entryEditForm.poster_url = entry.poster_url || ''
  entryEditForm.summary = entry.summary || ''
  entryEditForm.tags_text = listTextFromJson(entry.tags_json)
  entryEditForm.genres_text = listTextFromJson(entry.genres_json)
  metadataFetchProgress.value = 0
  entryEditDialogOpen.value = true
}

function entryEditPayload() {
  return {
    title_cn: entryEditForm.title_cn,
    bangumi_id: entryEditForm.bangumi_id,
    tmdb_id: entryEditForm.tmdb_id,
    year: entryEditForm.year,
    month: entryEditForm.month,
    season_number: entryEditForm.season_number,
    media_type: entryEditForm.media_type,
    region: entryEditForm.region,
    title_romaji: entryEditForm.title_romaji,
    title_raw: entryEditForm.title_raw || entryEditForm.title_romaji,
    poster_url: entryEditForm.poster_url,
    summary: entryEditForm.summary,
    genres_json: jsonFromListText(entryEditForm.genres_text),
    tags_json: jsonFromListText(entryEditForm.tags_text),
  }
}

async function fetchEntryMetadata() {
  const entry = selectedEntry.value
  if (!entry?.id) return
  metadataFetching.value = true
  startMetadataProgress()
  try {
    const result = await postAction(`/media/${selectedEntryMediaType.value || entryMediaType(entry)}/${entry.id}/metadata/fetch`, {
      bangumi_id: entryEditForm.bangumi_id,
      tmdb_id: entryEditForm.tmdb_id,
      provider: 'bangumi',
    })
    selectedEntryDetail.value = result
    metadataFetchProgress.value = 100
    ElMessage.success('元数据已填入，请确认后保存')
    openEntryEditDialog()
    metadataFetchProgress.value = 100
  } catch (error) {
    metadataFetchProgress.value = 0
    ElMessage.error(apiErrorMessage(error))
  } finally {
    metadataFetching.value = false
    stopMetadataProgress()
  }
}

async function saveEntryEditForm() {
  const entry = selectedEntry.value
  if (!entry?.id) return
  const result = await saveMediaItem(selectedEntryMediaType.value || entryEditForm.media_type, entry.id, entryEditPayload())
  selectedEntryDetail.value = result
  entryEditDialogOpen.value = false
  ElMessage.success(selectedEntryDomain.value === 'library' ? '番剧库条目已保存' : '番剧设置已保存')
  await reload()
}

function normalizeSettingsShape() {
  if (!Array.isArray(settings.downloaders)) settings.downloaders = []
  for (const key of [
    'subtitle_priority',
    'resolution_priority',
    'language_priority',
    'secondary_language_priority',
    'movie_quality_priority',
    'movie_source_priority',
    'movie_subtitle_priority',
    'tv_quality_priority',
    'tv_source_priority',
    'tv_subtitle_priority',
  ]) {
    if (Array.isArray(settings[key])) continue
    settings[key] = String(settings[key] || '')
      .replace(/,/g, '\n')
      .split('\n')
      .map(item => item.trim())
      .filter(Boolean)
  }
  settings.backfill_current_season = Boolean(settings.backfill_current_season)
  settings.auto_generate_nfo = settings.auto_generate_nfo !== false
  settings.movie_name_template = settings.movie_name_template || '{title_cn} ({year})/{title_cn} ({year})'
  settings.tv_name_template = settings.tv_name_template || '{title_cn} ({year})/Season {season:02d}/{title_cn} - S{season:02d}E{episode:02d}'
  settings.episode_name_template = settings.episode_name_template || '{title_cn} - S{season:02d}E{episode:02d} - {episode_title}'
}

function resetSelectionRules(type) {
  normalizeSettingsShape()
  if (type === 'movie') {
    settings.movie_quality_priority = ['2160p', '1080p', '720p']
    settings.movie_source_priority = ['BluRay', 'WEB-DL', 'WebRip', 'HDTV']
    settings.movie_subtitle_priority = ['简繁', '简体', '繁体', '双语', '中字']
    ElMessage.success('已重置电影自动选集规则，保存设置后生效')
    return
  }
  if (type === 'tv') {
    settings.tv_quality_priority = ['2160p', '1080p', '720p']
    settings.tv_source_priority = ['WEB-DL', 'WebRip', 'HDTV']
    settings.tv_subtitle_priority = ['简繁', '简体', '繁体', '双语', '中字']
    ElMessage.success('已重置电视剧自动选集规则，保存设置后生效')
    return
  }
  settings.subtitle_priority = ['LoliHouse', '喵萌奶茶屋', '猎户压制部', '百冬练习组']
  settings.resolution_priority = ['2160p', '1080p', '720p']
  settings.language_priority = ['简繁', '简体', '繁体']
  settings.secondary_language_priority = ['内封', '内嵌', '外挂']
  ElMessage.success('已重置动画自动选集规则，保存设置后生效')
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
      rclone_remote: 'pikpak',
      rclone_config_path: '/data/rclone/rclone.conf',
      rclone_command: 'rclone',
      rpc_url: '',
      token: '',
      auth_mode: 'token',
      username: '',
      password: '',
      access_token: '',
      refresh_token: '',
      proxy: '',
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

watch(consoleNavMode, value => {
  selectedConsoleSection.value = ''
})
watch(selectedScheduledJob, job => {
  syncScheduledJobForm(job)
})
watch(entryEditDialogOpen, value => {
  if (!value) {
    stopMetadataProgress()
    metadataFetching.value = false
    metadataFetchProgress.value = 0
  }
})
watch(view, value => {
  if (['seasonal', 'library', 'movies', 'tv'].includes(value)) {
    libraryYearFilter.value = ''
    libraryMonthFilter.value = ''
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
  stopMetadataProgress()
})
</script>



