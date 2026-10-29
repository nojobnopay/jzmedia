import { networkRuleChoice } from './onlineCatalog.js'

export const SOURCE_NETWORK_PURPOSES = [
  { key: 'default', label: '来源默认' }, { key: 'api', label: '搜索与详情' },
  { key: 'media', label: '视频播放' }, { key: 'image', label: '海报图片' },
  { key: 'subtitle', label: '字幕' }, { key: 'subscription', label: '订阅更新' },
]

export const emptyOnlineSourceForm = () => ({ name: '', provider: 'maccms', api_base_url: '',
  hosts: '', enabled: true, priority: 100, search_action: 'list', detail_action: 'detail' })

export function resolveSourceNetworkChoice(choices, purpose, applicationRule) {
  for (const key of purpose === 'default' ? ['default'] : [purpose, 'default']) {
    if (choices[key] && choices[key] !== 'inherit') return { choice: choices[key], from: key }
  }
  return { choice: applicationRule ? networkRuleChoice(applicationRule) : '', from: 'application' }
}

export function networkChoiceSummary(choice, profiles) {
  if (choice === 'direct') return '直连'
  if (choice === 'new-proxy') return '新代理（待保存）'
  const profile = profiles.find(item => 'profile:' + item.id === choice)
  if (!profile) return '网络配置暂不可用'
  return profile.mode === 'proxy' ? `代理 · ${profile.name}` : `直连 · ${profile.name}`
}

export function proxyProfilePayload(form, sourceName) {
  const protocol = form.protocol
  let host = String(form.host || '').trim()
  const portText = String(form.port || '').trim()
  if (!['http', 'https'].includes(protocol) || !host || /[\s/@?#\\]/.test(host)
    || !/^\d+$/.test(portText) || Number(portText) < 1 || Number(portText) > 65535) {
    throw new Error('请填写代理服务器地址和 1–65535 之间的端口；地址中不包含协议、路径或密码。')
  }
  // IPv6 is accepted as either a bare address or a bracketed address.
  if (host.includes(':') && !host.startsWith('[')) host = '[' + host + ']'
  let endpoint
  try {
    const url = new URL(`${protocol}://${host}:${portText}`)
    if (!url.hostname || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error()
    endpoint = url.origin
  } catch { throw new Error('代理服务器地址格式无效，请填写主机名或 IP 地址。') }
  const username = String(form.username || ''), password = String(form.password || '')
  const hasControl = value => [...value].some(character => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
  if (hasControl(username) || username.includes(':') || hasControl(password)
    || username.length > 1024 || password.length > 1024) throw new Error('代理认证信息格式无效。')
  return { name: (String(sourceName || '在线视频源') + '代理').slice(0, 128), mode: 'proxy',
    proxy_url: endpoint, username, password }
}
