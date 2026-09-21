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
    { path: '/settings', component: Settings },
    // 未知路径统一回首页（此前空 router-view + 导航残影；评审 R14-D5）
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})
