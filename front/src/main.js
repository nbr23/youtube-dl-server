import { createApp } from 'vue';
import App from './App.vue';
import VueCookies from 'vue-cookies'
import { createRouter, createWebHashHistory } from 'vue-router';
import zhCN from './locales/zh-CN'

import 'bootstrap/dist/css/bootstrap.css';

import './assets/style.css';
import Logs from './components/Logs.vue';
import Home from './components/Home.vue';
import Finished from './components/Finished.vue';

const routes = [
  { path: '/', component: Home },
  { path: '/home', component: Home },
  { path: '/logs', component: Logs },
  { path: '/finished', component: Finished },
];

const router = createRouter({
  history: createWebHashHistory(),
  routes,
});
const app = createApp(App);
app.use(router);
app.use(VueCookies);

// 添加全局 i18n 对象
app.config.globalProperties.$t = function(key) {
    const keys = key.split('.')
    let value = zhCN
    for (const k of keys) {
        value = value[k]
        if (!value) return key
    }
    return value
}

app.mount('#app');
