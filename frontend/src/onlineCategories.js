// Group the source's real taxonomy. A channel never implies a cross-category
// aggregate request: only a selected category ID is sent to the provider.
const ID = /^[A-Za-z0-9_-]{1,128}$/
const CHANNELS = [
  { names: ['电影', '电影片'], label: '电影' },
  { names: ['剧集', '电视剧', '连续剧'], label: '剧集' },
  { names: ['动漫', '动漫片'], label: '动漫' },
  { names: ['综艺', '综艺片'], label: '综艺' },
  { names: ['短剧', '短剧大全'], label: '短剧' },
]
const validId = value => typeof value === 'string' && ID.exec(value)?.[0] === value

export function onlineCategoryTree(rows = []) {
  const categories = [], byId = new Map()
  for (const row of Array.isArray(rows) ? rows.slice(0, 256) : []) {
    if (!validId(row?.id) || typeof row.name !== 'string' || !row.name.trim() || byId.has(row.id)) continue
    const node = { id: row.id, name: row.name.trim().slice(0, 128), parent_id: validId(row.parent_id) ? row.parent_id : '' }
    categories.push(node); byId.set(node.id, node)
  }
  for (const node of categories) {
    if (node.parent_id === node.id || !byId.has(node.parent_id)) node.parent_id = ''
    const seen = new Set([node.id])
    let parent = byId.get(node.parent_id)
    while (parent) {
      if (seen.has(parent.id)) { node.parent_id = ''; break }
      seen.add(parent.id); parent = byId.get(parent.parent_id)
    }
  }
  const rootByCategory = new Map()
  for (const node of categories) {
    let root = node
    while (root.parent_id) root = byId.get(root.parent_id)
    rootByCategory.set(node.id, root.id)
  }
  const channels = categories.filter(node => !node.parent_id).map((node, index) => {
    const rank = CHANNELS.findIndex(channel => channel.names.includes(node.name))
    return { ...node, label: rank >= 0 ? CHANNELS[rank].label : node.name, rank: rank >= 0 ? rank : CHANNELS.length + index,
      children: categories.filter(child => child.id !== node.id && rootByCategory.get(child.id) === node.id) }
  }).sort((a, b) => a.rank - b.rank)
  return { categories, channels, byId, rootByCategory }
}

export function visibleOnlineChannels(tree, selectedId = '', limit = 5) {
  const result = tree.channels.slice(0, limit)
  const selected = tree.channels.find(channel => channel.id === selectedId)
  if (selected && !result.includes(selected)) result.splice(Math.max(0, limit - 1), 1, selected)
  return result
}

export function onlineCategoryLabel(tree, categoryId) {
  const category = tree.byId.get(categoryId)
  if (!category) return ''
  return tree.channels.some(channel => channel.id === categoryId && channel.children.length)
    ? `${category.name}综合条目` : category.name
}

export function onlineChannelCategory(channel, rememberedId = '') {
  if (!channel) return ''
  if (rememberedId === channel.id || channel.children.some(child => child.id === rememberedId)) return rememberedId
  return channel.children[0]?.id || channel.id
}

export function visibleOnlineCategories(channel, selectedId = '', limit = 8) {
  if (!channel?.children.length) return []
  const result = channel.children.slice(0, limit)
  const selected = channel.children.find(category => category.id === selectedId)
  if (selected && !result.includes(selected)) result.splice(Math.max(0, limit - 1), 1, selected)
  return result
}

export function filterOnlineChannels(tree, query = '') {
  const text = typeof query === 'string' ? query.trim().slice(0, 80).toLowerCase() : ''
  return tree.channels.map(channel => {
    const rootMatches = !text || channel.name.toLowerCase().includes(text) || channel.label.toLowerCase().includes(text)
    return { ...channel, showRoot: rootMatches,
      children: rootMatches ? channel.children : channel.children.filter(category => category.name.toLowerCase().includes(text)) }
  }).filter(channel => channel.showRoot || channel.children.length)
}

export function validOnlineYear(value) {
  return typeof value === 'string' && (/^[1-9][0-9]{3}$/.exec(value)?.[0] === value || value === '')
}
