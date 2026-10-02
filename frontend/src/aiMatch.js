const LEGACY_EXTERNAL_SOURCES = ['wikidata', 'tvmaze', 'bgm', 'douban', 'nfo']

export function isAiExternalCandidate(candidate) {
  if (candidate.bindable === false || !candidate.source || candidate.source_id == null || !String(candidate.source_id).trim()) return false
  return candidate.bindable === true || LEGACY_EXTERNAL_SOURCES.includes(candidate.source)
}

export function canBindAiCandidate(candidate) {
  return candidate.bindable !== false && (!!candidate.tmdb_id || isAiExternalCandidate(candidate))
}
