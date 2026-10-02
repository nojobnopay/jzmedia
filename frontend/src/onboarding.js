// Registered media, not transferred file counts, determines guide completion.
export const SETUP_STEPS = ['资料来源', '视频库', '添加内容', '查看结果']
export function checkReady(result) {
  return !!(result?.readable && result?.video?.ok)
}
export function itemLink(item, kind) {
  return kind === 'tv' ? '/tv/' + item.show_id + '/s/' + item.season + '/e/' + item.id : '/m/' + item.id
}
export function uploadSummary(result) {
  if (!result) return ''
  return '上传' + (result.cancelled ? '已取消' : '处理结束') + '：成功 ' + result.uploaded
    + ' · 同名跳过 ' + result.skipped + ' · 失败 ' + result.failed
    + '。文件传输成功后仍需确认已登记的电影或分集。'
}
