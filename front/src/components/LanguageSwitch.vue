<template>
  <div class="language-switch">
    <div class="dropdown">
      <button class="btn btn-secondary dropdown-toggle" type="button" id="languageDropdown" data-bs-toggle="dropdown" aria-expanded="false">
        {{ currentLanguage }}
      </button>
      <ul class="dropdown-menu" aria-labelledby="languageDropdown">
        <li><a class="dropdown-item" href="#" @click="changeLanguage('en-US')">{{ t('language.en') }}</a></li>
        <li><a class="dropdown-item" href="#" @click="changeLanguage('zh-CN')">{{ t('language.zh') }}</a></li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useLanguageStore } from '../stores/language'

const languageStore = useLanguageStore()
const t = languageStore.t

const currentLanguage = computed(() => {
  return languageStore.currentLocale === 'zh-CN' ? '中文' : 'English'
})

const changeLanguage = (locale) => {
  languageStore.setLocale(locale)
}
</script>

<style scoped>
.language-switch {
  position: fixed;
  top: 1rem;
  right: 1rem;
  z-index: 1000;
}

.dropdown-menu {
  min-width: 100px;
}

.btn-secondary {
  background-color: rgba(0, 0, 0, 0.5);
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.btn-secondary:hover {
  background-color: rgba(0, 0, 0, 0.7);
}
</style> 