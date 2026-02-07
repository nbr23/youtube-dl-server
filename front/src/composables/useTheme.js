import { ref } from 'vue'
import { saveConfig, getConfig } from '../utils'

const theme = ref(getInitialTheme())

function getInitialTheme() {
  const stored = getConfig('theme', null)
  if (stored) return stored
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

function applyTheme(value) {
  document.documentElement.setAttribute('data-theme', value)
}

const mql = window.matchMedia('(prefers-color-scheme: light)')
mql.addEventListener('change', (e) => {
  if (getConfig('theme', null)) return
  theme.value = e.matches ? 'light' : 'dark'
  applyTheme(theme.value)
})

applyTheme(theme.value)

export function useTheme() {
  function toggleTheme() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    saveConfig('theme', theme.value)
    applyTheme(theme.value)
  }

  return { theme, toggleTheme }
}
