import { readdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const docsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

export function walkFiles(root, prefix = '') {
  return readdirSync(path.join(root, prefix), { withFileTypes: true }).flatMap(entry => {
    if (entry.isSymbolicLink() || entry.name === 'node_modules') return []
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name
    return entry.isDirectory() ? walkFiles(root, rel) : [rel]
  })
}

export function isPublicPage(file) {
  return publicPaths.has(file)
}

export function publicPages(root = docsRoot) {
  return walkFiles(root).filter(isPublicPage).sort()
}

export function sourceExcludes(root = docsRoot) {
  return ['assets/**', 'private/**', 'roadmap/**', 'scripts/**', 'tests/**', '.vitepress/**',
    ...walkFiles(root).filter(file => file.endsWith('.md') && !isPublicPage(file))]
}

export function htmlPath(page) { return page.replace(/\.md$/, '.html') }

const item = (text, slug, group = 'user-guide') => ({ text, link: `/${group}/${slug}.html` })
export const sidebar = [
  { text: '开始使用', items: [
    item('安装 jzmedia', 'deployment', 'getting-started'),
    item('配置并播放第一部内容', 'onboarding'),
    item('连接本地目录或 NAS', 'libraries'),
    item('扫描与上传文件', 'files'),
  ] },
  { text: '观看影片', items: [
    item('找片与筛选', 'find-movies'), item('影片版本与文件', 'movie-versions'),
    item('追剧与连续播放', 'watch-tv'), item('播放控件与画质', 'playback-controls'),
    item('音轨与字幕', 'subtitles'), item('进度预览与预缓存', 'playback-previews'),
  ] },
  { text: '管理片库', collapsed: true, items: [
    item('修正影片匹配', 'metadata'), item('编辑资料与换海报', 'movie-metadata'),
    item('合集与人物', 'collections'), item('修正剧集匹配', 'tv-matching'),
    item('剧集归属与季号', 'tv-bindings'), item('整理电影与还原', 'organizing'),
    item('整理剧集与撤销', 'tv-organizing'), item('设置与维护入口', 'settings'),
  ] },
  { text: '解决问题', items: [
    item('按现象排查', 'troubleshooting'),
    item('安装与连接排障', 'troubleshooting', 'getting-started'),
  ] },
  { text: '部署与维护', collapsed: true, items: [
    item('配置参考', 'configuration', 'getting-started'),
    item('升级、备份与迁移', 'operations', 'getting-started'),
  ] },
  { text: '开发参考', collapsed: true, items: [
    item('开发导航', 'README', 'developer'), item('架构', 'architecture', 'developer'),
    item('模块与数据', 'modules', 'developer'), item('数据模型', 'data', 'developer'),
    item('存储', 'storage', 'developer'), item('资料匹配', 'metadata', 'developer'),
    item('播放链路', 'playback', 'developer'), item('API', 'api', 'developer'),
    item('开发与验证', 'development', 'developer'), item('同步发布镜像与 APK', 'releasing', 'developer'),
    item('界面规范', 'design-system', 'developer'), item('文档维护', 'documentation', 'developer'),
  ] },
]

// The navigation is the publication allowlist; older entry pages remain explicit
// compatibility entries. New reports and drafts are never published by a glob.
const publicPaths = new Set([
  'index.md', 'README.md', 'getting-started/README.md', 'user-guide/README.md',
  'user-guide/movies.md', 'user-guide/tv.md', 'user-guide/player.md',
  ...sidebar.flatMap(group => group.items.map(entry => entry.link.slice(1).replace(/\.html$/, '.md'))),
])
