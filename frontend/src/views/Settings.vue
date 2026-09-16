<template>
  <div class="settings-layout">
    <aside class="side-nav">
      <button v-for="n in navs" :key="n.id" :class="{ on: active === n.id }" @click="go(n.id)">
        {{ n.label }}<span v-if="n.badge" class="nav-badge">{{ n.badge }}</span>
      </button>
    </aside>
    <div class="page settings-main">
      <h2>设置</h2>

      <section id="sec-status" class="card-block">
        <h3>库状态</h3>
        <p v-if="s" class="meta-line">媒体目录：{{ s.media_root }} · 语言：{{ s.tmdb_language }} · TMDB Token：{{ s.tmdb_configured ? '已配' : '未配' }} · 图片源：{{ s.tmdb_image_base }}</p>
        <div v-if="stats" class="stat-grid">
          <div class="stat"><b>{{ stats.grouped }}</b><span>影片</span></div>
          <div class="stat"><b>{{ stats.versions }}</b><span>文件版本</span></div>
          <div class="stat"><b>{{ stats.needs_review }}</b><span>待确认</span></div>
          <div class="stat"><b>{{ stats.no_match }}</b><span>未匹配</span></div>
          <div class="stat warn"><b>{{ stats.missing_files }}</b><span>失效文件</span></div>
          <div class="stat"><b>{{ stats.tmdb_cache }}</b><span>镜像缓存</span></div>
          <div class="stat"><b>{{ stats.persons }}</b><span>人物</span></div>
          <div class="stat"><b>{{ fmtBytes(stats.db_bytes) }}</b><span>数据库</span></div>
          <div class="stat"><b>{{ fmtBytes(stats.posters_bytes) }}</b><span>海报</span></div>
        </div>
        <div class="bar">
          <button @click="testTmdb" :disabled="!!busy">测试TMDB连接</button>
          <span>{{ tmdbMsg }}</span>
        </div>
      </section>

      <section id="sec-tmdb" class="card-block">
        <h3>TMDB 配置</h3>
        <p class="hint">库里的值优先于 `.env`，保存后免重启生效；密钥输入框留空表示不动它。密钥只显示脱敏后 4 位，不回显明文。</p>
        <div class="tmdb-grid">
          <label>Read Token <span class="src-badge">{{ srcText(s?.tmdb_read_token_source) }} {{ s?.tmdb_read_token_masked || '未配' }}</span></label>
          <div class="bar">
            <input v-model="tmdbForm.readToken" type="password" placeholder="粘贴新的 Bearer Token，留空不动" style="flex:1" autocomplete="off" />
            <button v-if="tmdbForm.readToken" @click="tmdbForm.readToken = ''" :disabled="!!busy">清空输入</button>
          </div>
          <label>API Key <span class="src-badge">{{ srcText(s?.tmdb_api_key_source) }} {{ s?.tmdb_api_key_masked || '未配' }}</span></label>
          <div class="bar">
            <input v-model="tmdbForm.apiKey" type="password" placeholder="Token 优先；无 Token 才用 Key，留空不动" style="flex:1" autocomplete="off" />
            <button v-if="tmdbForm.apiKey" @click="tmdbForm.apiKey = ''" :disabled="!!busy">清空输入</button>
          </div>
          <label>代理 <span class="src-badge">{{ srcText(s?.tmdb_proxy_source) }}{{ s?.tmdb_proxy ? '' : ' · 直连' }}</span></label>
          <div class="bar">
            <input v-model="tmdbForm.proxy" placeholder="http://host:port，留空=直连" style="flex:1" autocomplete="off" />
          </div>
          <label>语言 <span class="src-badge">{{ srcText(s?.tmdb_language_source) }}</span></label>
          <div class="bar">
            <input v-model="tmdbForm.language" placeholder="zh-CN" style="flex:1" autocomplete="off" />
          </div>
          <label>图片源 <span class="src-badge">{{ srcText(s?.tmdb_image_base_source) }}</span></label>
          <div class="bar">
            <input v-model="tmdbForm.imageBase" placeholder="https://image.tmdb.org" style="flex:1" autocomplete="off" />
          </div>
        </div>
        <div class="bar">
          <button @click="saveTmdb" :disabled="!!busy">{{ busy === 'tmdb' ? '保存中…' : '保存并测试' }}</button>
          <button @click="clearTmdb" :disabled="!!busy">{{ armClearTmdb ? '确认恢复跟随 .env' : '恢复跟随 .env' }}</button>
          <span>{{ tmdbCfgMsg }}</span>
        </div>
        <p v-if="armClearTmdb" class="hint warn-text">将清空库里的 5 项 TMDB 配置，改回跟随 .env/默认值。再点一次执行。</p>
      </section>

      <section id="sec-auth" class="card-block">
        <h3>访问控制</h3>
        <p class="hint">配置后，写操作（扫描/编辑/整理/删除/设置等 POST/PUT/PATCH/DELETE）需要访问令牌；GET 读取、海报与电视/Kodi 直链不受影响。令牌存库（优先于 <code>.env</code> 的 <code>JZMEDIA_TOKEN</code>），免重启生效；留空并保存 = 关闭鉴权（恢复完全开放）。</p>
        <div class="tmdb-grid">
          <label>访问令牌 <span class="src-badge">{{ srcText(s?.jzmedia_token_source) }} {{ s?.jzmedia_token_masked || '未设置（开放）' }}</span></label>
          <div class="bar">
            <input v-model="authForm.token" type="password" placeholder="新令牌（≥8 位），留空保存=关闭鉴权" style="flex:1" autocomplete="off" />
            <button @click="saveAuth" :disabled="!!busy">{{ busy === 'auth' ? '保存中…' : '保存' }}</button>
            <span>{{ authMsg }}</span>
          </div>
        </div>
        <p class="hint">浏览器首次遇到 401 会弹输入框；输入后令牌存在本机 localStorage。令牌遗失时可直接清空数据库该项或改 `.env` 后重启。</p>
      </section>

      <section id="sec-sync" class="card-block">
        <h3>新片入库</h3>
        <p class="hint">NAS 直拷 / 软件外删片后用这里：先扫描新增入库，再检查并清理失效条目。</p>
        <div class="bar">
          <button @click="doScan" :disabled="!!busy">{{ busy === 'scan' ? '扫描中…' : '扫描新文件' }}</button>
          <span>{{ scanMsg }}</span>
        </div>
        <div class="bar">
          <button @click="loadMissing" :disabled="!!busy">检查失效条目</button>
          <button v-if="missing.length" @click="toggleAllMissing">{{ allChecked ? '全不选' : '全选' }}</button>
          <span v-if="missing.length">共 {{ missing.length }} 条失效</span>
          <button v-if="missing.length > COLLAPSE_N" @click="showAllMissing = !showAllMissing">{{ showAllMissing ? '收起' : `展开全部 (${missing.length})` }}</button>
        </div>
        <ul v-if="missing.length" class="miss-list">
          <li v-for="m in visibleMissing" :key="m.id" class="miss-row">
            <input type="checkbox" :value="m.id" v-model="checkedMissing" />
            <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
            <span class="miss-path">{{ m.file_path }}</span>
          </li>
        </ul>
        <div v-if="missing.length" class="bar">
          <button @click="doClean" :disabled="!!busy || !checkedMissing.length">
            {{ busy === 'clean' ? '清理中…' : `删除选中 (${checkedMissing.length})` }}
          </button>
          <span>{{ cleanMsg }}</span>
        </div>
      </section>

      <section id="sec-pending" class="card-block">
        <h3>匹配确认</h3>
        <p class="hint">上传/扫描后没认出来的片在这里核对。未匹配：TMDB 没找到数据；待确认：模糊命中需人工核对；疑似英文标题：非英语片却显示英文（错配或缺翻译）；未归属花絮：对不上任何影片。点「去处理」到详情页手动绑定。</p>
        <div class="bar">
          <button @click="loadUnmatched" :disabled="!!busy">刷新</button>
          <span v-if="pendingTotal">未匹配 {{ unmatched.length }} · 待确认 {{ needsReview.length }} · 疑似英文 {{ suspectHigh.length + suspectInfo.length }} · 未归属花絮 {{ orphans.length }}</span>
          <span v-else>全部已匹配</span>
        </div>
        <h4 v-if="unmatched.length" class="sub-h">未匹配（{{ unmatched.length }}）<button v-if="unmatched.length > COLLAPSE_N" @click="showAllUnmatched = !showAllUnmatched">{{ showAllUnmatched ? '收起' : '展开全部' }}</button></h4>
        <ul v-if="unmatched.length" class="miss-list">
          <li v-for="m in visibleUnmatched" :key="'u' + m.id" class="miss-row">
            <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
            <span class="miss-path">{{ m.file_path }}</span>
            <button @click="$router.push('/m/' + m.id)">去处理</button>
          </li>
        </ul>
        <h4 v-if="needsReview.length" class="sub-h">待确认（{{ needsReview.length }}）<button v-if="needsReview.length > COLLAPSE_N" @click="showAllNeedsReview = !showAllNeedsReview">{{ showAllNeedsReview ? '收起' : '展开全部' }}</button></h4>
        <ul v-if="needsReview.length" class="miss-list">
          <li v-for="m in visibleNeedsReview" :key="'n' + m.id" class="miss-row">
            <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
            <span class="miss-path">{{ m.file_path }}</span>
            <button @click="$router.push('/m/' + m.id)">去处理</button>
          </li>
        </ul>
        <h4 v-if="suspectHigh.length" class="sub-h">疑似英文标题·重点看（{{ suspectHigh.length }}）<button v-if="suspectHigh.length > COLLAPSE_N" @click="showAllSuspectHigh = !showAllSuspectHigh">{{ showAllSuspectHigh ? '收起' : '展开全部' }}</button></h4>
        <ul v-if="suspectHigh.length" class="miss-list">
          <li v-for="m in visibleSuspectHigh" :key="'sh' + m.id" class="miss-row">
            <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
            <span class="miss-path">{{ m.file_path }}</span>
            <button @click="$router.push('/m/' + m.id)">去处理</button>
          </li>
        </ul>
        <h4 v-if="suspectInfo.length" class="sub-h">英文标题·信息（{{ suspectInfo.length }}，英语片多为正常，刷新后复看）<button v-if="suspectInfo.length > COLLAPSE_N" @click="showAllSuspectInfo = !showAllSuspectInfo">{{ showAllSuspectInfo ? '收起' : '展开全部' }}</button></h4>
        <ul v-if="suspectInfo.length" class="miss-list">
          <li v-for="m in visibleSuspectInfo" :key="'si' + m.id" class="miss-row">
            <span class="miss-title">{{ m.title || '(未命名)' }}<span v-if="m.year"> ({{ m.year }})</span></span>
            <span class="miss-path">{{ m.file_path }}</span>
            <button @click="$router.push('/m/' + m.id)">去处理</button>
          </li>
        </ul>
        <h4 v-if="orphans.length" class="sub-h">未归属花絮（{{ orphans.length }}，文件原地保留，填影片ID认领）<button v-if="orphans.length > COLLAPSE_N" @click="showAllOrphans = !showAllOrphans">{{ showAllOrphans ? '收起' : '展开全部' }}</button></h4>
        <ul v-if="orphans.length" class="miss-list">
          <li v-for="e in visibleOrphans" :key="'o' + e.id" class="miss-row">
            <span class="miss-title">{{ e.kind }}</span>
            <span class="miss-path">{{ e.file_path }}</span>
            <input v-model="orphanMovie[e.id]" placeholder="影片ID" style="width:80px" />
            <button @click="attachOrphan(e.id)" :disabled="!!busy">认领</button>
          </li>
        </ul>
        <div class="bar">
          <button @click="doCleanSidecars" :disabled="!!busy">{{ busy === 'sidecars' ? '清理中…' : (armSidecars ? '确认清理脏行' : '清理历史花絮脏行（只删库，文件保留）') }}</button>
          <span>{{ sidecarsMsg }}</span>
        </div>
        <div class="bar">
          <button @click="doCollectExtras" :disabled="!!busy">{{ busy === 'collect' ? '归位中…' : (armCollect ? '确认归位花絮' : '归位已归属花絮到各片 extras/') }}</button>
          <span>{{ collectMsg }}</span>
        </div>
        <div class="bar">
          <button @click="doCleanEpisodes" :disabled="!!busy">{{ busy === 'episodes' ? '清理中…' : (armEpisodes ? '确认清理剧集行' : '清理历史剧集行（只删库，文件保留）') }}</button>
          <span>{{ episodesMsg }}</span>
        </div>
      </section>

      <section id="sec-meta" class="card-block">
        <h3>元数据维护</h3>
        <div class="bar">
          <button @click="doBackfill" :disabled="!!busy">{{ busy === 'backfill' ? '补数据中…' : '补产地信息' }}</button>
          <span>{{ backfillMsg }}</span>
        </div>
        <div class="bar">
          <button @click="doRefreshAll" :disabled="!!busy">
            {{ busy === 'refresh' ? '刷新中…' : (armRefresh ? `确认刷新全部（约${stats ? stats.grouped : '?'}部）` : '刷新全部TMDB数据') }}
          </button>
          <span>{{ refreshMsg }}</span>
        </div>
        <p v-if="armRefresh" class="hint warn-text">将逐部请求 TMDB（以 limit 截断），无变化的不动，手工标题不受影响。再点一次执行。</p>
        <div class="bar">
          <button @click="doRebuildNfo" :disabled="!!busy">{{ busy === 'nfo' ? '重建中…' : '重建全部NFO' }}</button>
          <span>{{ nfoMsg }}</span>
        </div>
        <div class="bar">
          <button @click="doRebuildFts" :disabled="!!busy">{{ busy === 'fts' ? '重建中…' : '重建搜索索引' }}</button>
          <span>{{ ftsMsg }}</span>
        </div>
      </section>

      <section id="sec-display" class="card-block">
        <h3>显示</h3>
        <div class="slider-row">
          <label>字体大小 <b>{{ prefs.fontSize }}px</b></label>
          <input type="range" min="13" max="20" step="1" v-model.number="prefs.fontSize" @input="saveDisplay" />
        </div>
        <div class="slider-row">
          <label>海报墙密度 <b>{{ prefs.posterMin }}px</b></label>
          <input type="range" min="120" max="200" step="10" v-model.number="prefs.posterMin" @input="saveDisplay" />
        </div>
        <div class="bar">
          <button @click="resetDisplay">恢复默认</button>
        </div>
      </section>

      <section id="sec-organize" class="card-block">
        <h3>归档整理</h3>
        <p class="hint">已匹配确认的片在这里归档到正式库。就地归档：保留原父目录，只建“标题 (年份)/”子目录；搬到顶层：如 待整理 → 电影，按“电影/大区/标题 (年份)/文件”归类。命名均为“标题 (年份)[-版本][-规格][-分卷][-版本N].ext”，先预览再执行。</p>
        <div class="bar">
          <label><input type="radio" value="inplace" v-model="orgMode" /> 就地归档</label>
          <label><input type="radio" value="relocate" v-model="orgMode" /> 搬到顶层</label>
        </div>
        <div v-if="orgMode === 'relocate'" class="bar">
          <label>源 <input v-model="relocateFrom" placeholder="待整理" style="width:120px" /></label>
          <label>目标 <input v-model="relocateTo" placeholder="电影" style="width:120px" /></label>
          <label><input type="checkbox" v-model="groupByRegion" /> 按大区分二级目录</label>
        </div>
        <div class="bar">
          <button @click="loadOrgPreview" :disabled="!!busy">预览</button>
          <button @click="doOrganize" :disabled="!!busy || !orgPlans.length">{{ busy === 'organize' ? '执行中…' : '执行' }}</button>
          <span>{{ orgMsg }}</span>
          <button v-if="orgPlans.length > COLLAPSE_N" @click="showAllPlans = !showAllPlans">{{ showAllPlans ? '收起' : `展开全部 (${orgPlans.length})` }}</button>
        </div>
        <p class="hint">当前：{{ orgMode === 'inplace' ? '就地归档（全库）' : `搬到顶层（${relocateFrom || '待整理'} → ${relocateTo || '电影'}${groupByRegion ? '，按大区' : ''}，含目标下分区过期/未分区）` }} · 列表随参数自动刷新</p>
        <ul v-if="orgPlans.length" class="plan-list">
          <li v-for="p in visiblePlans" :key="p.id" class="plan-row">
            <span class="plan-from" :title="p.from">{{ p.from }}</span>
            <span class="plan-arrow">→</span>
            <span class="plan-to" :title="p.to">{{ p.to }}</span>
            <span v-if="p.numbered" class="plan-status warn">编号{{ p.numbered }}·可改备注</span>
            <span v-if="p.region_stale" class="plan-status warn">原分区过期</span>
            <span v-if="p.status" :class="['plan-status', p.status === 'moved' ? 'ok' : 'fail']">{{ p.status }}</span>
          </li>
        </ul>
        <p v-if="orgConflicts.length" class="hint warn-text">冲突 {{ orgConflicts.length }} 项：
          <span v-if="mismatchCount">疑似错配 {{ mismatchCount }}（需重匹配，不自动加后缀）</span>
          <span v-if="diskCount">磁盘占用 {{ diskCount }}</span>
          <span v-if="dbCount">库内占用 {{ dbCount }}</span>
          <button @click="loadOrgPreview" :disabled="!!busy">重新预览</button>
          <button v-if="conflictGroups.length > COLLAPSE_N" @click="showAllConflicts = !showAllConflicts">{{ showAllConflicts ? '收起' : '展开全部' }}</button>
        </p>
        <div v-if="orgConflicts.length" class="conflict-groups">
          <div v-for="g in visibleConflictGroups" :key="g.to" class="conflict-card">
            <div class="conflict-target">→ {{ g.to }}
              <span v-if="g.kind === 'suspect_mismatch'" class="kind-badge bad">疑似错配·请重匹配</span>
              <span v-else-if="g.kind === 'db'" class="kind-badge">库内占用</span>
              <span v-else class="kind-badge">磁盘占用</span>
            </div>
            <div v-for="p in g.items" :key="'c' + p.id" class="conflict-row">
              <div class="conflict-file" :title="(p.title || '') + ' ' + p.from">
                <span class="conflict-title">{{ p.title || '(未命名)' }}<span v-if="p.tmdb_id"> · TMDB {{ p.tmdb_id }}</span></span>
                <span class="miss-path">{{ p.from }}</span>
              </div>
              <div class="conflict-actions">
                <button @click="$router.push('/m/' + p.id)">去详情匹配</button>
              </div>
              <div class="conflict-note">
                <input v-model="noteEdits[p.id].edition" placeholder="版本" style="width:90px" />
                <input v-model="noteEdits[p.id].spec" placeholder="规格/备注" style="width:90px" />
                <button @click="saveNote(p.id)" :disabled="!!busy">改备注</button>
                <span>{{ noteMsg[p.id] }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>
      <section id="sec-restore" class="card-block">
        <h3>恢复到原始位置</h3>
        <p class="hint">整理/搬迁后偏离首次入库位置的影片可搬回原处。先预览再执行，目标被占用或源文件缺失会跳过上报、绝不覆盖。</p>
        <div class="bar">
          <button @click="loadRestorePreview()" :disabled="!!busy">预览</button>
          <button @click="doRestore" :disabled="!!busy || !checkedRestore.length">{{ busy === 'restore' ? '恢复中…' : (armRestore ? `确认恢复 (${checkedRestore.length})` : '恢复选中') }}</button>
          <button v-if="restorePlans.length" @click="toggleAllRestore">{{ allRestoreChecked ? '全不选' : '全选' }}</button>
          <span>{{ restoreMsg }}</span>
          <button v-if="restorePlans.length > COLLAPSE_N" @click="showAllRestore = !showAllRestore">{{ showAllRestore ? '收起' : `展开全部 (${restorePlans.length})` }}</button>
        </div>
        <p v-if="armRestore" class="hint warn-text">再点一次执行恢复，无二次弹窗。目标被占用/源缺失的项会自动跳过。</p>
        <ul v-if="restorePlans.length" class="plan-list">
          <li v-for="p in visibleRestorePlans" :key="'r' + p.id" class="plan-row">
            <input type="checkbox" :value="p.id" v-model="checkedRestore" />
            <span class="conflict-title">{{ p.title || '(未命名)' }}<span v-if="p.year"> ({{ p.year }})</span></span>
            <span class="miss-path">{{ p.from }} → {{ p.to }}</span>
            <span v-if="p.status" :class="['plan-status', p.status === 'restored' ? 'ok' : 'fail']">{{ restoreStatusText(p.status) }}</span>
          </li>
        </ul>
      </section>

      <section id="sec-files" class="card-block">
        <h3>文件浏览</h3>
        <p class="hint">直接翻目录动手：建子目录、改名、移动、删除，日常少用。花絮/字幕/周边直接删；正片删文件同时清库（影响海报墙），需二次确认。</p>
        <div class="bar fs-crumbs">
          <button @click="loadFs('')" :disabled="!!busy">根</button>
          <span v-for="c in fsCrumbs" :key="c.rel"> / <button @click="loadFs(c.rel)" :disabled="!!busy" class="linklike">{{ c.name }}</button></span>
          <span v-if="fsPath" class="miss-path">{{ fsPath }}</span>
        </div>
        <div class="bar">
          <input v-model="fsMkdirName" placeholder="新子目录名" style="width:160px" />
          <button @click="doFsMkdir" :disabled="!!busy || !fsMkdirName.trim()">新建目录</button>
          <input v-model="fsMoveDir" placeholder="移至目录（相对路径）" style="width:200px; margin-left:12px" />
        </div>
        <div v-if="fsParent !== null" class="bar">
          <button @click="loadFs(fsParent)" :disabled="!!busy">‹ 上级目录</button>
          <span>{{ fsMsg }}</span>
        </div>
        <ul v-if="fsDirs.length" class="miss-list">
          <li v-for="d in fsDirs" :key="'d' + d.rel" class="miss-row">
            <span class="miss-title">📁 {{ d.name }}</span>
            <span class="miss-path">{{ d.children }} 项</span>
            <button @click="loadFs(d.rel)" :disabled="!!busy">进入</button>
            <button @click="doFsDelete(d.rel)" :disabled="!!busy">删空目录</button>
          </li>
        </ul>
        <ul v-if="fsFiles.length" class="miss-list">
          <li v-for="f in fsFiles" :key="'f' + f.rel" class="miss-row fs-file-row">
            <span v-if="f.kind === 'feature'" class="kind-badge bad">正片</span>
            <span v-else-if="f.kind === 'sidecar'" class="kind-badge">花絮</span>
            <span v-else-if="f.kind === 'subtitle'" class="kind-badge">字幕</span>
            <span v-else-if="f.kind === 'nfo'" class="kind-badge">NFO</span>
            <span v-else class="kind-badge">其他</span>
            <span class="miss-title">{{ f.name }}</span>
            <span class="miss-path">{{ fmtBytes(f.size) }}{{ f.title ? ` · ${f.title}` : '' }}</span>
            <input v-model="fsRenameEdits[f.rel]" placeholder="新文件名" style="width:140px" />
            <button @click="doFsRename(f.rel)" :disabled="!!busy">{{ fsArmAction('rename', f.rel) ? '确认改名' : '改名' }}</button>
            <button @click="doFsMove(f.rel)" :disabled="!!busy || !fsMoveDir.trim()">{{ fsArmAction('move', f.rel) ? '确认移动' : '移动' }}</button>
            <button v-if="!fsArmDelete[f.rel]" @click="doFsDelete(f.rel)" :disabled="!!busy">删除</button>
            <button v-else @click="doFsDeleteConfirm(f.rel)" :disabled="!!busy" class="danger">确认删除正片</button>
          </li>
        </ul>
        <p v-if="fsArmHint" class="hint warn-text">{{ fsArmHint }}</p>
        <p v-if="!fsDirs.length && !fsFiles.length" class="hint">空目录</p>
      </section>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, setToken } from '../api.js'
import { loadPrefs, savePrefs, PREF_DEFAULTS } from '../prefs.js'

const route = useRoute()

const COLLAPSE_N = 20

const s = ref(null)
const stats = ref(null)
const busy = ref(null) // scan|clean|backfill|refresh|nfo|fts|organize|restore|tmdb

const tmdbMsg = ref('')
const tmdbCfgMsg = ref('')
const armClearTmdb = ref(false)
const tmdbForm = ref({ readToken: '', apiKey: '', proxy: '', language: '', imageBase: '' })
function srcText(src) {
  return { db: '库', env: '环境变量', default: '默认', unset: '未设' }[src] || ''
}
function syncTmdbForm() {
  tmdbForm.value.proxy = s.value?.tmdb_proxy ?? ''
  tmdbForm.value.language = s.value?.tmdb_language ?? ''
  tmdbForm.value.imageBase = s.value?.tmdb_image_base ?? ''
}
async function saveTmdb() {
  const payload = {}
  if (tmdbForm.value.readToken.trim()) payload.tmdb_read_token = tmdbForm.value.readToken.trim()
  if (tmdbForm.value.apiKey.trim()) payload.tmdb_api_key = tmdbForm.value.apiKey.trim()
  if (tmdbForm.value.proxy.trim() !== (s.value?.tmdb_proxy || '')) payload.tmdb_proxy = tmdbForm.value.proxy.trim()
  if (tmdbForm.value.language.trim() !== (s.value?.tmdb_language || '')) payload.tmdb_language = tmdbForm.value.language.trim()
  if (tmdbForm.value.imageBase.trim().replace(/\/+$/, '') !== (s.value?.tmdb_image_base || '')) payload.tmdb_image_base = tmdbForm.value.imageBase.trim()
  if (!Object.keys(payload).length) {
    tmdbCfgMsg.value = '没有改动'
    return
  }
  busy.value = 'tmdb'
  tmdbCfgMsg.value = ''
  try {
    s.value = await api('/api/settings', { method: 'PUT', body: JSON.stringify(payload) })
    tmdbForm.value.readToken = ''
    tmdbForm.value.apiKey = ''
    syncTmdbForm()
    await testTmdb()
    tmdbCfgMsg.value = '已保存，' + tmdbMsg.value
  } catch (e) {
    tmdbCfgMsg.value = '保存失败：' + e.message
  } finally {
    busy.value = null
  }
}
async function clearTmdb() {
  if (!armClearTmdb.value) {
    armClearTmdb.value = true
    tmdbCfgMsg.value = '将清空库里的 5 项配置，改回跟随 .env/默认值。再点一次确认执行'
    return
  }
  armClearTmdb.value = false
  busy.value = 'tmdb'
  tmdbCfgMsg.value = ''
  try {
    s.value = await api('/api/settings', {
      method: 'PUT',
      body: JSON.stringify({ tmdb_read_token: '', tmdb_api_key: '', tmdb_proxy: '', tmdb_language: '', tmdb_image_base: '' })
    })
    tmdbForm.value.readToken = ''
    tmdbForm.value.apiKey = ''
    syncTmdbForm()
    tmdbCfgMsg.value = '已恢复跟随 .env'
  } catch (e) {
    tmdbCfgMsg.value = '恢复失败：' + e.message
  } finally {
    busy.value = null
  }
}
const scanMsg = ref('')
const cleanMsg = ref('')
const backfillMsg = ref('')
const refreshMsg = ref('')
const nfoMsg = ref('')
const ftsMsg = ref('')
const orgMsg = ref('')

const missing = ref([])
const checkedMissing = ref([])
const allChecked = computed(() => missing.value.length > 0 && checkedMissing.value.length === missing.value.length)
const showAllMissing = ref(false)
const visibleMissing = computed(() => showAllMissing.value ? missing.value : missing.value.slice(0, COLLAPSE_N))

const unmatched = ref([])
const needsReview = ref([])
const suspectHigh = ref([])
const suspectInfo = ref([])
const orphans = ref([])
const orphanMovie = ref({})
const sidecarsMsg = ref('')
const collectMsg = ref('')
const showAllUnmatched = ref(false)
const showAllNeedsReview = ref(false)
const showAllSuspectHigh = ref(false)
const showAllSuspectInfo = ref(false)
const showAllOrphans = ref(false)
const visibleUnmatched = computed(() => showAllUnmatched.value ? unmatched.value : unmatched.value.slice(0, COLLAPSE_N))
const visibleNeedsReview = computed(() => showAllNeedsReview.value ? needsReview.value : needsReview.value.slice(0, COLLAPSE_N))
const visibleSuspectHigh = computed(() => showAllSuspectHigh.value ? suspectHigh.value : suspectHigh.value.slice(0, COLLAPSE_N))
const visibleSuspectInfo = computed(() => showAllSuspectInfo.value ? suspectInfo.value : suspectInfo.value.slice(0, COLLAPSE_N))
const visibleOrphans = computed(() => showAllOrphans.value ? orphans.value : orphans.value.slice(0, COLLAPSE_N))
const pendingTotal = computed(() => unmatched.value.length + needsReview.value.length + suspectHigh.value.length + orphans.value.length)

// 归档整理（统一口 /api/files/organize）
const orgMode = ref('inplace')
const relocateFrom = ref('待整理')
const relocateTo = ref('电影')
const groupByRegion = ref(true)
const orgPlans = ref([])
const orgConflicts = ref([])
const showAllPlans = ref(false)
const showAllConflicts = ref(false)
const visiblePlans = computed(() => showAllPlans.value ? orgPlans.value : orgPlans.value.slice(0, COLLAPSE_N))
// 冲突按目标分组（一张卡放一起：保留方 + 冲突方）
const conflictGroups = computed(() => {
  const map = new Map()
  for (const p of orgConflicts.value) {
    if (!map.has(p.to)) map.set(p.to, { to: p.to, kind: p.kind || '', items: [] })
    map.get(p.to).items.push(p)
  }
  return [...map.values()]
})
const visibleConflictGroups = computed(() => showAllConflicts.value ? conflictGroups.value : conflictGroups.value.slice(0, COLLAPSE_N))
const mismatchCount = computed(() => orgConflicts.value.filter(p => p.kind === 'suspect_mismatch').length)
const diskCount = computed(() => orgConflicts.value.filter(p => p.status === 'conflict_disk_exists').length)
const dbCount = computed(() => orgConflicts.value.filter(p => p.status === 'conflict_db_occupied').length)
// 行内改备注（版本/规格）编辑态
const noteEdits = ref({})
const noteMsg = ref({})
function ensureNote(id) {
  if (!noteEdits.value[id]) noteEdits.value[id] = { edition: '', spec: '' }
  return noteEdits.value[id]
}
async function saveNote(id) {
  const n = ensureNote(id)
  noteMsg.value[id] = ''
  try {
    await api('/api/movies/' + id, {
      method: 'PATCH',
      body: JSON.stringify({ edition: (n.edition || '').trim(), spec: (n.spec || '').trim() })
    })
    noteMsg.value[id] = '已保存，请重新预览'
  } catch (e) {
    noteMsg.value[id] = '保存失败：' + e.message
  }
}

const prefs = ref(loadPrefs())

// 左侧悬浮导航
const pendingCount = computed(() => pendingTotal.value)
const navs = computed(() => [
  { id: 'sec-status', label: '库状态' },
  { id: 'sec-tmdb', label: 'TMDB 配置' },
  { id: 'sec-auth', label: '访问控制' },
  { id: 'sec-sync', label: '新片入库' },
  { id: 'sec-pending', label: '匹配确认', badge: pendingCount.value || '' },
  { id: 'sec-meta', label: '元数据维护' },
  { id: 'sec-display', label: '显示' },
  { id: 'sec-organize', label: '归档整理' },
  { id: 'sec-restore', label: '恢复原始位置', badge: restoreCount.value || '' },
  { id: 'sec-files', label: '文件浏览' },
])
const active = ref('sec-status')
let observer = null
function go(id) {
  active.value = id
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function fmtBytes(n) {
  n = Number(n) || 0
  if (n < 1024) return n + 'B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + 'K'
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + 'M'
  return (n / 1024 / 1024 / 1024).toFixed(2) + 'G'
}

async function loadStats() {
  try { stats.value = await api('/api/jobs/stats') } catch (e) { /* 忽略 */ }
}

async function testTmdb() {
  tmdbMsg.value = '测试中…'
  const t0 = performance.now()
  try {
    await api('/api/tmdb/search?q=' + encodeURIComponent('阿凡达'))
    tmdbMsg.value = `连接正常（${Math.round(performance.now() - t0)}ms）`
  } catch (e) {
    tmdbMsg.value = '连接失败：' + e.message
  }
}

async function doScan() {
  busy.value = 'scan'
  scanMsg.value = ''
  try {
    const d = await api('/api/scan', { method: 'POST' })
    const c = d.counts || {}
    const ok = (c.ok || 0) + (c.ok_needs_review || 0)
    scanMsg.value = `完成：新增/更新 ${ok}，已同步跳过 ${c.skipped_cached || 0}，未匹配 ${c.no_match || 0}`
      + (c.skipped_episode_v1 ? `，剧集跳过 ${c.skipped_episode_v1}` : '')
      + ((d.errors || []).length ? `，失败 ${d.errors.length}` : '')
    await loadStats()
    await loadMissing(true)
    await loadUnmatched(true)
  } catch (e) {
    scanMsg.value = '扫描失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function loadMissing(silent) {
  if (!silent) cleanMsg.value = ''
  try {
    const d = await api('/api/files/missing')
    missing.value = d.items
    checkedMissing.value = d.items.map(m => m.id)
    if (!silent) cleanMsg.value = d.total ? '' : '没有失效条目'
  } catch (e) {
    if (!silent) cleanMsg.value = '检查失败：' + e.message
  }
}

function toggleAllMissing() {
  checkedMissing.value = allChecked.value ? [] : missing.value.map(m => m.id)
}

async function doClean() {
  busy.value = 'clean'
  cleanMsg.value = ''
  try {
    const d = await api('/api/files/clean', { method: 'POST', body: JSON.stringify({ ids: checkedMissing.value, dry_run: false }) })
    const removed = new Set(d.results.map(r => r.id))
    missing.value = missing.value.filter(m => !removed.has(m.id))
    checkedMissing.value = checkedMissing.value.filter(id => !removed.has(id))
    cleanMsg.value = `已删除 ${d.deleted}/${d.total}` + (d.failed.length ? `，失败 ${d.failed.length}` : '')
    await loadStats()
  } catch (e) {
    cleanMsg.value = '清理失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function loadUnmatched(silent) {
  try {
    const d = await api('/api/files/unmatched')
    unmatched.value = d.unmatched || []
    needsReview.value = d.needs_review || []
    suspectHigh.value = d.suspect_title_high || []
    suspectInfo.value = d.suspect_title_info || []
    orphans.value = d.orphan_extras || []
  } catch (e) {
    if (!silent) scanMsg.value = '待处理加载失败：' + e.message
  }
}

async function attachOrphan(id) {
  const mid = Number((orphanMovie.value[id] || '').toString().trim())
  if (!mid) return
  busy.value = 'attach'
  try {
    await api('/api/extras/' + id + '/attach', { method: 'POST', body: JSON.stringify({ movie_id: mid }) })
    orphans.value = orphans.value.filter(e => e.id !== id)
  } catch (e) {
    scanMsg.value = '认领失败：' + e.message
  } finally {
    busy.value = null
  }
}

const armSidecars = ref(false)
const armCollect = ref(false)
async function doCleanSidecars() {
  if (!armSidecars.value) {
    armSidecars.value = true
    sidecarsMsg.value = '只删除库中花絮/样片行，视频文件保留。再点一次确认执行'
    return
  }
  armSidecars.value = false
  busy.value = 'sidecars'
  sidecarsMsg.value = ''
  try {
    const prev = await api('/api/files/clean-sidecars', { method: 'POST', body: JSON.stringify({ dry_run: true }) })
    if (!prev.total) {
      sidecarsMsg.value = '没有花絮脏行'
      return
    }
    const d = await api('/api/files/clean-sidecars', { method: 'POST', body: JSON.stringify({ dry_run: false }) })
    sidecarsMsg.value = `已删除 ${d.deleted}/${d.total}` + (d.failed.length ? `，失败 ${d.failed.length}` : '')
    await loadStats()
    await loadUnmatched(true)
  } catch (e) {
    sidecarsMsg.value = '清理失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function doCollectExtras() {
  if (!armCollect.value) {
    armCollect.value = true
    collectMsg.value = '把已归属但散落在外的花絮搬进各片 extras/。再点一次确认执行'
    return
  }
  armCollect.value = false
  busy.value = 'collect'
  collectMsg.value = ''
  try {
    const prev = await api('/api/extras/collect', { method: 'POST', body: JSON.stringify({ dry_run: true }) })
    if (!prev.total) {
      collectMsg.value = '没有待归位花絮'
      return
    }
    const d = await api('/api/extras/collect', { method: 'POST', body: JSON.stringify({ dry_run: false }) })
    collectMsg.value = `已归位 ${d.moved} 个文件（${d.total} 部片）`
    await loadUnmatched(true)
  } catch (e) {
    collectMsg.value = '归位失败：' + e.message
  } finally {
    busy.value = null
  }
}

const armEpisodes = ref(false)
const episodesMsg = ref('')
const authForm = ref({ token: '' })
const authMsg = ref('')
async function saveAuth() {
  busy.value = 'auth'
  authMsg.value = ''
  try {
    const v = authForm.value.token.trim()
    const d = await api('/api/settings', { method: 'PUT', body: JSON.stringify({ jzmedia_token: v }) })
    s.value = d
    setToken(v)            // 本浏览器后续写操作直接带令牌
    authForm.value.token = ''
    authMsg.value = v ? '已启用写操作鉴权（本浏览器已记住令牌）' : '已关闭鉴权（完全开放）'
  } catch (e) {
    authMsg.value = '保存失败：' + e.message
  } finally {
    busy.value = null
  }
}
async function doCleanEpisodes() {
  if (!armEpisodes.value) {
    armEpisodes.value = true
    episodesMsg.value = '只删除库中剧集行（未匹配且文件名解析为剧集），视频文件保留。再点一次确认执行'
    return
  }
  armEpisodes.value = false
  busy.value = 'episodes'
  episodesMsg.value = ''
  try {
    const prev = await api('/api/files/clean-episodes', { method: 'POST', body: JSON.stringify({ dry_run: true }) })
    if (!prev.total) {
      episodesMsg.value = '没有剧集脏行'
      return
    }
    const d = await api('/api/files/clean-episodes', { method: 'POST', body: JSON.stringify({ dry_run: false }) })
    episodesMsg.value = `已删除 ${d.deleted}/${d.total}` + (d.failed.length ? `，失败 ${d.failed.length}` : '')
    await loadStats()
    await loadUnmatched(true)
  } catch (e) {
    episodesMsg.value = '清理失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function doBackfill() {
  busy.value = 'backfill'
  backfillMsg.value = ''
  try {
    const d = await api('/api/jobs/backfill-meta', { method: 'POST', body: JSON.stringify({}) })
    backfillMsg.value = `回填完成：${d.ok}/${d.total}，失败 ${d.failed.length}`
  } catch (e) {
    backfillMsg.value = '回填失败：' + e.message
  } finally {
    busy.value = null
  }
}

const armRefresh = ref(false)
async function doRefreshAll() {
  if (!armRefresh.value) {
    armRefresh.value = true
    refreshMsg.value = '再点一次确认执行'
    return
  }
  armRefresh.value = false
  busy.value = 'refresh'
  refreshMsg.value = ''
  try {
    const d = await api('/api/jobs/tmdb-refresh', { method: 'POST', body: JSON.stringify({ limit: 5000 }) })
    const changed = d.results.filter(r => r.changed).length
    refreshMsg.value = `完成：${d.total} 部中有变化 ${changed} 部，失败 ${d.failed.length}`
  } catch (e) {
    refreshMsg.value = '刷新失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function doRebuildNfo() {
  busy.value = 'nfo'
  nfoMsg.value = ''
  try {
    const d = await api('/api/jobs/rebuild-nfo', { method: 'POST', body: JSON.stringify({}) })
    nfoMsg.value = `完成：重写 ${d.ok}/${d.total}，跳过缺失 ${d.skipped_missing}，失败 ${d.failed.length}`
  } catch (e) {
    nfoMsg.value = '重建失败：' + e.message
  } finally {
    busy.value = null
  }
}

async function doRebuildFts() {
  busy.value = 'fts'
  ftsMsg.value = ''
  try {
    const d = await api('/api/jobs/rebuild-fts', { method: 'POST' })
    ftsMsg.value = `索引已重建（${d.rows} 行）`
  } catch (e) {
    ftsMsg.value = '重建失败：' + e.message
  } finally {
    busy.value = null
  }
}

function saveDisplay() {
  savePrefs({ ...prefs.value })
}

function resetDisplay() {
  prefs.value = { ...PREF_DEFAULTS }
  savePrefs({ ...prefs.value })
}

function orgBody(dry_run) {
  const b = { mode: orgMode.value, dry_run }
  if (orgMode.value === 'relocate') {
    b.from_prefix = relocateFrom.value.trim()
    b.to_dir = relocateTo.value.trim()
    b.group_by_region = !!groupByRegion.value
  }
  return JSON.stringify(b)
}

function syncNotes() {
  for (const p of orgConflicts.value) ensureNote(p.id)
}

async function loadOrgPreview() {
  orgMsg.value = ''
  try {
    const d = await api('/api/files/organize', { method: 'POST', body: orgBody(true) })
    orgPlans.value = d.plans
    orgConflicts.value = d.conflicts || []
    syncNotes()
    if (!d.plans.length) orgMsg.value = orgConflicts.value.length ? `无可整理，冲突 ${orgConflicts.value.length} 项` : '没有需要整理的'
  } catch (e) {
    orgMsg.value = '预览失败：' + e.message
  }
}

// 模式/参数一变自动重跑预览（防“列表与模式不符”），防抖 300ms，忙时跳过
let orgPreviewTimer = null
watch([orgMode, relocateFrom, relocateTo, groupByRegion], () => {
  if (orgPreviewTimer) clearTimeout(orgPreviewTimer)
  orgPreviewTimer = setTimeout(() => {
    if (!busy.value) loadOrgPreview()
  }, 300)
})

async function doOrganize() {
  busy.value = 'organize'
  orgMsg.value = ''
  try {
    const d = await api('/api/files/organize', { method: 'POST', body: orgBody(false) })
    orgPlans.value = d.results
    orgConflicts.value = d.conflicts || []
    syncNotes()
    const ok = d.results.filter(r => r.status === 'moved').length
    orgMsg.value = `执行完毕：移动 ${ok}/${d.results.length}`
    await loadStats()
    await loadMissing(true)
  } catch (e) {
    orgMsg.value = '执行失败：' + e.message
  } finally {
    busy.value = null
  }
}

// 恢复到原始位置（读 original_file_path，两段确认防误操作）
const restorePlans = ref([])
const checkedRestore = ref([])
const restoreMsg = ref('')
const armRestore = ref(false)
const showAllRestore = ref(false)
const visibleRestorePlans = computed(() => showAllRestore.value ? restorePlans.value : restorePlans.value.slice(0, COLLAPSE_N))
const allRestoreChecked = computed(() => restorePlans.value.length > 0 && checkedRestore.value.length === restorePlans.value.length)
const restoreCount = computed(() => restorePlans.value.length)
function toggleAllRestore() {
  checkedRestore.value = allRestoreChecked.value ? [] : restorePlans.value.map(p => p.id)
}
const restoreStatusMap = {
  restored: '已恢复',
  planned: '待恢复',
  conflict_disk_exists: '目标被占跳过',
  conflict_db_occupied: '库内已占用跳过',
  skipped_missing_src: '源缺失跳过'
}
function restoreStatusText(s) {
  if (!s) return ''
  return restoreStatusMap[s] || (/^error/.test(s) ? '失败' : s)
}
async function loadRestorePreview(preselect) {
  restoreMsg.value = ''
  armRestore.value = false
  if (!Array.isArray(preselect)) preselect = []
  try {
    const d = await api('/api/files/restore-candidates')
    // 预览接口字段是 file_path/original_file_path，统一映射成 from/to（含标题供展示）
    restorePlans.value = (Array.isArray(d.items) ? d.items : []).map(e => ({
      id: e.id, title: e.title || '', year: e.year || '',
      from: e.file_path || '', to: e.original_file_path || ''
    }))
    const ids = (preselect || []).map(Number).filter(Number.isFinite)
    checkedRestore.value = ids.length
      ? restorePlans.value.filter(p => ids.includes(p.id)).map(p => p.id)
      : restorePlans.value.map(p => p.id)
    if (!restorePlans.value.length) restoreMsg.value = '没有偏离原始位置的影片'
    else if (checkedRestore.value.length !== restorePlans.value.length) restoreMsg.value = `共 ${restorePlans.value.length} 项，已预选 ${checkedRestore.value.length} 项`
  } catch (e) {
    restoreMsg.value = '预览失败：' + e.message
  }
}
async function doRestore() {
  if (!armRestore.value) {
    armRestore.value = true
    restoreMsg.value = `再点一次确认恢复 ${checkedRestore.value.length} 项`
    return
  }
  busy.value = 'restore'
  restoreMsg.value = ''
  try {
    const d = await api('/api/files/restore-original', {
      method: 'POST',
      body: JSON.stringify({ ids: checkedRestore.value, dry_run: false })
    })
    const ok = (d.results || []).filter(r => r.status === 'restored').length
    restoreMsg.value = `执行完毕：恢复 ${ok}/${d.results.length}`
    armRestore.value = false
    restorePlans.value = (d.results || []).map(r => ({ id: r.id, title: r.title, from: r.from, to: r.to, status: r.status }))
    checkedRestore.value = (d.results || []).filter(r => r.status !== 'restored').map(r => r.id)
    await loadStats()
    await loadMissing(true)
  } catch (e) {
    restoreMsg.value = '恢复失败：' + e.message
  } finally {
    busy.value = null
  }
}

// 文件浏览（直操 MEDIA_ROOT：浏览/建目录/改名/移动/删除，正片二次确认）
const fsPath = ref('')
const fsParent = ref('')
const fsCrumbs = ref([])
const fsDirs = ref([])
const fsFiles = ref([])
const fsMsg = ref('')
const fsMkdirName = ref('')
const fsMoveDir = ref('')
const fsRenameEdits = ref({})
const fsArmDelete = ref({})
const fsArmHint = ref('')
function fsKindText(k) {
  return { feature: '正片', sidecar: '花絮', subtitle: '字幕', nfo: 'NFO', other: '其他', dir: '目录' }[k] || k
}
async function loadFs(path) {
  fsMsg.value = ''
  fsArmHint.value = ''
  fsArmDelete.value = {}
  try {
    const d = await api('/api/fs/list?path=' + encodeURIComponent(path || ''))
    fsPath.value = d.path || ''
    fsParent.value = d.parent ?? ''
    fsCrumbs.value = d.crumbs || []
    fsDirs.value = d.dirs || []
    fsFiles.value = d.files || []
    for (const f of fsFiles.value) {
      if (!(f.rel in fsRenameEdits.value)) fsRenameEdits.value[f.rel] = ''
    }
  } catch (e) {
    fsMsg.value = '加载失败：' + e.message
  }
}
async function doFsMkdir() {
  const name = fsMkdirName.value.trim()
  if (!name) return
  busy.value = 'fs'
  try {
    await api('/api/fs/mkdir', { method: 'POST', body: JSON.stringify({ path: fsPath.value, name }) })
    fsMkdirName.value = ''
    fsMsg.value = '已创建'
    await loadFs(fsPath.value)
  } catch (e) {
    fsMsg.value = '创建失败：' + e.message
  } finally {
    busy.value = null
  }
}
function fsArmAction(kind, rel) {
  return fsArmDelete.value[kind + ':' + rel] || null
}
async function doFsRename(rel) {
  const name = (fsRenameEdits.value[rel] || '').trim()
  if (!name) {
    fsMsg.value = '先填新文件名'
    return
  }
  const armedKey = 'rename:' + rel
  if (!fsArmAction('rename', rel)) {
    // 两步确认（评审 B8/R14-D2：与删除一致，先预览再执行）
    busy.value = 'fs'
    try {
      const d = await api('/api/fs/rename', { method: 'POST', body: JSON.stringify({ from: rel, name, dry_run: true }) })
      const p = (d.plans || [])[0] || {}
      fsArmDelete.value = { ...fsArmDelete.value, [armedKey]: p }
      fsArmHint.value = p.status === 'conflict_disk_exists'
        ? `目标已存在：${p.to || name}`
        : `将改名：${rel} → ${p.to || name}。再点「确认改名」执行`
    } catch (e) {
      fsMsg.value = '改名预览失败：' + e.message
    } finally {
      busy.value = null
    }
    return
  }
  delete fsArmDelete.value[armedKey]
  busy.value = 'fs'
  try {
    const d = await api('/api/fs/rename', { method: 'POST', body: JSON.stringify({ from: rel, name, dry_run: false }) })
    const r = (d.results || [])[0] || {}
    fsMsg.value = r.status === 'moved' ? `已改名${r.followed ? `（跟随 ${r.followed} 个）` : ''}` : ('改名：' + (r.status || '失败'))
    await loadFs(fsPath.value)
    await loadStats()
  } catch (e) {
    fsMsg.value = '改名失败：' + e.message
  } finally {
    busy.value = null
  }
}
async function doFsMove(rel) {
  const toDir = fsMoveDir.value.trim()
  if (!toDir) {
    fsMsg.value = '先填移至目录'
    return
  }
  const armedKey = 'move:' + rel
  if (!fsArmAction('move', rel)) {
    busy.value = 'fs'
    try {
      const d = await api('/api/fs/move', { method: 'POST', body: JSON.stringify({ from: rel, to_dir: toDir, dry_run: true }) })
      const p = (d.plans || [])[0] || {}
      fsArmDelete.value = { ...fsArmDelete.value, [armedKey]: p }
      fsArmHint.value = p.status === 'conflict_disk_exists'
        ? `目标已存在：${p.to || ''}`
        : `将移动到：${p.to || toDir}。再点「确认移动」执行`
    } catch (e) {
      fsMsg.value = '移动预览失败：' + e.message
    } finally {
      busy.value = null
    }
    return
  }
  delete fsArmDelete.value[armedKey]
  busy.value = 'fs'
  try {
    const d = await api('/api/fs/move', { method: 'POST', body: JSON.stringify({ from: rel, to_dir: toDir, dry_run: false }) })
    const r = (d.results || [])[0] || {}
    fsMsg.value = r.status === 'moved' ? '已移动' : ('移动：' + (r.status || '失败'))
    await loadFs(fsPath.value)
    await loadStats()
  } catch (e) {
    fsMsg.value = '移动失败：' + e.message
  } finally {
    busy.value = null
  }
}
async function doFsDelete(rel) {
  busy.value = 'fs'
  fsArmHint.value = ''
  try {
    const d = await api('/api/fs/delete', { method: 'POST', body: JSON.stringify({ paths: [rel], dry_run: true }) })
    const p = (d.plans || [])[0] || {}
    if (p.status === 'dir_not_empty') {
      fsMsg.value = '目录非空，先清空再删（不做递归删）'
      return
    }
    if (p.requires_confirm) {
      fsArmDelete.value[rel] = p
      const vers = p.version_count > 1 ? `（共 ${p.version_count} 个版本中的 1 个）` : ''
      fsArmHint.value = `警告：将删除正片《${p.title || p.name}》${vers}，海报墙同步移除，关联与索引清理。再点「确认删除正片」执行`
      return
    }
    const d2 = await api('/api/fs/delete', { method: 'POST', body: JSON.stringify({ paths: [rel], dry_run: false }) })
    const r = (d2.results || [])[0] || {}
    fsMsg.value = r.status === 'deleted' ? `已删除（${fsKindText(p.kind)}）` : ('删除：' + (r.status || '失败'))
    await loadFs(fsPath.value)
    await loadStats()
  } catch (e) {
    fsMsg.value = '删除失败：' + e.message
  } finally {
    busy.value = null
  }
}
async function doFsDeleteConfirm(rel) {
  busy.value = 'fs'
  try {
    const d = await api('/api/fs/delete', { method: 'POST', body: JSON.stringify({ paths: [rel], dry_run: false, confirm: true }) })
    const r = (d.results || [])[0] || {}
    fsMsg.value = r.status === 'deleted' ? '正片已删除，库已同步清理' : ('删除：' + (r.status || '失败'))
    delete fsArmDelete.value[rel]
    fsArmHint.value = ''
    await loadFs(fsPath.value)
    await loadStats()
  } catch (e) {
    fsMsg.value = '删除失败：' + e.message
  } finally {
    busy.value = null
  }
}

onMounted(async () => {
  // 首屏请求并行（评审 B8/R14-D1）：此前 6 个重查询串行，大库首开很慢
  const [settingsResp] = await Promise.all([
    api('/api/settings').catch(() => null),
    loadStats(),
    loadFs(''),
    loadOrgPreview(),
    loadMissing(true),
    loadUnmatched(true),
  ])
  if (settingsResp) { s.value = settingsResp; syncTmdbForm() }
  // 详情页“去恢复”跳转承接：?sec=sec-restore&ids=1,2 → 预选并滚动定位
  try {
    const q = route.query || {}
    const ids = (Array.isArray(q.ids) ? q.ids : String(q.ids || '').split(','))
      .map(Number).filter(Number.isFinite)
    if (q.sec || ids.length) await loadRestorePreview(ids)
    if (q.sec && document.getElementById(String(q.sec))) {
      active.value = String(q.sec)
      document.getElementById(String(q.sec))?.scrollIntoView({ block: 'start' })
    }
  } catch (e) { /* 忽略 */ }
  observer = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) active.value = e.target.id
    }
  }, { rootMargin: '-20% 0px -70% 0px' })
  for (const n of navs.value) {
    const el = document.getElementById(n.id)
    if (el) observer.observe(el)
  }
})

onUnmounted(() => {
  if (observer) observer.disconnect()
  if (orgPreviewTimer) clearTimeout(orgPreviewTimer)
})
</script>
<style scoped>
.settings-layout { display: flex; gap: 12px; align-items: flex-start; }
.side-nav { position: sticky; top: 12px; display: flex; flex-direction: column; gap: 6px; min-width: 140px; padding-top: 44px; }
.side-nav button { text-align: left; white-space: nowrap; }
.side-nav button.on { border-color: #e50914; color: #ff8a8a; }
.nav-badge { margin-left: 6px; font-size: 0.75rem; color: #e0a63c; }
.settings-main { flex: 1; min-width: 0; }
.settings-main section { scroll-margin-top: 12px; }
@media (max-width: 860px) {
  .settings-layout { flex-direction: column; }
  .side-nav { position: static; flex-direction: row; overflow-x: auto; padding-top: 0; min-width: 0; }
  .side-nav button { flex-shrink: 0; }
}
.card-block { background: #1c1c1c; border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.card-block h3 { margin: 0 0 10px; font-size: 1.0625rem; color: #ddd; }
.sub-h { margin: 10px 0 4px; font-size: 0.9375rem; color: #ccc; display: flex; gap: 8px; align-items: center; }
.meta-line { color: #aaa; font-size: 0.875rem; margin: 8px 0; }
.hint { color: #777; font-size: 0.8125rem; margin: 0 0 4px; }
.warn-text { color: #e0a63c; }
.stat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 8px; margin: 8px 0; }
.stat { background: #262626; border: 1px solid #3a3a3a; border-radius: 8px; padding: 8px 4px; text-align: center; }
.stat b { display: block; font-size: 1.25rem; }
.stat span { color: #888; font-size: 0.75rem; }
.stat.warn b { color: #ff8a8a; }
.slider-row { display: flex; align-items: center; gap: 12px; padding: 6px 12px; }
.slider-row label { min-width: 150px; font-size: 0.875rem; }
.slider-row input[type="range"] { flex: 1; }
.miss-list, .plan-list { list-style: none; margin: 4px 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.tmdb-grid label { display: block; font-size: 0.875rem; color: #ccc; margin: 8px 0 2px; }
.src-badge { margin-left: 8px; font-size: 0.75rem; color: #888; border: 1px solid #444; border-radius: 999px; padding: 1px 10px; }
.miss-row, .plan-row { display: flex; gap: 8px; align-items: center; background: #262626; border: 1px solid #3a3a3a; border-radius: 8px; padding: 6px 10px; font-size: 0.8125rem; }
.miss-title { white-space: nowrap; }
.miss-path, .plan-from { color: #888; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.plan-arrow { color: #6ab0ff; }
.plan-to { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.plan-status { font-size: 0.75rem; }
.plan-status.ok { color: #7ed321; }
.plan-status.fail { color: #ff8a8a; }
.plan-status.warn { color: #e0a63c; }
.conflict-groups { display: flex; flex-direction: column; gap: 8px; margin: 4px 0; }
.conflict-card { background: #262626; border: 1px solid #6e2b2b; border-radius: 8px; padding: 8px 10px; }
.conflict-target { font-size: 0.8125rem; color: #ccc; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kind-badge { font-size: 0.75rem; border: 1px solid #444; border-radius: 999px; padding: 1px 10px; margin-left: 8px; color: #aaa; }
.kind-badge.bad { color: #ff8a8a; border-color: #6e2b2b; }
.conflict-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 6px 0; border-top: 1px dashed #3a3a3a; font-size: 0.8125rem; }
.conflict-file { flex: 1; min-width: 200px; }
.conflict-title { display: block; }
.conflict-actions { display: flex; gap: 6px; }
.conflict-note { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.fs-crumbs { flex-wrap: wrap; }
.linklike { background: none; border: none; color: #6ab0ff; cursor: pointer; padding: 0 2px; }
button.danger { border-color: #6e2b2b; color: #ff8a8a; }
.fs-file-row { flex-wrap: wrap; }
</style>
