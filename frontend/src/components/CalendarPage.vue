<script>
import { appContextComponent } from '../composables/appContext'

export default appContextComponent()
</script>

<template>
      <section v-if="view === 'calendar'" class="calendar-page">
        <div class="toolbar calendar-toolbar">
          <el-date-picker
            v-model="calendarWeek"
            type="week"
            format="[第] ww [周] YYYY"
            value-format="YYYY-MM-DD"
            placeholder="选择周"
          />
          <el-button-group>
            <el-button @click="shiftCalendarWeek(-1)">上一周</el-button>
            <el-button @click="setCalendarThisWeek">本周</el-button>
            <el-button @click="shiftCalendarWeek(1)">下一周</el-button>
          </el-button-group>
        </div>
        <div class="week-calendar-grid">
          <section v-for="day in weekDays" :key="day.key" class="week-day-column" :class="{ today: day.isToday }">
            <header>
              <strong>{{ day.label }}</strong>
              <span>{{ day.dateLabel }}</span>
            </header>
            <article
              v-for="item in day.items"
              :key="`${day.key}-${item.entry_id}-${item.episode_number}-${item.updated_at}`"
              class="calendar-entry-card"
              @click="openEntry(item.entry_id, 'seasonal')"
            >
              <div class="calendar-entry-cover">
                <img v-if="item.poster_url" :src="item.poster_url" />
                <span v-else>{{ (item.work_display_title || item.entry_display_title || item.display_title || 'AN').slice(0, 2) }}</span>
              </div>
              <div class="calendar-entry-meta">
                <strong>{{ item.work_display_title || item.entry_display_title || item.display_title }}</strong>
                <span>{{ item.entry_scope_label || item.entry_secondary_title || '-' }}</span>
              </div>
              <div class="calendar-entry-tags">
                <el-tag size="small" type="primary">第 {{ item.episode_number || '?' }} 集</el-tag>
                <el-tag size="small" :type="item.synced ? 'success' : 'warning'">{{ item.synced ? '已下载' : '已更新' }}</el-tag>
              </div>
            </article>
            <div v-if="!day.items.length" class="calendar-empty">无更新</div>
          </section>
        </div>
      </section>

</template>

