import test from 'node:test'
import assert from 'node:assert/strict'
import { onlineCategoryTree, visibleOnlineChannels, onlineCategoryLabel, onlineChannelCategory,
  visibleOnlineCategories, filterOnlineChannels, validOnlineYear } from '../src/onlineCategories.js'

const taxonomy = [
  { id: 'commentary', name: '电影解说' }, { id: 'trailer', name: '预告片' }, { id: 'sport', name: '体育赛事' },
  { id: 'movie', name: '电影片' }, { id: 'tv', name: '连续剧' }, { id: 'anime', name: '动漫片' },
  { id: 'variety', name: '综艺片' }, { id: 'short', name: '短剧大全' },
  { id: 'urban', parent_id: 'short', name: '都市脑洞' },
  { id: 'costume', parent_id: 'short', name: '古装仙侠' },
  { id: 'special', parent_id: 'urban', name: '特别篇' },
]

test('common channels include short dramas without hard-coding provider category IDs', () => {
  const tree = onlineCategoryTree(taxonomy)
  assert.deepEqual(visibleOnlineChannels(tree).map(channel => channel.label), ['电影', '剧集', '动漫', '综艺', '短剧'])
  assert.deepEqual(visibleOnlineChannels(tree).map(channel => channel.id), ['movie', 'tv', 'anime', 'variety', 'short'])
  assert.ok(visibleOnlineChannels(tree, 'sport').some(channel => channel.id === 'sport'))
  assert.equal(tree.byId.get('movie').name, '电影片', 'native category names remain intact')
})

test('all descendants remain selectable and a native parent is an explicit independent category', () => {
  const tree = onlineCategoryTree(taxonomy)
  const short = tree.channels.find(channel => channel.id === 'short')
  assert.deepEqual(short.children.map(category => category.id), ['urban', 'costume', 'special'])
  assert.equal(tree.rootByCategory.get('special'), 'short')
  assert.equal(onlineCategoryLabel(tree, 'short'), '短剧大全综合条目')
  assert.equal(onlineCategoryLabel(tree, 'urban'), '都市脑洞')
  assert.equal(onlineChannelCategory(short), 'urban')
  assert.equal(onlineChannelCategory(short, 'costume'), 'costume')
  assert.equal(onlineChannelCategory(short, 'short'), 'short')
  assert.equal(onlineChannelCategory(short, 'movie'), 'urban')
})

test('legacy flat categories, missing parents and cycles cannot hide categories or loop forever', () => {
  const tree = onlineCategoryTree([{ id: '1', name: '电影' }, { id: '2', name: '剧集', parent_id: 'missing' },
    { id: 'a', name: 'A', parent_id: 'b' }, { id: 'b', name: 'B', parent_id: 'a' },
    { id: 'self', name: 'Self', parent_id: 'self' }, { id: '1', name: 'Duplicate' },
    { id: 'bad\n', name: 'Malformed' }, { id: 'x', name: '' }])
  assert.equal(tree.categories.length, 5)
  assert.ok(tree.categories.every(category => tree.rootByCategory.has(category.id)))
  assert.equal(onlineChannelCategory(tree.channels.find(channel => channel.id === '2')), '2')
  assert.equal(onlineCategoryLabel(tree, '1'), '电影')
})

test('compact category choices keep a selected late category visible', () => {
  const channel = { id: 'root', children: Array.from({ length: 12 }, (_, index) => ({ id: String(index), name: `分类${index}` })) }
  const visible = visibleOnlineCategories(channel, '11')
  assert.equal(visible.length, 8)
  assert.equal(visible.at(-1).id, '11')
})

test('category search matches native names and channels without inventing aggregate choices', () => {
  const tree = onlineCategoryTree(taxonomy)
  const child = filterOnlineChannels(tree, '都市')
  assert.equal(child.length, 1)
  assert.equal(child[0].showRoot, false)
  assert.deepEqual(child[0].children.map(category => category.id), ['urban'])
  assert.equal(filterOnlineChannels(tree, '短剧')[0].showRoot, true)
  assert.deepEqual(filterOnlineChannels(tree, 'does-not-exist'), [])
})

test('year choices use exactly four ASCII digits in the supported range', () => {
  for (const value of ['', '1000', '2026', '9999']) assert.equal(validOnlineYear(value), true)
  for (const value of ['999', '0999', '20260', '２０２６', '2026\n', '2026x', 2026, null]) assert.equal(validOnlineYear(value), false)
})
