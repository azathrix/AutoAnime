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
              <el-descriptions-item label="国家 / 地区">{{ regionLabel(selectedEntry.region) }}</el-descriptions-item>
              <el-descriptions-item label="追番状态">{{ selectedEntryDomain === 'seasonal' ? '追番中' : '普通媒体库条目' }}</el-descriptions-item>
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
              <el-button type="primary" @click="downloadCurrentEntryResources">批量下载</el-button>
              <el-button plain @click="openEpisodeImportDialog">手动导入集数</el-button>
              <el-button type="primary" @click="openBatchSubtitleDialog">字幕批量配置</el-button>
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
                      <strong>资源信息</strong>
                      <div><span>字幕组</span><code>{{ row.subtitle_group || '-' }}</code></div>
                      <div><span>分辨率</span><code>{{ row.resolution || '-' }}</code></div>
                      <div><span>语言</span><code>{{ row.language || '-' }}</code></div>
                      <div><span>字幕类型</span><code>{{ subtitleFormatText(row.subtitle_format) }}</code></div>
                      <div><span>来源类型</span><code>{{ sourceModeText(row.source_type) }}</code></div>
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
                      <div><span>下载进度</span><code>{{ row.download_progress ? `${row.download_progress}%` : '-' }}</code></div>
                      <div><span>进度详情</span><code>{{ row.download_progress_text || '-' }}</code></div>
                      <div><span>下载错误</span><code>{{ row.download_error || '-' }}</code></div>
                      <div class="resource-expand-actions">
                        <el-button size="small" plain @click="openEpisodeResourceEditor(row)">配置</el-button>
                        <el-button size="small" plain :disabled="row.downloaded || !row.release_id" @click="downloadEpisodeResource(row)">下载</el-button>
                        <el-button size="small" plain :disabled="!episodeCanPause(row)" @click="pauseEpisodeDownload(row)">暂停</el-button>
                        <el-button size="small" plain :disabled="!episodeCanCancel(row)" @click="cancelEpisodeDownload(row)">取消</el-button>
                        <el-button size="small" plain @click="refreshEpisodeResource(row)">刷新</el-button>
                        <el-popconfirm title="删除该资源配置？已下载或正在下载的资源需要先清理任务。" @confirm="deleteEpisodeResource(row)">
                          <template #reference>
                            <el-button size="small" type="danger">删除</el-button>
                          </template>
                        </el-popconfirm>
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
</template>

