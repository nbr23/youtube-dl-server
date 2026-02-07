<script setup>
import { getAPIUrl } from '../utils';
import { map, capitalize } from 'lodash'
import { inject } from 'vue'
</script>
<script>
export default {
  data: () => ({
    stats: {},
    server_info: {},
  }),
  mounted() {
    this.fetchStats();
    this.server_info = inject('serverInfo');
  },
  computed: {
    prettyModule: function () {
      if (!this.server_info.ydl_module_name) {
        return ''
      }
      const parts = this.server_info.ydl_module_name.split('-')
      return [capitalize(parts[0]), ...map(parts.slice(1), s => s.toUpperCase())].join('')
    }
  },
  methods: {
    async fetchStats() {
      const url = getAPIUrl('api/downloads/stats', import.meta.env);
      this.stats = (await (await fetch(url)).json()).stats || {};
      setTimeout(() => {
        this.fetchStats()
      }, 5000)
    },
  }
}
</script>
<template>
  <header>
    <nav class="navbar navbar-expand-md navbar-dark">
      <div class="container-fluid">
        <router-link to="/" class="navbar-brand">{{ prettyModule }}</router-link>
        <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#collapsingNavbar"
          aria-controls="collapsingNavbar" aria-expanded="false" aria-label="Toggle navigation">
          <span class="navbar-toggler-icon"></span>
        </button>
        <div class="collapse navbar-collapse" id="collapsingNavbar">
          <ul class="navbar-nav me-auto mb-2 mb-md-0">
            <li class="nav-item">
              <router-link to="/" class="nav-link" exact-active-class="router-link-active">Home</router-link>
            </li>
            <li class="nav-item">
              <router-link to="/logs" class="nav-link">Logs</router-link>
            </li>
            <li class="nav-item">
              <router-link to="/finished" class="nav-link">Finished</router-link>
            </li>
          </ul>
          <div class="stats-bar d-flex align-items-center gap-2 flex-wrap">
            <router-link to="/logs?status=PENDING">
              <span v-if="stats.queue === stats.pending" title="Pending"
                id='queue_pending_size' class="badge bg-secondary">{{ stats.queue }}</span>
              <span v-else title="Pending" id='queue_pending_size'
                class="badge bg-secondary">{{ stats.queue }} | {{ stats.pending }}</span>
            </router-link>
            <router-link to="/logs?status=RUNNING">
              <span title="Running / Total Workers" id='running_size'
                class="badge bg-info">{{ stats.running }}/{{ server_info.download_workers_count }}</span>
            </router-link>
            <router-link to="/logs?status=COMPLETED">
              <span title="Completed" id='completed_size'
                class="badge bg-success">{{ stats.completed }}</span>
            </router-link>
            <router-link to="/logs?status=ABORTED">
              <span title="Aborted" id='aborted_size'
                class="badge bg-warning">{{ stats.aborted }}</span>
            </router-link>
            <router-link to="/logs?status=FAILED">
              <span title="Failed" id='failed_size'
                class="badge bg-danger">{{ stats.failed }}</span>
            </router-link>
          </div>
        </div>
      </div>
    </nav>
  </header>
</template>
