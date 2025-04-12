import { ref } from 'vue'
import { defineStore } from 'pinia'
import zhCN from '../locales/zh-CN'
import enUS from '../locales/en-US'

export const useLanguageStore = defineStore('language', () => {
  const currentLocale = ref(localStorage.getItem('locale') || 'en-US')
  const messages = {
    'zh-CN': zhCN,
    'en-US': enUS
  }

  function setLocale(locale) {
    currentLocale.value = locale
    localStorage.setItem('locale', locale)
  }

  function t(key) {
    const keys = key.split('.')
    let value = messages[currentLocale.value]
    for (const k of keys) {
      value = value[k]
      if (!value) return key
    }
    return value
  }

  return { currentLocale, setLocale, t }
}) 