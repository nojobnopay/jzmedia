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
