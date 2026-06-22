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
                <el-form-item label="动画命名模板"><el-input v-model="settings.episode_name_template" /></el-form-item>
                <el-form-item label="电影命名模板"><el-input v-model="settings.movie_name_template" /></el-form-item>
                <el-form-item label="电视剧命名模板"><el-input v-model="settings.tv_name_template" /></el-form-item>
              </el-tab-pane>
            </el-tabs>
            <div class="form-actions"><el-button type="primary" size="large" :loading="savingSettings" @click="saveAllSettings">保存设置</el-button></div>
          </el-form>
        </el-card>
      </section>
</template>
