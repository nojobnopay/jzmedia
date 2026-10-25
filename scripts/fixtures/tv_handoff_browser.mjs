// Real PlayerModal, isolated synthetic media; receiver registry/acknowledgements are browser fixtures.
import assert from 'node:assert/strict'
import path from 'node:path'

export async function smokeTvHandoff({ browser, base, fixture, output }) {
  for (const width of [1440, 390, 375, 320]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, hasTouch: width <= 390, reducedMotion: 'reduce' })
    const page = await context.newPage(), errors = [], creates = [], commands = []
    let remote = null, available = [], rejectStop = false, preparing = false
    page.setDefaultTimeout(15000)
    page.on('pageerror', error => errors.push(String(error)))
    await context.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort())
    const json = (route, value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) })
    await page.route('**/api/cast/apps', route => json(route, { items: available }))
    await page.route('**/api/cast/current?**', route => json(route, { session: remote }))
    await page.route('**/api/cast/sessions', async route => {
      const body = route.request().postDataJSON()
      creates.push(body)
      const app = body.receiver_type === 'jzmedia_tv'
      const pending = app || preparing
      remote = { id: `fixture-${creates.length}`, active: true, receiver_type: body.receiver_type,
        version_id: body.version_id, kind: body.kind, state: app ? 'WAITING_FOR_APP' : preparing ? 'PREPARING' : 'PLAYING',
        device: app ? available.find(item => item.id === body.device_id) : { id: body.device_id, name: '合成 DLNA 接收器', volume_control: true },
        position: pending ? 0 : body.position, last_position: pending ? null : body.position,
        position_known: !pending, resume_pending: pending, resume_target: body.position,
        duration: 90, volume: null, prepare_progress: preparing && !app ? .4 : null }
      await json(route, remote)
    })
    await page.route('**/api/cast/sessions/**', async route => {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON()
        commands.push(body)
        if (body.command === 'stop') {
          if (rejectStop) return json(route, { detail: '模拟停止失败，请核对电视' }, 409)
          remote = { ...remote, active: false, state: 'STOPPED' }
        } else if (body.command === 'seek') remote = { ...remote, position: body.value, last_position: body.value }
        else remote = { ...remote, state: body.command === 'pause' ? 'PAUSED_PLAYBACK' : 'PLAYING' }
      }
      await json(route, remote)
    })
    await page.goto(base)
    const player = page.getByRole('dialog', { name: '合成视频 · 投屏验证', exact: true })
    const picker = page.getByRole('dialog', { name: '投屏到电视', exact: true })
    await page.getByRole('button', { name: '合成电影', exact: true }).click()
    await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2)
    const tracks = player.getByRole('button', { name: '字幕与音轨', exact: true })
    await tracks.click()
    const trackPanel = player.getByRole('region', { name: '字幕与音轨', exact: true })
    await trackPanel.getByLabel('音轨', { exact: true }).selectOption('1')
    await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2)
    await trackPanel.getByLabel('字幕轨道', { exact: true }).selectOption('0')
    await page.keyboard.press('Escape')
    await page.locator('video').evaluate(video => { video.pause(); video.currentTime = 23 })
    await page.waitForFunction(() => document.querySelector('video')?.currentTime >= 23 && !document.querySelector('video').seeking)
    await player.getByRole('button', { name: '投屏', exact: true }).click()
    await picker.getByRole('radio').waitFor()
    assert.equal(await picker.getByRole('button', { name: '开始投屏', exact: true }).isDisabled(), true)
    await picker.getByText(/手机选择了字幕/).waitFor()
    await picker.getByRole('button', { name: '使用 jzmedia-tv 播放', exact: true }).click()
    await picker.getByText(/尚未找到可接收的 jzmedia-tv/).waitFor()
    assert.equal(creates.length, 0, 'Opening the App picker never sends a Play')
    await page.screenshot({ path: path.join(output, `app-empty-${width}.png`) })
    available = [
      { id: 'fixture-bedroom', name: '主卧电视 · jzmedia-tv', address: '192.0.2.21', receiver_type: 'jzmedia_tv', volume_control: false },
      { id: 'fixture-living', name: '客厅电视 · jzmedia-tv', address: '192.0.2.22', receiver_type: 'jzmedia_tv', volume_control: false },
    ]
    await picker.getByRole('button', { name: '刷新', exact: true }).click()
    await picker.getByRole('radio', { name: /主卧/ }).waitFor()
    assert.equal(await picker.getByRole('radio').evaluateAll(radios => radios.some(radio => radio.checked)), false)
    const start = picker.getByRole('button', { name: '在电视 App 播放', exact: true })
    assert.equal(await start.isDisabled(), true, 'The user explicitly chooses the target TV')
    await picker.getByRole('radio', { name: /主卧/ }).check()
    await page.screenshot({ path: path.join(output, `app-picker-${width}.png`) })
    await start.click()
    await picker.getByText('等待 jzmedia-tv 确认播放', { exact: true }).waitFor()
    assert.equal(creates.length, 1)
    assert.equal(creates[0].device_id, 'fixture-bedroom')
    assert.equal(creates[0].receiver_type, 'jzmedia_tv')
    assert.equal(creates[0].version_id, fixture.movie_id)
    assert.equal(creates[0].audio, 1)
    assert.equal(creates[0].sub, 0)
    assert.ok(Math.abs(creates[0].position - 23) <= .2)
    assert.equal(await picker.getByRole('slider').count(), 0, 'An unacknowledged App has no fake real controls')
    assert.equal(await page.locator('video').evaluate(video => video.paused && !video.getAttribute('src')), true)
    await page.screenshot({ path: path.join(output, `app-waiting-${width}.png`) })
    remote = { ...remote, state: 'PLAYING', position: 38, last_position: 38, position_known: true, resume_pending: false }
    await picker.getByText('电视正在播放', { exact: true }).waitFor()
    assert.equal(await picker.getByRole('slider', { name: '电视播放进度', exact: true }).inputValue(), '38')
    assert.equal(await picker.getByRole('slider', { name: '设置电视音量', exact: true }).count(), 0)
    await page.screenshot({ path: path.join(output, `app-playing-${width}.png`) })
    await picker.getByRole('button', { name: '暂停', exact: true }).click()
    await picker.getByText('电视已暂停', { exact: true }).waitFor()
    await picker.getByRole('button', { name: '播放', exact: true }).click()
    await picker.getByText('电视正在播放', { exact: true }).waitFor()
    await page.keyboard.press('Escape')
    await picker.waitFor({ state: 'hidden' })
    assert.equal(await player.getByRole('button', { name: '投屏控制', exact: true }).first().evaluate(element => document.activeElement === element), true)
    if (width === 390) {
      await player.getByRole('button', { name: '全屏', exact: true }).click()
      await page.waitForFunction(() => !!document.fullscreenElement)
      await player.getByRole('button', { name: '投屏控制', exact: true }).first().click()
      await picker.waitFor()
      assert.equal(await picker.evaluate(element => !!element.closest('.player-dlg')), true)
      await page.screenshot({ path: path.join(output, `app-fullscreen-${width}.png`) })
      await page.keyboard.press('Escape')
      await picker.waitFor({ state: 'hidden' })
      assert.equal(await page.evaluate(() => !!document.fullscreenElement), true)
      await player.getByRole('button', { name: '退出全屏', exact: true }).click()
    }
    await player.getByRole('button', { name: '投屏控制', exact: true }).first().click()
    await picker.getByRole('button', { name: '结束投屏，手机播放', exact: true }).click()
    await picker.waitFor({ state: 'hidden' })
    await page.waitForFunction(() => !!document.querySelector('video')?.getAttribute('src') && document.querySelector('video').readyState >= 2)
    await tracks.click()
    await trackPanel.getByLabel('字幕轨道', { exact: true }).selectOption('-1')
    await page.keyboard.press('Escape')
    await player.getByRole('button', { name: '投屏', exact: true }).click()
    await picker.getByRole('button', { name: '电视内置投屏', exact: true }).click()
    await picker.getByRole('radio').check()
    preparing = true
    await picker.getByRole('button', { name: '开始投屏', exact: true }).click()
    await picker.getByText('正在准备所选音轨', { exact: true }).waitFor()
    assert.equal(await picker.getByRole('progressbar', { name: '音轨准备进度', exact: true }).getAttribute('value'), '0.4')
    assert.equal(await picker.getByRole('slider').count(), 0)
    await page.screenshot({ path: path.join(output, `audio-preparing-${width}.png`) })
    // A failed Stop leaves the current session intact and never opens another TV.
    rejectStop = true
    await picker.getByRole('button', { name: '改用 jzmedia-tv', exact: true }).click()
    await picker.getByRole('alert').filter({ hasText: '模拟停止失败' }).waitFor()
    assert.equal(remote.active, true)
    assert.equal(await picker.getByRole('radiogroup', { name: 'jzmedia-tv 设备', exact: true }).count(), 0)
    assert.equal(creates.length, 2)
    rejectStop = false
    const preservedTarget = remote.resume_target
    await picker.getByRole('button', { name: '改用 jzmedia-tv', exact: true }).click()
    await picker.getByRole('radio', { name: /客厅/ }).check()
    assert.equal(remote.active, false)
    await picker.getByRole('button', { name: '在电视 App 播放', exact: true }).click()
    await picker.getByText('等待 jzmedia-tv 确认播放', { exact: true }).waitFor()
    assert.equal(creates.length, 3)
    assert.equal(creates[2].device_id, 'fixture-living')
    assert.ok(Math.abs(creates[2].position - preservedTarget) <= .2)
    assert.equal(creates[2].sub, -1)
    await picker.getByRole('button', { name: '取消本次播放', exact: true }).click()
    await picker.getByText('投屏已停止', { exact: true }).waitFor()
    // A renderer can report PLAYING while displaying no picture and no warning.
    // The App escape route must stay available without backend error detection.
    preparing = false
    await picker.getByRole('button', { name: '电视内置投屏', exact: true }).click()
    await picker.getByRole('radio').check()
    await picker.getByRole('button', { name: '开始投屏', exact: true }).click()
    await picker.getByText('电视正在播放', { exact: true }).waitFor()
    await picker.getByText('电视无画面、无声音或需要字幕时，可改用已安装的 jzmedia-tv。', { exact: true }).waitFor()
    assert.equal(remote.error, undefined)
    assert.equal(remote.warning, undefined)
    await page.screenshot({ path: path.join(output, `dlna-no-picture-route-${width}.png`) })
    await picker.getByRole('button', { name: '改用 jzmedia-tv', exact: true }).click()
    await picker.getByRole('radio', { name: /客厅/ }).waitFor()
    assert.equal(remote.active, false)
    const dimensions = await picker.locator('button').evaluateAll(buttons => buttons.filter(button => button.checkVisibility()).map(button => ({ w: button.getBoundingClientRect().width, h: button.getBoundingClientRect().height })))
    if (width <= 390) assert.ok(dimensions.every(size => size.w >= 43.5 && size.h >= 43.5), JSON.stringify(dimensions))
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    assert.deepEqual(errors, [])
    assert.ok(commands.some(command => command.command === 'stop'))
    await context.close()
    console.log(`PASS ${width}px App empty/online explicit target, audio/subtitle/time handoff, acknowledgement/control, cancellation, DLNA prepare and safe switch`)
  }
}
