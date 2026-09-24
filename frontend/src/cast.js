// 演职员统一契约（P1）：电影 persons 表与 TV tmdb_cache 双源归一。
// 后端已做双字段兼容输出（id/tmdb_id、character/character_name、
// profile_path/avatar），此处做前端归一 + 头像解析 + 跳转 id 提取。
import { posterUrl, castAvatarUrl } from './api.js'

export function shouldShowCharacter(originalLanguage) {
  return String(originalLanguage || '').toLowerCase().startsWith('en')
}

export function castId(p) {
  const v = Number(p?.tmdb_id ?? p?.id ?? 0)
  return Number.isFinite(v) ? v : 0
}

export function castName(p) {
  return String(p?.name || '')
}

export function castCharacter(p) {
  return String(p?.character ?? p?.character_name ?? '')
}

function _isTmdbPath(v) {
  return typeof v === 'string' && v.startsWith('/')
}

// 头像解析优先级：TMDB profile_path（走代理）> 本地 avatar（走 /posters）> ''（占位）。
// 电影旧行只有 avatar，TV 行只有 profile_path，统一后双字段都有时优先远端原图。
export function castAvatarSrc(p) {
  const profile = p?.profile_path
  if (_isTmdbPath(profile)) return castAvatarUrl(profile)
  const avatar = p?.avatar
  if (typeof avatar === 'string' && avatar && avatar !== '-') return posterUrl(avatar)
  // 兼容：后端把 TV profile_path 同时写入 avatar 别名时，avatar 也可能是 TMDB 路径
  if (_isTmdbPath(avatar)) return castAvatarUrl(avatar)
  return ''
}

export function normalizeCast(p) {
  const src = castAvatarSrc(p)
  return {
    id: castId(p),
    name: castName(p),
    character: castCharacter(p),
    avatarSrc: src,
    guest: !!(p && p.guest),
    raw: p,
  }
}
