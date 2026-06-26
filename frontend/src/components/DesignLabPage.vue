<script>
import { appContextComponent } from '../composables/appContext'

export default appContextComponent()
</script>

<template>
  <section v-if="view === 'design'" class="design-lab-page">
    <el-alert
      type="info"
      show-icon
      :closable="false"
      title="这里是临时设计实验室，用来比较 AniTrack 后续页面的视觉语言；不接业务数据。"
    />

    <div class="design-style-grid">
      <article class="design-style-card sakura-panel">
        <header>
          <span>Sakura Panel</span>
          <strong>浅粉/浅蓝，柔和卡片，轻动效</strong>
        </header>
        <div class="design-button-row">
          <button class="anime-btn primary">主要操作</button>
          <button class="anime-btn secondary">次要操作</button>
          <button class="anime-btn danger">危险操作</button>
          <button class="anime-link-btn">文字按钮</button>
        </div>
        <div class="design-tabs horizontal">
          <span class="active">概览</span><span>资源</span><span>设置</span>
        </div>
        <div class="design-preview-body">
          <div class="design-list-item active"><b>搜索源 A</b><small>Mikan · 启用 · 最近成功</small></div>
          <div class="design-toggle-row"><i></i><span><b>生成 NFO 元数据</b><small>整理后自动生成识别文件</small></span></div>
          <div class="design-empty-state"><div>AN</div><strong>没有匹配的番剧</strong><span>调整关键词或清空筛选后再试。</span></div>
        </div>
      </article>

      <article class="design-style-card clean-anime">
        <header>
          <span>Clean Anime</span>
          <strong>克制、清晰、适合设置页和维护页</strong>
        </header>
        <div class="design-button-row">
          <button class="anime-btn primary">保存</button>
          <button class="anime-btn secondary">测试</button>
          <button class="anime-btn danger">删除</button>
          <button class="anime-link-btn">取消</button>
        </div>
        <div class="design-columns">
          <div class="design-tabs vertical">
            <span class="active">基础</span><span>搜索源</span><span>维护</span>
          </div>
          <div class="design-form-preview">
            <label><span>名称</span><input value="Prowlarr" /></label>
            <label><span>类型</span><input value="torznab" /></label>
            <label><span>超时秒数</span><input value="20" /></label>
          </div>
        </div>
      </article>

      <article class="design-style-card glass-pop">
        <header>
          <span>Glass Pop</span>
          <strong>半透明面板，适合发现页和卡片流</strong>
        </header>
        <div class="design-resource-card">
          <div class="mock-cover">AT</div>
          <div>
            <h3>欢迎来到实力至上主义的教室</h3>
            <p>Season 04 · 12 集资源 · LoliHouse · 1080p</p>
            <div class="design-chip-row"><span>日本</span><span>校园</span><span>智斗</span></div>
          </div>
        </div>
        <div class="design-maintenance-group">
          <strong>缓存</strong>
          <div><button>清除 RSS 缓存</button><button>清除过期缓存</button></div>
        </div>
        <div class="design-loading-bar"><span></span></div>
      </article>
    </div>
  </section>
</template>
