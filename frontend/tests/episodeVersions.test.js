import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  basename, episodeVersion, groupEpisodesByVersion, seasonStats,
} from '../src/episodeVersions.js'

test('episodeVersion prefers backend field then parses path', () => {
  assert.equal(episodeVersion({ version: 2, file_path: 'x.mkv' }), 2)
  assert.equal(episodeVersion({ file_path: 'Show/Season 01/剧-S01E01-事故.mkv' }), 1)
  assert.equal(episodeVersion({ file_path: 'Show/Season 01/剧-V2-S01E01-事故.mp4' }), 2)
  assert.equal(episodeVersion({ file_path: 'Show/Season 01/剧-S01E01-事故-V2.mp4' }), 2)
  assert.equal(episodeVersion({ file_path: 'Show/Season 01/剧-V10-S01E01-x.mkv' }), 10)
  assert.equal(episodeVersion({}), 1)
  assert.equal(basename('a/b/c.mkv'), 'c.mkv')
})

test('groupEpisodesByVersion groups and sorts by version', () => {
  const eps = [
    { id: 1, file_path: 's/剧-S01E01-一.mkv' },
    { id: 2, file_path: 's/剧-V2-S01E01-一.mp4' },
    { id: 3, file_path: 's/剧-V2-S01E02-二.mp4' },
    { id: 4, file_path: 's/剧-S01E02-二.mkv' },
  ]
  const groups = groupEpisodesByVersion(eps)
  assert.equal(groups.length, 2)
  assert.deepEqual(groups.map((g) => g.version), [1, 2])
  assert.deepEqual(groups[0].episodes.map((e) => e.id), [1, 4])
  assert.deepEqual(groups[1].episodes.map((e) => e.id), [2, 3])
})

test('single-version season stays flat', () => {
  const groups = groupEpisodesByVersion([
    { id: 1, file_path: 's/剧-S01E01.mkv' },
    { id: 2, file_path: 's/剧-S01E02.mkv' },
  ])
  assert.equal(groups.length, 1)
  assert.equal(groups[0].version, 1)
})

test('seasonStats counts distinct episodes and versions', () => {
  const st = seasonStats([
    { season: 1, episode: 1, file_path: 's/剧-S01E01-一.mkv' },
    { season: 1, episode: 1, file_path: 's/剧-V2-S01E01-一.mp4' },
    { season: 1, episode: 2, file_path: 's/剧-S01E02-二.mkv' },
    { season: 1, episode: 2, file_path: 's/剧-V2-S01E02-二.mp4' },
  ])
  assert.deepEqual(st, { distinct: 2, versions: 2 })
  assert.deepEqual(seasonStats([]), { distinct: 0, versions: 0 })
})
