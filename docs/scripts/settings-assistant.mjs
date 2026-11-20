import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { SETTINGS_PAGES, SETTINGS_TASK_PAGES } from '../../frontend/src/settingsNavigation.js'
import { docsRoot, isPublicPage } from '../.vitepress/public-pages.mjs'
import { attr, elements, textContent } from './html.mjs'
import { defaultDist } from './server.mjs'

// IDs are the assistant's vocabulary. Each excerpt comes from the body of
// the linked heading in this version of the published user guide.
const topics = [
  ['overview', 'sec-status', 'settings', '设置页的范围', '查看设置概览', '概览 待办 分类 范围 新手'],
  ['assistant', 'sec-status', 'settings', '问设置助手', '使用设置助手', '设置助手 小放映机 悬浮助手 拖动助手 如何提问 教程链接 确认应用 对话'],
  ['assistant-tips', 'sec-status', 'settings', '浏览时使用小映', '浏览时提问和调整提示', '问小映 频道提问 继续追问 播放器 日常提示 主动提示 只在点开时提示 待机动作 待机动画 招手 皱眉 瞌睡 减少动态效果'],
  ['libraries', 'sec-libraries', 'libraries', '创建本地媒体库', '添加本地媒体库', '添加媒体库 本地路径 容器 挂载目录 视频库 子目录'],
  ['smb', 'sec-libraries', 'libraries', 'smb', '连接 SMB 共享', 'smb samba NAS 网络共享 群晖 远程连接'],
  ['nfs', 'sec-libraries', 'libraries', 'nfs', '连接 NFS 导出目录', 'nfs NAS 导出路径 挂载'],
  ['scan', 'sec-libtools', 'metadata', '入库整理的三个步骤', '扫描与整理视频库', '扫描 整理 入库 重新匹配'],
  ['organize-movie', 'sec-libtools', 'organizing', '还原位置', '还原电影文件位置', '还原位置 电影还原 移回原处 还原目录 撤销电影整理'],
  ['organize-tv', 'sec-libtools', 'tv-organizing', '剧集整理历史与撤销', '查看历史并撤销剧集整理', '撤销 整理历史 撤回 恢复目录 剧集整理 还原剧集'],
  ['files', 'sec-files', 'files', '文件浏览器', '打开文件管理', '文件管理 文件浏览 文件夹 移动 改名 复制'],
  ['delete-files', 'sec-files', 'files', '删除与清理的区别', '核对删除和清理的影响', '删除 清理失效 移除文件 删除媒体库 磁盘文件 不可恢复'],
  ['online', 'sec-online', 'watch-online', 'step-1-打开在线点播', '启用在线点播', '在线视频源 在线点播 在线观看'],
  ['online-source', 'sec-online', 'watch-online', 'step-2-添加并启用来源', '添加并启用视频源', '视频源 添加来源 资源站 接口 MacCMS'],
  ['online-source-directory', 'sec-online', 'watch-online', 'online-source-directory', '去哪里找视频源？', '去哪里找视频源 哪里找视频源 哪里找片源 找片源 在线资源哪里找 稳定的在线资源 获取接口 来源官网 JSON采集接口 主接口 备用接口 暴风 量子 非凡 开放样例'],
  ['online-source-diagnosis', 'sec-online', 'watch-online', 'online-source-diagnosis', '问小映为什么在线资源无法播放', '为什么在线资源无法播放 在线资源播不了 视频源故障 分集播放失败 播放诊断 检测依据 目录失效 临时直连 同意修改 重新诊断 保存后复测'],
  ['online-source-migration', 'sec-online', 'watch-online', 'online-source-migration', '请小映更新视频源接口', '更新视频源接口 更新量子资源接口 更换接口 更换视频源地址 更新来源地址 换接口 换址 替换旧域名 保留旧域名 主机替换'],
  ['online-network', 'sec-online', 'watch-online', 'step-3-选择网络方式', '配置视频源网络', '视频源 网络 直连 代理'],
  ['tmdb', 'sec-tmdb', 'settings', '在线资料服务', '配置和测试 TMDB 连接', 'tmdb 在线资料 海报 语言 代理 api key read token'],
  ['matching', 'sec-matching', 'settings', '匹配规则', '调整视频库的匹配规则', '匹配 匹配规则 来源顺序 优先级 资料来源 本地索引 TMDB 维基数据 豆瓣 Bangumi TVmaze Wikidata NFO 优先 首位 排第一 放在第一 排在前面'],
  ['ai', 'sec-ai', 'settings', '智能辅助', '配置智能辅助服务', 'ai 大模型 智能辅助 智能搜索 模型 接口 DeepSeek OpenCode'],
  ['ai-provider', 'sec-ai', 'settings', 'step-1-选择服务并填写配置', '填写模型服务和基础地址', '服务商 基础地址 本地模型 模型名称 模型地址 API地址 API地址怎么填'],
  ['ai-check', 'sec-ai', 'settings', 'step-2-保存并测试连接', '测试已保存的模型连接', '测试模型 模型连不上 测试智能辅助 连接模型'],
  ['ai-usage', 'sec-ai', 'settings', '密钥、限额与停用', '调整 AI 限额和超时', 'ai 密钥 限额 超时 调用次数 停用'],
  ['offline', 'sec-offline', 'settings', '导入并测试查找', '导入并测试 IMDb 离线资料', 'IMDb 离线资料 离线数据集 tsv 导入'],
  ['protection', 'sec-auth', 'settings', '管理授权与密码修改', '设置儿童与隐私保护密码', '保护密码 管理授权 儿童模式 隐私模式 修改密码 解锁'],
  ['token', 'sec-token', 'settings', '访问保护', '设置访问令牌', '访问令牌 写操作令牌 鉴权 token'],
  ['display', 'sec-display', 'settings', '界面显示与系统维护', '调整字体、海报和剧集提醒', '界面显示 字体 字号 字太小 字太大 字调大 字调小 字大 字小 放大 缩小 海报 大小 每行 剧集更新推荐 更新推荐 更新提醒 每周提醒 关闭推荐 推荐新剧集 剧集推荐 新剧集'],
  ['cache', 'sec-index', 'settings', '调整应用数据与缓存', '调整应用数据与缓存容量', '系统维护 缓存容量 总预算 额度 播放缓存 缩略图 存储空间 空间不够'],
  ['cache-cleanup', 'sec-index', 'settings', '清理缓存与废弃缩略图', '检查并清理废弃缩略图', '清理缓存 废弃缩略图 回收空间 释放空间 删除缓存 空间满了 空间不足 硬盘满了 磁盘满了'],
  ['maintenance', 'sec-maintenance', 'settings', '剧集播出资料维护', '检查剧集播出资料', '资料维护 播出资料 缺季 新集'],
  ['children-metadata', 'sec-maintenance', 'settings', '补全儿童筛选资料', '补全儿童筛选资料', '儿童筛选 分级 补全资料 家长'],
  ['search-index', 'sec-maintenance', 'settings', '修复搜索索引', '修复搜索索引', '搜索结果缺失 重建索引 搜索索引 找不到影片'],
]

function excerptFor(heading) {
  const siblings = heading.parentNode.childNodes
  const paragraphs = []
  for (const node of siblings.slice(siblings.indexOf(heading) + 1)) {
    if (/^h[1-6]$/.test(node.tagName || '')) break
    let lines
    if (node.tagName === 'p') lines = [textContent(node)]
    else if (['ul', 'ol'].includes(node.tagName)) {
      lines = node.childNodes.filter(child => child.tagName === 'li').map(textContent)
    } else if (node.tagName === 'table') {
      const rows = node.childNodes.find(child => child.tagName === 'tbody')?.childNodes || []
      lines = rows.filter(row => row.tagName === 'tr').map(row =>
        row.childNodes.filter(cell => cell.tagName === 'td').map(textContent).join(' · '))
    } else continue
    for (const line of lines) {
      const value = line.trim().replace(/\*\*/g, '')
      if (!value) continue
      if (paragraphs.length && paragraphs.join('\n').length + value.length > 420) return paragraphs.join('\n')
      paragraphs.push(value)
    }
  }
  return paragraphs.join('\n')
}

function renderIndex(dist) {
  const pages = new Map()
  const sections = new Set([...SETTINGS_PAGES, ...SETTINGS_TASK_PAGES].map(item => item.id))
  const covered = new Set()
  const seen = new Set()
  const index = topics.map(([id, section, slug, anchor, title, aliases]) => {
    const page = 'user-guide/' + slug
    if (seen.has(id) || !sections.has(section) || !isPublicPage(page + '.md')) {
      throw new Error(`设置助手主题无效 ${id}: ${section}, ${page}`)
    }
    seen.add(id)
    covered.add(section)
    if (!pages.has(page)) pages.set(page, elements(readFileSync(path.join(dist, page + '.html'), 'utf8')))
    const heading = pages.get(page).find(node => /^h[1-6]$/.test(node.tagName) && attr(node, 'id') === anchor)
    if (!heading) throw new Error(`设置助手缺少教程章节 ${page}#${anchor}`)
    const excerpt = excerptFor(heading)
    if (!excerpt) throw new Error(`设置助手缺少教程正文 ${page}#${anchor}`)
    return { id, title, page, anchor, section, keywords: aliases.split(' '), excerpt }
  })
  const missing = [...sections].filter(section => !covered.has(section))
  if (missing.length) throw new Error(`设置助手缺少分区教程 ${missing.join(', ')}`)
  return JSON.stringify(index, null, 2) + '\n'
}

const indexPath = path.join(docsRoot, 'settings-assistant-topics.json')

export function checkAssistantTopics(dist = defaultDist) {
  if (readFileSync(indexPath, 'utf8') !== renderIndex(dist)) {
    throw new Error('设置助手摘录与本版手册不符。请在 docs 目录执行 npm run assistant:index 并提交更新。')
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  writeFileSync(indexPath, renderIndex(defaultDist))
}
