<script>
import draggable from 'vuedraggable'
import { appContextComponent } from '../composables/appContext'
import PriorityList from './PriorityList.vue'

export default appContextComponent({ draggable, PriorityList })
</script>

<template>
      <section v-if="view === 'settings'" class="settings-page mochi-settings-page">
        <el-card class="settings-shell-card">
          <template #header>
            <div class="settings-shell-head">
              <div>
                <strong>设置工坊</strong>
                <span>全局行为、下载器、搜索源和维护动作集中管理</span>
              </div>
              <el-button type="primary" :loading="savingSettings" @click="saveAllSettings">保存设置</el-button>
            </div>
          </template>
          <el-form :model="settings" label-position="top" class="settings-form">
            <el-tabs tab-position="left" class="settings-workbench-tabs">
              <el-tab-pane label="基础">
                <el-alert
                  type="info"
                  show-icon
                  :closable="false"
                  title="RSS 订阅入口在新番页；这里保留代理、补全和命名规则等全局行为。"
                  class="settings-alert"
                />
                <el-form-item label="代理"><el-input v-model="settings.rss_proxy" placeholder="http://NAS_IP:20171；留空则直连" /></el-form-item>
                <el-form-item label="TMDB Token"><el-input v-model="settings.tmdb_token" placeholder="用于电影/电视剧搜索，可留空" show-password /></el-form-item>
                <div class="settings-toggle-stack">
                  <label class="switch-setting">
                    <el-switch v-model="settings.backfill_current_season" />
                    <span>
                      <strong>补全本季</strong>
                      <small>扫描到新番时自动尝试补齐缺失集数。</small>
                    </span>
                  </label>
                  <label class="switch-setting">
                    <el-switch v-model="settings.auto_generate_nfo" />
                    <span>
                      <strong>生成 NFO 元数据</strong>
                      <small>整理或刷新元数据后为 Jellyfin 生成识别配置。</small>
                    </span>
                  </label>
                </div>
                <el-collapse-transition>
                  <div v-if="settings.auto_generate_nfo" class="nfo-config-panel">
                    <div>
                      <strong>NFO 写入方式</strong>
                      <span>本轮先保留核心写入模式，字段级选项后续扩展。</span>
                    </div>
                    <el-select v-model="settings.nfo_write_mode" class="nfo-mode-select">
                      <el-option label="空缺补齐" value="fill_missing" />
                      <el-option label="覆盖更新" value="overwrite" />
                    </el-select>
                  </div>
                </el-collapse-transition>
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
              <el-tab-pane label="搜索源">
                <el-alert
                  type="info"
                  show-icon
                  :closable="false"
                  title="发现页和本季补全会按优先级使用启用的搜索源。第一版优先支持 Mikan/RSS 和 Torznab/Prowlarr/Jackett。"
                  class="settings-alert"
                />
                <div class="settings-section-toolbar source-toolbar">
                  <div>
                    <strong>搜索源</strong>
                    <span>拖拽调整优先级，编辑配置在弹窗里完成。</span>
                  </div>
                  <el-button type="primary" @click="openSearchSourceDialog()">新增搜索源</el-button>
                </div>
                <draggable
                  v-model="searchSources"
                  item-key="id"
                  handle=".drag-handle"
                  class="search-source-list"
                  v-loading="searchSourcesLoading"
                  @end="saveSearchSourceOrder"
                >
                  <template #item="{ element, index }">
                    <div class="search-source-row" :class="{ disabled: !Number(element.enabled || 0) }">
                      <span class="drag-handle">⋮⋮</span>
                      <span class="rank">{{ index + 1 }}</span>
                      <div>
                        <strong>{{ element.name }}</strong>
                        <span>{{ element.kind }} · {{ element.base_url || '未配置地址' }}</span>
                        <small v-if="element.last_error">{{ element.last_error }}</small>
                      </div>
                      <el-tag :type="Number(element.enabled || 0) ? 'success' : 'info'">{{ Number(element.enabled || 0) ? '启用' : '关闭' }}</el-tag>
                      <el-tag v-if="element.last_status" :type="element.last_status === 'failed' ? 'danger' : 'success'">{{ element.last_status }}</el-tag>
                      <el-button plain @click="openSearchSourceDialog(element)">编辑</el-button>
                      <el-button plain @click="testSearchSource(element)">测试</el-button>
                      <el-popconfirm title="删除这个搜索源？" @confirm="deleteSearchSource(element.id)">
                        <template #reference>
                          <el-button type="danger" plain>删除</el-button>
                        </template>
                      </el-popconfirm>
                    </div>
                  </template>
                </draggable>
                <el-empty v-if="!searchSources.length && !searchSourcesLoading" description="暂无搜索源" />
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
              <el-tab-pane label="定时器">
                <el-alert
                  type="info"
                  show-icon
                  :closable="false"
                  title="定时器只负责触发动作；具体执行会显示在控制台任务列表。"
                  class="settings-alert"
                />
                <div class="schedule-settings-list">
                  <div v-for="item in dashboard.schedules || []" :key="item.id" class="schedule-settings-row">
                    <div>
                      <strong>{{ item.name || item.action_name || item.action }}</strong>
                      <span>{{ item.action_name || item.action }} · {{ Number(item.interval_minutes || 0) }} 分钟</span>
                    </div>
                    <el-tag :type="Number(item.enabled || 0) ? 'success' : 'info'">{{ Number(item.enabled || 0) ? '启用' : '关闭' }}</el-tag>
                    <el-tag v-if="item.last_status" :type="item.last_status === 'failed' ? 'danger' : 'info'">{{ item.last_status }}</el-tag>
                    <span class="schedule-run-at">{{ item.last_run_at || '未执行' }}</span>
                    <el-button plain @click="triggerSchedule(item)">立即执行</el-button>
                    <el-button type="primary" plain @click="openScheduledSettings(item)">设置</el-button>
                  </div>
                  <el-empty v-if="!(dashboard.schedules || []).length" description="暂无定时器" />
                </div>
              </el-tab-pane>
              <el-tab-pane label="维护">
                <div class="maintenance-group-list">
                  <section class="maintenance-group">
                    <h3>缓存</h3>
                    <p>清理 RSS、发现搜索和元数据处理缓存。</p>
                    <div class="maintenance-actions">
                      <el-button plain @click="runAction('/cache/rss/clear')">清除 RSS 缓存</el-button>
                      <el-button plain @click="runAction('/cache/expired/clear')">清除过期缓存</el-button>
                      <el-popconfirm title="会清空全部处理缓存，包括元数据和匹配缓存。确定？" @confirm="runAction('/cache/clear')">
                        <template #reference>
                          <el-button type="warning" plain>清除全部处理缓存</el-button>
                        </template>
                      </el-popconfirm>
                    </div>
                  </section>
                  <section class="maintenance-group">
                    <h3>元数据与本地状态</h3>
                    <p>用于修正可观看状态、补齐作品信息和重新整理已有文件。</p>
                    <div class="maintenance-actions">
                      <el-button plain @click="refreshAllLocalStatus">刷新全部本地状态</el-button>
                      <el-popconfirm title="会按现有 Bangumi/TMDB ID 刷新所有条目元数据，并按设置校验 NFO。确定执行？" @confirm="refreshAllMetadata">
                        <template #reference>
                          <el-button plain>刷新全部元数据</el-button>
                        </template>
                      </el-popconfirm>
                      <el-popconfirm title="会把已绑定且存在的本地文件整理到当前命名规则路径，目标同名文件会被覆盖。确定执行？" @confirm="organizeAllLocalFiles">
                        <template #reference>
                          <el-button plain>整理全部本地资源</el-button>
                        </template>
                      </el-popconfirm>
                    </div>
                  </section>
                  <section class="maintenance-group">
                    <h3>数据修复</h3>
                    <p>只在迁移旧数据或排查异常集数时使用。</p>
                    <div class="maintenance-actions">
                      <el-popconfirm title="迁移前会自动备份数据库。确定把旧资源模型迁移为每集一条资源，并按纯作品名目录计算路径？" @confirm="migrateEpisodeModel">
                        <template #reference>
                          <el-button type="warning" plain>迁移集数模型</el-button>
                        </template>
                      </el-popconfirm>
                      <el-popconfirm title="会把已识别到的旧本地文件移动到纯作品名目录，并同步修复数据库状态。确定执行？" @confirm="repairLocalPaths">
                        <template #reference>
                          <el-button type="warning" plain>修复为纯作品名路径</el-button>
                        </template>
                      </el-popconfirm>
                      <el-popconfirm title="会清理无法识别集数的发布、资源、字幕和下载记录。确定执行？" @confirm="runAction('/maintenance/cleanup-invalid-episodes')">
                        <template #reference>
                          <el-button type="warning" plain>清理无效集数</el-button>
                        </template>
                      </el-popconfirm>
                    </div>
                  </section>
                  <section class="maintenance-group danger-zone">
                    <h3>危险操作</h3>
                    <p>会删除运行和媒体数据，执行前请确认已经备份。</p>
                    <div class="maintenance-actions">
                      <el-popconfirm title="会清空番剧、候选、任务、下载记录、本地资源记录和日志。确定？" @confirm="runAction('/system/clear-data')">
                        <template #reference>
                          <el-button type="danger" plain>清除所有数据</el-button>
                        </template>
                      </el-popconfirm>
                    </div>
                  </section>
                </div>
              </el-tab-pane>
            </el-tabs>
          </el-form>
        </el-card>
      </section>

      <el-dialog v-model="searchSourceDialogOpen" :title="searchSourceEditingId ? '编辑搜索源' : '新增搜索源'" width="720px" top="6vh">
        <el-form :model="searchSourceForm" label-position="top" class="settings-form dialog-form">
          <div class="form-row">
            <el-form-item label="名称"><el-input v-model="searchSourceForm.name" placeholder="Mikan / Prowlarr / Jackett" /></el-form-item>
            <el-form-item label="类型">
              <el-select v-model="searchSourceForm.kind">
                <el-option label="Mikan" value="mikan" />
                <el-option label="RSS" value="rss" />
                <el-option label="Torznab" value="torznab" />
                <el-option label="Prowlarr" value="prowlarr" />
                <el-option label="Jackett" value="jackett" />
              </el-select>
            </el-form-item>
          </div>
          <el-form-item label="Base URL">
            <el-input v-model="searchSourceForm.base_url" placeholder="Mikan: https://mikanani.me/RSS/Search?searchstr={keyword}；Torznab: http://host:9696/1/api" />
          </el-form-item>
          <div class="form-row">
            <el-form-item label="Token / API Key"><el-input v-model="searchSourceForm.api_key" show-password /></el-form-item>
            <el-form-item label="分类"><el-input v-model="searchSourceForm.categories" placeholder="Torznab cat，用逗号分隔" /></el-form-item>
          </div>
          <div class="form-row">
            <el-form-item label="超时秒数"><el-input v-model="searchSourceForm.timeout_seconds" placeholder="20" /></el-form-item>
            <el-form-item label="限速秒数"><el-input v-model="searchSourceForm.rate_limit_seconds" placeholder="0" /></el-form-item>
            <el-form-item label="启用">
              <label class="switch-setting compact">
                <el-switch v-model="searchSourceForm.enabled" />
                <span><strong>启用搜索源</strong><small>关闭后发现页不会请求它。</small></span>
              </label>
            </el-form-item>
          </div>
          <el-alert type="info" show-icon :closable="false" title="外部请求统一使用基础设置里的代理；单个搜索源不再单独配置代理。" />
        </el-form>
        <template #footer>
          <el-button @click="searchSourceDialogOpen = false">取消</el-button>
          <el-button type="primary" @click="saveSearchSource">{{ searchSourceEditingId ? '保存' : '添加' }}</el-button>
        </template>
      </el-dialog>
</template>
