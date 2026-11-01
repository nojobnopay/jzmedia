import test from 'node:test'
import assert from 'node:assert/strict'
import {scanApplies,scanSummary} from '../src/useLibraryScan.js'

test('wall and tools share task scope without attributing another library scan',()=>{
 const lib={id:3,media_library_id:2}
 assert.equal(scanApplies({job_id:'1',library_id:4},lib),false)
 assert.equal(scanApplies({job_id:'1',library_id:3},lib),true)
 assert.equal(scanApplies({job_id:'1',media_library_id:2},lib),true)
 assert.equal(scanApplies({job_id:'1',media_library_id:1},lib),false)
 assert.equal(scanApplies({job_id:'1'},lib),true)
 const job={job_id:'1',state:'done',summary:{counts:{ok:99},by_library:{3:{tv_ok:2,no_match:1}}}}
 assert.equal(scanSummary(job,lib),'扫描完成：新增/更新 2，未匹配 1')
})

test('scan summary reports new ingestion metadata progress and partial failures', () => {
 const library={id:3,media_library_id:2}
 const job={job_id:'1',library_id:3,state:'done',summary:{counts:{tv_ok:8},tv_ingest:{metadata_results:[
   {show_id:7,filled:8,status:'ok',issues:[]},{show_id:8,filled:0,status:'partial',issues:[{code:'season_unavailable'}]},
 ]}}}
 assert.match(scanSummary(job,library),/已补全 8 集资料/)
 assert.match(scanSummary(job,library),/资料待补全 1 部/)
 job.summary.tv_ingest={error:'TMDB暂不可用'}
 assert.match(scanSummary(job,library),/剧集入库检查失败：TMDB暂不可用/)
})

test('scan reconciliation counters remain visible without metadata or physical organize plans', () => {
 const library={id:3,media_library_id:2}
 const job={job_id:'1',library_id:3,state:'done',summary:{counts:{tv_ok:4,replaced_episode:1,changed_episode:1,removed_episode:2,tv_reconcile_review:3}}}
 assert.match(scanSummary(job,library),/识别替换 1 集 · 更新片源 1 集 · 清理失效记录 2 条 · 待确认 3 项/)
})

test('scan metadata file results use the same counts once and keep file review distinct from metadata failures', () => {
 const issue={code:'ambiguous_replacement',message:'存在多个版本'}
 const library={id:3,media_library_id:2}
 const job={job_id:'1',library_id:3,state:'done',summary:{counts:{replaced_episode:1,removed_episode:2,tv_reconcile_review:3},
   tv_ingest:{plans:[{show_id:7,library_id:3},{show_id:8,library_id:4}],metadata_results:[
     {show_id:7,filled:0,issues:[issue],files:{replaced:1,removed:2,review:3,issues:[issue]}},
     {show_id:8,filled:9,issues:[],files:{replaced:9}},
   ]}}}
 const text=scanSummary(job,library)
 assert.match(text,/识别替换 1 集 · 清理失效记录 2 条 · 待确认 3 项/)
 assert.doesNotMatch(text,/识别替换 2 集|识别替换 10 集|已补全 9 集|资料待补全/)
})
