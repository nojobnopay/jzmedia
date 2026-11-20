import assert from 'node:assert/strict'
import test from 'node:test'
import { createAssistantRoutine } from '../src/assistantRoutine.js'

function fixture() {
  let time = 0
  const routine = createAssistantRoutine(() => time, () => 0)
  return { routine, at(value, id, motion = true) {
    time = value
    return routine.tick({ tip: id ? { id } : null, motion })?.phase ?? null
  } }
}

test('one tip per visit, shared quiet period across navigation and gestures', () => {
  const { at, routine } = fixture()
  assert.equal(at(3_999, 'movie'), null)
  assert.equal(at(4_000, 'movie'), 'tip')
  assert.equal(at(5_000, 'movie'), 'tip')
  routine.visit()
  assert.equal(at(9_001, 'series'), null)
  assert.equal(at(64_999, 'series'), null)
  // The early tip from the previous visit does not queue up after the cooldown.
  assert.equal(at(65_001, 'series'), 'ambient-wave')
  assert.equal(at(67_000, 'series'), null)
  routine.visit()
  assert.equal(at(90_000, 'online'), null)
})

test('idle sleep is held until activity; returning to a hidden tab starts a fresh wait', () => {
  const { at, routine } = fixture()
  assert.equal(at(60_000), 'ambient-wave')
  assert.equal(at(62_000), null)
  assert.equal(at(179_000), null)
  assert.equal(at(180_000), 'ambient-sleep')
  assert.equal(at(300_000), 'ambient-sleep')
  routine.activity()
  assert.equal(at(300_001), null)
  routine.resume()
  assert.equal(at(359_999), null)
  assert.equal(at(360_001), 'ambient-frown')
})

test('opening the panel consumes the visit; reduced motion allows text without gestures', () => {
  const { at, routine } = fixture()
  assert.equal(at(4_000, 'movie', false), 'tip')
  assert.equal(at(12_000, 'movie', false), null)
  assert.equal(at(75_000, 'another', false), null)
  routine.visit()
  routine.opened()
  assert.equal(at(82_000, 'series', false), null)
  assert.equal(at(400_000, null, false), null)
})

test('a newly relevant tip can surface after prolonged browsing and stays readable on pointer movement', () => {
  const { at, routine } = fixture()
  assert.equal(at(45_000), null)
  routine.activity()
  assert.equal(at(49_001, 'no-results'), 'tip')
  assert.equal(routine.activity(true).phase, 'tip')
  assert.equal(at(52_000, 'no-results'), 'tip')
  assert.equal(at(53_000), null)
})
