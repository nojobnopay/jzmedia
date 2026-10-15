// Browsing range and child suitability are separate choices. A private item
// cannot be admitted to ordinary or child browsing by a child-setting change.
export function visibilityPatch(kind, ids, range, children) {
  const body = { kind, ids }
  if (range === 'private') body.is_private = true
  if (range === 'normal') {
    body.is_private = false
    if (['auto', 'allow', 'block'].includes(children)) body.children_override = children
  }
  return body
}

export function visibilityPreview(range, children, policy = null) {
  const kept = { value: '保持各自设置', state: 'kept' }
  const shown = { value: '显示', state: 'shown' }
  const hidden = { value: '不显示', state: 'hidden' }
  let normal = kept, child = kept, privacy = kept
  if (range === 'private') {
    normal = hidden
    child = hidden
    privacy = shown
  } else if (range === 'normal') {
    normal = shown
    privacy = hidden
    if (children === 'allow') child = shown
    else if (children === 'block') child = hidden
    else if (children === 'auto') {
      if (!policy) child = { value: '按每部资料筛选', state: 'conditional' }
      else if (policy.children_auto_status === 'allow') child = shown
      else if (policy.children_auto_status === 'block') child = hidden
      else child = { value: '不显示（资料不足）', state: 'hidden' }
    }
  }
  return [
    { mode: 'normal', label: '普通模式', ...normal },
    { mode: 'children', label: '儿童模式', ...child },
    { mode: 'private', label: '隐私模式', ...privacy },
  ]
}
