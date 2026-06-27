<script>
import { appContextComponent } from '../composables/appContext'

export default appContextComponent({
  computed: {
    totalTasks() {
      return (this.dashboard.tasks || []).length;
    },
    completedTasks() {
      return (this.dashboard.tasks || []).filter(t => t.status === 'completed').length;
    },
    activeTasks() {
      return (this.dashboard.tasks || []).filter(t => ['pending', 'running', 'waiting', 'submitting', 'downloading', 'remote_downloading', 'local_copying'].includes(t.status)).length;
    },
    failedTasks() {
      return (this.dashboard.tasks || []).filter(t => t.status === 'failed' || t.status === 'cancelled').length;
    },
    completedPercent() {
      return this.totalTasks ? Math.round((this.completedTasks / this.totalTasks) * 100) : 0;
    },
    activePercent() {
      return this.totalTasks ? Math.round((this.activeTasks / this.totalTasks) * 100) : 0;
    },
    failedPercent() {
      return this.totalTasks ? Math.round((this.failedTasks / this.totalTasks) * 100) : 0;
    },
    levelProgress() {
      const watchTotal = this.watchableTotal || 1;
      const localTotal = this.localAssetTotal || 0;
      return Math.min(100, Math.round((localTotal / watchTotal) * 100));
    },
    levelTitle() {
      const local = this.localAssetTotal || 0;
      if (local > 100) return '媒体库覆盖优秀';
      if (local > 50) return '媒体库稳定增长';
      if (local > 20) return '本地缓存充足';
      if (local > 5) return '本地缓存起步';
      return '等待更多资源';
    }
  }
})
</script>

<template>
  <section v-if="view === 'dashboard'" class="mochi-dashboard-container">
    
    <!-- 1. 顶部数据胶囊 (Mochi Pro Metrics Grid - 3 Dense Columns) -->
    <div class="mochi-metrics-row">
      <div class="mochi-metric-pro-card pink">
        <div class="pro-info">
          <span class="pro-label">已收录新番</span>
          <strong class="pro-value">{{ seasonalCatalogTotal }} <span class="pro-unit">部</span></strong>
          <p class="pro-sub">当前追番条目</p>
        </div>
        <div class="pro-icon">新</div>
      </div>

      <div class="mochi-metric-pro-card blue">
        <div class="pro-info">
          <span class="pro-label">就绪可观看剧集</span>
          <strong class="pro-value">{{ watchableTotal }} <span class="pro-unit">集</span></strong>
          <p class="pro-sub">本地已确认可播放</p>
        </div>
        <div class="pro-icon">播</div>
      </div>

      <div class="mochi-metric-pro-card purple">
        <div class="pro-info">
          <span class="pro-label">本地资源记录</span>
          <strong class="pro-value">{{ localAssetTotal }} <span class="pro-unit">个</span></strong>
          <p class="pro-sub">已整理到媒体目录</p>
        </div>
        <div class="pro-icon">库</div>
      </div>
    </div>

    <!-- 2. 中层网格 (Mochi Mid Grid: SVG Category Preference Chart + Awakening Progress Card) -->
    <div class="mochi-mid-grid">
      <!-- Donut preference chart -->
      <div class="mochi-chart-card">
        <div class="card-head-simple">
          <h4>任务状态分布</h4>
          <span class="tag-pill text-pink">实时运行态</span>
        </div>
        
        <div class="donut-chart-wrapper">
          <!-- Animated SVG Donut -->
          <div class="donut-svg-box">
            <svg class="donut-svg" viewBox="0 0 36 36">
              <path class="circle-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" fill="none" stroke="#FFF5F6" stroke-width="3" />
              <!-- Completed slice -->
              <path class="circle-segment pink" :stroke-dasharray="`${completedPercent}, 100`" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831" fill="none" stroke="var(--ani-pink)" stroke-width="3.5" stroke-linecap="round" />
              <!-- Active slice -->
              <path class="circle-segment blue" :stroke-dasharray="`${activePercent}, 100`" :stroke-dashoffset="`-${completedPercent}`" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831" fill="none" stroke="var(--ani-blue)" stroke-width="3.5" stroke-linecap="round" />
              <!-- Failed slice -->
              <path class="circle-segment purple" :stroke-dasharray="`${failedPercent}, 100`" :stroke-dashoffset="`-${completedPercent + activePercent}`" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831" fill="none" stroke="var(--ani-purple)" stroke-width="3.5" stroke-linecap="round" />
            </svg>
            <div class="donut-center-text">
              <span class="center-label">队列状态</span>
              <p class="center-value" v-if="completedPercent > 50">运行正常</p>
              <p class="center-value" v-else-if="failedPercent > 30">需要处理</p>
              <p class="center-value" v-else>等待任务</p>
            </div>
          </div>

          <!-- Legend list -->
          <div class="donut-legend">
            <div class="legend-row">
              <span class="legend-item"><span class="legend-dot pink"></span> 已完成 ({{ completedPercent }}%)</span>
              <span class="legend-count">{{ completedTasks }} 项</span>
            </div>
            <div class="legend-row">
              <span class="legend-item"><span class="legend-dot blue"></span> 活动中 ({{ activePercent }}%)</span>
              <span class="legend-count">{{ activeTasks }} 项</span>
            </div>
            <div class="legend-row">
              <span class="legend-item"><span class="legend-dot purple"></span> 异常项 ({{ failedPercent }}%)</span>
              <span class="legend-count">{{ failedTasks }} 项</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Level Up Card -->
      <div class="mochi-level-card">
        <div class="card-head-simple">
          <h4>本地资源覆盖</h4>
          <p class="card-subtitle-small">本地已缓存比例 (物理/就绪)</p>
        </div>
        
        <div class="level-showcase">
          <span class="level-emoji">AT</span>
          <h3 class="level-title">{{ levelTitle }}</h3>
          <p class="level-desc">本地资源与可观看条目的匹配情况</p>
        </div>

        <div class="level-progress-section">
          <div class="progress-labels">
            <span>覆盖率</span>
            <span>{{ levelProgress }}%</span>
          </div>
          <div class="level-progress-bar">
            <div class="progress-fill" :style="`width: ${levelProgress}%`"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- 3. 底层网格 (Mochi Bottom Grid: High-density Tasks + Radar Log Timeline) -->
    <div class="mochi-bottom-grid">
      
      <!-- 3.1 左半边：任务中心 (High-density Task Cards) -->
      <div class="mochi-tasks-panel">
        <div class="panel-header-row">
          <div class="header-titles">
            <h3>任务列表</h3>
            <p>管理自动同步、元数据抓取和缓存流水线</p>
          </div>
          <div class="header-taglines">
            <el-button size="small" plain @click="cancelAllGenericTasks">取消全部</el-button>
            <el-button size="small" plain @click="pauseAllGenericTasks">暂停全部</el-button>
            <el-button size="small" plain @click="resumeAllGenericTasks">继续全部</el-button>
            <el-button size="small" type="primary" plain @click="retryFailedGenericTasks">重试失败</el-button>
            <el-button size="small" type="primary" plain @click="clearCompletedDownloadTasks">清理</el-button>
          </div>
        </div>

        <!-- Task type toggle pills -->
        <div class="task-filter-strip">
          <button :class="{ active: !selectedTaskType }" @click="selectedTaskType = ''" class="filter-pill">
            <span>全部</span>
            <span v-if="(dashboard.tasks || []).length" class="badge">{{ (dashboard.tasks || []).length }}</span>
          </button>
          <button
            v-for="item in taskTypeRows"
            :key="item.type"
            :class="{ active: selectedTaskType === item.type }"
            @click="selectedTaskType = item.type"
            class="filter-pill"
          >
            <span>{{ item.name }}</span>
            <span v-if="item.total || item.running || item.pending || item.failed" class="badge" :class="{ warning: item.running, danger: item.failed }">
              {{ item.running ? '运行' : (item.pending ? '待审' : (item.failed ? '失败' : item.total)) }}
            </span>
          </button>
        </div>

        <!-- High-density Task cards grid -->
        <div class="task-cards-list-box">
          <div v-for="row in filteredConsoleTasks" :key="row.id" class="task-card-block">
            <div class="task-block-head">
              <div class="task-block-title">
                <el-tag :type="taskTag(row.status)" size="small">{{ row.status_text || taskStatusText(row) }}</el-tag>
                <strong class="title-text">{{ row.title }}</strong>
              </div>
              <span class="type-badge">{{ row.type_name }}</span>
            </div>

            <div class="task-block-progress">
              <div class="progress-bar-wrapper">
                <el-progress
                  :percentage="Number(row.progress || 0)"
                  :status="row.status === 'failed' ? 'exception' : (row.status === 'completed' ? 'success' : undefined)"
                  :stroke-width="6"
                  :show-text="false"
                />
              </div>
              <span class="progress-num">{{ Number(row.progress || 0) }}%</span>
            </div>

            <div class="task-block-foot">
              <span class="foot-time">{{ row.updated_at || '-' }}</span>
              <div class="block-actions">
                <el-button v-if="row.entry_id" size="small" plain @click="openQueueEntry(row)">打开</el-button>
                <el-button v-if="taskCanPause(row)" size="small" plain @click="pauseGenericTask(row)">暂停</el-button>
                <el-button v-if="taskCanResume(row)" size="small" type="primary" plain @click="resumeGenericTask(row)">继续</el-button>
                <el-button v-if="taskCanRetry(row)" size="small" type="primary" plain @click="retryGenericTask(row)">重试</el-button>
                <el-button v-if="taskCanCancel(row)" size="small" type="danger" plain @click="cancelGenericTask(row)">取消</el-button>
                <el-popconfirm v-if="taskCanClear(row)" title="清理这条任务记录？" @confirm="clearGenericTask(row)">
                  <template #reference>
                    <el-button size="small" plain>清理</el-button>
                  </template>
                </el-popconfirm>
              </div>
            </div>
          </div>
          <el-empty v-if="!filteredConsoleTasks.length" description="暂无活动任务" />
        </div>
      </div>

      <!-- 3.2 右半边：最近操作 -->
      <div class="mochi-radar-logs-panel">
        <div class="live-timeline-box">
          <div class="timeline-title-row">
            <h4>最近操作</h4>
            <div class="recent-title-actions">
              <span class="live-badge">最多 100 条</span>
              <el-button size="small" plain @click="clearRecentOperationEvents">清理</el-button>
            </div>
          </div>

          <div class="timeline-stream">
            <div class="stream-line"></div>
            
            <div v-for="item in recentOperationRows" :key="item.id" class="stream-item" :class="item.level === 'error' ? 'pink' : (item.level === 'warn' ? 'purple' : 'blue')">
              <span class="stream-dot"></span>
              <div class="stream-content">
                <div class="content-meta">
                  <strong>{{ item.title }}</strong>
                  <span class="time">{{ item.created_at || '-' }}</span>
                </div>
                <p class="comment">{{ item.message || item.action }}</p>
              </div>
            </div>

            <el-empty v-if="!recentOperations.length" description="暂无最近操作" :image-size="40" />
          </div>
          <div v-if="recentOperations.length > recentOperationRows.length" class="recent-operation-pager">
            <el-button size="small" plain :disabled="recentOperationPage <= 1" @click="recentOperationPage -= 1">上一页</el-button>
            <span>{{ recentOperationPage }} / {{ recentOperationPageCount }}</span>
            <el-button size="small" plain :disabled="recentOperationPage >= recentOperationPageCount" @click="recentOperationPage += 1">下一页</el-button>
          </div>
        </div>

      </div>

    </div>

  </section>
</template>
