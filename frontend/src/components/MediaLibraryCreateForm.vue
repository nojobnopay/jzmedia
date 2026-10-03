<template>
    <div class="create-library-form"><fieldset :disabled="!!busy || test.busy || disabled">
    <h4>添加文件存放位置</h4><p class="hint">一个媒体库对应一个存储位置，可包含多个电影库或剧集库。</p>
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
            <br /><template v-if="smbDriver === 'mount'">连接后自动挂载，无需填写挂载路径。</template>
            <template v-else>直接读取远程文件，可先测试连接。</template>
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
        <JzButton @click="testConn" :disabled="test.busy || busy" type="button" icon="link">
          {{ test.busy ? '测试中…' : '测试连接' }}
        </JzButton>
        <div v-if="test.text" class="parse-line test-result" :class="{ 'warn-text': !test.ok }">
          <AppIcon :name="test.ok ? 'check-circle' : 'error'" :size="16" /> {{ test.text }}
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
          连接后自动挂载。请确认 NFS 服务器已导出该路径。
        </div>
      </template>
    </div>

    <div class="lib-form vid-form">
      <span class="form-sec-title">视频库 <span class="fhint">（至少一个；每个视频库指定媒体库下的子目录与类型：电影/剧集）</span></span>
      <div v-for="(v, i) in form.videos" :key="i" class="vid-line">
        <input v-model="v.name" :aria-label="'视频库名称 ' + (i + 1)" v-bind="NOFILL" placeholder="名称（如 Movies）" />
        <input v-model="v.subpath" :aria-label="'视频子目录 ' + (i + 1)" v-bind="NOFILL" placeholder="子目录（如 Movies；空=媒体库根）" @change="fillName(i)" />
        <select v-model="v.kind" :aria-label="'视频库类型 ' + (i + 1)">
          <option value="movie">电影</option>
          <option value="tv">剧集</option>
        </select>
        <JzButton v-if="form.videos.length > 1" @click="form.videos.splice(i, 1)" type="button" icon="delete">移除</JzButton>
      </div>
      <JzButton v-if="!compact" @click="form.videos.push({ name: '', subpath: '', kind: 'movie' })" type="button" icon="plus">添加视频库</JzButton>
    </div>

    <details class="policy-details"><summary>命名、资料落盘与只读选项</summary><div class="lib-form lib-form-policy">
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

    </details>
    <p class="hint">扫描可能按设置向媒体目录写入 NFO；目录整理仍需另行预览确认。</p>
      <JzButton variant="primary" @click="create" :disabled="!!busy || !form.name.trim() || (form.source === 'local' && !form.path.trim())">
        {{ busy === 'create' ? '创建中…' : '创建媒体库' }}
      </JzButton>
    <p role="status">{{ msg }}</p>
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
    </fieldset></div>
</template>
<script setup>
import AppIcon from './AppIcon.vue'

import { computed, reactive, ref, watch } from 'vue'
import { useSettingsDraft } from '../settingsDrafts.js'
import { api } from '../api.js'
import { parseSmbInput } from '../smb.js'
import JzButton from './JzButton.vue'
const props = defineProps({
  kind: { type: String, default: 'movie' }, compact: Boolean, disabled: Boolean,
  smbDriver: { type: String, default: 'auto' },
})
const emit = defineEmits(['created', 'busy'])
const busy = ref('')
const msg = ref('')
const advSplitting = ref(false)
const test = reactive({ busy: false, ok: null, text: '', suggestions: [], stages: [] })
const smbPreview = computed(() => parseSmbInput(form.smb_url))
const NOFILL = { autocomplete: 'off', 'data-lpignore': 'true', 'data-1p-ignore': '', 'data-bwignore': 'true' }
const NOFILL_PW = { ...NOFILL, autocomplete: 'new-password' }
const form = reactive({
  name: '', source: 'local', path: '', naming_profile: 'kodi',
  artwork_mode: 'nfo', read_only: false,
  smb_url: '', smb_host: '', smb_share: '', smb_subpath: '',
  smb_username: '', smb_password: '', smb_connect_host: '',
  nfs_export: '',
  videos: [{ name: '', subpath: '', kind: props.kind }],
})

const emptyForm = JSON.stringify(form)
const dirty = computed(() => JSON.stringify(form) !== emptyForm)
function discard() {
  Object.assign(form, JSON.parse(emptyForm))
  advSplitting.value = false
  msg.value = ''
  test.text = ''; test.ok = null; test.suggestions = []; test.stages = []
}
useSettingsDraft({ section: 'sec-libraries', label: '新建媒体库', dirty: () => dirty.value,
  busy: () => !!busy.value || test.busy, discard })

watch(() => form.smb_url, (v) => {
  const p = parseSmbInput(v)
  if (p.username && !form.smb_username) form.smb_username = p.username
})

watch(() => !!busy.value || test.busy, value => emit('busy', value), { flush: 'sync' })
watch(form, () => { test.text = ''; test.ok = null })
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
  if (busy.value || test.busy || props.disabled) return
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
function fillName (i) {
  const v = form.videos[i]
  if (v && !v.name && v.subpath) {
    v.name = v.subpath.split('/').filter(Boolean).pop() || ''
  }
}
async function create () {
  if (busy.value || test.busy || props.disabled) return
  busy.value = 'create'
  msg.value = ''
  try {
    const videos = form.videos
      .map((v) => ({ name: v.name.trim(), subpath: v.subpath.trim(), kind: v.kind }))
      .filter((v) => v.name || v.subpath)
      .map((v) => ({ ...v, naming_profile: form.naming_profile, artwork_mode: form.artwork_mode, name: v.name || (v.subpath.split('/').filter(Boolean).pop() || '') }))
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
    discard()
    emit('created', d)
  } catch (e) {
    msg.value = '创建失败：' + e.message
  } finally { busy.value = '' }
}

</script>
<style scoped>
.create-library-form { max-width: var(--jz-form-width); margin-top: 28px; padding-top: 24px; border-top: 1px solid var(--jz-border); }
fieldset { border: 0; padding: 0; margin: 0; min-width: 0; }
h4 { margin: 0 0 8px; font-size: var(--jz-font-xl); }
.policy-details { margin: 20px 0; padding: 12px 0; border-top: 1px solid var(--jz-border); border-bottom: 1px solid var(--jz-border); }
summary { cursor: pointer; font-size: var(--jz-font-m); }
input, select { min-width: 0 !important; width: 100%; max-width: 100%; box-sizing: border-box; }
.lib-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; align-items: start; margin-top: 20px; }
.lib-form label { display: flex; flex-direction: column; gap: 8px; font-size: var(--jz-font-m); }
.lib-form label.ck { grid-column: 1 / -1; flex-direction: row; align-items: center; min-height: 40px; }
.lib-form label.ck input { width: 16px; height: 16px; }
.lib-form > button { justify-self: start; }
.vid-form { border-top: 1px solid var(--jz-border); padding-top: 20px; }
.vid-line { grid-column: 1 / -1; display: grid; grid-template-columns: 1fr 1fr 100px auto; gap: 10px; align-items: center; }
.form-sec-title { grid-column: 1 / -1; color: var(--jz-text); font-size: var(--jz-font-m); font-weight: 600; }
.form-sec-title .fhint { display: block; margin-top: 4px; font-weight: normal; }
.parse-line { grid-column: 1 / -1; color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: 1.8; }
.test-result { padding: 10px 14px; background: var(--jz-success-soft); border-left: 3px solid var(--jz-success-border); }
.test-result.warn-text { background: var(--jz-danger-soft); border-color: var(--jz-danger-border); }
.stage-line { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 8px; }
.stage-chip { font-size: var(--jz-font-s); border: 1px solid var(--jz-success-border); color: var(--jz-success); border-radius: 4px; padding: 0 6px; }
.stage-chip.bad { border-color: var(--jz-danger-border); color: var(--jz-danger); }
.stage-chip.skip { border-color: var(--jz-border-strong); color: var(--jz-text-dim); }
.parse-line b { color: var(--jz-text); font-weight: 600; }
.parse-line code, .hint-list code { color: var(--jz-link); }
.hint-list { margin: 0; padding-left: 20px; color: var(--jz-text-dim); font-size: var(--jz-font-s); line-height: 1.9; }
.hint-list li { margin: 8px 0; }
.fhint { color: var(--jz-text-dim); font-size: var(--jz-font-s); font-weight: normal; }
.hint { color: var(--jz-text-dim); font-size: var(--jz-font-m); line-height: 1.6; }
.settings-details { grid-column: 1 / -1; }
@media (max-width: 700px) {
  .lib-form { grid-template-columns: minmax(0, 1fr); gap: 16px; }
  .vid-line { grid-template-columns: minmax(0, 1fr) auto; }
  .vid-line > input { grid-column: 1 / -1; }
  summary { min-height: 44px; box-sizing: border-box; padding: 10px 0; }
  .policy-details { padding: 0; }
}
</style>
