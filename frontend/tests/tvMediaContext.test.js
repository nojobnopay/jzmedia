import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

function source (path) {
  return readFileSync(new URL(path, import.meta.url), 'utf8')
}

test('TV details and recommendations expose and follow their media library context', () => {
  const detail = source('../src/views/TvShow.vue')
  const app = source('../src/App.vue')
  const similar = source('../src/components/SimilarRow.vue')

  assert.match(detail, /媒体库：\{\{ show\.media_name \}\}/)
  assert.match(detail, /switchMedia\(mediaId\)/)
  assert.match(app, /onLibChange\(syncLibs\)/)
  assert.match(similar, /class="similar-library"/)
  assert.match(similar, /item\?\.media_name, item\?\.library_name/)
})
