import { inject } from 'vue'

export function appContextComponent(components = {}) {
  return {
    components,
    setup() {
      return inject('appContext') || {}
    },
  }
}
