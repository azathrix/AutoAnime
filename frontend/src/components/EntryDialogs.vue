<script>
import { appContextComponent } from '../composables/appContext'

export default appContextComponent()
</script>

<template>
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
        <el-form-item label="原名"><el-input v-model="entryEditForm.title_raw" /></el-form-item>
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
        <el-button plain @click="openMetadataSearch('bangumi', 'entry')">匹配</el-button>
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
                <div class="el-upload__tip">{{ episodeResourceForm.subtitle_file_name || '选择后会上传到服务端临时区，保存配置时写入该集字幕信息。' }}</div>
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
                  <div class="el-upload__tip">{{ batchSubtitleForm.file_names.length ? batchSubtitleForm.file_names.join('，') : '选择后会上传到服务端临时区，并按文件名集数自动匹配。' }}</div>
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

    <el-dialog v-model="processorSettingsDialogOpen" title="下载处理器设置" width="520px">
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

    <el-dialog v-model="scheduledSettingsDialogOpen" title="定时任务设置" width="520px">
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

    <el-dialog v-model="metadataSearchDialogOpen" title="作品匹配" width="900px">
      <div class="metadata-search-dialog">
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
              <article v-for="item in metadataSearchResults.bangumi" :key="`${item.provider}-${item.id}`" class="metadata-result-card">
                <img v-if="item.poster_url" :src="item.poster_url" />
                <span v-else>{{ (item.title || item.original_title || '候选').slice(0, 2) }}</span>
                <div>
                  <strong>{{ item.title || item.original_title }}</strong>
                  <small>{{ item.original_title || '-' }}</small>
                  <p>{{ item.summary || '暂无简介' }}</p>
                  <code>{{ item.provider }} · {{ item.id }} · {{ item.year || '年份未知' }}</code>
                </div>
                <el-button type="primary" @click="applyMetadataSearchItem(item)">填入</el-button>
              </article>
              <el-empty v-if="!metadataSearchLoading && !metadataSearchResults.bangumi.length" description="暂无 Bangumi 匹配结果" />
            </div>
          </el-tab-pane>
          <el-tab-pane label="TMDB" name="tmdb">
            <div class="metadata-result-list" v-loading="metadataSearchLoading">
              <article v-for="item in metadataSearchResults.tmdb" :key="`${item.provider}-${item.id}`" class="metadata-result-card">
                <img v-if="item.poster_url" :src="item.poster_url" />
                <span v-else>{{ (item.title || item.original_title || '候选').slice(0, 2) }}</span>
                <div>
                  <strong>{{ item.title || item.original_title }}</strong>
                  <small>{{ item.original_title || '-' }}</small>
                  <p>{{ item.summary || '暂无简介' }}</p>
                  <code>{{ item.provider }} · {{ item.id }} · {{ item.year || '年份未知' }}</code>
                </div>
                <el-button type="primary" @click="applyMetadataSearchItem(item)">填入</el-button>
              </article>
              <el-empty v-if="!metadataSearchLoading && !metadataSearchResults.tmdb.length" description="暂无 TMDB 匹配结果" />
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
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
          <el-upload
            v-if="mediaWizardDraft.source_mode === 'local'"
            v-model:file-list="mediaWizardFiles"
            drag
            action="#"
            :auto-upload="false"
            multiple
          >
            <p>选择本地文件或目录</p>
            <small>提交后会先上传到服务端临时区，再由上传整理队列收录到媒体库。</small>
          </el-upload>
          <el-form v-if="mediaWizardDraft.source_mode === 'link'" label-position="top" class="wizard-form">
            <el-form-item label="资源链接">
              <el-input
                v-model="mediaWizardDraft.resources_text"
                type="textarea"
                :rows="5"
                placeholder="每行一个磁链、种子链接或下载链接。下一步会从文件名或链接中提取标题。"
              />
            </el-form-item>
          </el-form>
          <el-alert v-else-if="mediaWizardDraft.source_mode === 'metadata'" type="info" show-icon :closable="false" title="仅收录作品时，下一步填写作品标题并匹配元数据。" />
        </template>
        <template v-else-if="mediaWizardStep === 1">
          <div class="wizard-intro">
            <strong>确认作品信息</strong>
            <span>先填写作品标题，再点击匹配，从 Bangumi 或 TMDB 结果中选择并填入基础信息。</span>
          </div>
          <el-form :model="mediaWizardDraft" label-position="top" class="wizard-form">
            <div class="wizard-form-grid labeled">
              <el-form-item label="作品标题">
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
              <el-form-item label="海报 URL"><el-input v-model="mediaWizardDraft.poster_url" placeholder="可选，匹配后会自动填入" /></el-form-item>
              <el-form-item label="标签"><el-input v-model="mediaWizardDraft.tags_text" type="textarea" :rows="2" placeholder="一行一个标签" /></el-form-item>
              <el-form-item label="类型 / 题材"><el-input v-model="mediaWizardDraft.genres_text" type="textarea" :rows="2" placeholder="一行一个类型" /></el-form-item>
              <el-form-item label="简介"><el-input v-model="mediaWizardDraft.summary" type="textarea" :rows="4" /></el-form-item>
            </div>
          </el-form>
        </template>
        <template v-else-if="mediaWizardStep === 2">
          <div class="wizard-intro">
            <strong>配置第一条集数资源</strong>
            <span>一行一个磁力链接或下载链接，系统按链接/标题里的集数自动匹配。字幕也按相同方式逐行匹配。</span>
          </div>
          <el-form :model="mediaWizardDraft" label-position="top" class="wizard-form">
            <el-form-item label="资源链接">
              <el-input v-model="mediaWizardDraft.resources_text" type="textarea" :rows="7" placeholder="magnet:?xt=urn:btih:...&#10;https://example.com/show.S01E05.torrent" />
            </el-form-item>
            <div class="form-row">
              <el-form-item label="字幕类型">
                <el-select v-model="mediaWizardDraft.subtitle_format" clearable placeholder="可选">
                  <el-option label="无字幕 / 未配置" value="" />
                  <el-option label="外挂" value="external" />
                  <el-option label="内封（软字幕）" value="muxed" />
                  <el-option label="内嵌（硬字幕）" value="embedded" />
                </el-select>
              </el-form-item>
              <el-form-item label="语言"><el-input v-model="mediaWizardDraft.language" placeholder="例如 简繁、简体、双语" /></el-form-item>
            </div>
            <el-form-item label="外挂字幕链接 / 文件名">
              <el-input v-model="mediaWizardDraft.subtitles_text" type="textarea" :rows="4" placeholder="可选，一行一个字幕链接或字幕文件名" />
            </el-form-item>
          </el-form>
          <div class="guide-preview" v-if="mediaWizardResourceRows.length">
            <strong>资源识别</strong>
            <div v-for="item in mediaWizardResourceRows" :key="item.key" :class="['guide-preview-row', { invalid: !item.valid }]">
              <span>第 {{ item.episode }} 集</span>
              <code>{{ item.text }}</code>
              <el-tag size="small" :type="item.valid ? 'success' : 'danger'">{{ item.valid ? item.kind : item.reason }}</el-tag>
            </div>
          </div>
          <div class="guide-preview" v-if="mediaWizardSubtitleRows.length">
            <strong>字幕识别</strong>
            <div v-for="item in mediaWizardSubtitleRows" :key="item.key" :class="['guide-preview-row', { invalid: !item.valid }]">
              <span>第 {{ item.episode }} 集</span>
              <code>{{ item.text }}</code>
              <el-tag size="small" :type="item.valid ? 'success' : 'danger'">{{ item.valid ? '可导入' : item.reason }}</el-tag>
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
              <div><span>资源</span><code>{{ mediaWizardDraft.source_mode === 'local' ? `${mediaWizardFiles.length} 个文件` : (mediaWizardResourceRows.length ? `${mediaWizardResourceRows.length} 条` : '未填写资源') }}</code></div>
              <div><span>字幕链接</span><code>{{ mediaWizardSubtitleRows.length ? `${mediaWizardSubtitleRows.length} 条` : '未填写字幕' }}</code></div>
              <div><span>字幕规则</span><code>{{ subtitleFormatText(mediaWizardDraft.subtitle_format) || '-' }} · {{ mediaWizardDraft.language || '-' }}</code></div>
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
</template>

