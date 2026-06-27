<script setup>
import { computed, inject } from 'vue'

const app = inject('appContext') || {}

const discoverySourceGroups = computed(() => {
  const orderedSources = app.searchSources || []
  const groups = new Map()
  for (const source of orderedSources) {
    groups.set(Number(source.id || 0), {
      id: Number(source.id || 0),
      name: source.name || '搜索源',
      kind: source.kind || '',
      items: [],
    })
  }
  for (const item of app.discoveryState.items || []) {
    const resources = item.resources || []
    const sourceIds = Array.from(new Set(resources.map(row => Number(row.source_id || 0)).filter(Boolean)))
    const targetIds = sourceIds.length ? sourceIds : [0]
    for (const sourceId of targetIds) {
      if (!groups.has(sourceId)) {
        const sourceName = resources.find(row => Number(row.source_id || 0) === sourceId)?.source_name || '其他来源'
        groups.set(sourceId, { id: sourceId, name: sourceName, kind: '', items: [] })
      }
      groups.get(sourceId).items.push({
        ...item,
        resources: sourceId ? resources.filter(row => Number(row.source_id || 0) === sourceId) : resources,
      })
    }
  }
  return Array.from(groups.values()).filter(group => group.items.length)
})

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
    <el-card class="discovery-search-card">
      <div class="discovery-search-bar">
        <el-input
          v-model="app.discoveryState.keyword"
          clearable
          size="large"
          placeholder="搜索作品、资源标题或关键词"
          @keyup.enter="app.runDiscoverySearch"
        />
        <el-button type="primary" size="large" :loading="app.discoveryState.loading" @click="app.runDiscoverySearch">搜索</el-button>
      </div>
      <div class="discovery-hint">
        <span>会按设置中的搜索源顺序依次搜索；结果只是候选数据，收录或应用补全后才会写入媒体库。</span>
      </div>
    </el-card>

    <el-alert
      v-if="app.discoveryState.backfill_entry_id"
      type="info"
      show-icon
      :closable="false"
      title="当前是本季补全候选，只会补缺失集数或没有资源链接的集数。"
    />

    <div class="discovery-results-board" v-loading="app.discoveryState.loading">
      <section v-for="group in discoverySourceGroups" :key="group.id || group.name" class="discovery-source-section">
        <div class="discovery-source-head">
          <div>
            <strong>{{ group.name }}</strong>
            <span>{{ group.kind || '搜索源' }} · {{ group.items.length }} 个候选</span>
          </div>
        </div>
        <div class="discovery-grid">
          <article v-for="item in group.items" :key="`${group.id}-${item.id}`" class="discovery-card">
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
                <span>{{ item.resources?.length || 0 }} 条资源</span>
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
        </div>
      </section>
      <el-empty v-if="!app.discoveryState.loading && !discoverySourceGroups.length" description="输入关键词后搜索资源" />
    </div>
  </section>
</template>
