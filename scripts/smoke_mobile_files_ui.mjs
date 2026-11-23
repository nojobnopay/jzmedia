// Real Vue UI with the repository's isolated demo and intercepted mutation fixtures.
// Run: node scripts/smoke_mobile_files_ui.mjs [http://127.0.0.1:DEMO_PORT]
// An optional URL must be an already running ui_visual_review.mjs --demo instance.
import assert from 'node:assert/strict'
import path from 'node:path'
import {spawn} from 'node:child_process'
import {once} from 'node:events'
import {mkdir,writeFile} from 'node:fs/promises'
import {fileURLToPath,pathToFileURL} from 'node:url'
const root=fileURLToPath(new URL('../',import.meta.url))
assert.ok(process.argv.length<=3,'Only an optional isolated demo URL is accepted')
let demo
async function stopDemo(){if(demo&&demo.exitCode===null&&!demo.killed){demo.kill('SIGTERM');await once(demo,'exit')}}
async function startDemo(){
 demo=spawn(process.execPath,['scripts/ui_visual_review.mjs','--demo','--label','mobile-files-regression'],{cwd:root,stdio:['ignore','pipe','pipe']})
 try{return await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(new Error('Isolated demo startup timed out')),60000)
  const finish=(error,url)=>{clearTimeout(timer);error?reject(error):resolve(url)}
  demo.stdout.on('data',data=>{output+=data;const match=output.match(/http:\/\/127\.0\.0\.1:\d+/);if(match)finish(null,match[0])})
  demo.stderr.on('data',data=>{output+=data})
  demo.once('error',error=>finish(error));demo.once('exit',code=>finish(new Error(`Isolated demo exited (${code}): ${output}`)))
 })}catch(error){await stopDemo();throw error}
}
const base=process.argv[2]||await startDemo()
const demoUrl=new URL(base)
assert.equal(demoUrl.hostname,'127.0.0.1','Only a loopback fixture server is allowed')
assert.equal(demoUrl.origin,base,'Use the isolated demo origin without a path')
const {chromium}=await import(pathToFileURL(path.join(root,'docs/node_modules/playwright/index.mjs')))
const output=path.join(root,'output/playwright/mobile-files-implementation')
await mkdir(output,{recursive:true})
const browser=await chromium.launch({headless:true}).catch(async error=>{await stopDemo();throw error}),context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block',hasTouch:true,reducedMotion:'reduce'})
let batchRequest, batchCount=0
let oldDeleteServer=false, deleteRevision=0
const deleteConfirmations=[]
const deletionPlans=['远山来信','最后一班夜航'].map((title,index)=>({
 id:101+index,title,library_name:'演示电影库',feature_count:1,extra_count:1,total_size:50000,
 files:[{rel:`电影/${title}/${title}.mkv`,kind:'feature',size:49000},
        {rel:`字幕/${title}.zh.srt`,kind:'subtitle',size:1000}]
}))
const errors=[]
await context.route('**/*', async route=>{
 const url=new URL(route.request().url())
 if(url.origin!==base)return route.abort()
 if(url.pathname==='/api/fs/blob')return route.fulfill({status:200,contentType:'text/plain; charset=utf-8',body:'1\n00:00:00,000 --> 00:00:04,000\n手机布局预览样本'})
 if(url.pathname==='/api/tmdb/check')return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({ok:false,elapsed_ms:1,message:'连接失败，请检查服务器代理设置和网络访问权限。'.repeat(70)})})
 if(url.pathname==='/api/movies/batch'){batchCount++;return new Promise(resolve=>{batchRequest=()=>{route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'模拟保存失败，请重试'})}).catch(()=>{}).finally(resolve)}})}
 if(url.pathname==='/api/movies/batch-delete'){
  const body=route.request().postDataJSON()
  if(body.dry_run===false){
   deleteConfirmations.push(body)
   return route.fulfill({status:409,json:{detail:'删除清单已变化，请重新预览，再确认删除'}})
  }
  const header=oldDeleteServer?{}:{preview_id:'preview-'+(++deleteRevision)}
  const plans=oldDeleteServer?deletionPlans.map(({files,...plan})=>plan):deletionPlans
  return route.fulfill({status:200,json:{total_movies:2,total_versions:2,total_files:4,total_bytes:100000,plans,...header}})
 }
 return route.continue()
})
const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(String(e)))
const report=[]
async function snapshot(name){
 if(await page.getByRole('dialog').count()){
  const dialog=page.getByRole('dialog');const box=await dialog.boundingBox();const viewport=page.viewportSize();assert.ok(box.x>=0&&box.y>=0&&box.x+box.width<=viewport.width+1&&box.y+box.height<=viewport.height+1,'dialog fits viewport')
  const footer=await dialog.locator('.jz-dialog-footer').boundingBox();if(footer)assert.ok(footer.y>=0&&footer.y+footer.height<=viewport.height,'dialog actions remain visible')
 }
 await page.screenshot({path:path.join(output,name+'.png'),animations:'disabled'});await writeFile(path.join(output,name+'.txt'),await page.locator('body').ariaSnapshot())}
try{
 for(const width of [1440,390,320,760,761]){
  await page.setViewportSize({width,height:844});await page.goto(base+'/settings?sec=sec-files&library=1');await page.locator('.fs-row').first().waitFor();await page.evaluate(()=>document.fonts.ready)
  const mobile=width<=760
  assert.equal(await page.locator('.fs-secondary-action:visible').count(),mobile?0:4,'secondary toolbar visibility '+width)
  assert.equal(await page.locator('.fs-desktop-sort:visible').count(),mobile?0:width<=1100?4:5,'only mobile or desktop sort '+width)
  if(mobile){
   assert.equal(await page.locator('.fs-library-picker select').isVisible(),true)
   assert.equal(await page.locator('.fs-search').isVisible(),false)
   await page.getByRole('button',{name:'搜索当前目录',exact:true}).click()
  }
  await page.getByRole('searchbox',{name:'搜索当前目录',exact:true}).fill('远山')
  assert.equal(await page.locator('.fs-row').count(),2)
  await page.getByRole('searchbox',{name:'搜索当前目录',exact:true}).fill('')
  if(mobile)await page.getByRole('button',{name:'搜索当前目录',exact:true}).click()
  if(mobile){
   await page.getByRole('button',{name:'打开文件夹 待整理',exact:true}).click();await page.locator('.fs-empty').waitFor()
   await page.getByRole('button',{name:'更多文件操作',exact:true}).click();await page.getByRole('menuitem',{name:'返回上一目录',exact:true}).click();await page.locator('.fs-row').first().waitFor()
   await page.getByRole('button',{name:'更多文件操作',exact:true}).click();await page.getByRole('menuitem',{name:'前进下一目录',exact:true}).click();await page.locator('.fs-empty').waitFor()
   await page.getByRole('button',{name:'上级目录',exact:true}).click();await page.locator('.fs-row').first().waitFor()
  }
  const checked=page.getByRole('checkbox',{name:'选择 远山来信.mkv',exact:true});await checked.check()
  await page.locator('.fs-row').filter({hasText:'最后一班夜航.mkv'}).locator('.fs-name').click()
  assert.equal(await checked.isChecked(),true)
  assert.equal(await page.locator('.fs-row.selected').count(),1)
  if(width<=700){
   await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight))
   const header=await page.locator('.settings-mobile-header').boundingBox();const selection=await page.locator('.fs-selection-summary').boundingBox()
   assert.ok(selection.y>=header.y+header.height-1,'selection toolbar clears the measured task header')
  }
  await page.getByRole('button',{name:'更多文件操作',exact:true}).click()
  assert.match(await page.locator('.fs-menu-target').innerText(),/已勾选 1 项/)
  await page.keyboard.press('Escape')
  assert.equal(await checked.isChecked(),true)
  if(mobile)await page.getByRole('button',{name:'预览 远山来信.zh-Hans.srt',exact:true}).click()
  else {await page.locator('.fs-row').filter({hasText:'远山来信.zh-Hans.srt'}).locator('.fs-name').click();await page.locator('.fs-open-action').click()}
  await page.getByRole('dialog').waitFor();assert.match(await page.getByRole('dialog').innerText(),/远山来信.zh-Hans.srt/)
  await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'})
  assert.equal(await checked.isChecked(),true,'preview preserves checked items')
  await page.getByRole('button',{name:'清除勾选',exact:true}).click();await page.evaluate(()=>window.scrollTo(0,0))
  const metrics=await page.evaluate(()=>({firstFile:document.querySelector('.fs-row').getBoundingClientRect().top,overflow:document.documentElement.scrollWidth>innerWidth,internalScroll:getComputedStyle(document.querySelector('.fs-table-wrap')).overflowY,historyVisible:[...document.querySelectorAll('.fs-history-action')].filter(e=>e.getBoundingClientRect().width).length}))
  assert.equal(metrics.overflow,false)
  if(mobile){assert.equal(metrics.internalScroll,'visible');assert.equal(metrics.historyVisible,0)}
  if(width<=700)assert.ok(metrics.firstFile<=360,'first file remains in the phone viewport')
  report.push({width,...metrics});await snapshot('files-'+width)
 }
 for(const width of [390,320,1440]){
  await page.setViewportSize({width,height:844});await page.goto(base+'/?media=1');await page.locator('.sel-circle').first().waitFor();await page.locator('.sel-circle').nth(0).click({force:true});await page.locator('.sel-circle').nth(1).click({force:true})
  const previousBatchCount=batchCount
  const mobile=width<=1000,toolbar=page.getByRole('toolbar',{name:'多选操作'});await toolbar.waitFor()
  const r=await toolbar.boundingBox();assert.ok(r.x>=0&&r.x+r.width<=width+1)
  const cancel=toolbar.getByRole('button',{name:'取消',exact:true});assert.equal(await cancel.isVisible(),true)
  await snapshot('batch-'+width)
  const openTag=async()=>{if(mobile){await page.locator('.batch-more summary').click();await page.getByRole('button',{name:'批量标签',exact:true}).click()}else await page.getByRole('button',{name:'标签',exact:true}).click();await page.getByRole('dialog',{name:/批量标签/}).waitFor()}
  await openTag();for(let i=0;i<12;i++){await page.keyboard.press('Tab');assert.equal(await page.getByRole('dialog').evaluate(el=>el.contains(document.activeElement)),true,'Tab remains in the dialog')}await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'})
  assert.match(await page.evaluate(()=>document.activeElement.textContent),mobile?/更多/:/标签/,'focus returns to visible opener')
  await openTag();await page.getByLabel('新标签',{exact:true}).fill('手机样本');await page.getByRole('dialog').getByRole('button',{name:'加入',exact:true}).click()
  await page.getByRole('button',{name:'确认提交',exact:true}).click();await page.waitForFunction(()=>document.querySelector('[role=dialog] button[aria-busy=true]'))
  await page.keyboard.press('Escape');assert.equal(await page.getByRole('dialog').count(),1)
  assert.equal(await page.getByRole('dialog').getByRole('button',{name:'取消',exact:true}).isDisabled(),true)
  await batchRequest();await page.getByRole('dialog').getByText(/批量失败/).waitFor();assert.equal(batchCount,previousBatchCount+1)
  await snapshot('batch-tag-error-'+width)
  await page.getByRole('dialog').getByRole('button',{name:'取消',exact:true}).click()
  if(mobile){await page.locator('.batch-more summary').click();await page.getByRole('button',{name:'加入合集',exact:true}).click()}else await page.getByRole('button',{name:'合集',exact:true}).click()
  await page.getByRole('dialog',{name:/加入合集/}).waitFor();await snapshot('batch-collections-'+width);await page.keyboard.press('Escape')
  const openDelete=async()=>{
   if(mobile){await page.locator('.batch-more summary').click();await page.getByRole('button',{name:'删除选中影片',exact:true}).click()}else await page.getByRole('button',{name:'删除',exact:true}).click()
   await page.getByRole('dialog',{name:/删除影片/}).waitFor()
  }
  const dialog=page.getByRole('dialog',{name:/删除影片/}),confirm=dialog.getByRole('button',{name:/确认删除/})
  oldDeleteServer=true
  await openDelete();await dialog.getByText(/服务端未提供完整/).waitFor()
  assert.equal(await confirm.isDisabled(),true);await page.keyboard.press('Escape')
  oldDeleteServer=false
  await openDelete()
  for(const plan of deletionPlans)for(const file of plan.files)await dialog.getByText(file.rel,{exact:false}).waitFor()
  assert.equal(await confirm.isEnabled(),true);await snapshot('batch-delete-'+width)
  const confirmedPreview='preview-'+deleteRevision
  await confirm.click();await dialog.getByText(/删除失败：.*删除清单已变化/).waitFor()
  assert.equal(deleteConfirmations.at(-1).preview_id,confirmedPreview)
  assert.equal(await confirm.count(),0)
  await dialog.getByRole('button',{name:'重新预览'}).click()
  await confirm.waitFor();assert.notEqual('preview-'+deleteRevision,confirmedPreview)
  await page.keyboard.press('Escape')
  assert.equal(await page.getByRole('dialog').count(),0);assert.equal(await toolbar.isVisible(),true)
  if(mobile){
   await page.setViewportSize({width,height:500});await page.evaluate(()=>window.scrollTo(0,document.documentElement.scrollHeight))
   await page.waitForFunction(()=>{const button=document.querySelector('.back-to-top'),bar=document.querySelector('.floatbar');if(!button||!bar)return false;const b=button.getBoundingClientRect(),r=bar.getBoundingClientRect();return b.top>=0&&b.bottom<=r.top})
   await snapshot('batch-back-to-top-'+width)
  }
  await cancel.click();await toolbar.waitFor({state:'hidden'})
 }
 for(const width of [390,320]){
  await page.setViewportSize({width,height:844});await page.goto(base+'/settings?sec=sec-tmdb');await page.locator('#tmdb-read-token').waitFor()
  await page.locator('.tmdb-options > summary').click();await page.getByRole('button',{name:'保存并测试 TMDB 连接',exact:true}).click();await page.locator('.tmdb-result.error').waitFor()
  // A long API error below the form lets its sticky footer scroll out through
  // the top. It must not push the return-to-top control above the viewport.
  await page.evaluate(()=>{const bar=document.querySelector('.tmdb-actions');window.scrollTo(0,bar.closest('fieldset').getBoundingClientRect().bottom+scrollY-bar.getBoundingClientRect().height+20)})
  await page.waitForFunction(()=>{const button=document.querySelector('.back-to-top');if(!button)return false;const rect=button.getBoundingClientRect();return rect.top>=0&&rect.bottom<=innerHeight})
  const metrics=await page.evaluate(()=>{const bar=document.querySelector('.tmdb-actions').getBoundingClientRect(),button=document.querySelector('.back-to-top').getBoundingClientRect();return{scrollY,bar:{top:bar.top,bottom:bar.bottom},button:{top:button.top,bottom:button.bottom}}})
  assert.ok(metrics.scrollY>844&&metrics.bar.top<0&&metrics.bar.bottom>0,'long error reproduces a partly visible top footer')
  assert.ok(metrics.button.top>=0&&metrics.button.bottom<=844,'return-to-top remains inside the viewport')
  report.push({width,scenario:'back-to-top-long-error',...metrics});await snapshot('back-to-top-long-error-'+width)
  await page.getByRole('button',{name:'回到顶部',exact:true}).click();await page.waitForFunction(()=>scrollY===0)
 }
 assert.deepEqual(errors,[])
 await writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({passed:true,report,batchCount},null,2))
}finally{await browser.close();await stopDemo()}
