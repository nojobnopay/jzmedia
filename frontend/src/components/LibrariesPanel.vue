<template>
  <section id="sec-libraries" class="card-block">
    <div class="section-heading"><h3>媒体库列表</h3><button class="primary" @click="showCreate = !showCreate">{{ showCreate ? '收起新建表单' : '添加媒体库' }}</button></div>
    <p v-if="!mediaItems.length" class="hint">添加存储位置，再选择其中的电影或剧集目录。</p>
    <div v-else class="table-scroll"><table class="lib-table">
      <thead>
        <tr><th>媒体库</th><th>来源</th><th>地址</th><th>视频库</th><th>状态</th><th></th></tr>
      </thead>
      <tbody>
        <template v-for="m in mediaItems" :key="m.id">
          <tr :class="{ off: !m.enabled }">
            <td>
              <button class="expander" :title="expanded[m.id] ? '收起视频库' : '展开视频库'" @click="expanded[m.id] = !expanded[m.id]">
                {{ expanded[m.id] ? '▾' : '▸' }}
              </button>
              <b>{{ m.name }}</b>
              <span v-if="m.read_only" class="badge">只读</span>
              <span class="fhint media-count">{{ m.movie_count }} 片 / {{ m.episode_count }} 集</span>
            </td>
            <td>
              {{ m.source }}
              <span v-if="driverText(m)" class="badge drv" :class="{ direct: m.driver !== 'mount' }">{{ driverText(m) }}</span>
            </td>
            <td class="path" :title="pathTitle(m)">{{ pathText(m) }}</td>
            <td class="vid-counts">{{ videoCounts(m) }}</td>
            <td>
              <span class="status-pill" :class="'st-' + statusKind(m)">{{ statusText(m) }}</span>
              <div v-if="m.last_error && m.enabled" class="fhint err" :title="m.last_error">{{ m.last_error }}</div>
            </td>
            <td class="ops">
              <div class="ops-wrap">
                <button @click="connect(m)" :disabled="!!busy">
                  {{ busy === 'conn' + m.id ? (m.source === 'local' ? '检查中…' : '连接中…') : (m.source === 'local' ? '检查路径' : statusKind(m) === 'ok' ? '检查连接' : '连接') }}
                </button>
                <button @click="scanAll(m)" :disabled="!!busy || scanning['m:' + m.id]">
                  {{ scanning['m:' + m.id] ? '扫描中…' : '扫描此媒体库' }}
                </button>
                <button v-if="scanning['m:' + m.id]" @click="cancelScan('m:' + m.id)">取消</button>
                <details class="more">
                  <summary title="更多操作">更多</summary>
                  <div class="more-menu">
                    <button @click="addVideoOpen(m); closeMenu($event)">添加视频库</button>
                    <button v-if="m.source !== 'local'" @click="editConn(m); closeMenu($event)">编辑连接</button>
                    <button v-if="m.source === 'local' && m.movie_count + m.episode_count === 0" @click="editPath(m); closeMenu($event)">修改根目录</button>
                    <button v-if="m.source !== 'local'" @click="mount(m, true); closeMenu($event)">挂载</button>
                    <button v-if="m.source !== 'local'" @click="mount(m, false); closeMenu($event)">卸载</button>
                    <button @click="toggleReadOnly(m); closeMenu($event)">{{ m.read_only ? '取消只读' : '设只读' }}</button>
                    <button @click="toggleEnabled(m); closeMenu($event)">{{ m.enabled ? '停用' : '启用' }}</button>
                    <button class="danger" @click="armDelete(m); closeMenu($event)">移除媒体库…</button>
                  </div>
                </details>
              </div>
            </td>
          </tr>
          <tr v-if="rowMsg['m:' + m.id]" class="row-msg">
            <td colspan="6">
              <span :class="msgClass('m:' + m.id)">{{ rowMsg['m:' + m.id].text }}</span>
              <button v-if="rowMsg['m:' + m.id].cmd" @click="copyCmd('m:' + m.id)">复制宿主挂载命令</button>
              <button v-if="rowMsg['m:' + m.id].cmd" @click="showCmd['m:' + m.id] = !showCmd['m:' + m.id]">{{ showCmd['m:' + m.id] ? '收起命令' : '查看命令' }}</button>
              <code v-if="rowMsg['m:' + m.id].cmd && showCmd['m:' + m.id]" class="cmd-text">{{ rowMsg['m:' + m.id].cmd }}</code>
              <ul v-if="rowMsg['m:' + m.id].videos && rowMsg['m:' + m.id].videos.length" class="vid-check">
                <li v-for="v in rowMsg['m:' + m.id].videos" :key="v.id" :class="{ bad: !v.ok }">
                  {{ v.name }}（{{ v.subpath || '根' }}）{{ v.ok ? '✓' : '✗ ' + (v.error || '不可达') }}
                </li>
              </ul>
            </td>
          </tr>
          <tr v-if="connEdit && connEdit.id === m.id">
            <td colspan="6" class="path-edit">
              <input v-model="connEdit.url" v-bind="NOFILL" name="jz-conn-url" placeholder="\\ServerName\ShareName\Folder" style="min-width:300px" />
              <input v-model="connEdit.username" v-bind="NOFILL" name="jz-conn-user" placeholder="SMB 登录用户名" />
              <input v-model="connEdit.password" v-bind="NOFILL_PW" name="jz-conn-pass" type="password" placeholder="SMB 密码（留空不改）" />
              <input v-model="connEdit.connect_host" v-bind="NOFILL" name="jz-conn-connect" placeholder="连接地址（可选，Tailscale IP）" />
              <button @click="saveConn(m)" :disabled="!!busy">{{ busy === 'conn' ? '保存中…' : '保存并连接' }}</button>
              <button @click="connEdit = null">取消</button>
              <span class="fhint" :class="{ 'warn-text': connPreview.error }">{{ connPreview.error || `→ ${connPreview.host}/${connPreview.share}/${connPreview.subpath}` }}</span>
            </td>
          </tr>
          <tr v-if="pathEdit && pathEdit.id === m.id">
            <td colspan="6" class="path-edit">
              <input v-model="pathEdit.value" v-bind="NOFILL" name="jz-path-edit" placeholder="/media（容器内路径）" style="min-width:320px" />
              <button @click="savePath(m)" :disabled="!!busy">{{ busy === 'path' ? '保存中…' : '保存并检查' }}</button>
              <button @click="pathEdit = null">取消</button>
              <span class="fhint">媒体库根；仅当库内 0 记录时可改。NAS Docker 里填 /media 这类容器路径</span>
            </td>
          </tr>

          <template v-if="expanded[m.id]">
            <tr class="vid-head">
              <td colspan="6">
                视频库（{{ m.video_libraries.length }}）
              </td>
            </tr>
            <tr v-for="v in m.video_libraries" :key="v.id" class="vid-row" :class="{ off: !m.enabled || !v.enabled }">
              <td>
                <b>{{ v.name }}</b>
                <span class="badge" :class="{ tv: v.kind === 'tv' }">{{ kindText(v.kind) }}</span>
                <span v-if="!v.enabled" class="badge">停用</span>
              </td>
              <td class="fhint">子目录：{{ v.subpath || '（媒体库根）' }}</td>
              <td class="fhint">{{ v.movie_count }} 片 / {{ v.episode_count }} 集</td>
              <td class="path" :title="v.path">{{ v.path }}</td>
              <td>
                <span v-if="rowMsg['v:' + v.id]" :class="msgClass('v:' + v.id)">{{ rowMsg['v:' + v.id].text }}</span>
              </td>
              <td class="ops">
                <div class="ops-wrap">
                  <button @click="scanVideo(v)" :disabled="!!busy || scanning['v:' + v.id]">
                    {{ scanning['v:' + v.id] ? '扫描中…' : '扫描' }}
                  </button>
                  <button v-if="scanning['v:' + v.id]" @click="cancelScan('v:' + v.id)">取消</button>
                  <button @click="editVideo(v, m)">编辑</button>
                  <button class="danger" @click="armDeleteVideo(v, m)">移除视频库…</button>
                </div>
              </td>
            </tr>
            <tr v-if="videoEdit && videoEdit.mediaId === m.id">
              <td colspan="6" class="path-edit">
                <input v-model="videoEdit.name" v-bind="NOFILL" name="jz-v-name" placeholder="视频库名（如 Movies）" />
                <input v-model="videoEdit.subpath" v-bind="NOFILL" name="jz-v-sub" placeholder="子目录（相对媒体库根，空=根）" />
                <select v-model="videoEdit.kind">
                  <option value="movie">电影</option>
                  <option value="tv">剧集</option>
                </select>
                <button @click="saveVideo(videoEdit)" :disabled="!!busy">{{ busy === 'video' ? '保存中…' : '保存' }}</button>
                <button @click="videoEdit = null">取消</button>
                <span class="fhint" v-if="videoEdit.movie_count + videoEdit.episode_count > 0">库内已有记录，类型不可改（名称/子目录可改）</span>
              </td>
            </tr>
            <tr v-if="newVideo[m.id]" class="path-edit">
              <td colspan="6">
                <input v-model="newVideo[m.id].name" v-bind="NOFILL" name="jz-nv-name" placeholder="名称（如 TV Shows，留空按子目录名）" />
                <input v-model="newVideo[m.id].subpath" v-bind="NOFILL" name="jz-nv-sub" placeholder="子目录（相对媒体库根，空=根）" />
                <select v-model="newVideo[m.id].kind">
                  <option value="movie">电影</option>
                  <option value="tv">剧集</option>
                </select>
                <button @click="createVideo(m)" :disabled="!!busy">{{ busy === 'video' ? '创建中…' : '创建视频库' }}</button>
                <button @click="newVideo[m.id] = null">取消</button>
                <button @click="detectSubdirs(m)" :disabled="subdirLoading[m.id]">
                  {{ subdirLoading[m.id] ? '读取中…' : '检测子目录' }}
                </button>
                <div v-if="subdirs[m.id] && subdirs[m.id].length" class="subdir-chips">
                  <button v-for="d in subdirs[m.id]" :key="d.rel" class="chip" @click="pickSubdir(m, d)">
                    {{ d.rel }}
                  </button>
                </div>
                <span v-else-if="subdirs[m.id]" class="fhint">没有可用子目录</span>
              </td>
            </tr>
          </template>
        </template>
      </tbody>
    </table></div>

    <p v-if="msg" class="feedback" role="status">{{ msg }}</p>
    <div v-if="created" class="created-bar">
      <span class="cb-title">媒体库「{{ created.name }}」已创建 · 下一步</span>
      <span>① <button @click="connect(created)">{{ created.source === 'local' ? '检查路径' : '连接' }}</button></span>
      <span>② <button @click="scanAll(created)">扫描全部视频库</button></span>
      <button class="cb-close" @click="created = null">关闭引导</button>
    </div>

    <div v-if="arm" class="danger-box">
      <p>将删除媒体库「{{ arm.name }}」及其 <b>{{ arm.video_libraries.length }}</b> 个视频库的
        <b>{{ arm.movie_count }}</b> 条影片记录 / <b>{{ arm.episode_count }}</b> 条剧集记录与合集/花絮/缓存索引；
        <b>磁盘文件与远端数据不会被删除</b>（Plex/文件浏览不受影响）。</p>
      <div class="bar">
        <button class="danger" @click="doDeleteMedia" :disabled="!!busy">{{ busy === 'delete' ? '删除中…' : '确认删除记录' }}</button>
        <button @click="arm = null">取消</button>
      </div>
    </div>

    <div v-if="armVideo" class="danger-box">
      <p>将删除视频库「{{ armVideo.name }}」（{{ armVideo.subpath || '媒体库根' }}）的
        <b>{{ armVideo.movie_count }}</b> 条影片 / <b>{{ armVideo.episode_count }}</b> 条剧集记录；
        <b>磁盘文件不会被删除</b>。媒体库连接保留。</p>
      <div class="bar">
        <button class="danger" @click="doDeleteVideo" :disabled="!!busy">{{ busy === 'delete' ? '删除中…' : '确认删除记录' }}</button>
        <button @click="armVideo = null">取消</button>
      </div>
    </div>

    <div v-if="showCreate || !mediaItems.length" class="create-library-form">
    <h4>添加媒体库</h4><p class="hint">一个媒体库对应一个存储位置，可包含多个电影库或剧集库。</p>
    <div class="lib-form">
      <label>名称 <input v-model="form.name" v-bind="NOFILL" name="jz-lib-name" placeholder="如 NAS" /></label>
      <label>来源
        <select v-model="form.source">
          <option value="local">本地路径</option>
          <option value="smb">SMB（直读 / 挂载）</option>
          <option value="nfs">NFS（应用内挂载）</option>
        </select>
      </label>
      <label v-if="form.source === 'smb'" class="ck adv-switch">
        <input type="checkbox" v-model="advSplitting" /> 高级：手动拆分
      </label>
      <label v-if="form.source === 'local'">根路径
        <input v-model="form.path" v-bind="NOFILL" name="jz-lib-path" placeholder="容器内路径，NAS 上如 /media" style="min-width:280px" /></label>
      <template v-if="form.source === 'smb'">
        <label v-if="!advSplitting">服务器 / 共享路径
          <input v-model="form.smb_url" v-bind="NOFILL" name="jz-smb-url" placeholder="\\ServerName\ShareName（媒体库根；子目录在下方视频库填）" style="min-width:320px" /></label>
        <div v-if="!advSplitting" class="parse-line" :class="{ 'warn-text': smbPreview.error }">
          <template v-if="smbPreview.error">{{ smbPreview.error }}</template>
          <template v-else>
            主机 <b>{{ smbPreview.host }}</b> · 共享 <b>{{ smbPreview.share }}</b>
            · 根目录 <b>{{ smbPreview.subpath || '（共享根）' }}</b>
            <br /><template v-if="smbDriver === 'mount'">→ 自动挂载到 <code>data/mounts/lib_N</code>（无需填写）</template>
            <template v-else>→ 直读模式（<code>SMB_DRIVER={{ smbDriver }}</code>，免挂载），点「测试连接」检测</template>
          </template>
        </div>
        <template v-if="advSplitting">
          <label>服务器地址 <input v-model="form.smb_host" v-bind="NOFILL" name="jz-smb-host" placeholder="ServerName 或 IP" /></label>
          <label>共享名 <input v-model="form.smb_share" v-bind="NOFILL" name="jz-smb-share" placeholder="ShareName" /></label>
          <label>共享内目录 <input v-model="form.smb_subpath" v-bind="NOFILL" name="jz-smb-subpath" placeholder="媒体库根，如 Folder（可空）" /></label>
        </template>
        <label>用户名 <input v-model="form.smb_username" v-bind="NOFILL" name="jz-smb-user" placeholder="SMB 登录用户名（可空）" /></label>
        <label>密码 <input v-model="form.smb_password" v-bind="NOFILL_PW" name="jz-smb-pass" type="password" placeholder="SMB 密码（可空）" /></label>
        <label>连接地址 <input v-model="form.smb_connect_host" v-bind="NOFILL" name="jz-smb-connect" placeholder="可选：Tailscale IP / 内网地址（留空=用服务器地址）" style="min-width:240px" /></label>
        <button @click="testConn" :disabled="test.busy || busy">
          {{ test.busy ? '测试中…' : '测试连接' }}
        </button>
        <div v-if="test.text" class="parse-line test-result" :class="{ 'warn-text': !test.ok }">
          {{ test.ok ? '✓' : '✗' }} {{ test.text }}
          <template v-if="!test.ok && test.suggestions.length">
            <br /><span v-for="(s, i) in test.suggestions" :key="i">· {{ s }}<br /></span>
          </template>
          <div v-if="test.stages.length" class="stage-line">
            <span v-for="st in test.stages" :key="st.stage" class="stage-chip"
                  :class="{ bad: !st.ok, skip: st.skipped }"
                  :title="st.message || ''">{{ st.stage }}<template v-if="st.ms"> {{ st.ms }}ms</template></span>
          </div>
        </div>
      </template>
      <template v-if="form.source === 'nfs'">
        <label>导出路径 <input v-model="form.nfs_export" v-bind="NOFILL" name="jz-nfs-export" placeholder="ServerName:/volume1/video" style="min-width:260px" /></label>
        <div class="parse-line">
          → 自动挂载到 <code>data/mounts/lib_N</code>（无需填写），NFS 服务器需已导出该路径
        </div>
      </template>
    </div>

    <div class="lib-form vid-form">
      <span class="form-sec-title">视频库 <span class="fhint">（至少一个；每个视频库指定媒体库下的子目录与类型：电影/剧集）</span></span>
      <div v-for="(v, i) in form.videos" :key="i" class="vid-line">
        <input v-model="v.name" v-bind="NOFILL" placeholder="名称（如 Movies）" />
        <input v-model="v.subpath" v-bind="NOFILL" placeholder="子目录（如 Movies；空=媒体库根）" @change="fillName(i)" />
        <select v-model="v.kind">
          <option value="movie">电影</option>
          <option value="tv">剧集</option>
        </select>
        <button v-if="form.videos.length > 1" @click="form.videos.splice(i, 1)">移除</button>
      </div>
      <button @click="form.videos.push({ name: '', subpath: '', kind: 'movie' })">添加视频库</button>
    </div>

    <div class="lib-form lib-form-policy">
      <span class="form-sec-title">命名与资料文件 <span class="fhint">（新视频库的初始值；可逐个视频库再改）</span></span>
      <label title="归档整理时如何重命名文件/目录">命名规则
        <select v-model="form.naming_profile">
          <option value="kodi">Kodi / 通用命名</option>
          <option value="plex">Plex 命名</option>
          <option value="off">保持原名</option>
        </select>
      </label>
      <label title="是否在影片目录写 NFO / 海报图片">保存资料
        <select v-model="form.artwork_mode">
          <option value="nfo">仅 NFO</option>
          <option value="nfo_art">NFO + 本地海报（Plex 推荐）</option>
          <option value="none">只写数据库</option>
        </select>
      </label>
      <label class="ck"><input type="checkbox" v-model="form.read_only" /> 只读媒体库</label>
      <button @click="create" :disabled="!!busy || !form.name.trim() || (form.source === 'local' && !form.path.trim())">
        {{ busy === 'create' ? '创建中…' : '创建媒体库' }}
      </button>

      <details class="settings-details"><summary>查看命名示例</summary>
        <template v-if="form.naming_profile === 'plex'">
          命名档 <b>plex</b>（Plex 规范，目录+文件名都带剪辑）：
          普通片 <code>标题 (年份)/标题 (年份).ext</code>；
          剪辑片 <code>标题 (年份) {edition-版本}/标题 (年份) {edition-版本} - 规格.ext</code>——
          Plex 只有看到 <code>{edition-…}</code> 才会把导演剪辑版等拆成独立条目。归档预览会做 Plex 兼容性检查（可能被 Plex 认错的名字先告警）。
        </template>
        <template v-else-if="form.naming_profile === 'off'">
          命名档 <b>off</b>：不改文件名和目录，只入库/浏览/播放（对该库不执行归档改名）。
        </template>
        <template v-else>
          命名档 <b>kodi</b>（通用模板，兼容 Kodi/Jellyfin/Emby 与现有目录）：
          普通片 <code>标题 (年份)/标题 (年份).ext</code>；带剪辑时目录不加 <code>{edition-…}</code>，版本只写在文件名：
          <code>标题 (年份)/标题 (年份)[-版本][-规格][-分卷].ext</code>——Plex 下仍能匹配影片，但多个剪辑会合并为同一部片的多版本。
        </template>
        <br />
        <template v-if="form.artwork_mode === 'nfo_art'">
          落盘 <b>NFO + 本地海报</b>：除 <code>movie.nfo</code> 外，把 <code>poster.jpg</code>/<code>fanart.jpg</code>
          写进影片目录；Plex 抓不到海报时会优先用这张本地图（配 plex 命名档最合适）。
        </template>
        <template v-else-if="form.artwork_mode === 'none'">
          落盘 <b>只写数据库</b>：不向媒体目录写任何文件（NFO/图片都不写），适合只读库或不想被写目录的场景。
        </template>
        <template v-else>
          落盘 <b>仅 NFO</b>：在影片目录写 <code>movie.nfo</code>（Kodi/Jellyfin/Emby 可读；Plex 需启用 NFO Agent 才会读）。
        </template>
      </details>
    </div>

    <details class="settings-details">
      <summary>路径填写与连接帮助</summary>
      <ul class="hint-list">
        <li v-if="form.source === 'local'"><b>NAS Docker（推荐）</b>：媒体库根填容器内路径——compose 把宿主
          <code>/volume1/video</code> 挂到容器 <code>/media</code> 后填 <code>/media</code>，视频库子目录填
          <code>Movies</code> / <code>TV Shows</code> 等。</li>
        <li v-else><b>远程访问</b>：SMB 粘贴媒体库根 <code>\\ServerName\共享名</code>（子目录留在视频库里填），
          NFS 填 <code>ServerName:/导出路径</code>；挂载点自动分配。SMB 默认走用户态直读
          （无需 <code>SYS_ADMIN</code>，点「测试连接」可看 11 阶段诊断）；也可设 <code>SMB_DRIVER=mount</code>
          改用容器内挂载（需 compose <code>cap_add: [SYS_ADMIN]</code>，能力不足时点「连接」会给出宿主挂载命令）。</li>
        <li><b>视频库子目录</b>：相对媒体库根。创建媒体库时本地子目录会自动建立；远程请在 NAS 上先建好，
          或用「添加视频库 → 检测子目录」列出根下目录。</li>
        <li><b>安全与限制</b>：远程凭据 Fernet 加密存储、绝不回显；只读库禁止归档/上传/删除/NFO 写入；
          媒体库内视频库子目录不允许互相重叠（含根）。</li>
      </ul>
    </details>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api.js'
import { loadLibs } from '../libraries.js'
import { parseSmbInput, smbUrlOf } from '../smb.js'

const emit = defineEmits(['changed'])
const mediaItems = ref([])
const busy = ref('')
const msg = ref('')
const smbDriver = ref('auto')       // 服务端 SMB_DRIVER：auto|direct|mount
const showCreate = ref(false)
const expanded = reactive({})       // media_id -> 展开视频库
const arm = ref(null)
const armVideo = ref(null)
const pathEdit = ref(null)
const connEdit = ref(null)
const videoEdit = ref(null)
const newVideo = reactive({})       // media_id -> 新视频库表单或 null
const subdirs = reactive({})        // media_id -> [{name, rel}]
const subdirLoading = reactive({})
const advSplitting = ref(false)
const created = ref(null)
const rowMsg = reactive({})     // key -> {text, kind: info|ok|error|warn, cmd?, videos?}
const showCmd = reactive({})
const scanJobs = reactive({})   // key（m:id / v:id）-> {jobId, label}
const test = reactive({ busy: false, ok: null, text: '', suggestions: [], stages: [] })
const scanning = computed(() => {
  const out = {}
  for (const k of Object.keys(scanJobs)) out[k] = true
  return out
})
const smbPreview = computed(() => parseSmbInput(form.smb_url))
const connPreview = computed(() => parseSmbInput(connEdit.value ? connEdit.value.url : ''))

const NOFILL = { autocomplete: 'off', 'data-lpignore': 'true', 'data-1p-ignore': '', 'data-bwignore': 'true' }
const NOFILL_PW = { ...NOFILL, autocomplete: 'new-password' }
const form = reactive({
  name: '', source: 'local', path: '', naming_profile: 'kodi',
  artwork_mode: 'nfo', read_only: false,
  smb_url: '', smb_host: '', smb_share: '', smb_subpath: '',
  smb_username: '', smb_password: '', smb_connect_host: '',
  nfs_export: '',
  videos: [{ name: '', subpath: '', kind: 'movie' }],
})

watch(() => form.smb_url, (v) => {
  const p = parseSmbInput(v)
  if (p.username && !form.smb_username) form.smb_username = p.username
})

function setMsg (key, text, kind = 'info', cmd = '', videos = null) {
  rowMsg[key] = { text, kind, cmd, videos }
  if (!cmd) delete showCmd[key]
}
function msgClass (key) {
  const k = rowMsg[key]?.kind
  return { 'msg-err': k === 'error', 'msg-ok': k === 'ok', 'msg-warn': k === 'warn' }
}

const STATUS_TEXT = {
  auth_failed: '认证失败',
  hostname_resolve_failed: '主机名无法解析',
  tcp_timeout: '连接超时',
  connection_refused: '连接被拒',
  tcp_unreachable: '网络不可达',
  smb_negotiate_failed: 'SMB 协商失败',
  share_not_found: '共享不存在',
  share_access_denied: '共享无权限',
  path_not_found: '目录不存在',
  read_failed: '读取失败',
  write_failed: '写入失败',
  stream_failed: '随机读失败',
  ffprobe_failed: '探测失败',
  ffprobe_missing: '缺少 ffprobe',
  unreachable: '连接失败',
  error: '不可读',
  library_offline: '已离线',
}

function statusKind (m) {
  if (!m.enabled) return 'off'
  if (m.last_status === 'ok') return 'ok'
  if (m.last_status === 'not_mounted' || !m.last_status) return 'idle'
  return 'err'
}

function statusText (m) {
  if (!m.enabled) return '已停用'
  if (m.last_status === 'ok') return '可读'
  if (m.last_status === 'not_mounted') return '未连接'
  return STATUS_TEXT[m.last_status] || '未检查'
}

function kindText (kind) {
  return kind === 'tv' ? '剧集' : '电影'
}

function videoCounts (m) {
  const vids = m.video_libraries || []
  const movies = vids.filter((v) => v.kind !== 'tv').length
  const tvs = vids.filter((v) => v.kind === 'tv').length
  return `${vids.length} 个（电影 ${movies} / 剧集 ${tvs}）`
}

async function load () {
  try {
    const d = await api('/api/media-libraries')
    mediaItems.value = d.items || []
    smbDriver.value = d.smb_driver || 'auto'
    // 默认展开最近使用的媒体库
    const cur = mediaItems.value[0]
    if (cur && expanded[cur.id] === undefined) expanded[cur.id] = true
    try { await loadLibs(api, { force: true }) } catch (e) { /* 忽略 */ }
    emit('changed')
  } catch (e) { msg.value = '媒体库加载失败：' + e.message }
}

function pathText (m) {
  if (m.source === 'smb') return smbUrlOf(m) || m.path
  if (m.source === 'nfs') return m.nfs_export || m.path
  return m.path
}

function pathTitle (m) {
  if (m.source === 'local') return m.path
  const lines = [pathText(m)]
  if (m.source === 'smb') {
    lines.push(`访问方式：${m.driver === 'mount' ? '容器/宿主挂载' : '用户态直读（无需挂载）'}`)
    if (m.smb_connect_host && m.smb_connect_host !== m.smb_host) {
      lines.push(`实际连接地址：${m.smb_connect_host}`)
    }
  } else {
    lines.push('访问方式：宿主挂载')
  }
  lines.push(`宿主挂载回退路径：${m.path}`)
  return lines.join('\n')
}

function driverText (m) {
  if (m.source === 'smb') return m.driver === 'mount' ? '挂载' : '直读'
  if (m.source === 'nfs') return '挂载'
  return ''
}

function closeMenu (e) {
  const d = e.target.closest('details')
  if (d) d.removeAttribute('open')
}

async function copyCmd (key) {
  const cmd = rowMsg[key]?.cmd || ''
  try {
    await navigator.clipboard.writeText(cmd)
    setMsg(key, '宿主挂载命令已复制，到 NAS 终端执行后点「检查」', 'ok', cmd)
  } catch (e) {
    setMsg(key, '复制失败，请手动选择命令文本复制', 'warn', cmd)
  }
}

// 主操作：本地=检查；远程=挂载 + 检查（挂载模式）或直读诊断（direct/auto）
async function connect (m) {
  busy.value = 'conn' + m.id
  const direct = m.source === 'smb' && smbDriver.value !== 'mount'
  setMsg('m:' + m.id, m.source === 'local' ? '正在检查路径…'
    : direct ? '正在检测直读连接（11 阶段，最多约 1 分钟）…'
      : '正在连接（挂载 + 检查，最多约 20 秒）…')
  try {
    if (m.source !== 'local' && !direct) {
      const r = await api(`/api/media-libraries/${m.id}/mount`, { method: 'POST' })
      if (!r.ok) {
        const cmd = r.suggested_cmd || ''
        setMsg('m:' + m.id, `连接失败：${r.error || '未知原因'}`
          + (cmd ? '（容器无挂载权限，可用下方宿主命令挂载）' : ''), 'error', cmd)
        await load()
        return
      }
    }
    const d = await api(`/api/media-libraries/${m.id}/check`, { method: 'POST' })
    const bad = (d.video_libraries || []).filter((v) => !v.ok)
    if (d.readable) {
      const head = `连接正常：${d.writable ? '可读可写' : '可读（只读）'}`
        + (direct ? '，直读模式（免挂载）' : '') + '，可以扫描入库了'
      if (bad.length) {
        setMsg('m:' + m.id, `${head}，但 ${bad.length} 个视频库子目录不可达（见下）`, 'warn', '', bad)
      } else if (d.warning) {
        setMsg('m:' + m.id, `${head}。注意：${d.warning}`
          + '（归档/NFO 写入将跳过，可把该库勾选为「只读库」）', 'warn')
      } else {
        setMsg('m:' + m.id, head, 'ok', '', (d.video_libraries || []))
      }
    } else {
      const sug = (d.suggestions || []).slice(0, 2).join('；')
      const why = d.message || d.reason || d.error || '请检查路径/权限'
      setMsg('m:' + m.id, `检测失败${d.stage ? '（' + d.stage + '）' : ''}：${why}`
        + (sug ? '。建议：' + sug : ''), 'error',
      direct ? '' : (d.suggested_cmd || ''))
    }
    await load()
  } catch (e) {
    setMsg('m:' + m.id, '连接失败：' + e.message, 'error')
  } finally {
    busy.value = ''
  }
}

// SMB 连接预检（不落库）——直读/挂载同一套诊断
function _smbTestBody () {
  if (advSplitting.value) {
    return { host: form.smb_host, share: form.smb_share, subpath: form.smb_subpath,
             username: form.smb_username, password: form.smb_password,
             connect_host: form.smb_connect_host }
  }
  const p = smbPreview.value
  if (p.error) throw new Error(p.error)
  return { host: p.host, share: p.share, subpath: p.subpath,
           username: form.smb_username, password: form.smb_password,
           connect_host: form.smb_connect_host }
}

async function testConn () {
  test.busy = true; test.ok = null; test.text = ''; test.suggestions = []; test.stages = []
  try {
    const d = await api('/api/media-libraries/diag/smb', {
      method: 'POST', body: JSON.stringify(_smbTestBody()),
    })
    test.ok = !!d.ok
    test.stages = d.stages || []
    test.text = d.ok ? '远程库就绪，可以创建'
      : `${d.stage || ''}：${d.message || '检测失败'}`
    test.suggestions = d.suggestions || []
  } catch (e) {
    test.ok = false
    test.text = '测试失败：' + e.message
  } finally {
    test.busy = false
  }
}

let scanTimer = null
function stopScanTimer () {
  if (scanTimer) { clearInterval(scanTimer); scanTimer = null }
}
function ensureScanTimer () {
  if (!scanTimer) scanTimer = setInterval(pollScans, 1200)
}

async function _scanStart (key, body, resumedCheck) {
  try {
    const d = await api('/api/jobs/scan', { method: 'POST', body: JSON.stringify(body) })
    if (d.resumed && resumedCheck(d)) {
      setMsg(key, '已有其他扫描在跑，完成后请再试', 'warn')
      return
    }
    scanJobs[key] = { jobId: d.job_id }
    if (d.resumed) setMsg(key, '已有扫描在跑，正在跟踪进度…')
    ensureScanTimer()
  } catch (e) {
    setMsg(key, '扫描启动失败：' + e.message, 'error')
  }
}

async function scanAll (m) {
  setMsg('m:' + m.id, '正在启动扫描（全部视频库）…')
  await _scanStart('m:' + m.id, { media_library_id: m.id },
    (d) => d.media_library_id == null || Number(d.media_library_id) !== Number(m.id))
}

async function scanVideo (v) {
  setMsg('v:' + v.id, '正在启动扫描…')
  await _scanStart('v:' + v.id, { library_id: v.id },
    (d) => d.library_id == null || Number(d.library_id) !== Number(v.id))
}

async function cancelScan (key) {
  const job = scanJobs[key]
  if (!job) return
  try { await api('/api/jobs/scan/' + job.jobId + '/cancel', { method: 'POST' }) } catch (e) { /* 下次轮询收尾 */ }
}

async function pollScans () {
  const keys = Object.keys(scanJobs)
  if (!keys.length) { stopScanTimer(); return }
  for (const key of keys) {
    try {
      const st = await api('/api/jobs/scan/' + scanJobs[key].jobId)
      if (st.state === 'running') {
        setMsg(key, st.total ? `扫描中 ${st.done || 0}/${st.total}…` : '扫描中…')
        continue
      }
      const c = (st.summary || {}).counts || {}
      if (st.state === 'done') {
        const ok = (c.ok || 0) + (c.ok_needs_review || 0)
        setMsg(key, `扫描完成：新增/更新 ${ok}，未匹配 ${c.no_match || 0}`
          + (c.scan_failed ? `，刮削失败 ${c.scan_failed}（可在详情页重试）` : '')
          + (c.library_offline ? '；有库离线已跳过' : '')
          + '；未匹配片可到「入库流程 → 待匹配确认」处理', 'ok')
      } else if (st.state === 'cancelled') {
        setMsg(key, `已取消（${st.done || 0}/${st.total || 0}）`, 'warn')
      } else {
        setMsg(key, '扫描失败：' + (st.error || '未知错误'), 'error')
      }
      delete scanJobs[key]
    } catch (e) { /* 轮询失败下次继续 */ }
  }
  if (!Object.keys(scanJobs).length) { stopScanTimer(); await load() }
}

function editPath (m) {
  pathEdit.value = { id: m.id, value: m.path || '' }
}

async function savePath (m) {
  const v = (pathEdit.value?.value || '').trim()
  if (!v) return
  busy.value = 'path'
  try {
    await api(`/api/media-libraries/${m.id}`, {
      method: 'PATCH', body: JSON.stringify({ path: v }),
    })
    pathEdit.value = null
    await connect({ id: m.id, name: m.name, source: m.source })
  } catch (e) {
    setMsg('m:' + m.id, '改路径失败：' + e.message, 'error')
    busy.value = ''
  }
}

function editConn (m) {
  connEdit.value = { id: m.id, url: smbUrlOf(m), username: m.smb_username || '',
                     password: '', connect_host: m.smb_connect_host || '' }
}

async function saveConn (m) {
  const p = connPreview.value
  if (p.error) { setMsg('m:' + m.id, p.error, 'error'); return }
  busy.value = 'conn'
  try {
    const smb = { username: connEdit.value.username,
                  connect_host: connEdit.value.connect_host || '',
                  domain: m.smb_domain || '', options: m.smb_options || '' }
    if (connEdit.value.password) smb.password = connEdit.value.password
    await api(`/api/media-libraries/${m.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ smb_url: connEdit.value.url, smb }),
    })
    connEdit.value = null
    setMsg('m:' + m.id, '连接信息已保存，正在重新连接…', 'ok')
    await connect({ id: m.id, name: m.name, source: m.source })
  } catch (e) {
    setMsg('m:' + m.id, '保存连接失败：' + e.message, 'error')
    busy.value = ''
  }
}

async function toggleReadOnly (m) {
  busy.value = 'patch' + m.id
  try {
    await api(`/api/media-libraries/${m.id}`, {
      method: 'PATCH', body: JSON.stringify({ read_only: !m.read_only }),
    })
    await load()
  } catch (e) {
    setMsg('m:' + m.id, '保存失败：' + e.message, 'error')
  } finally {
    busy.value = ''
  }
}

async function toggleEnabled (m) {
  busy.value = 'patch' + m.id
  try {
    await api(`/api/media-libraries/${m.id}`, {
      method: 'PATCH', body: JSON.stringify({ enabled: !m.enabled }),
    })
    await load()
  } catch (e) {
    setMsg('m:' + m.id, '保存失败：' + e.message, 'error')
  } finally {
    busy.value = ''
  }
}

function armDelete (m) {
  arm.value = m
  armVideo.value = null
}

async function doDeleteMedia () {
  if (!arm.value) return
  busy.value = 'delete'
  try {
    const d = await api(`/api/media-libraries/${arm.value.id}`, { method: 'DELETE' })
    msg.value = `已移除媒体库「${d.library}」：影片 ${d.movies} / 剧集 ${d.tv_episodes} / 合集 ${d.collections}（文件保留）`
    delete rowMsg['m:' + arm.value.id]
    arm.value = null
    await load()
  } catch (e) {
    msg.value = '删除失败：' + e.message
  } finally {
    busy.value = ''
  }
}

function armDeleteVideo (v, m) {
  arm.value = null
  armVideo.value = { id: v.id, name: v.name, subpath: v.subpath,
    movie_count: v.movie_count, episode_count: v.episode_count, mediaId: m.id }
}

async function doDeleteVideo () {
  if (!armVideo.value) return
  busy.value = 'delete'
  try {
    const d = await api(`/api/libraries/${armVideo.value.id}`, { method: 'DELETE' })
    msg.value = `已移除视频库「${d.library}」：影片 ${d.movies}（文件保留）`
    delete rowMsg['v:' + armVideo.value.id]
    armVideo.value = null
    await load()
  } catch (e) {
    msg.value = '删除失败：' + e.message
  } finally {
    busy.value = ''
  }
}

async function mount (m, up) {
  busy.value = (up ? 'mount' : 'unmount') + m.id
  try {
    const d = await api(`/api/media-libraries/${m.id}/${up ? 'mount' : 'unmount'}`,
                        { method: 'POST' })
    if (d.ok) {
      setMsg('m:' + m.id, up ? '已挂载，可点「检查」确认可读' : '已卸载', 'ok')
    } else {
      setMsg('m:' + m.id, `挂载失败：${d.error || '未知原因'}`, 'error', d.suggested_cmd || '')
    }
    await load()
  } catch (e) {
    setMsg('m:' + m.id, '挂载操作失败：' + e.message, 'error')
  } finally {
    busy.value = ''
  }
}

function addVideoOpen (m) {
  expanded[m.id] = true
  subdirs[m.id] = null
  newVideo[m.id] = { name: '', subpath: '', kind: 'movie' }
}

async function detectSubdirs (m) {
  subdirLoading[m.id] = true
  try {
    const d = await api(`/api/media-libraries/${m.id}/subdirs`)
    subdirs[m.id] = d.dirs || []
  } catch (e) {
    subdirs[m.id] = []
    setMsg('m:' + m.id, '检测子目录失败：' + e.message, 'error')
  } finally {
    subdirLoading[m.id] = false
  }
}

function pickSubdir (m, d) {
  if (!newVideo[m.id]) return
  newVideo[m.id].subpath = d.rel
  if (!newVideo[m.id].name) newVideo[m.id].name = d.name
}

function fillName (i) {
  const v = form.videos[i]
  if (v && !v.name && v.subpath) {
    v.name = v.subpath.split('/').filter(Boolean).pop() || ''
  }
}

async function createVideo (m) {
  const v = newVideo[m.id]
  if (!v) return
  busy.value = 'video'
  try {
    const d = await api('/api/libraries', {
      method: 'POST',
      body: JSON.stringify({ media_library_id: m.id, name: v.name, subpath: v.subpath,
                             kind: v.kind }),
    })
    msg.value = `已创建视频库「${d.name}」`
    newVideo[m.id] = null
    subdirs[m.id] = null
    await load()
  } catch (e) {
    setMsg('m:' + m.id, '创建视频库失败：' + e.message, 'error')
  } finally {
    busy.value = ''
  }
}

function editVideo (v, m) {
  videoEdit.value = { id: v.id, mediaId: m.id, name: v.name, subpath: v.subpath,
                      kind: v.kind, movie_count: v.movie_count,
                      episode_count: v.episode_count }
}

async function saveVideo (v) {
  busy.value = 'video'
  try {
    await api(`/api/libraries/${v.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name: v.name, subpath: v.subpath, kind: v.kind }),
    })
    videoEdit.value = null
    await load()
  } catch (e) {
    setMsg('v:' + v.id, '保存失败：' + e.message, 'error')
  } finally {
    busy.value = ''
  }
}

async function create () {
  busy.value = 'create'
  msg.value = ''
  try {
    const videos = form.videos
      .map((v) => ({ name: v.name.trim(), subpath: v.subpath.trim(), kind: v.kind }))
      .filter((v) => v.name || v.subpath)
      .map((v) => ({ ...v, name: v.name || (v.subpath.split('/').filter(Boolean).pop() || '') }))
    if (!videos.length) { msg.value = '请至少填写一个视频库（名称或子目录）'; return }
    const body = {
      name: form.name, source: form.source, path: form.path,
      read_only: form.read_only, video_libraries: videos,
    }
    if (form.source === 'smb') {
      body.smb = advSplitting.value
        ? { host: form.smb_host, share: form.smb_share,
            subpath: form.smb_subpath, username: form.smb_username,
            password: form.smb_password, connect_host: form.smb_connect_host }
        : { url: form.smb_url, username: form.smb_username,
            password: form.smb_password, connect_host: form.smb_connect_host }
    } else if (form.source === 'nfs') {
      body.nfs = { export: form.nfs_export }
    }
    const d = await api('/api/media-libraries', {
      method: 'POST', body: JSON.stringify(body),
    })
    // 视频库默认整理策略（创建后逐个 PATCH：媒体库创建口只带连接与清单）
    for (const v of d.video_libraries || []) {
      if (form.naming_profile !== 'kodi' || form.artwork_mode !== 'nfo') {
        try {
          await api(`/api/libraries/${v.id}`, {
            method: 'PATCH',
            body: JSON.stringify({ naming_profile: form.naming_profile,
                                   artwork_mode: form.artwork_mode }),
          })
        } catch (e) { /* 单个失败不挡创建 */ }
      }
    }
    msg.value = `已创建媒体库「${d.name}」`
    form.name = ''
    form.path = ''
    form.smb_url = ''
    form.smb_host = ''
    form.smb_share = ''
    form.smb_subpath = ''
    form.smb_password = ''
    form.smb_connect_host = ''
    form.nfs_export = ''
    form.videos = [{ name: '', subpath: '', kind: 'movie' }]
    showCreate.value = false
    created.value = d
    expanded[d.id] = true
    await load()
    if (d.source !== 'local') await connect(created.value)
  } catch (e) {
    msg.value = '创建失败：' + e.message
  } finally {
    busy.value = ''
  }
}

onMounted(load)
onBeforeUnmount(stopScanTimer)
defineExpose({ ensure: load })
</script>

<style scoped>
.lib-table { width: 100%; border-collapse: collapse; font-size: 0.875rem; }
.lib-table th, .lib-table td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #333; vertical-align: middle; }
.lib-table tr.off { opacity: .5; }
.lib-table .path { max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lib-table .ops { min-width: 260px; }
.lib-table .ops-wrap { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.badge { margin-left: 6px; font-size: 0.75rem; border: 1px solid #6b5518; color: #e0b34a; border-radius: 999px; padding: 1px 8px; }
.badge.drv { border-color: #2b4a6e; color: #6ab0ff; }
.badge.tv { border-color: #4a2b6e; color: #c08aff; }
.danger { border-color: #6e2b2b; color: #ff8a8a; }
.danger-box { border: 1px solid #6e2b2b; border-radius: 8px; padding: 10px; margin: 10px 0; }
.status-pill { display: inline-block; font-size: 0.75rem; border-radius: 999px; padding: 1px 8px; border: 1px solid #444; color: #aaa; }
.status-pill.st-ok { border-color: #2f6b3a; color: #7fd18b; }
.status-pill.st-err { border-color: #6e2b2b; color: #ff8a8a; }
.status-pill.st-idle { border-color: #6b5518; color: #e0b34a; }
.status-pill.st-off { border-color: #444; color: #777; }
.fhint.err { color: #d97b7b; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row-msg td { background: #202020; color: #bbb; font-size: 0.8125rem; }
.row-msg .msg-ok { color: #7fd18b; }
.row-msg .msg-err { color: #ff8a8a; }
.row-msg .msg-warn { color: #e0b34a; }
.row-msg button { margin-left: 8px; }
.row-msg .cmd-text { display: block; margin-top: 6px; padding: 6px 8px; background: #151515; border: 1px solid #333; border-radius: 6px; color: #9ecfff; white-space: pre-wrap; word-break: break-all; }
.vid-check { margin: 6px 0 0; padding-left: 20px; color: #7fd18b; }
.vid-check li.bad { color: #ff8a8a; }
.vid-head td { background: #1b1b1b; color: #999; font-size: 0.8125rem; border-bottom: 1px solid #333; }
.vid-row td { background: #191919; }
.vid-row .fhint { margin-right: 10px; }
.vid-counts { color: #bbb; white-space: nowrap; }
.expander { background: none; border: none; color: #bbb; cursor: pointer; padding: 0 6px 0 0; font-size: 0.875rem; }
.more { position: relative; display: inline-block; }
.more > summary { list-style: none; cursor: pointer; padding: 2px 10px; border: 1px solid #444; border-radius: 6px; color: #bbb; font-size: 0.8125rem; }
.more > summary::-webkit-details-marker { display: none; }
.more[open] > summary { border-color: #666; color: #eee; }
.more-menu { position: absolute; right: 0; top: calc(100% + 4px); z-index: 30; display: flex; flex-direction: column; gap: 4px; min-width: 140px; padding: 6px; background: #1e1e1e; border: 1px solid #444; border-radius: 8px; box-shadow: 0 6px 18px rgba(0, 0, 0, .45); }
.more-menu button { text-align: left; }
.created-bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 10px 0; padding: 8px 10px; border: 1px solid #2f4a6b; background: #17202b; border-radius: 8px; font-size: 0.8125rem; }
.created-bar .cb-title { color: #9ecfff; font-weight: 600; }
.created-bar .cb-close { margin-left: auto; }
.lib-form { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
.lib-form label { display: inline-flex; gap: 6px; align-items: center; }
.lib-form label.ck { gap: 4px; }
.lib-form-policy { border-top: 1px dashed #333; padding-top: 10px; margin-top: 12px; }
.vid-form { border-top: 1px dashed #333; padding-top: 10px; }
.vid-line { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.form-sec-title { flex-basis: 100%; color: #999; font-size: 0.8125rem; font-weight: 600; }
.form-sec-title .fhint { font-weight: normal; }
.parse-line { flex-basis: 100%; color: #888; font-size: 0.8125rem; line-height: 1.8; padding-left: 6px; }
.test-result { border-left: 2px solid #3a5a3a; }
.test-result.warn-text { border-left-color: #6e2b2b; }
.stage-line { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 4px; }
.stage-chip { font-size: 0.6875rem; border: 1px solid #3a5a3a; color: #7fd18b; border-radius: 999px; padding: 0 6px; }
.stage-chip.bad { border-color: #6e2b2b; color: #ff8a8a; }
.stage-chip.skip { border-color: #444; color: #777; }
.parse-line b { color: #ccc; font-weight: 600; }
.parse-line code { color: #9ecfff; }
.hint-block { flex-basis: 100%; margin-top: 10px; }
.hint-title { margin: 0 0 2px; color: #999; font-size: 0.8125rem; font-weight: 600; }
.hint-list { margin: 0; padding-left: 20px; color: #888; font-size: 0.8125rem; line-height: 1.9; }
.hint-list li { margin: 2px 0; }
.hint-list code { color: #9ecfff; }
.fhint { color: #777; font-size: 0.75rem; font-weight: normal; }
.hint { color: #888; font-size: 0.8125rem; line-height: 1.6; }
.path-edit { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.subdir-chips { flex-basis: 100%; display: flex; gap: 6px; flex-wrap: wrap; margin-top: 4px; }
.subdir-chips .chip { font-size: 0.75rem; padding: 1px 8px; }
</style>
