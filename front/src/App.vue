<script setup>
import { ref, provide, onMounted } from 'vue'
import { getAPIUrl } from './utils';
import { useTheme } from './composables/useTheme'

import Header from './components/Header.vue'
import Footer from './components/Footer.vue'

const { theme, toggleTheme } = useTheme()
const serverInfo = ref({})

const fetchServerInfo = async () => {
  const url = getAPIUrl('api/info', import.meta.env);
  serverInfo.value = await (await fetch(url)).json();
};

provide('serverInfo', serverInfo);
provide('theme', theme);
provide('toggleTheme', toggleTheme);

onMounted(() => {
  fetchServerInfo()
});
</script>

<template>
  <Header />
  <router-view></router-view>
  <Footer />
</template>

<style scoped></style>
