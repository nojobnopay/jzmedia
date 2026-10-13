<template>
  <JzDialog :open="casting.open.value" title="投屏到电视" size="small" layer="preview"
    :teleport-disabled="teleportDisabled" close-label="关闭投屏面板" :busy="casting.busy.value" @close="casting.close">
    <p class="cast-intro">电视需开启 DLNA 接收，并与 jzmedia 服务器处于可互通的局域网。</p>
    <p v-if="casting.error.value || casting.session.value?.error" class="cast-error" role="alert">
      {{ casting.error.value || casting.session.value.error }}
    </p>
    <section v-if="casting.session.value" class="cast-session" aria-label="电视播放状态">
      <strong>{{ casting.session.value.device.name }}</strong>
      <p role="status" aria-live="polite">{{ casting.stateLabel.value }}</p>
      <p>进度：{{ casting.session.value.position_known ? fmt(casting.session.value.position) : '电视未上报' }} / {{ fmt(casting.session.value.duration) }}</p>
      <p v-if="casting.session.value.warning" class="cast-hint">{{ casting.session.value.warning }}</p>
      <div v-if="casting.active.value" class="cast-actions">
        <JzButton :disabled="casting.busy.value" icon="play" @click="casting.control('play')">播放</JzButton>
        <JzButton :disabled="casting.busy.value" icon="pause" @click="casting.control('pause')">暂停</JzButton>
        <JzButton :disabled="casting.busy.value" @click="casting.control('stop')">停止投屏</JzButton>
      </div>
      <div v-if="casting.active.value" class="cast-fields">
        <JzField id="cast-seek" label="跳转到（秒）">
          <input id="cast-seek" v-model.number="seekTarget" type="number" min="0" :max="casting.session.value.duration || undefined" step="1" :disabled="casting.busy.value" />
          <JzButton :disabled="casting.busy.value || !validSeek" @click="casting.control('seek', seekTarget)">跳转</JzButton>
        </JzField>
        <JzField v-if="casting.session.value.device.volume_control" id="cast-volume" label="电视音量设置（0–100）">
          <input id="cast-volume" v-model.number="desiredVolume" type="number" min="0" max="100" step="1" :disabled="casting.busy.value" />
          <JzButton :disabled="casting.busy.value || !validVolume" @click="casting.control('volume', desiredVolume)">设置音量</JzButton>
        </JzField>
      </div>
      <p class="cast-hint">关闭此面板或手机页面，电视会继续播放。进度以电视实际上报为准。</p>
    </section>
    <template v-if="!casting.active.value">
      <div class="cast-search-heading">
        <strong>选择电视</strong>
        <JzButton icon="refresh" :loading="casting.searching.value" :disabled="casting.busy.value" @click="casting.discover">重新搜索</JzButton>
      </div>
      <p v-if="casting.searching.value" role="status">正在搜索服务器局域网内的电视…</p>
      <div v-else-if="casting.devices.value.length" class="cast-devices" role="radiogroup" aria-label="投屏设备">
        <label v-for="device in casting.devices.value" :key="device.id" class="cast-device">
          <input v-model="casting.selected.value" type="radio" name="cast-device" :value="device.id" :disabled="casting.busy.value" />
          <AppIcon name="tv" />
          <span><strong>{{ device.name }}</strong><small>{{ device.model }} · {{ device.address }}</small></span>
        </label>
      </div>
      <p v-else-if="casting.searched.value" class="cast-hint">未找到设备。请开启电视的 DLNA 接收功能，并检查服务器是否能发送局域网组播。Docker 桥接、访客 Wi-Fi 或模拟器虚拟网络可能阻止发现。</p>
      <form class="cast-manual" @submit.prevent="casting.addManual">
        <JzField id="cast-device-location" label="手动添加电视" hint="填写电视的 DLNA 描述地址，只有 IP 地址还不够。例：http://192.168.1.20:49152/description.xml" v-slot="field">
          <input id="cast-device-location" v-model="casting.manualLocation.value" type="url" required :aria-describedby="field.describedby"
            placeholder="http://192.168.1.20:49152/description.xml" :disabled="casting.busy.value" />
        </JzField>
        <JzButton type="submit" :loading="casting.busy.value" :disabled="casting.busy.value || !casting.manualLocation.value.trim()">添加电视</JzButton>
      </form>
      <JzField id="cast-server-url" label="电视可访问的服务器地址" hint="通常是服务器的局域网 IP 和端口。不要使用 localhost、模拟器专用地址或容器内部地址。" v-slot="field">
        <input id="cast-server-url" v-model="casting.serverUrl.value" type="url" :aria-describedby="field.describedby" placeholder="http://192.168.1.10:8080" :disabled="casting.busy.value" />
      </JzField>
      <p class="cast-hint">使用原文件投屏，由电视解码。网页画质、倍速、音轨选择和自绘字幕不随投屏传递；格式不支持时请换一个兼容版本。</p>
    </template>
    <template #footer>
      <HelpLink page="user-guide/playback-controls" label="投屏帮助" />
      <JzButton v-if="held" :disabled="casting.busy.value" @click="$emit('return-phone')">{{ casting.active.value ? '结束投屏，手机播放' : '手机播放' }}</JzButton>
      <JzButton v-if="!casting.active.value" variant="primary" icon="cast" :loading="casting.busy.value"
        :disabled="casting.searching.value || !casting.selected.value" @click="casting.play">开始投屏</JzButton>
      <JzButton :disabled="casting.busy.value" @click="casting.close">关闭</JzButton>
    </template>
  </JzDialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import JzDialog from './JzDialog.vue'
import JzButton from './JzButton.vue'
import JzField from './JzField.vue'
import AppIcon from './AppIcon.vue'
import HelpLink from './HelpLink.vue'
import { fmtTime as fmt } from '../playerLabels.js'

const props = defineProps({ casting: { type: Object, required: true }, held: Boolean, teleportDisabled: Boolean })
defineEmits(['return-phone'])
const seekTarget = ref(0), desiredVolume = ref(50)
const validSeek = computed(() => Number.isFinite(seekTarget.value) && seekTarget.value >= 0
  && (!props.casting.session.value?.duration || seekTarget.value <= props.casting.session.value.duration))
const validVolume = computed(() => Number.isFinite(desiredVolume.value) && desiredVolume.value >= 0 && desiredVolume.value <= 100)
</script>

<style scoped>
.cast-intro, .cast-hint { color: var(--jz-text-dim); font-size: var(--jz-font-s); }
.cast-intro { margin-top: 0; }
.cast-error { color: var(--jz-danger); overflow-wrap: anywhere; }
.cast-session { padding-bottom: 16px; margin-bottom: 16px; border-bottom: 1px solid var(--jz-border); }
.cast-actions, .cast-search-heading { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.cast-search-heading { justify-content: space-between; margin: 16px 0 12px; }
.cast-fields { display: grid; gap: 12px; margin-top: 16px; }
.cast-fields input { max-width: 130px; margin-right: 8px; }
.cast-devices { display: grid; gap: 8px; margin-bottom: 16px; }
.cast-manual { margin-bottom: 16px; }
.cast-manual .jz-field { margin-bottom: 8px; }
.cast-device { display: flex; align-items: center; gap: 12px; min-height: var(--jz-touch-target); padding: 12px; border: 1px solid var(--jz-border); border-radius: var(--jz-radius-s); cursor: pointer; }
.cast-device:has(input:checked) { border-color: var(--jz-accent); background: var(--jz-surface); }
.cast-device input { flex: none; accent-color: var(--jz-accent); }
.cast-device span { min-width: 0; overflow-wrap: anywhere; }
.cast-device small { display: block; color: var(--jz-text-dim); }
</style>
