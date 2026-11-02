import test from 'node:test'
import assert from 'node:assert/strict'
import {followingPlayback} from '../src/episodePlayback.js'

test('chained playback advances from the playing item and preserves version across seasons',async()=>{
 const requested=[]
 const api=async path=>{requested.push(path);return {next:path.endsWith('/1/next')?{id:2,season:1,episode:3,version:2}:{id:3,season:2,episode:1,version:2}}}
 const second=await followingPlayback({kind:'episode',id:1},'剧',api)
 const third=await followingPlayback(second,'剧',api)
 assert.equal(second.kind,'episode');assert.equal(third.id,3)
 assert.match(third.label,/S02E01.*V2/)
 assert.deepEqual(requested,['/api/tv/episodes/1/next','/api/tv/episodes/2/next'])
 assert.equal(await followingPlayback(third,'剧',async()=>({next:null})),null)
 assert.equal(await followingPlayback({kind:'extra',id:1},'剧',api),null)
})

test('automatic playback carries the complete file range and derived title across versions', async () => {
  const result = await followingPlayback({ kind: 'episode', id: 1 }, '剧', async () => ({
    next: { id: 2, season: 1, episode: 3, episode_end: 4, version: 2, title: '第 3 集', display_title: '第3-4集' },
  }))
  assert.equal(result.label, '剧 S01E03-E04 · 第3-4集（V2）')
  const fallback = await followingPlayback(result, '剧', async () => ({
    next: { id: 3, season: 1, episode: 5, episode_end: 6, title: '旧服务端正式集名' },
  }))
  assert.equal(fallback.label, '剧 S01E05-E06 · 旧服务端正式集名')
})
