<script>
import { appContextComponent } from '../composables/appContext'

export default appContextComponent()
</script>

<template>
      <section v-if="isMediaCatalogView" class="library media-page">
        <div class="media-command-bar">
          <div class="media-search-box">
            <strong>{{ currentMediaPageTitle }}影视墙</strong>
            <el-input v-model="keyword" clearable :placeholder="`搜索${currentMediaPageTitle}、Bangumi ID、TMDB ID、标题`" />
          </div>
          <div class="media-command-actions">
            <el-button plain @click="advancedFilterOpen = !advancedFilterOpen">{{ advancedFilterOpen ? '收起筛选' : '高级筛选' }}</el-button>
            <el-button type="primary" @click="openMediaWizard">收录{{ currentMediaPageTitle }}</el-button>
          </div>
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
        <div
          v-infinite-scroll="loadMoreCatalog"
          :infinite-scroll-disabled="catalogState.loading || catalogState.loading_more || !catalogState.has_more"
          :infinite-scroll-distance="240"
          class="anime-grid catalog-card-grid mochi-media-grid"
        >
          <article v-for="item in filteredSeries" :key="item.id" class="anime-card catalog-card mochi-media-card" @click="openEntry(item.id, 'library', entryMediaType(item))">
            <div class="cover poster-cover media-card-cover">
              <img v-if="item.poster_url" :src="item.poster_url" />
              <span v-else>{{ cardInitials(item) }}</span>
            </div>
            <div class="anime-body">
              <div class="media-card-kicker">{{ cardSubtitle(item) }}</div>
              <h3>{{ entryTitle(item) }}</h3>
              <div class="tagline">
                <el-tag size="small" type="success">可观看 {{ watchableCount(item) }} 集</el-tag>
                <el-tag v-if="hasRecentUpdate(item)" size="small" type="primary">已更新</el-tag>
                <el-tag v-for="score in metadataScores(item)" :key="score.key" size="small" type="warning">{{ score.label }}</el-tag>
              </div>
              <div class="media-card-foot">
                <span>{{ item.year || '未知年份' }}</span>
                <span>{{ mediaTypeLabel(entryMediaType(item)) }}</span>
              </div>
            </div>
          </article>
          <el-empty v-if="!catalogState.loading && !filteredSeries.length" :description="`没有匹配的${currentMediaPageTitle}`" />
        </div>
        <div class="catalog-load-more">
          <el-button v-if="catalogState.has_more" :loading="catalogState.loading_more" plain @click="loadMoreCatalog">加载更多</el-button>
        </div>
      </section>

</template>

