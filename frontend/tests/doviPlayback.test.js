import test from 'node:test'
import assert from 'node:assert/strict'
import { isDoviP5Playback, doviHeartbeatAction, compatiblePlaybackVersion } from '../src/doviPlayback.js'
import { blockedPlaybackMessage } from '../src/playbackCompatibility.js'

const automatic = { p5: true, quality: 'auto', height: 1080, downgraded: false }
test('P5 automatic downgrade only replaces a 1080p stream once and never masks a color failure', () => {
  assert.equal(doviHeartbeatAction({ recommended_height: 720 }, automatic), 'downgrade')
  assert.equal(doviHeartbeatAction({ failed: true, failure_code: 'dovi_too_slow', recommended_height: 720 }, automatic), 'downgrade')
  for (const override of [{ quality: '1080p' }, { height: 720 }, { downgraded: true }, { p5: false }]) {
    assert.equal(doviHeartbeatAction({ recommended_height: 720 }, { ...automatic, ...override }), null)
  }
  for (const code of ['dovi_metadata_invalid', 'dovi_pipeline_unavailable']) {
    assert.equal(doviHeartbeatAction({ failed: true, failure_code: code, recommended_height: 720 }, automatic), 'stop')
  }
  assert.equal(doviHeartbeatAction({ failed: true, failure_code: 'dovi_too_slow' }, { ...automatic, height: 720 }), 'stop')
  assert.equal(doviHeartbeatAction({ failed: true, complete: true }, automatic), 'stop')
})

test('blocked P5 plans remain identifiable and expose a useful recovery reason', () => {
  assert.equal(isDoviP5Playback({ color_pipeline: 'dovi_p5_sdr' }), true)
  assert.equal(isDoviP5Playback(null, { dv_profile: 5 }), true)
  assert.equal(isDoviP5Playback({}, {}, ['dovi_p5_unavailable']), true)
  assert.equal(isDoviP5Playback({}, { dv_profile: 7 }), false)
  for (const reason of ['dovi_p5_web_disabled', 'dovi_p5_unavailable', 'dovi_p5_source_unsupported',
    'dovi_p5_packaging_unsupported', 'dovi_p5_burn_unsupported']) {
    assert.match(blockedPlaybackMessage([reason]), /杜比视界/)
    assert.match(blockedPlaybackMessage([reason]), /复制直链/)
  }
})

test('compatible version recovery excludes unavailable and P5 versions and prefers an existing direct stream', () => {
  const versions = [
    { version_id: 1, playable: true, method: 'direct' },
    { version_id: 2, playable: true, method: 'video_transcode', dv_profile: 0 },
    { version_id: 3, playable: true, method: 'direct', dv_profile: 5 },
    { version_id: 4, playable: false, method: 'direct' },
    { version_id: 5, playable: true, method: 'blocked' },
    { version_id: 6, playable: true, method: 'remux', dv_profile: 0 },
  ]
  assert.equal(compatiblePlaybackVersion(versions, 1).version_id, 6)
  assert.equal(compatiblePlaybackVersion([versions[0]], 1), null)
})
