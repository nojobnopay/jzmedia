import { createRouter, createWebHistory } from 'vue-router'

// 路由级懒加载（评审 R14-D5）：各 View 独立分包，首屏不再吃下全部页面
const Library = () => import('./views/Library.vue')
const Detail = () => import('./views/Detail.vue')
const Person = () => import('./views/Person.vue')
const Settings = () => import('./views/Settings.vue')
const Collections = () => import('./views/Collections.vue')
const CollectionDetail = () => import('./views/CollectionDetail.vue')
const Tv = () => import('./views/Tv.vue')
const TvShow = () => import('./views/TvShow.vue')
const SeasonView = () => import('./views/SeasonView.vue')
const EpisodeView = () => import('./views/EpisodeView.vue')

export default createRouter({
  history: createWebHistory(),
  scrollBehavior(to, from, savedPosition) {
    // The wall restores its loaded rows before restoring the anchor.
    if (to.path === '/' || to.path === '/tv') return false
    if (savedPosition) return savedPosition
    if (to.path !== from.path) return { top: 0 }
    return false  // 搜索/筛选只更新 query，保留当前浏览位置。
  },
  routes: [
    { path: '/', component: Library },
    { path: '/m/:id', component: Detail },
    { path: '/p/:tmdb_id', component: Person },
    { path: '/collections', component: Collections },
    { path: '/c/:id', component: CollectionDetail },
    { path: '/tv', component: Tv },
    { path: '/tv/:id', component: TvShow },
    { path: '/tv/:showId/s/:season', component: SeasonView },
    { path: '/tv/:showId/s/:season/e/:epId', component: EpisodeView },
    { path: '/settings', component: Settings },
    // 未知路径统一回首页（此前空 router-view + 导航残影；评审 R14-D5）
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})
