<script>
import draggable from 'vuedraggable'

export default {
  props: ['modelValue', 'title', 'placeholder'],
  emits: ['update:modelValue'],
  components: { draggable },
  data() {
    return { input: '' }
  },
  computed: {
    items: {
      get() { return this.modelValue || [] },
      set(value) { this.$emit('update:modelValue', value) },
    },
  },
  methods: {
    add() {
      const value = this.input.trim()
      if (!value) return
      this.items = [...this.items, value]
      this.input = ''
    },
    remove(index) {
      this.items = this.items.filter((_, i) => i !== index)
    },
  },
}
</script>

<template>
  <div class="priority-card">
    <div class="priority-title">{{ title }}</div>
    <draggable v-model="items" item-key="name" handle=".drag-handle" class="priority-list">
      <template #item="{ element, index }">
        <div class="priority-item">
          <span class="drag-handle">⋮⋮</span>
          <span class="rank">{{ index + 1 }}</span>
          <span>{{ element }}</span>
          <button type="button" @click="remove(index)">×</button>
        </div>
      </template>
    </draggable>
    <div class="priority-add">
      <el-input v-model="input" :placeholder="placeholder" @keyup.enter="add" />
      <el-button @click="add">添加</el-button>
    </div>
  </div>
</template>
