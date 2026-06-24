<script>
import { appContextComponent } from '../composables/appContext'

export default appContextComponent()
</script>

<template>
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
                <p>{{ normalizedSeasonLabel(selectedEntry) }}</p>
                <div class="tagline">
                  <el-tag size="small">{{ mediaTypeLabel(selectedEntry.media_type) }}</el-tag>
                  <el-tag size="small" type="info">{{ regionLabel(selectedEntry.region) }}</el-tag>
                  <el-tag v-if="selectedEntryDomain === 'seasonal'" size="small" type="success">可观看 {{ watchableCount(selectedEntryStats) }} 集</el-tag>
                  <el-tag v-if="selectedEntryDomain === 'seasonal'" size="small" type="primary">追番中</el-tag>
                  <el-tag v-for="score in metadataScores(selectedEntry)" :key="score.key" size="small" type="warning">{{ score.label }}</el-tag>
                </div>
              </div>
            </div>
            <el-descriptions :column="2" border class="entry-meta-descriptions">
              <el-descriptions-item label="标题">{{ selectedEntry.title_cn || selectedEntry.display_title || '-' }}</el-descriptions-item>
              <el-descriptions-item label="媒体类型">{{ mediaTypeLabel(selectedEntry.media_type) }}</el-descriptions-item>
              <el-descriptions-item label="Bangumi ID">
                <a v-if="selectedEntry.bangumi_id" :href="`https://bgm.tv/subject/${selectedEntry.bangumi_id}`" target="_blank" rel="noreferrer">{{ selectedEntry.bangumi_id }}</a>
                <span v-else>-</span>
              </el-descriptions-item>
              <el-descriptions-item label="TMDB ID">
                <a v-if="selectedEntry.tmdb_id" :href="`https://www.themoviedb.org/${selectedEntry.media_type === 'movie' ? 'movie' : 'tv'}/${selectedEntry.tmdb_id}`" target="_blank" rel="noreferrer">{{ selectedEntry.tmdb_id }}</a>
                <span v-else>-</span>
              </el-descriptions-item>
              <el-descriptions-item label="首播月份">{{ selectedEntry.year || '-' }} / {{ selectedEntry.month || '-' }}</el-descriptions-item>
              <el-descriptions-item label="季 / 章节 / 部分">{{ normalizedSeasonLabel(selectedEntry) }}</el-descriptions-item>
              <el-descriptions-item label="Bangumi 评分">{{ selectedEntry.bangumi_score ? Number(selectedEntry.bangumi_score).toFixed(1) : '-' }}</el-descriptions-item>
              <el-descriptions-item label="TMDB 评分">{{ selectedEntry.tmdb_score ? Number(selectedEntry.tmdb_score).toFixed(1) : '-' }}</el-descriptions-item>
              <el-descriptions-item label="国家 / 地区">{{ regionLabel(selectedEntry.region) }}</el-descriptions-item>
              <el-descriptions-item label="别名" :span="2">{{ selectedEntry.title_romaji || selectedEntry.title_raw || '-' }}</el-descriptions-item>
              <el-descriptions-item label="标签" :span="2">
                <div class="mini-tag-row">
                  <span v-for="tag in catalogTags(selectedEntry)" :key="tag">{{ tag }}</span>
                  <em v-if="!catalogTags(selectedEntry).length">-</em>
                </div>
              </el-descriptions-item>
              <el-descriptions-item label="简介" :span="2">{{ selectedEntry.summary || '-' }}</el-descriptions-item>
            </el-descriptions>
            <div class="drawer-actions">
              <el-button type="primary" @click="openEntryEditDialog">编辑信息</el-button>
              <el-popconfirm
                v-if="selectedEntryDomain === 'seasonal'"
                title="取消追番后新番页不再显示，番剧库仍会保留该动画条目。确定取消？"
                @confirm="archiveCurrentEntry"
              >
                <template #reference>
                  <el-button plain>取消追番</el-button>
                </template>
              </el-popconfirm>
              <el-button
                v-if="selectedEntryDomain !== 'seasonal'"
                plain
                @click="setCurrentEntryFollowing(!Number(selectedEntry.following || 0))"
              >
                {{ Number(selectedEntry.following || 0) ? '取消追番' : '追番' }}
              </el-button>
              <el-popconfirm title="删除后只隐藏/清理数据库记录，不删除本地媒体文件。确定删除？" @confirm="deleteCurrentEntry">
                <template #reference>
                  <el-button type="danger" plain>删除条目</el-button>
                </template>
              </el-popconfirm>
            </div>
          </el-tab-pane>
          <el-tab-pane label="集数资源">
            <div class="resource-toolbar">
              <el-dropdown trigger="click" @command="command => {
                if (command === 'download') downloadCurrentEntryResources()
                if (command === 'refresh') refreshCurrentEntryLocalStatus()
                if (command === 'backfill') backfillCurrentEntrySeason()
                if (command === 'match') openServerFileBrowser('match')
                if (command === 'organize') organizeCurrentEntryLocalFiles()
                if (command === 'import') openEpisodeImportDialog()
              }">
                <el-button type="primary">操作</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="download">批量下载</el-dropdown-item>
                    <el-dropdown-item command="refresh">刷新本地状态</el-dropdown-item>
                    <el-dropdown-item command="backfill">补全本季</el-dropdown-item>
                    <el-dropdown-item command="match">批量匹配本地资源</el-dropdown-item>
                    <el-dropdown-item command="organize">整理本地资源</el-dropdown-item>
                    <el-dropdown-item command="import">手动导入集数</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
            <el-table
              :data="entryResourceRows"
              row-key="key"
              :expand-row-keys="expandedResourceKeys"
              height="620"
              class="episode-resource-table"
              empty-text="暂无集数资源"
              @row-click="toggleEntryResourceRow"
            >
              <el-table-column type="expand" width="44">
                <template #default="{ row }">
                  <div class="resource-expand">
                    <section class="resource-expand-section">
                      <strong>视频</strong>
                      <div><span>资源链接</span><code>{{ row.source_ref || row.magnet || row.torrent_url || '-' }}</code></div>
                      <div><span>本地视频</span><code>{{ row.local_path || '-' }}</code></div>
                      <div><span>下载任务</span><code>{{ row.download_progress_text || row.download_status || '-' }}</code></div>
                    </section>
                    <section class="resource-expand-section">
                      <strong>字幕</strong>
                      <div><span>字幕链接</span><code>{{ row.subtitle_url || '-' }}</code></div>
                      <div><span>本地字幕</span><code>{{ row.subtitle_file || '-' }}</code></div>
                      <div><span>字幕类型</span><code>{{ subtitleFormatText(row.subtitle_format) }}</code></div>
                      <div><span>语言</span><code>{{ row.language || '-' }}</code></div>
                    </section>
                    <section class="resource-expand-section">
                      <strong>识别信息</strong>
                      <div><span>字幕组</span><code>{{ row.subtitle_group || '-' }}</code></div>
                      <div><span>分辨率</span><code>{{ row.resolution || '-' }}</code></div>
                      <div><span>来源标题</span><code>{{ row.resource_title || '-' }}</code></div>
                    </section>
                  </div>
                </template>
              </el-table-column>
              <el-table-column prop="episode_number" label="集" width="58" />
              <el-table-column prop="display_name" label="名称" min-width="280" show-overflow-tooltip />
              <el-table-column label="可观看" width="94">
                <template #default="{ row }">
                  <el-tag :type="episodeDownloadTag(row)" size="small">{{ episodeDownloadText(row) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="92">
                <template #default="{ row }">
                  <el-dropdown trigger="click" @command="command => {
                    if (command === 'refresh') refreshEpisodeResource(row)
                    if (command === 'download') downloadEpisodeResource(row)
                    if (command === 'edit') openEpisodeResourceEditor(row)
                    if (command === 'delete') deleteEpisodeResource(row)
                  }">
                    <el-button size="small" type="primary" @click.stop>操作</el-button>
                    <template #dropdown>
                      <el-dropdown-menu>
                        <el-dropdown-item command="refresh">刷新</el-dropdown-item>
                        <el-dropdown-item command="download" :disabled="row.downloaded || !row.source_ref">下载</el-dropdown-item>
                        <el-dropdown-item command="edit">编辑</el-dropdown-item>
                        <el-dropdown-item command="delete">删除</el-dropdown-item>
                      </el-dropdown-menu>
                    </template>
                  </el-dropdown>
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </el-drawer>
</template>
