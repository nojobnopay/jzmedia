// Deterministic real-page visual review; all APIs mocked, no application backend or real media.
// node scripts/ui_visual_review.mjs [--source /tmp/jzmedia-goal-before] [--label goal-before] [--demo] [--core]
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { build } from '../frontend/node_modules/vite/dist/node/index.js'
import vue from '../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { chromium } from '../docs/node_modules/playwright/index.mjs'
import { createFixture, fixtureVersion } from './fixtures/uiReviewData.mjs'
const root=fileURLToPath(new URL('../',import.meta.url)),args=process.argv.slice(2)
let source=root,label='goal-after',demo=false,core=false,only='',captureDocsMode=false,beforeLabel='goal-before',galleryOnly=false
for(let i=0;i<args.length;i++) { if(args[i]==='--source')source=path.resolve(args[++i]);else if(args[i]==='--label')label=args[++i];else if(args[i]==='--demo')demo=true;else if(args[i]==='--core')core=true;else if(args[i]==='--only')only=args[++i];else if(args[i]==='--capture-docs')captureDocsMode=true;else if(args[i]==='--before-label')beforeLabel=args[++i];else if(args[i]==='--gallery-only')galleryOnly=true;else throw new Error('Unknown argument '+args[i]) }
assert.match(label,/^[a-z0-9-]+$/)
assert.match(beforeLabel,/^[a-z0-9-]+$/)
assert.ok(!captureDocsMode||(!demo&&!only&&!core&&source===root&&label==='goal-after'),'Documentation capture requires a complete current-source goal-after review')
const output=path.join(root,'output/playwright',label),temp=await mkdtemp(path.join(os.tmpdir(),'jzmedia-visual-')),dist=path.join(temp,'dist')
const fixture=createFixture(),results=[],external=[],issues=[],timers=new Set()
let server,browser
const coreScenes=[
  {name:'movie-wall',route:'/?media=1',expect:'.grid > .card',count:13}, {name:'tv-wall',route:'/tv?media=1',expect:'.grid > .card',count:6},
  {name:'collections',route:'/collections',expect:'.grid > .card',count:4}, {name:'collection-detail',route:'/c/301',expect:'.grid > .card',count:4},
  {name:'movie-detail',route:'/m/101'}, {name:'movie-unmatched',route:'/m/113'},
  {name:'show-detail',route:'/tv/201'}, {name:'season-detail',route:'/tv/201/s/1'},
  {name:'episode-detail',route:'/tv/201/s/1/e/403'},
  {name:'settings-overview',route:'/settings?sec=sec-status'}, {name:'settings-tmdb',route:'/settings?sec=sec-tmdb'},
  {name:'settings-matching',route:'/settings?sec=sec-matching&library=1'},
  {name:'settings-files',route:'/settings?sec=sec-files&library=1'},
  {name:'settings-libraries',route:'/settings?sec=sec-libraries'}, {name:'settings-ai',route:'/settings?sec=sec-ai'},
  {name:'settings-workflow',route:'/settings?sec=sec-libtools&library=1'},
  {name:'settings-maintenance',route:'/settings?sec=sec-index'},
  {name:'library-new',route:'/settings?sec=sec-libraries',action:async page=>{await page.getByRole('button',{name:'添加媒体库',exact:true}).click();await page.locator('.create-library-form').waitFor()}},
]
const tutorialScenes=[
  {name:'movie-cast',route:'/m/101',capture:'.body-grid'},
  {name:'movie-similar',route:'/m/101',capture:'.similar-block'},
  {name:'movie-posters',route:'/m/101',overlay:true,action:async page=>{await page.locator('.poster.zoomable').click();await page.getByRole('button',{name:'换海报',exact:true}).click();await page.locator('.poster-cand').first().waitFor()}},
  {name:'movie-multiselect',route:'/',overlay:true,action:async page=>{await page.locator('.sel-circle').nth(0).click();await page.locator('.sel-circle').nth(2).click();await page.getByRole('toolbar',{name:'多选操作'}).waitFor()}},
  {name:'person',route:'/p/501'},
  {name:'scan-review',route:'/settings?sec=sec-libtools&library=1',capture:'.settings-main'},
  {name:'match-review',route:'/settings?sec=sec-libtools&library=2',capture:'.settings-main'},
  {name:'rematch-candidates',route:'/m/101',capture:'.edit-panel',action:async page=>{await page.locator('.action-menu summary').first().click();await page.getByRole('button',{name:'重新匹配',exact:true}).click();await page.getByRole('button',{name:'搜索匹配',exact:true}).click();await page.locator('.edit-panel li').first().waitFor()}},
  {name:'tv-bindings',height:1440,route:'/settings?sec=sec-libtools&library=2',overlay:true,action:async page=>{await page.getByRole('button',{name:'剧集归属与季号',exact:true}).click();await page.locator('.binding-directory').first().waitFor();await page.getByRole('combobox',{name:'库中已有目标剧集'}).selectOption('201');await page.locator('.binding-season-row').first().waitFor()}},
  {name:'tv-bindings-preview',route:'/settings?sec=sec-libtools&library=2',overlay:true,action:async page=>{await page.getByRole('button',{name:'剧集归属与季号',exact:true}).click();await page.locator('.binding-directory').first().waitFor();await page.getByRole('combobox',{name:'库中已有目标剧集'}).selectOption('201');await page.getByRole('button',{name:'预览归属变更',exact:true}).click();await page.locator('.binding-preview').waitFor();await page.locator('.binding-preview').scrollIntoViewIfNeeded()}},
  {name:'tv-episode-match',route:'/tv/201/s/1/e/403',capture:'.match.card-block',action:async page=>{await page.locator('.action-menu summary').first().click();await page.getByRole('button',{name:'修正集号匹配',exact:true}).click();await page.getByRole('button',{name:'查询该季',exact:true}).click();await page.locator('.mrow').first().waitFor()}},
  {name:'tv-versions',route:'/tv/201/s/1'},
  {name:'tv-extras',route:'/tv/201',capture:'.extras'},
  {name:'tv-organize-panel',route:'/settings?sec=sec-tvorganize&library=2',capture:'#sec-tvorganize',action:async page=>{await page.getByRole('button',{name:'预览整理计划',exact:true}).click();await page.locator('.plan-card').waitFor();await page.locator('.plan-card .card-head').click()}},
  {name:'tv-organize-preview',route:'/tv/201',overlay:true,action:async page=>{await page.locator('.action-menu summary').first().click();await page.getByRole('button',{name:'整理剧集目录',exact:true}).click();await page.locator('.organize-dialog .change-card').first().waitFor();await page.locator('.organize-dialog summary').filter({hasText:'查看文件示例'}).click()}},
  {name:'organize-history',route:'/settings?sec=sec-tvorganize&library=2',capture:'.undo',action:async page=>{await page.locator('.undo summary').click();await page.getByRole('button',{name:'预览撤销',exact:true}).click();await page.getByText(/可还原 2 条/).waitFor()}},
  {name:'restore-panel',route:'/settings?sec=sec-restore&library=1',capture:'#sec-restore'},
  {name:'tv-maint',route:'/settings?sec=sec-libtools&library=2&tool=maintenance',capture:'.settings-main',action:async page=>{await page.getByRole('button',{name:'资料维护',exact:true}).click()}},
  {name:'organize-preview',route:'/settings?sec=sec-organize&library=1',capture:'#sec-organize',action:async page=>{await page.locator('.plan-item').first().waitFor()}},
].map(scene=>({...scene,viewports:[1440]}))
const scenes=[...coreScenes,...(core?[]:[...tutorialScenes,
  ...[1,2,3,4].map(step=>({name:'setup-'+step,route:'/setup',step})),
  {name:'movie-filters',route:'/',overlay:true,action:async page=>{await page.getByRole('button',{name:/^全部筛选/}).click();await page.getByRole('dialog',{name:'全部筛选',exact:true}).waitFor()}},
  {name:'file-checkbox-selection',route:'/settings?sec=sec-files&library=1',action:async page=>{const row=page.locator('.fs-row').filter({hasText:'远山来信.mkv'});await row.locator('.fs-name').click();assert.equal(await row.locator('input').isChecked(),false);await page.getByRole('checkbox',{name:'选择 远山来信.mkv',exact:true}).check();await page.locator('.fs-row').filter({hasText:'最后一班夜航.mkv'}).locator('.fs-name').click();assert.equal(await page.locator('.fs-row.selected').count(),1);assert.equal(await page.locator('.fs-row.focused input').isChecked(),false)}},
  {name:'movie-empty',route:'/',mode:'empty'}, {name:'movie-no-results',route:'/?q='+encodeURIComponent('没有这部影片')},
  {name:'tv-empty',route:'/tv',mode:'empty'},
  {name:'tv-error',route:'/tv',mode:'error',apiPath:'/api/tv/shows',expectedText:'剧集加载失败'},
  {name:'collections-empty',route:'/collections',mode:'empty'},
  {name:'collections-error',route:'/collections',mode:'error',apiPath:'/api/collections',expectedText:'合集加载失败'},
  {name:'movie-error',route:'/',mode:'error'}, {name:'movie-loading',route:'/',mode:'loading'},
  {name:'upload-dialog',route:'/',action:async page=>{ await page.locator('summary').filter({hasText:'添加影片'}).click();await page.getByRole('button',{name:'上传文件',exact:true}).click();await page.getByRole('dialog').waitFor() }},
  {name:'tv-upload-dialog',route:'/tv',action:async page=>{await page.locator('summary').filter({hasText:'添加剧集'}).click();await page.getByRole('button',{name:'上传文件',exact:true}).click();await page.getByRole('dialog').waitFor();await page.getByLabel('多选文件',{exact:true}).check()}},
  {name:'collection-delete-dialog',route:'/c/301',action:async page=>{await page.locator('summary').filter({hasText:'管理合集'}).click();await page.getByRole('button',{name:'删除合集',exact:true}).click();await page.getByRole('dialog').waitFor()}},
])]
async function sourceFingerprint() {
  const hash=createHash('sha256')
  async function walk(directory) {
    for(const entry of (await readdir(directory,{withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name))) {
      const file=path.join(directory,entry.name)
      if(entry.isDirectory())await walk(file)
      else if(entry.isFile()){hash.update(path.relative(source,file));hash.update(await readFile(file))}
    }
  }
  await walk(path.join(source,'frontend/src'))
  for(const name of ['frontend/index.html','frontend/package.json']){hash.update(name);hash.update(await readFile(path.join(source,name)))}
  return hash.digest('hex')
}
const source_sha256=await sourceFingerprint()
const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')
async function gallery(){
  const before=await readFile(path.join(root,'output/playwright',beforeLabel,'manifest.json'),'utf8').then(JSON.parse).catch(()=>null)
  const after=await readFile(path.join(root,'output/playwright/goal-after/manifest.json'),'utf8').then(JSON.parse).catch(()=>null)
  const all=after?.results||before?.results||results
  const content=all.filter(r=>r.viewport.width!==375).map(r=>{const old=before?.results.find(b=>b.name===r.name&&b.viewport.width===r.viewport.width);const latest=after?.results.find(b=>b.name===r.name&&b.viewport.width===r.viewport.width);return `<section><h2>${esc(r.name)} · ${r.viewport.width}px</h2><div class="pair">${[[old,beforeLabel,'改前'],[latest,'goal-after','改后']].map(([item,folder,title])=>item?`<figure><figcaption>${title} ${item.metrics.firstCardY!=null?'· 首卡 '+item.metrics.firstCardY+'px':''}</figcaption><a href="../${folder}/${item.file}"><img loading="lazy" src="../${folder}/${item.file}" alt="${esc(r.name)} ${title}"></a></figure>`:'<p>待生成</p>').join('')}</div></section>`}).join('')
  const dir=path.join(root,'output/playwright/ui-review');await mkdir(dir,{recursive:true})
  if(before&&after){
    const comparisons=after.results.map(current=>{const previous=before.results.find(r=>r.name===current.name&&r.viewport.width===current.viewport.width);if(!previous)return null;return {name:current.name,viewport:current.viewport,first_card:{before:previous.metrics.firstCardY,after:current.metrics.firstCardY,delta:current.metrics.firstCardY!=null&&previous.metrics.firstCardY!=null?current.metrics.firstCardY-previous.metrics.firstCardY:null},toolbar:{before:previous.metrics.toolbar,after:current.metrics.toolbar},settings_content:{before:previous.metrics.settingsContent,after:current.metrics.settingsContent},hero:{before:previous.metrics.hero,after:current.metrics.hero}}}).filter(Boolean)
    await writeFile(path.join(dir,'comparisons.json'),JSON.stringify({before_label:beforeLabel,after_label:'goal-after',comparable:before.fixture_sha256===after.fixture_sha256,fixture_sha256:after.fixture_sha256,before_source:before.source_sha256,after_source:after.source_sha256,comparisons},null,2)+'\n')
  }
  await writeFile(path.join(dir,'index.html'),`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>jzmedia UI 同数据对照</title><style>body{background:#141414;color:#eee;margin:0;padding:24px;font:15px/1.5 system-ui}h1{margin:0}p{color:#aaa}section{border-top:1px solid #444;margin-top:32px}h2{font-size:20px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}figure{margin:0;min-width:0}figcaption{padding:10px;background:#252525}img{width:100%;height:auto}a{color:inherit}@media(max-width:700px){.pair{grid-template-columns:1fr}}</style><h1>jzmedia · 界面对照</h1><p>相同虚构数据、原创新海报、相同视口。图像检查不能替代业务验收。每个场景的API错误、溢出与测量见manifest.json。</p>${content}</html>`)
}
async function captureDocs() {
  let ffmpeg='ffmpeg'
  try { execFileSync(ffmpeg,['-version'],{stdio:'ignore'}) }
  catch {
    ffmpeg=null
    for(const python of await readdir(path.join(root,'.venv/lib'))) {
      const bins=path.join(root,'.venv/lib',python,'site-packages/static_ffmpeg/bin')
      for(const platform of await readdir(bins).catch(()=>[])) {
        const candidate=path.join(bins,platform,'ffmpeg')
        try { execFileSync(candidate,['-version'],{stdio:'ignore'});ffmpeg=candidate;break } catch { /* Try another existing platform binary. */ }
      }
      if(ffmpeg)break
    }
    assert.ok(ffmpeg,'An existing FFmpeg installation is required for WebP documentation capture')
  }
  const assets=path.join(root,'docs/assets'),entries=[]
  const appVersion=JSON.parse(await readFile(path.join(root,'frontend/package.json'),'utf8')).version
  const sourceCommit=execFileSync('git',['rev-parse','--short','HEAD'],{cwd:root,encoding:'utf8'}).trim()
  const samples=[
    ['movie-library','movie-wall',1440,'user-guide/find-movies.md','紧凑继续观看、完整虚构海报墙与独立排序'],
    ['movie-filters','movie-filters',1440,'user-guide/find-movies.md','电影墙右侧的全部筛选面板、分组条件与固定应用操作'],
    ['mobile-library','movie-wall',390,'user-guide/find-movies.md','390px 手机的搜索、继续观看、排序与三列海报墙'],
    ['collections','collection-detail',1440,'user-guide/collections.md','虚构合集详情及成员海报'],
    ['library-connection','settings-libraries',1440,'user-guide/libraries.md','媒体库连接及分组视频库条目'],
    ['library-new','library-new',1440,'user-guide/libraries.md','按需展开的新建媒体库表单'],
    ['settings-index','settings-maintenance',1440,'user-guide/settings.md','分开的索引修复与播放缓存维护'],
    ['movie-detail','movie-detail',1440,'user-guide/movie-versions.md','电影详情、播放版本与继续观看入口'],
    ['tv-show','show-detail',1440,'user-guide/watch-tv.md','虚构剧集详情、播出与收藏状态、本地及官方季目录'],
    ['tv-season','season-detail',1440,'user-guide/watch-tv.md','季页分集、剧照和播放状态'],
    ['upload-movie','upload-dialog',1440,'user-guide/files.md','电影上传目标与固定底部操作'],
    ['upload-tv','tv-upload-dialog',1440,'user-guide/files.md','剧集散文件上传、所属剧与季号'],
    ['movie-cast','movie-cast',1440,'user-guide/movie-versions.md','虚构电影的演职员头像墙与影片信息'],
    ['movie-similar','movie-similar',1440,'user-guide/movie-versions.md','虚构库中类似推荐与本地海报'],
    ['movie-posters','movie-posters',1440,'user-guide/movie-metadata.md','换海报候选：原创模拟海报与当前选择标记'],
    ['movie-multiselect','movie-multiselect',1440,'user-guide/collections.md','电影海报多选与批量操作浮条'],
    ['person','person',1440,'user-guide/collections.md','虚构人物简介、参演与执导作品'],
    ['scan-review','scan-review',1440,'user-guide/metadata.md','选定电影视频库的入库流程，无待处理项目'],
    ['match-review','match-review',1440,'user-guide/metadata.md','剧集库未匹配、待确认与集号待处理项目'],
    ['rematch-candidates','rematch-candidates',1440,'user-guide/metadata.md','手动匹配候选列表；模拟标题、年份与来源，不实际绑定'],
    ['tv-bindings','tv-bindings',1440,'user-guide/tv-bindings.md','目录归属三步：两个模拟目录、目标剧与逐目录季号'],
    ['tv-bindings-preview','tv-bindings-preview',1440,'user-guide/tv-bindings.md','模拟同集多版本重叠预览与禁止直接保存的冲突提示'],
    ['tv-episode-match','tv-episode-match',1440,'user-guide/tv-matching.md','分集资料匹配：季号查询及候选集名、日期'],
    ['tv-versions','tv-versions',1440,'user-guide/watch-tv.md','同季 V1/V2 版本分组与版本筛选'],
    ['tv-extras','tv-extras',1440,'user-guide/watch-tv.md','虚构幕后花絮与删减片段的独立播放入口'],
    ['tv-organize-panel','tv-organize-panel',1440,'user-guide/tv-organizing.md','剧集批量整理预览、动作统计及文件名变更示例'],
    ['tv-organize-dialog','tv-organize-preview',1440,'user-guide/tv-organizing.md','单剧整理对话框的变更与季分布；未执行文件操作'],
    ['organize-history','organize-history',1440,'user-guide/tv-organizing.md','虚构整理批次、动作类型及撤销预览'],
    ['restore-panel','restore-panel',1440,'user-guide/organizing.md','电影恢复原始位置候选与目标路径；未执行恢复'],
    ['tv-maint','tv-maint',1440,'user-guide/settings.md','当前剧集视频库的资料维护与预览图生成入口'],
    ['organize-preview','organize-preview',1440,'user-guide/organizing.md','电影就地整理预览、勾选范围与逐行动作；未执行移动'],
  ]
  for(const [name,scene,width,page,description] of samples){
    const result=results.find(r=>r.name===scene&&r.viewport.width===width)
    assert.ok(result?.file&&!result.errors.length&&!result.metrics.overflow,'Documentation source must pass the current review: '+name)
    const file='screenshots/'+name+'.webp',destination=path.join(assets,file)
    execFileSync(ffmpeg,['-hide_banner','-loglevel','error','-y','-i',path.join(output,result.file),'-quality','86',destination])
    const bytes=await readFile(destination)
    entries.push({file,page,scene:description,verified_at:new Date().toISOString().slice(0,10),app_version:appVersion,source_commit:sourceCommit,source_state:'当前工作区文档素材核对；源代码哈希 '+source_sha256,source:'真实 Vue 界面与全模拟 API；虚构影片及原创 SVG 海报，不连接真实媒体、数据库或外网',viewport:{...result.viewport,device_scale_factor:1},capture_mode:result.screenshot_mode,recording_script:'node scripts/ui_visual_review.mjs --capture-docs',fixtures:'scripts/fixtures/uiReviewData.mjs',fixture_sha256:createHash('sha256').update(await readFile(path.join(root,'scripts/fixtures/uiReviewData.mjs'))).digest('hex'),format:'WebP q86',bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')})
    console.log('CAPTURE '+file)
  }
  const manifestFile=path.join(assets,'manifest.json'),manifest=JSON.parse(await readFile(manifestFile,'utf8'))
  const replaced=new Set(entries.map(entry=>entry.file))
  manifest.assets=[...manifest.assets.filter(entry=>!replaced.has(entry.file)),...entries]
  await writeFile(manifestFile,JSON.stringify(manifest,null,2)+'\n')
}
if(galleryOnly) {
  try { await gallery(); console.log('Comparison: output/playwright/ui-review/index.html; baseline '+beforeLabel) }
  finally { await rm(temp,{recursive:true,force:true}) }
  process.exit(0)
}
try {
  await mkdir(output,{recursive:true})
  await build({root:path.join(source,'frontend'),configFile:false,envFile:false,cacheDir:path.join(temp,'cache'),plugins:[vue()],worker:{format:'es'},logLevel:'error',build:{outDir:dist,emptyOutDir:true,reportCompressedSize:false}})
  server=createServer(async(req,res)=>{
    res.setHeader('Content-Security-Policy',"default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; base-uri 'self'")
    const url=new URL(req.url,'http://127.0.0.1'),key=url.pathname
    try{
      if(key.startsWith('/posters/')||/\/backdrop$|\/still$|\/poster-orig$/.test(key)){
        res.writeHead(200,{'Content-Type':'image/svg+xml'});res.end(fixture.artwork(key));return
      }
      if(key.startsWith('/api/')){
        const chunks=[];let size=0;for await(const chunk of req){size+=chunk.length;if(size>65536)throw new Error('oversized mock input');chunks.push(chunk)}
        const raw=Buffer.concat(chunks).toString(),value=fixture.api(url,req.method,raw?JSON.parse(raw):null)
        if(key===fixture.state.failurePath&&fixture.state.mode==='error'){res.writeHead(503,{'Content-Type':'application/json'});res.end(JSON.stringify({detail:'演示：资料暂时无法加载，请重试'}));return}
        const send=()=>{if(res.destroyed)return;res.writeHead(value===undefined?501:200,{'Content-Type':'application/json'});res.end(JSON.stringify(value===undefined?{detail:'Unmocked isolated API'}:value))}
        if(key==='/api/search'&&fixture.state.mode==='loading'){const timer=setTimeout(()=>{timers.delete(timer);send()},15000);timers.add(timer)}else send();return
      }
      let file
      if(/^\/assets\/[\w.-]+$/.test(key))file=path.join(dist,key)
      else if(/^\/(?:favicon[^/]*|logo\.svg|icon-512\.png|apple-touch-icon\.png)$/.test(key))file=path.join(dist,key)
      else if(/^\/(?:settings|setup|collections|c\/\d+|m\/\d+|p\/\d+|tv(?:\/\d+(?:\/s\/\d+(?:\/e\/\d+)?)?)?)?\/?$/.test(key))file=path.join(dist,'index.html')
      else{res.writeHead(404);res.end('Only isolated UI routes are served');return}
      const content=await readFile(file);res.writeHead(200,{'Content-Type':{'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png'}[path.extname(file)]||'application/octet-stream'});res.end(content)
    }catch(error){console.error(error);if(!res.headersSent)res.writeHead(500);res.end('Isolated fixture failure')}
  })
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve)})
  const base=`http://127.0.0.1:${server.address().port}`
  if(demo){console.log(`隔离 UI 演示（全部虚构数据与原创矢量素材）：${base}`);console.log('无 .env / 后端 / 数据库 / NAS；Ctrl+C 结束。');await new Promise(resolve=>{process.once('SIGTERM',resolve);process.once('SIGINT',resolve)})}
  else{
    browser=await chromium.launch({headless:true})
    for(const requestedViewport of [{width:1440,height:1000},{width:390,height:844},{width:375,height:812}]){
      for(const scene of scenes.filter(scene=>(!scene.viewports||scene.viewports.includes(requestedViewport.width))&&(!only||only.split(',').includes(scene.name)))){
        const viewport={...requestedViewport,height:scene.height||requestedViewport.height}
        fixture.state.scene=scene.name;fixture.state.mode=scene.mode||'normal';fixture.state.failurePath=scene.apiPath||'/api/search';fixture.state.step=scene.step||1;fixture.state.unexpected=[]
        const context=await browser.newContext({viewport,serviceWorkers:'block',reducedMotion:'reduce'})
        await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==base){external.push(route.request().url());return route.abort()}return route.continue()})
        const page=await context.newPage(),errors=[],failedRequests=[];page.setDefaultTimeout(8000)
        page.on('pageerror',error=>errors.push(String(error)))
        page.on('console',message=>{if(message.type()==='error')errors.push(message.text())})
        page.on('response',response=>{if(response.status()>=400)failedRequests.push({status:response.status(),url:new URL(response.url()).pathname})})
        try{
          await page.goto(base+scene.route)
          await page.getByRole('navigation',{name:'主导航'}).waitFor()
          if(scene.mode==='loading')await page.waitForTimeout(180);else await page.waitForLoadState('networkidle')
          if(scene.action)await scene.action(page)
          if(scene.capture){await page.locator(scene.capture).waitFor();await page.locator(scene.capture).scrollIntoViewIfNeeded();await page.waitForLoadState('networkidle')}
          if(scene.expect)assert.equal(await page.locator(scene.expect).count(),scene.count,scene.name+' must render the complete fixture')
          if(scene.step)await page.locator('.setup-steps [aria-current="step"]').filter({hasText:String(scene.step)}).waitFor()
          if(scene.mode==='loading')await page.getByText('正在加载影片',{exact:true}).waitFor()
          if(scene.mode==='error')await page.getByText(scene.expectedText||'影片加载失败',{exact:true}).waitFor()
          // Full-page Chromium captures can composite offscreen fixed elements at scrollY - 80.
          // Normalize the document scroll while preserving the active control and nested list scrolling.
          await page.evaluate(()=>window.scrollTo(0,0))
          await page.evaluate(()=>document.fonts.ready)
          await page.evaluate(async()=>{await Promise.all([...document.images].filter(image=>image.loading!=='lazy'||image.getBoundingClientRect().top<innerHeight).map(image=>image.decode().catch(()=>{})))})
          const metrics=await page.evaluate(()=>{
            const rect=selector=>{const e=document.querySelector(selector);if(!e)return null;const r=e.getBoundingClientRect();return {x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)}}
            const card=rect('.grid .card, .season-card, .ep-card')
            return {overflow:document.documentElement.scrollWidth>innerWidth+1,firstCardY:card?.y??null,toolbar:rect('.browse-toolbar'),heading:rect('.browse-heading'),settingsContent:rect('.settings-main, .settings-content'),hero:rect('.media-hero'),h1:document.querySelector('h1')?.textContent?.trim(),activeElement:{tag:document.activeElement?.tagName,text:document.activeElement?.textContent?.trim().slice(0,80)},scrollY,skipLink:(()=>{const e=document.querySelector('.skip-link');const s=getComputedStyle(e);return {focused:e.matches(':focus'),top:s.top,position:s.position}})(),imageCount:document.images.length,brokenImages:[...document.images].filter(i=>i.complete&&!i.naturalWidth).map(i=>i.src),overflowElements:[...document.querySelectorAll('body *')].filter(e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.right>innerWidth+1&&s.position!=='fixed'&&!e.closest('.cw-track, .cast-wall, .similar-row')}).slice(0,6).map(e=>({tag:e.tagName,cls:e.className,width:Math.round(e.getBoundingClientRect().width)}))}
          })
          if(scene.name==='movie-unmatched')metrics.posterFallback=await page.locator('.poster-empty').evaluate(element=>{const s=getComputedStyle(element);return {display:s.display,align:s.alignItems,justify:s.justifyContent,font:parseFloat(s.fontSize),iconWidth:element.querySelector('svg')?.getBoundingClientRect().width,label:element.querySelector('span')?.textContent}})
          if(scene.name.endsWith('-dialog'))metrics.dialog=await page.getByRole('dialog').last().evaluate(element=>{const r=element.getBoundingClientRect(),f=element.querySelector('.jz-dialog-footer')?.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,footerBottom:f?.bottom??null,scrollLocked:document.body.style.overflow==='hidden',focusInside:element.contains(document.activeElement),activeTag:document.activeElement?.tagName,activeText:document.activeElement?.textContent?.trim().slice(0,90)}})
          const file=`${scene.name}-${viewport.width}.png`
          if(viewport.width!==375&&scene.capture)await page.locator(scene.capture).screenshot({path:path.join(output,file),animations:'disabled'})
          else if(viewport.width!==375)await page.screenshot({path:path.join(output,file),fullPage:!scene.name.endsWith('-dialog')&&!scene.overlay,animations:'disabled'})
          const expectedError=scene.mode==='error'
          const actualErrors=expectedError?errors.filter(e=>!e.includes('503')):errors
          const actualFailures=failedRequests.filter(r=>!(expectedError&&r.status===503&&r.url===(scene.apiPath||'/api/search')))
          const entry={name:scene.name,route:scene.route,viewport,file:viewport.width===375?null:file,screenshot_mode:scene.capture?'element':scene.name.endsWith('-dialog')||scene.overlay?'viewport':'full-page',metrics,errors:actualErrors,failedRequests:actualFailures,unexpected:[...fixture.state.unexpected],expectedFailure:expectedError?failedRequests:[]}
          if(scene.name==='movie-unmatched'&&!label.includes('before')){const f=metrics.posterFallback;assert.equal(f.display,'flex');assert.equal(f.align,'center');assert.equal(f.justify,'center');assert.ok(f.iconWidth>=24,'Missing-poster uses a legible shared asset');assert.ok(f.label?.trim(),'Missing-poster retains the media title')}
          if(scene.name.endsWith('-dialog')&&!label.includes('before')){
            assert.ok(metrics.dialog.x>=0&&metrics.dialog.y>=0&&metrics.dialog.right<=viewport.width+1&&metrics.dialog.bottom<=viewport.height+1,'Dialog fits viewport')
            assert.equal(metrics.dialog.scrollLocked,true,'Open dialog locks background scrolling')
            assert.equal(metrics.dialog.focusInside,true,'Dialog owns keyboard focus '+JSON.stringify(metrics.dialog))
            if(scene.name==='upload-dialog'||scene.name==='tv-upload-dialog')assert.ok(metrics.dialog.footerBottom!==null&&metrics.dialog.footerBottom<=viewport.height,'Upload primary action remains visible')
            await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'})
            assert.equal(await page.evaluate(()=>document.body.style.overflow),'','Closing dialog restores background scrolling')
            assert.equal(await page.evaluate(()=>document.activeElement?.tagName),'SUMMARY','Closing dialog restores the visible menu trigger')
          }
          results.push(entry)
          if(metrics.overflow||metrics.brokenImages.length||actualErrors.length||actualFailures.length||entry.unexpected.length)issues.push(entry)
          console.log(`${issues.at(-1)===entry?'ISSUE':'PASS'} ${scene.name} ${viewport.width}px → ${file}`)
        }catch(error){const entry={name:scene.name,route:scene.route,viewport,file:null,metrics:{},errors:[String(error)],failedRequests,unexpected:[...fixture.state.unexpected]};results.push(entry);issues.push(entry);console.error('ISSUE '+scene.name+' '+viewport.width+' '+error.message)}
        await context.close()
      }
    }
    let commit;try{commit=execFileSync('git',['rev-parse','HEAD'],{cwd:source,encoding:'utf8',stdio:['ignore','pipe','ignore']}).trim()}catch{commit=source.includes('goal-before')?'06b2a4f (frozen source)':'source archive'}
    const fixture_sha256=createHash('sha256').update(await readFile(path.join(root,'scripts/fixtures/uiReviewData.mjs'))).digest('hex')
    let mergedResults=results
    if(only){
      const prior=await readFile(path.join(output,'manifest.json'),'utf8').then(JSON.parse).catch(()=>null)
      if(prior){
        assert.equal(prior.fixture_sha256,fixture_sha256,'Partial recapture requires unchanged fixture')
        assert.equal(prior.source_sha256,source_sha256,'Partial recapture requires unchanged frontend source')
        const key=r=>r.name+':'+r.viewport.width,newResults=new Map(results.map(r=>[key(r),r]))
        mergedResults=prior.results.map(r=>{const next=newResults.get(key(r));newResults.delete(key(r));return next||r}).concat([...newResults.values()])
      }
    }
    await writeFile(path.join(output,'manifest.json'),JSON.stringify({fixtureVersion,fixture_sha256,source,source_commit:commit,source_sha256,source_changed_during_review:source_sha256!==await sourceFingerprint(),generated_at:new Date().toISOString(),all_api_mocked:true,external_requests:external,business_validation:'Visual and layout review only; pair with existing workflow smoke tests.',results:mergedResults},null,2)+'\n')
    await gallery()
    console.log(`Review ${output}: ${results.length} cases, ${issues.length} issues. Comparison: output/playwright/ui-review/index.html`)
    if(!label.includes('before')){
      assert.equal(issues.length+external.length,0,'Visual review contains unresolved issues; see manifest')
      assert.equal(source_sha256,await sourceFingerprint(),'Frontend source changed during review; rerun against stable source')
    }
    if(captureDocsMode)await captureDocs()
  }
}finally{if(browser)await browser.close();for(const timer of timers)clearTimeout(timer);if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve))}await rm(temp,{recursive:true,force:true})}
