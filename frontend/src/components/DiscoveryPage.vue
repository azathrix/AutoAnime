<script setup>
import { computed, inject } from 'vue'

const app = inject('appContext') || {}

const sourceOptions = computed(() => app.searchSources || [])

function uniqueValues(resources, key) {
  return Array.from(new Set((resources || []).map(item => item?.[key]).filter(Boolean))).slice(0, 5)
}

function episodeText(resources) {
  const episodes = Array.from(new Set((resources || []).map(item => Number(item.episode_number || 0)).filter(Boolean))).sort((a, b) => a - b)
  if (!episodes.length) return '暂无集数'
  if (episodes.length <= 6) return episodes.map(item => `第${item}集`).join('、')
  return `${episodes.length} 集 · ${episodes[0]}-${episodes[episodes.length - 1]}`
}

function mediaTypeLabel(type) {
  if (type === 'movie') return '电影'
  if (type === 'tv') return '电视剧'
  return '动画'
}
</script>

<template>
  <section v-if="app.view === 'discovery'" class="discovery-page">
    <section class="discovery-hero-card">
      <div>
        <span>资源发现</span>
        <strong>聚合搜索源，找到可收录的作品和集数</strong>
        <p>发现结果只是候选数据，点击收录或应用补全后才写入媒体库。</p>
      </div>
      <div class="discovery-search-card">
        <el-input
          v-model="app.discoveryState.keyword"
          clearable
          size="large"
          placeholder="搜索作品、资源标题或关键词"
          @keyup.enter="app.runDiscoverySearch"
        />
        <div class="discovery-search-options">
          <el-select v-model="app.discoveryState.media_type" size="large">
            <el-option label="动画" value="anime" />
            <el-option label="电影" value="movie" />
            <el-option label="电视剧" value="tv" />
          </el-select>
          <el-input v-model="app.discoveryState.year" size="large" placeholder="年份" />
          <el-select v-model="app.discoveryState.source_ids" multiple clearable collapse-tags size="large" placeholder="全部搜索源">
            <el-option v-for="item in sourceOptions" :key="item.id" :label="item.name" :value="item.id" />
          </el-select>
          <el-button type="primary" size="large" :loading="app.discoveryState.loading" @click="app.runDiscoverySearch">搜索资源</el-button>
        </div>
        <el-button link type="primary" @click="app.view = 'settings'">管理搜索源</el-button>
      </div>
    </section>

    <el-alert
      v-if="app.discoveryState.backfill_entry_id"
      type="info"
      show-icon
      :closable="false"
      title="当前是本季补全候选，只会补缺失集数或没有资源链接的集数。"
    />

    <div class="discovery-grid mochi-discovery-grid" v-loading="app.discoveryState.loading">
      <article v-for="item in app.discoveryState.items" :key="item.id" class="discovery-card">
        <div class="discovery-cover">
          <img v-if="item.poster_url" :src="item.poster_url" />
          <span v-else>{{ (item.title || item.original_title || 'AN').slice(0, 2) }}</span>
        </div>
        <div class="discovery-body">
          <div class="discovery-card-head">
            <div>
              <h3>{{ item.title || item.original_title }}</h3>
              <p>{{ item.original_title || '-' }}</p>
            </div>
            <el-tag size="small">{{ mediaTypeLabel(item.media_type) }}</el-tag>
          </div>
          <div class="discovery-meta">
            <el-tag v-if="item.year" size="small" type="info">{{ item.year }}</el-tag>
            <el-tag v-if="item.bangumi_id" size="small" type="success">Bangumi {{ item.bangumi_id }}</el-tag>
            <el-tag v-if="item.tmdb_id" size="small" type="warning">TMDB {{ item.tmdb_id }}</el-tag>
          </div>
          <p class="discovery-summary">{{ item.summary || episodeText(item.resources) }}</p>
          <div class="discovery-resource-tags">
            <span v-for="value in uniqueValues(item.resources, 'subtitle_group')" :key="`g-${value}`">{{ value }}</span>
            <span v-for="value in uniqueValues(item.resources, 'resolution')" :key="`r-${value}`">{{ value }}</span>
            <span v-for="value in uniqueValues(item.resources, 'language')" :key="`l-${value}`">{{ value }}</span>
          </div>
          <div class="discovery-stat-row">
            <span>{{ episodeText(item.resources) }}</span>
            <span>{{ item.resource_count || item.resources?.length || 0 }} 条资源</span>
            <span>{{ item.source_count || 1 }} 个来源</span>
          </div>
          <div class="discovery-actions">
            <el-button
              v-if="app.discoveryState.backfill_entry_id"
              type="primary"
              :plain="Number(app.discoveryState.best_result_id || 0) !== Number(item.id || 0)"
              @click="app.applyBackfillResult(item)"
            >
              应用补全
            </el-button>
            <el-button plain @click="app.openDiscoveryCollectDraft(item)">收录</el-button>
          </div>
        </div>
      </article>
      <el-empty v-if="!app.discoveryState.loading && !(app.discoveryState.items || []).length" description="输入关键词后搜索资源" />
    </div>
  </section>
</template>
