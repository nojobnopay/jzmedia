import { createRouter, createWebHistory } from 'vue-router'
import Library from './views/Library.vue'
import Detail from './views/Detail.vue'
import Person from './views/Person.vue'
import Settings from './views/Settings.vue'
import Collections from './views/Collections.vue'
import CollectionDetail from './views/CollectionDetail.vue'
import Tv from './views/Tv.vue'
import TvShow from './views/TvShow.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Library },
    { path: '/m/:id', component: Detail },
    { path: '/p/:tmdb_id', component: Person },
    { path: '/collections', component: Collections },
    { path: '/c/:id', component: CollectionDetail },
    { path: '/tv', component: Tv },
    { path: '/tv/:id', component: TvShow },
    { path: '/settings', component: Settings }
  ]
})
