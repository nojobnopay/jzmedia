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
