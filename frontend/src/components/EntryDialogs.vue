<script>
import { appContextComponent } from '../composables/appContext'

export default appContextComponent()
</script>

<template>
    <el-dialog v-model="entryEditDialogOpen" title="编辑作品信息" width="760px" top="4vh" class="entry-edit-dialog">
      <el-form :model="entryEditForm" label-position="top">
        <el-form-item label="中文标题">
          <div class="field-with-action">
            <el-input v-model="entryEditForm.title_cn" />
            <el-button type="primary" plain @click="openMetadataSearch('bangumi', 'entry')">匹配</el-button>
          </div>
        </el-form-item>
        <div class="form-row">
          <el-form-item label="首播月份">
            <el-date-picker v-model="entryEditForm.release_month" type="month" value-format="YYYY-MM" format="YYYY年MM月" placeholder="选择月份" />
          </el-form-item>
          <el-form-item label="季 / 章节 / 部分">
            <el-input-number v-model="entryEditForm.season_number" :min="1" :max="99" controls-position="right" />
          </el-form-item>
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
        <div class="form-row">
          <el-form-item label="Bangumi ID"><el-input v-model="entryEditForm.bangumi_id" /></el-form-item>
          <el-form-item label="TMDB ID"><el-input v-model="entryEditForm.tmdb_id" /></el-form-item>
        </div>
        <div class="form-row">
          <el-form-item label="Bangumi 评分">
            <el-input-number v-model="entryEditForm.bangumi_score" :min="0" :max="10" :precision="1" :step="0.1" controls-position="right" />
          </el-form-item>
          <el-form-item label="TMDB 评分">
            <el-input-number v-model="entryEditForm.tmdb_score" :min="0" :max="10" :precision="1" :step="0.1" controls-position="right" />
          </el-form-item>
        </div>
        <el-form-item label="原名"><el-input v-model="entryEditForm.title_raw" /></el-form-item>
        <el-form-item label="海报 URL"><el-input v-model="entryEditForm.poster_url" /></el-form-item>
        <el-form-item label="标签">
          <el-input v-model="entryEditForm.tags_text" type="textarea" :rows="3" placeholder="逗号分隔，例如 轻改，校园，智斗" />
        </el-form-item>
        <el-form-item label="简介"><el-input v-model="entryEditForm.summary" type="textarea" :rows="4" /></el-form-item>
        <el-progress v-if="metadataFetching || metadataFetchProgress" :percentage="metadataFetchProgress" :status="metadataFetchProgress >= 100 ? 'success' : undefined" />
      </el-form>
      <template #footer>
        <el-button plain @click="clearEntryEditForm">清空</el-button>
        <el-button plain :loading="metadataFetching" @click="fetchEntryMetadata">按 ID 刷新元数据</el-button>
        <el-button @click="entryEditDialogOpen = false">取消</el-button>
        <el-button type="primary" @click="saveEntryEditForm">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="episodeResourceDialogOpen" title="配置集数资源" width="820px" top="4vh">
      <el-form :model="episodeResourceForm" label-position="top">
        <div class="form-row">
          <el-form-item label="集数"><el-input v-model="episodeResourceForm.episode_number" disabled /></el-form-item>
          <el-form-item label="资源类型">
            <el-select v-model="episodeResourceForm.source_type">
              <el-option label="磁力 / 下载链接" value="manual" />
              <el-option label="仅占位" value="metadata" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="当前资源标题"><el-input v-model="episodeResourceForm.title" /></el-form-item>
        <el-form-item label="资源链接">
          <el-input v-model="episodeResourceForm.source_ref" placeholder="magnet:? / https://... / 可留空作为占位资源" />
        </el-form-item>
        <div class="form-row">
          <el-form-item label="分辨率"><el-input v-model="episodeResourceForm.resolution" placeholder="1080p" /></el-form-item>
          <el-form-item label="字幕组"><el-input v-model="episodeResourceForm.subtitle_group" /></el-form-item>
        </div>
        <div class="form-row">
          <el-form-item label="语言"><el-input v-model="episodeResourceForm.language" /></el-form-item>
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
        <el-form-item label="本地视频文件">
          <div class="field-with-action">
            <el-input v-model="episodeResourceForm.local_path" placeholder="/media/anime/..." />
            <el-button plain @click="openServerFileBrowser('video')">选择文件</el-button>
          </div>
        </el-form-item>
        <el-form-item label="本地字幕文件">
          <div class="field-with-action">
            <el-input v-model="episodeResourceForm.subtitle_path" placeholder="/media/anime/.../*.ass" />
            <el-button plain @click="openServerFileBrowser('subtitle')">选择字幕</el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="episodeResourceDialogOpen = false">取消</el-button>
        <el-button type="primary" @click="saveEpisodeResource">保存配置</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="fileBrowser.open" :title="fileBrowser.mode === 'match' ? '批量匹配本地资源' : fileBrowser.mode === 'subtitle' ? '选择本地字幕文件' : '选择本地视频文件'" width="760px" top="6vh">
      <div class="file-browser">
        <div class="file-browser-toolbar">
          <el-button :disabled="!fileBrowser.parent" @click="browseServerFiles(fileBrowser.parent)">上一级</el-button>
          <code>{{ fileBrowser.current || '/media' }}</code>
        </div>
        <el-table :data="fileBrowser.items" height="420" v-loading="fileBrowser.loading" @row-dblclick="selectServerFile">
          <el-table-column label="名称" min-width="260" show-overflow-tooltip>
            <template #default="{ row }">
              <span>{{ row.kind === 'directory' ? '[目录]' : '[文件]' }} {{ row.name }}</span>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.kind === 'directory' ? 'info' : row.kind === 'video' ? 'success' : 'warning'">
                {{ row.kind === 'directory' ? '目录' : row.kind === 'video' ? '视频' : '字幕' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="path" label="路径" min-width="280" show-overflow-tooltip />
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button size="small" type="primary" @click="selectServerFile(row)">{{ row.kind === 'directory' ? '进入' : fileBrowser.mode === 'match' ? '匹配' : '选择' }}</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <template #footer>
        <el-button v-if="fileBrowser.mode === 'match'" type="primary" @click="selectServerFile({ kind: 'directory', path: fileBrowser.current, selectCurrent: true })">匹配当前目录</el-button>
        <el-button @click="fileBrowser.open = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="batchSubtitleDialogOpen" title="字幕批量配置" width="760px" top="4vh">
      <div class="guided-dialog">
        <el-steps :active="batchSubtitleStep" simple>
          <el-step title="提供字幕" />
          <el-step title="匹配规则" />
          <el-step title="确认写入" />
        </el-steps>
        <div v-if="batchSubtitleStep === 0" class="guided-step">
          <el-alert type="info" show-icon :closable="false" title="粘贴字幕下载链接；文件名或链接里包含集数时会自动匹配到对应集。" />
          <el-form :model="batchSubtitleForm" label-position="top">
            <el-form-item label="字幕链接 / 文件名">
              <el-input v-model="batchSubtitleForm.subtitles_text" type="textarea" :rows="8" placeholder="https://example.com/show.05.ass&#10;[Subtitle] Show - 06.srt" />
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

    <el-dialog v-model="episodeImportDialogOpen" title="手动导入集数资源" width="820px" top="4vh">
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

    <el-dialog v-model="processorSettingsDialogOpen" title="下载处理器设置" width="520px" top="4vh">
      <el-form :model="processorSettingsForm" label-position="top">
        <el-alert
          type="info"
          show-icon
          :closable="false"
          title="控制同时运行的下载处理器数量。过高可能让下载器或 NAS 压力变大，建议从 2-4 开始。"
          class="settings-alert"
        />
        <el-form-item label="下载并发数">
          <el-input-number v-model="processorSettingsForm.download_concurrency" :min="1" :max="12" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="processorSettingsDialogOpen = false">取消</el-button>
        <el-button type="primary" @click="saveProcessorSettings">保存设置</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="scheduledSettingsDialogOpen" title="定时任务设置" width="520px" top="4vh">
      <el-form :model="scheduledJobForm" label-position="top">
        <el-alert
          type="info"
          show-icon
          :closable="false"
          :title="selectedScheduledJob?.job_key === 'rss_scan' ? '配置 RSS 自动扫描。关闭后只会在手动触发时扫描。' : '配置运行时队列恢复调度。通常保持开启即可。'"
          class="settings-alert"
        />
        <div class="form-row">
          <el-form-item label="启用定时任务"><el-switch v-model="scheduledJobForm.enabled" /></el-form-item>
          <el-form-item label="执行间隔（分钟）"><el-input-number v-model="scheduledJobForm.interval_minutes" :min="1" /></el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="scheduledSettingsDialogOpen = false">取消</el-button>
        <el-button type="primary" @click="saveScheduledJob">保存设置</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="rssDialogOpen" title="RSS 订阅" width="760px" top="4vh">
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

    <el-dialog v-model="metadataSearchDialogOpen" title="作品匹配" width="900px" top="4vh">
      <div class="metadata-search-dialog">
        <el-steps :active="metadataSearchProvider === 'bangumi' ? 0 : 1" finish-status="success" simple class="metadata-match-steps">
          <el-step title="匹配 Bangumi" />
          <el-step title="匹配 TMDB" />
        </el-steps>
        <div class="metadata-search-bar">
          <el-input v-model="metadataSearchKeyword" clearable placeholder="输入作品名" @keyup.enter="runMetadataSearch" />
          <el-button type="primary" :loading="metadataSearchLoading" @click="runMetadataSearch">匹配</el-button>
        </div>
        <el-alert
          v-if="!settings.tmdb_token"
          type="warning"
          show-icon
          :closable="false"
          title="TMDB 搜索需要先在设置中心配置 TMDB token；Bangumi 搜索不受影响。"
        />
        <el-tabs v-model="metadataSearchProvider" class="metadata-result-tabs">
          <el-tab-pane label="Bangumi" name="bangumi">
            <div class="metadata-result-list" v-loading="metadataSearchLoading">
              <article
                v-for="item in metadataSearchResults.bangumi"
                :key="`${item.provider}-${item.id}`"
                :class="['metadata-result-card', { active: selectedMetadataCandidate('bangumi')?.id === item.id }]"
                @click="selectMetadataCandidate(item)"
              >
                <img v-if="item.poster_url" :src="item.poster_url" />
                <span v-else>{{ (item.title || item.original_title || '候选').slice(0, 2) }}</span>
                <div>
                  <strong>{{ item.title || item.original_title }}</strong>
                  <small>{{ item.original_title || '-' }}</small>
                  <p>{{ item.summary || '暂无简介' }}</p>
                  <code>{{ item.provider }} · {{ item.id }} · {{ item.year || '年份未知' }}</code>
                  <em v-if="selectedMetadataCandidate('bangumi')?.id === item.id">已选中，下一步匹配 TMDB</em>
                </div>
              </article>
              <el-empty v-if="!metadataSearchLoading && !metadataSearchResults.bangumi.length" description="暂无 Bangumi 匹配结果" />
            </div>
          </el-tab-pane>
          <el-tab-pane label="TMDB" name="tmdb">
            <div class="metadata-result-list" v-loading="metadataSearchLoading">
              <article
                v-for="item in metadataSearchResults.tmdb"
                :key="`${item.provider}-${item.id}`"
                :class="['metadata-result-card', { active: selectedMetadataCandidate('tmdb')?.id === item.id }]"
                @click="selectMetadataCandidate(item)"
              >
                <img v-if="item.poster_url" :src="item.poster_url" />
                <span v-else>{{ (item.title || item.original_title || '候选').slice(0, 2) }}</span>
                <div>
                  <strong>{{ item.title || item.original_title }}</strong>
                  <small>{{ item.original_title || '-' }}</small>
                  <p>{{ item.summary || '暂无简介' }}</p>
                  <code>{{ item.provider }} · {{ item.id }} · {{ item.year || '年份未知' }}</code>
                  <em v-if="selectedMetadataCandidate('tmdb')?.id === item.id">已选中</em>
                </div>
              </article>
              <el-empty v-if="!metadataSearchLoading && !metadataSearchResults.tmdb.length" description="暂无 TMDB 匹配结果" />
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
      <template #footer>
        <el-button @click="metadataSearchDialogOpen = false">取消</el-button>
        <el-button plain @click="skipMetadataProvider">{{ metadataSearchProvider === 'bangumi' ? '跳过 Bangumi' : '跳过 TMDB' }}</el-button>
        <el-button v-if="metadataSearchProvider === 'bangumi'" type="primary" @click="metadataSearchProvider = 'tmdb'">下一步 TMDB</el-button>
        <el-button v-else type="primary" @click="confirmMetadataMatch">确认填入</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="mediaWizardOpen" :title="mediaWizardTitle" width="920px" top="4vh">
      <el-steps :active="mediaWizardStep" finish-status="success" align-center>
        <el-step title="选择来源" />
        <el-step title="作品信息" />
        <el-step title="集数资源" />
        <el-step title="字幕配置" />
        <el-step title="确认收录" />
      </el-steps>
      <div class="wizard-panel">
        <template v-if="mediaWizardStep === 0">
          <div class="wizard-intro">
            <strong>选择收录来源</strong>
            <span>可以先给磁力或下载链接，系统会用链接提取作品线索；也可以只手动登记作品。</span>
          </div>
          <el-tabs v-model="mediaWizardDraft.source_mode" class="wizard-source-tabs">
            <el-tab-pane label="磁力 / 下载链接" name="link">
              <el-form label-position="top" class="wizard-form">
                <el-form-item label="资源链接">
                  <el-input v-model="mediaWizardDraft.resource_input" type="textarea" :rows="6" placeholder="每行一个磁力、种子或下载链接" />
                </el-form-item>
                <div class="wizard-input-actions">
                  <el-button type="primary" @click="addMediaWizardResourceLines">添加到集数资源</el-button>
                </div>
              </el-form>
            </el-tab-pane>
            <el-tab-pane label="手动收录" name="metadata">
              <el-alert type="info" show-icon :closable="false" title="只登记作品，不配置资源；后续可在卡片详情里继续添加集数资源。" />
            </el-tab-pane>
          </el-tabs>
        </template>
        <template v-else-if="mediaWizardStep === 1">
          <div class="wizard-intro">
            <strong>确认作品信息</strong>
            <span>先填写作品标题，再点击匹配，从 Bangumi 或 TMDB 结果中选择并填入基础信息。</span>
          </div>
          <el-form :model="mediaWizardDraft" label-position="top" class="wizard-form">
            <div class="wizard-form-grid labeled">
              <el-form-item label="作品标题" class="full-row">
                <div class="field-with-action">
                  <el-input v-model="mediaWizardDraft.title" placeholder="例如 欢迎来到实力至上主义的教室 第四季" />
                  <el-button type="primary" plain @click="openMetadataSearch('bangumi', 'wizard')">匹配</el-button>
                </div>
              </el-form-item>
              <el-form-item label="Bangumi ID">
                <el-input v-model="mediaWizardDraft.bangumi_id" placeholder="动画优先使用 Bangumi ID" />
              </el-form-item>
              <el-form-item label="TMDB ID">
                <el-input v-model="mediaWizardDraft.tmdb_id" placeholder="电影/电视剧可填 TMDB ID" />
              </el-form-item>
              <el-form-item label="首播月份">
                <el-date-picker v-model="mediaWizardDraft.release_month" type="month" value-format="YYYY-MM" format="YYYY年MM月" placeholder="选择月份" />
              </el-form-item>
              <el-form-item label="季 / 章节 / 部分">
                <el-input-number v-model="mediaWizardDraft.season_number" :min="1" :max="99" controls-position="right" />
              </el-form-item>
              <el-form-item label="国家 / 地区">
                <el-select v-model="mediaWizardDraft.region" clearable placeholder="可选">
                  <el-option label="日本" value="jp" />
                  <el-option label="中国" value="cn" />
                  <el-option label="欧美" value="us" />
                  <el-option label="韩国" value="kr" />
                  <el-option label="其他" value="other" />
                </el-select>
              </el-form-item>
              <el-form-item label="海报 URL"><el-input v-model="mediaWizardDraft.poster_url" placeholder="可选，匹配后会自动填入" /></el-form-item>
              <el-form-item label="标签"><el-input v-model="mediaWizardDraft.tags_text" type="textarea" :rows="2" placeholder="逗号分隔，例如 轻改，校园，智斗" /></el-form-item>
              <el-form-item label="简介" class="full-row"><el-input v-model="mediaWizardDraft.summary" type="textarea" :rows="4" /></el-form-item>
            </div>
          </el-form>
        </template>
        <template v-else-if="mediaWizardStep === 2">
          <div class="wizard-intro">
            <strong>配置集数资源</strong>
            <span>一行就是一集资源。自动识别只是初稿，集数、标题、来源都可以手动修正。</span>
          </div>
          <el-tabs v-model="mediaWizardDraft.source_mode" class="wizard-source-tabs">
            <el-tab-pane label="磁力 / 下载链接" name="link">
              <div class="wizard-resource-input">
                <el-input v-model="mediaWizardDraft.resource_input" type="textarea" :rows="4" placeholder="每行一个资源链接" />
                <div class="wizard-input-actions">
                  <el-button type="primary" @click="addMediaWizardResourceLines">添加到集数资源</el-button>
                </div>
              </div>
            </el-tab-pane>
          </el-tabs>
          <div class="wizard-resource-list">
            <div class="wizard-resource-list-head">
              <strong>集数资源</strong>
              <span>{{ mediaWizardResourceRows.length }} 条</span>
            </div>
            <div v-for="(item, index) in mediaWizardResourceItems" :key="item.id" class="wizard-resource-row">
              <el-input-number v-model="item.episode_number" :min="1" :max="999" controls-position="right" />
              <el-tag type="primary">下载</el-tag>
              <el-input v-model="item.title" placeholder="资源标题" />
              <el-input v-model="item.source_ref" placeholder="magnet:? / https://... / 种子链接" />
              <el-button type="danger" plain @click="removeMediaWizardResourceItem(index)">删除</el-button>
              <div class="wizard-resource-subline">
                <el-select v-model="item.subtitle_format" clearable placeholder="字幕类型">
                  <el-option label="无字幕 / 未配置" value="" />
                  <el-option label="外挂" value="external" />
                  <el-option label="内封（软字幕）" value="muxed" />
                  <el-option label="内嵌（硬字幕）" value="embedded" />
                </el-select>
                <el-input v-model="item.language" placeholder="语言，例如 简繁、简体、双语" />
                <el-input v-model="item.subtitle_ref" placeholder="这一集的字幕链接 / 字幕文件名，可留空" />
              </div>
            </div>
            <el-empty v-if="!mediaWizardResourceRows.length" description="还没有集数资源；可以只登记作品，也可以先添加磁链、种子或下载链接。" />
          </div>
        </template>
        <template v-else-if="mediaWizardStep === 3">
          <div class="wizard-intro">
            <strong>配置字幕</strong>
            <span>字幕会按文件名或链接里的集数自动匹配到对应集。识别不准时可以直接修改集数。</span>
          </div>
          <el-tabs class="wizard-source-tabs">
            <el-tab-pane label="字幕链接 / 文件名" name="link-subtitle">
              <div class="wizard-resource-input">
                <el-input v-model="mediaWizardDraft.subtitle_input" type="textarea" :rows="4" placeholder="每行一个字幕链接或字幕文件名" />
                <div class="wizard-input-actions">
                  <el-button type="primary" @click="addMediaWizardSubtitleLines">添加字幕</el-button>
                </div>
              </div>
            </el-tab-pane>
          </el-tabs>
          <div class="wizard-resource-list" v-if="mediaWizardSubtitleRows.length">
            <div class="wizard-resource-list-head">
              <strong>批量字幕</strong>
              <span>{{ mediaWizardSubtitleRows.length }} 条</span>
            </div>
            <div v-for="(item, index) in mediaWizardSubtitleItems" :key="item.id" class="wizard-resource-row subtitle-only">
              <el-input-number v-model="item.episode_number" :min="1" :max="999" controls-position="right" />
              <el-tag type="warning">字幕</el-tag>
              <el-input v-model="item.subtitle_ref" placeholder="字幕链接 / 字幕文件名" />
              <el-select v-model="item.subtitle_format" clearable placeholder="字幕类型">
                <el-option label="外挂" value="external" />
                <el-option label="内封（软字幕）" value="muxed" />
                <el-option label="内嵌（硬字幕）" value="embedded" />
              </el-select>
              <el-input v-model="item.language" placeholder="语言" />
              <el-button type="danger" plain @click="removeMediaWizardSubtitleItem(index)">删除</el-button>
            </div>
          </div>
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
              <div><span>资源</span><code>{{ mediaWizardResourceRows.length ? `${mediaWizardResourceRows.length} 条` : '只登记作品' }}</code></div>
              <div><span>字幕链接</span><code>{{ mediaWizardSubtitleRows.length ? `${mediaWizardSubtitleRows.length} 条` : '未填写字幕' }}</code></div>
              <div><span>字幕规则</span><code>{{ subtitleFormatText(mediaWizardDraft.subtitle_format) || '-' }} · {{ mediaWizardDraft.language || '-' }}</code></div>
            </div>
          </div>
        </template>
      </div>
      <template #footer>
        <el-button @click="mediaWizardOpen = false">关闭</el-button>
        <el-button :disabled="mediaWizardStep <= 0" @click="mediaWizardStep -= 1">上一步</el-button>
        <el-button v-if="mediaWizardStep < 4" type="primary" @click="advanceMediaWizard">下一步</el-button>
        <el-button v-else type="primary" :loading="mediaWizardSaving" @click="commitMediaWizard">收录</el-button>
      </template>
    </el-dialog>
</template>
