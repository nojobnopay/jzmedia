import { createRouter, createWebHistory } from 'vue-router'
import Library from './views/Library.vue'
import Detail from './views/Detail.vue'
import Person from './views/Person.vue'
import Settings from './views/Settings.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Library },
    { path: '/m/:id', component: Detail },
    { path: '/p/:tmdb_id', component: Person },
    { path: '/settings', component: Settings }
  ]
})
