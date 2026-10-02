// Leaving a directory inside the same library needs no prompt; leaving its workspace does.
export function leavesFileLibrary(to, libraryId, resolveLibrary) {
  if (to.path !== '/settings' || to.query?.sec !== 'sec-files') return true
  return Number(resolveLibrary(to.query)) !== Number(libraryId)
}

export function needsFileReview(change, operationRunning = false) {
  return operationRunning || Boolean(change?.pending) || Number(change?.count) > 0 || Boolean(change?.active_jobs?.length)
}

export function scanTarget(library) {
  const query = { sec: 'sec-sync', library: String(library.id) }
  if (library.media_id != null) query.media = String(library.media_id)
  return { path: '/settings', query }
}
