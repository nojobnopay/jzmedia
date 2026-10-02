// Deterministic fictional data and original vector art. No downloaded artwork or real media.
export const fixtureVersion = 'jzmedia-ui-review-v1'
const titles = ['远山来信', '最后一班夜航', '沿着海岸慢慢走', '星河尽头的约定', '旧城里的夏天', '风停之前', '二十四小时之外', '月光照亮无人抵达的北方站台', '雨季旅人', '橙色地平线', '明日重逢', '无声的森林', '未识别的家庭录像_2024_最终完整版']
const tvTitles = ['群岛之间', '长夜观测站', '街角食堂', '远方的回声：写给每一个仍在路上的人', '山海纪事', '星期天的房间']
const palettes = [['#172d39','#607976','#d5ab71'],['#181d38','#666487','#d79871'],['#17424b','#759e99','#e9c99a'],['#1a244c','#63739c','#e0b47a'],['#5a362b','#b68c69','#efd0a0'],['#273b2e','#7c966d','#c9d1a4'],['#2a243c','#987d96','#d9b0a1'],['#192e43','#50778e','#dce0d0'],['#203635','#698982','#c4b091'],['#663f2c','#b27444','#ffcf83'],['#323d4a','#91a0ad','#ddc8ae'],['#1c3029','#587966','#b2b791']]
export function art(index = 0, wide = false, portrait = false) {
  const i = Math.abs(Number(index) || 0), [dark, middle, light] = palettes[i % palettes.length]
  const w = wide ? 1600 : 600, h = wide ? 900 : 900
  const hills = `<path d="M0 ${h*.69} Q${w*.27} ${h*.32} ${w*.55} ${h*.65} T${w} ${h*.44} V${h}H0Z" fill="${middle}"/><path d="M0 ${h*.85} Q${w*.38} ${h*.51} ${w*.6} ${h*.76} T${w} ${h*.68}V${h}H0Z" fill="${dark}"/>`
  const city = Array.from({length: 12}, (_,n)=>`<rect x="${n*w/11}" y="${h*(.48+(n%4)*.045)}" width="${w/15}" height="${h*.5}" fill="${n%2?dark:middle}"/><path d="M${n*w/11+8} ${h*.65}v42" stroke="${light}" stroke-width="3" opacity=".5"/>`).join('')
  const scenery = i%3===0 ? hills : i%3===1 ? city : `<path d="M0 ${h*.54}Q${w*.3} ${h*.48} ${w*.6} ${h*.58}T${w} ${h*.54}V${h}H0Z" fill="${middle}"/><path d="M0 ${h*.7} Q${w*.35} ${h*.6} ${w} ${h*.77}M0 ${h*.8}Q${w*.5} ${h*.7} ${w} ${h*.87}" fill="none" stroke="${light}" opacity=".22" stroke-width="3"/>`
  const title = titles[i%12]
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><defs><linearGradient id="sky" x2=".25" y2="1"><stop stop-color="${dark}"/><stop offset="1" stop-color="${middle}"/></linearGradient><pattern id="grain" width="7" height="7" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".65" fill="#fff" opacity=".06"/></pattern></defs><rect width="${w}" height="${h}" fill="url(#sky)"/><circle cx="${w*(.62+(i%3)*.04)}" cy="${h*.28}" r="${wide?110:72}" fill="${light}" opacity=".85"/>${portrait ? `<circle cx="300" cy="360" r="122" fill="${light}"/><path d="M105 850Q80 510 300 495Q530 510 495 850" fill="${middle}"/><path d="M175 350Q150 190 310 195Q450 205 430 355Q390 250 220 300Z" fill="${dark}"/>` : scenery}<rect width="${w}" height="${h}" fill="url(#grain)"/>${wide||portrait ? '' : `<text x="46" y="100" fill="${light}" font-family="serif" font-size="17" letter-spacing="6">JZ · CINEMA STUDIES</text><text x="44" y="725" fill="#fff3de" font-family="serif" font-size="${title.length>7?34:48}" letter-spacing="4">${title}</text><path d="M48 764H140" stroke="${light}"/><text x="47" y="805" fill="${light}" font-family="sans-serif" font-size="16" letter-spacing="5">A FICTIONAL FILM · ${2014+i}</text><text x="48" y="856" fill="${light}" font-family="sans-serif" font-size="12" letter-spacing="2">ORIGINAL VECTOR ART / ISOLATED UI REVIEW</text>`}</svg>`
}
const overview = '一封迟到的信，把久未归家的旅人带回海边小城。在旧车站、雨后的街巷和即将关闭的放映室之间，人们重新发现被日常掩藏的温柔。这是一段关于相遇、告别与重新出发的故事。'
const progress = { position: 2180, duration: 6900, remaining_sec: 4720, position_text: '36:20', percent: 0.316 }
export const cast = ['陈予安','林知夏','周远山','许星河','宋雨宁','程望'].map((name,i)=>({id:501+i,tmdb_id:501+i,name,avatar:`posters/person-${i}.svg`,role:'actor',character:['林舟','江晚','陈默'][i%3],job:i===5?'Director':null}))
export const movies = titles.map((title,i)=>({id:101+i,title,original_title:['Letters from the Mountains','The Last Night Flight','Along the Coast'][i%3],year:2014+i,tmdb_id:i===12?null:7001+i,poster_path:i===12?null:`posters/movie-${i}.svg`,library_id:1,library_name:'电影',media_library_id:1,media_name:'客厅影库',file_path:`${title} (${2014+i})/${title}.mkv`,genres:i%2?['剧情','悬疑']:['剧情','冒险'],region:'华语',origin_countries:['CN'],original_language:'zh',tags:i%3===0?['周末片单','值得重看']:[],persons:cast,collections:i<4?[{id:301,name:'沿途风景 · 慢电影精选'}]:[],versions:[],watched:i%4===3?1:0,needs_review:i===10?1:0,tmdb_rating:7.3+(i%5)*.3,custom_rating:i===0?8.5:null,douban_rating:8.2,overview_display:overview,runtime:115,added_at:'2026-09-21T08:30:00',version_count:i===0?2:1,progress:i<3?progress:null,exists:true}))
for (const movie of movies) movie.versions=[{id:movie.id,file_path:movie.file_path,edition:'院线版',spec:'1080p',size:5832349200,library_id:1,exists:true},...(movie.id===101?[{id:150,file_path:'远山来信 (2014)/远山来信.4K.mkv',edition:'修复版',spec:'2160p',size:14256392000,library_id:1,exists:true}]:[])]
export const episodes = Array.from({length:8},(_,i)=>({id:401+i,show_id:201,show_title:tvTitles[0],show_year:2023,season:1,season_name:'第一季',episode:i+1,title:['潮汐带来的消息','第一班渡轮','无人接听的电话','雨落在旧码头','一起等到天亮','岛屿的另一边','风暴来临之前','我们终将再次相遇'][i],air_date:`2023-06-${String(1+i).padStart(2,'0')}`,overview,still_path:`posters/still-${i}.svg`,show_backdrop_path:'posters/backdrop-2.svg',exists:true,watched:i<2?1:0,progress:i===2?{...progress,duration:2700,position:820,remaining_sec:1880}:null,runtime:45,tmdb_rating:8.3,cast,season_count:2,file_path:`群岛之间/Season 01/S01E${String(i+1).padStart(2,'0')}.mkv`,library_id:2}))
const allEpisodes=[...episodes,...episodes.map(e=>({...e,id:e.id+20,season:2,season_name:'第二季',watched:0,progress:null}))]
export const shows = tvTitles.map((title,i)=>({id:201+i,title,year:2023-i,tmdb_id:9001+i,poster_path:`posters/tv-${i+2}.svg`,backdrop_path:`posters/backdrop-${i+2}.svg`,library_id:2,library_name:'剧集',media_library_id:1,media_name:'客厅影库',genres:['剧情','悬疑'],tags:[],region:'华语',status:i%2?'Ended':'Returning Series',episode_run_time:45,first_air_date:'2023-06-01',original_language:'zh',tmdb_rating:8.7-i*.2,overview,watched_count:2,episode_count:16,season_count:2,seasons:[1,2].map(s=>({season:s,name:`第${s===1?'一':'二'}季`,poster_path:`posters/tv-${i+s}.svg`,episode_count:8,watched_count:s===1?2:0})),episodes:allEpisodes,cast,extras:[],next_episode:episodes[2]}))
export const collections = ['沿途风景 · 慢电影精选','周末悬疑放映室','在星河与城市之间的夜晚','给未来的一封信'].map((name,i)=>({id:301+i,name,overview,cover:movies[i*2].poster_path,member_count:4,members:movies.slice(i*2,i*2+4)}))
export const libraries=[{id:1,name:'电影',kind:'movie',subpath:'电影'},{id:2,name:'剧集',kind:'tv',subpath:'剧集'},{id:3,name:'家庭录像与长标题纪录片收藏',kind:'movie',subpath:'家庭录像'}].map(l=>({...l,movie_count:l.id===1?13:0,episode_count:l.id===2?96:0,media_library_id:1,media_name:'客厅影库',source:'local',enabled:true,effective_enabled:true,media_enabled:true,read_only:false,metadata_providers:'["local","tmdb"]'}))
export const facets={genres:[{value:'剧情',count:12},{value:'悬疑',count:6},{value:'冒险',count:6}],regions:[{value:'华语',count:13}],countries:[{code:'CN',name:'中国',count:13}],years:[{value:2023,count:4},{value:2020,count:3}],decades:[{value:2020,count:8},{value:2010,count:5}],tags:[{value:'周末片单',count:4},{value:'值得重看',count:4}],collections:[],watched:{watched:3,unwatched:10},ratings:{tmdb:[{min:8,count:7},{min:7,count:13}],douban:[],custom:[]},status:[{value:'Ended',count:3},{value:'Returning Series',count:3}]}
export function createFixture() {
  const state={mode:'normal',failurePath:'/api/search',step:1,requests:[],unexpected:[]}
  const onboarding=()=>({show_welcome:false,status:'active',step:state.step,kind:'movie',library_id:1,tmdb_verified:true,tmdb_skipped:false,import_mode:'scan',upload_allowed:true,can_complete:true,content:{count:13,pending:2,items:movies.slice(0,4)}})
  const list=items=>({items,has_more:false,total:items.length})
  function api(url,method,body){
    const key=url.pathname;state.requests.push({key,method,query:Object.fromEntries(url.searchParams)})
    const id=Number(key.match(/\/(\d+)(?:\/|$)/)?.[1])
    if(key==='/api/libraries')return {items:libraries,default_id:1}
    if(key==='/api/media-libraries')return {items:[{id:1,name:'客厅影库',source:'local',path:'/demo/media',movie_count:13,episode_count:96,enabled:true,read_only:false,video_libraries:libraries}],smb_driver:'direct'}
    if(key==='/api/onboarding'){if(method==='PATCH')Object.assign(state,{step:body.step||state.step});return onboarding()}
    if(key==='/api/settings')return {tmdb_configured:true,tmdb_language:'zh-CN',tmdb_read_token_set:true,tmdb_read_token_source:'db',tmdb_read_token_masked:'****demo',jzmedia_token_source:'unset',libraries:[]}
    if(key==='/api/health')return {ffmpeg:true,version:'0.19.0',transcoder:{kind:'sw'}}
    if(key==='/api/jobs/stats')return {grouped:13,versions:14,no_match:1,needs_review:1,missing_files:0,by_library:[{library_id:1,name:'电影',grouped:13,pending:2,no_match:1,needs_review:1,missing_files:0}]}
    if(key==='/api/tv/stats')return {shows:6,seasons:12,episodes:96,pending:1,episode_review:1,by_library:[{library_id:2,name:'剧集',shows:6,episodes:96,pending:1,episode_review:1}]}
    if(key==='/api/metadata/providers')return {providers:[{name:'local',label:'本地索引',available:true},{name:'tmdb',label:'TMDB',available:true}]}
    if(key==='/api/metadata/test-search')return {library_id:1,kind:'movie',items:[{title:titles[0],year:2014,source:'tmdb'}],source:'tmdb',elapsed_ms:82,chain:['local','tmdb']}
    if(key==='/api/jobs/scan')return {state:'idle'}
    if(key==='/api/ai/settings')return {enabled:false,provider:'deepseek',base_url:'https://api.deepseek.com',model:'demo',timeout_seconds:12,daily_limit:100,usage:{requests:0,input_tokens:0,output_tokens:0}}
    if(key==='/api/tv/facets'&&state.mode!=='empty')return {...facets,genres:facets.genres.slice(0,2).map(g=>({...g,count:6})),regions:[{value:'华语',count:6}],countries:[{code:'CN',name:'中国',count:6}],watched:{watched:0,unwatched:6}}
    if(key==='/api/facets'||key==='/api/tv/facets')return state.mode==='empty'?{...facets,genres:[],regions:[],countries:[],years:[],decades:[],tags:[]}:facets
    if(key==='/api/search'||key==='/api/tv/shows')return list(state.mode==='empty'||url.searchParams.get('q')==='没有这部影片'?[]:key==='/api/search'?movies:shows)
    if(key==='/api/movies/recent-played')return list(state.mode==='empty'?[]:movies.slice(0,3))
    if(key==='/api/tv/recent-played')return list((state.mode==='empty'?[]:shows.slice(0,2)).map(s=>({...s,show_id:s.id,id:403+(s.id-201)*100,show_title:s.title,season:1,episode:3,progress})))
    if(key==='/api/search/suggest'||key==='/api/tv/suggest')return {items:[],persons:[]}
    if(key==='/api/collections')return list(state.mode==='empty'?[]:collections)
    if(key==='/api/collections/suggest')return {items:[],topups:[],coverage:{unchecked:2,standalone:3}}
    if(key==='/api/collections/suggest/backfill/status')return {state:'idle',done:0,total:0,failed:[]}
    if(/^\/api\/collections\/\d+$/.test(key))return collections.find(c=>c.id===id)
    if(/^\/api\/movies\/\d+$/.test(key))return movies.find(m=>m.id===id)
    if(/^\/api\/movies\/\d+\/similar$/.test(key))return list(movies.slice(1,7))
    if(/^\/api\/tv\/shows\/\d+\/similar$/.test(key))return list(shows.slice(1,5))
    if(/\/(collection-hint|organize-hint)$/.test(key))return {needs:false}
    if(/^\/api\/movies\/\d+\/files$/.test(key))return {items:[],videos:[],sidecars:[],extras:[]}
    if(/^\/api\/tv\/shows\/\d+$/.test(key))return shows.find(s=>s.id===id)
    if(/^\/api\/tv\/shows\/\d+\/seasons\/\d+$/.test(key))return {show_id:id,show_title:tvTitles[0],show_year:2023,show_backdrop_path:'posters/backdrop-2.svg',season:1,name:'第一季',poster_path:'posters/tv-3.svg',overview,episode_count:8,distinct_count:8,watched_count:2,episodes,total:8,has_more:false,next_episode:episodes[2],cast,versions:[{version:1,distinct:8}]}
    if(/^\/api\/tv\/episodes\/\d+$/.test(key))return {...episodes.find(e=>e.id===id),previous_episode:episodes[1],next_episode:episodes[3]}
    if(key==='/api/stream/versions')return {best_version_id:body?.movie_id||101,versions:(movies.find(m=>m.id===(body?.movie_id||101))?.versions||[]).map(v=>({version_id:v.id,method:'direct',playable:true,height:1080,vcodec:'h264',duration:6900,duration_text:'1小时55分',audio_count:2,sub_count:2}))}
    if(key==='/api/stream/progress')return progress
    if(key==='/api/fs/changes')return {library_id:1,pending:false,count:0,revision:0,paths:[],actions:{},active_jobs:[]}
    if(key==='/api/fs/list'){
      const dir=url.searchParams.get('path')||''
      const files=dir?[]:[...movies.slice(0,5).map(m=>({name:m.title+'.mkv',rel:m.title+'.mkv',kind:'feature',size:5832349200,mtime:1780000000,movie_id:m.id,tmdb_id:m.tmdb_id})),{name:'远山来信.zh-Hans.srt',rel:'远山来信.zh-Hans.srt',kind:'subtitle',size:28273,mtime:1780000000},{name:'movie.nfo',rel:'movie.nfo',kind:'nfo',size:5233,mtime:1780000000}]
      return {path:dir,parent:'',crumbs:dir?[{name:dir,rel:dir}]:[],fs_writable:true,dirs:dir?[]:[{name:'待整理',rel:'待整理',children:2},{name:'经典电影收藏',rel:'经典电影收藏',children:4}],files,total_files:files.length,has_more:false,offset:0,limit:200}
    }
    if(key==='/api/files/unmatched')return {items:[],unmatched:[movies[12]],needs_review:[movies[10]],suspect_title:[],orphan_extras:[]}
    if(['/api/files/missing','/api/files/restore-candidates','/api/jobs/tv-organize/history','/api/stream/previews/jobs/latest'].includes(key))return {items:[],state:'idle'}
    if(key==='/api/files/organize')return {plans:[],conflicts:[]}
    if(key==='/api/tmdb/search')return list(movies.slice(0,4))
    state.unexpected.push(`${method} ${key}`);return undefined
  }
  return {state,api}
}
