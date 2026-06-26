<script>
import draggable from 'vuedraggable'
import { appContextComponent } from '../composables/appContext'
import PriorityList from './PriorityList.vue'

export default appContextComponent({ draggable, PriorityList })
</script>

<template>
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
                  title="RSS 订阅入口在新番页；这里保留代理、补全和命名规则等全局行为。"
                  class="settings-alert"
                />
                <el-form-item label="RSS 代理"><el-input v-model="settings.rss_proxy" placeholder="http://NAS_IP:20171" /></el-form-item>
                <el-form-item label="TMDB Token"><el-input v-model="settings.tmdb_token" placeholder="用于电影/电视剧搜索，可留空" show-password /></el-form-item>
                <div class="form-row">
                  <el-form-item label="补全本季"><el-switch v-model="settings.backfill_current_season" /></el-form-item>
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
                <h3 class="settings-subtitle">生成配置</h3>
                <div class="config-toggle-list">
                  <label>
                    <span>生成 NFO 元数据</span>
                    <el-switch v-model="settings.auto_generate_nfo" />
                  </label>
                </div>
                <el-form-item label="NFO 写入模式">
                  <el-radio-group v-model="settings.nfo_write_mode">
                    <el-radio-button label="fill_missing">空缺补齐</el-radio-button>
                    <el-radio-button label="overwrite">覆盖更新</el-radio-button>
                  </el-radio-group>
                </el-form-item>
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
                <div class="detail-summary-grid maintenance-summary-grid">
                  <div><span>待处理任务</span><strong>{{ dashboard.console_overview?.pending_task_count || 0 }}</strong></div>
                  <div><span>失败任务</span><strong>{{ dashboard.console_overview?.failed_task_count || 0 }}</strong></div>
                  <div><span>等待重试</span><strong>{{ dashboard.console_overview?.waiting_retry_count || 0 }}</strong></div>
                  <div><span>运行队列</span><strong>{{ dashboard.console_overview?.running_queue_count || 0 }}</strong></div>
                </div>
                <div class="maintenance-actions maintenance-pane">
                  <el-button type="primary" plain @click="runAction('/cache/rss/clear')">清除 RSS 缓存</el-button>
                  <el-button type="primary" plain @click="runAction('/cache/expired/clear')">清除过期缓存</el-button>
                  <el-popconfirm title="会清空全部处理缓存，包括元数据和匹配缓存。确定？" @confirm="runAction('/cache/clear')">
                    <template #reference>
                      <el-button type="warning">清除全部处理缓存</el-button>
                    </template>
                  </el-popconfirm>
                  <el-button type="primary" @click="refreshAllLocalStatus">刷新全部本地状态</el-button>
                  <el-popconfirm title="会按现有 Bangumi/TMDB ID 刷新所有条目元数据，并按设置校验 NFO。确定执行？" @confirm="refreshAllMetadata">
                    <template #reference>
                      <el-button type="primary">刷新全部元数据</el-button>
                    </template>
                  </el-popconfirm>
                  <el-popconfirm title="会把已绑定且存在的本地文件整理到当前命名规则路径，目标同名文件会被覆盖。确定执行？" @confirm="organizeAllLocalFiles">
                    <template #reference>
                      <el-button type="primary">整理全部本地资源</el-button>
                    </template>
                  </el-popconfirm>
                  <el-popconfirm title="迁移前会自动备份数据库。确定把旧资源模型迁移为每集一条资源，并按纯作品名目录计算路径？" @confirm="migrateEpisodeModel">
                    <template #reference>
                      <el-button type="primary">迁移集数模型</el-button>
                    </template>
                  </el-popconfirm>
                  <el-popconfirm title="会把已识别到的旧本地文件移动到纯作品名目录，并同步修复数据库状态。确定执行？" @confirm="repairLocalPaths">
                    <template #reference>
                      <el-button type="primary">修复为纯作品名路径</el-button>
                    </template>
                  </el-popconfirm>
                  <el-popconfirm title="会清理无法识别集数的发布、资源、字幕和下载记录。确定执行？" @confirm="runAction('/maintenance/cleanup-invalid-episodes')">
                    <template #reference>
                      <el-button type="warning">清理无效集数</el-button>
                    </template>
                  </el-popconfirm>
                  <el-popconfirm title="会清空番剧、候选、任务、下载记录、本地资源记录和日志。确定？" @confirm="runAction('/system/clear-data')">
                    <template #reference>
                      <el-button type="danger" plain>清除所有数据</el-button>
                    </template>
                  </el-popconfirm>
                </div>
              </el-tab-pane>
            </el-tabs>
            <div class="form-actions"><el-button type="primary" size="large" :loading="savingSettings" @click="saveAllSettings">保存设置</el-button></div>
          </el-form>
        </el-card>
      </section>
</template>
