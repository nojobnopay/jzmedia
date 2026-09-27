import test from 'node:test'
import assert from 'node:assert/strict'
import {browseKey,saveBrowse,readBrowse,browseReturn,clearBrowse,captureAnchor,restoreBrowsePosition} from '../src/browseHistory.js'

test('wall history isolates type, media, filters and sorting but keeps all loaded pages',()=>{
 clearBrowse()
 const key=browseKey('/',2,'sort=year&order=asc&genre=Drama&offset=180&limit=60')
 assert.equal(key,browseKey('/',2,'genre=Drama&order=asc&sort=year&offset=0'))
 const snapshot={path:'/',mediaId:2,query:{genre:'Drama'},items:Array.from({length:240},(_,id)=>({id})),anchor:{id:'170',offset:-15},scrollTop:4115}
 saveBrowse(key,snapshot);snapshot.items.pop()
 assert.equal(readBrowse(key).items.length,240)
 for(const k of [browseKey('/tv',2,''),browseKey('/',3,'sort=year&order=asc&genre=Drama'),browseKey('/',2,'sort=year&order=desc&genre=Drama')])assert.equal(readBrowse(k),null)
 assert.deepEqual(browseReturn('/',2),{path:'/',query:{genre:'Drama'}})
 assert.deepEqual(browseReturn('/tv',3),{path:'/tv',query:{media:3}})
 for(let i=0;i<12;i++)saveBrowse('extra'+i,{path:'/',mediaId:2,query:{q:i}})
 assert.equal(readBrowse(key),null)
})

test('restore uses poster anchor after layout changes instead of stale pixel position',()=>{
 const els=[{dataset:{browseId:'1'},getBoundingClientRect:()=>({top:-180,bottom:-10})},{dataset:{browseId:'2'},getBoundingClientRect:()=>({top:440,bottom:700})}]
 const root={querySelectorAll:()=>els}
 assert.deepEqual(captureAnchor(root),{id:'2',offset:440})
 const oldWindow=globalThis.window,oldDocument=globalThis.document
 let target
 globalThis.document=root;globalThis.window={scrollY:100,scrollTo:opts=>target=opts}
 try{
  restoreBrowsePosition({anchor:{id:'2',offset:40},scrollTop:9999})
  assert.equal(target.top,500)
  restoreBrowsePosition({anchor:{id:'deleted',offset:0},scrollTop:123})
  assert.equal(target.top,123)
 }finally{globalThis.window=oldWindow;globalThis.document=oldDocument}
})
