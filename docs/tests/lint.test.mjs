import test from 'node:test'
import assert from 'node:assert/strict'
import MarkdownIt from 'markdown-it'
import { lint } from 'markdownlint-cli2/markdownlint/promise'
import options from '../../.markdownlint-cli2.mjs'
import { selectFiles, sourceFiles } from '../scripts/lint.mjs'

async function check(source, config = options.config) {
  const results = await lint({ strings: { sample: source }, config,
    customRules: options.customRules, markdownItFactory: () => new MarkdownIt({ html: true }) })
  return results.sample
}

test('lint catches structural mistakes without imposing Chinese line lengths', async () => {
  const errors = await check('# 示例\n\n### 跳级\n\n```\ncommand\n```\n\n| A | B |\n|---|---|\n| 只有一列 |\n')
  const rules = new Set(errors.map(error => error.ruleNames[0]))
  for (const rule of ['MD001', 'MD040', 'MD056']) assert.ok(rules.has(rule), rule)
  assert.deepEqual(await check(`# 示例\n\n${'这是一段待人工判断的长中文说明。'.repeat(15)}\n`), [])
})

test('step numbering restarts per H2, excluding fences and quoted examples', async () => {
  const source = '# 教程\n\n## 构建\n\n### Step 1：准备\n\n正文。\n\n### Step 2：构建\n\n正文。\n\n## 安装\n\n### Step 1：安装\n\n```md\n### Step 9：示例\n```\n\n> ### Step 9：引用示例\n\n### Step 2：验证\n\n完成。\n'
  assert.deepEqual(await check(source), [])
  const skipped = await check(source.replace('Step 2：构建', 'Step 3：构建'))
  assert.equal(skipped.filter(error => error.ruleNames[0] === 'JZ001').length, 1)
  const wrongLevel = await check('# 教程\n\n## Step 1：构建\n\n正文。\n')
  assert.ok(wrongLevel.some(error => error.ruleNames[0] === 'JZ001'))
  for (const title of ['Step 1：', 'Step 1. 准备']) {
    assert.ok((await check(`# 教程\n\n## 操作\n\n### ${title}\n\n正文。\n`))
      .some(error => error.ruleNames[0] === 'JZ001'), title)
  }
})

test('Vue steps require Markdown headings while component code examples remain valid', async () => {
  const legacy = await check('---\nversion: 1\n---\n\n# 教程\n\n<DocStep number="1" title="准备">\n\n正文。\n\n</DocStep>\n')
  assert.equal(legacy.find(error => error.ruleNames[0] === 'JZ002')?.lineNumber, 7)
  assert.deepEqual(await check('# 教程\n\n## 操作\n\n### Step 1：准备\n\n<DocStep>\n\n正文。\n\n</DocStep>\n\n```html\n<DocStep number="1" title="旧用法" />\n```\n'), [])
  assert.deepEqual(await check('# 教程\n\n<!-- <DocStep title="说明" number="9" /> -->\n'), [])
  for (const tag of ['<DocStep title="准备" number="1" />', '<DocStep :title="name" />']) {
    const inline = await check(`# 教程\n\n正文 ${tag}\n`)
    assert.equal(inline.find(error => error.ruleNames[0] === 'JZ002')?.lineNumber, 3)
  }
  const repeated = await check('# 教程\n\n旧写法 `<DocStep title="准备" />`。\n后续文字 <DocStep title="准备" />\n')
  assert.equal(repeated.find(error => error.ruleNames[0] === 'JZ002')?.lineNumber, 4)
})

test('home-page exception stays scoped while ordinary pages require an H1', async () => {
  assert.ok((await check('## 正文\n\n内容。\n')).some(error => error.ruleNames[0] === 'MD041'))
  const exception = options.overrides[0]
  assert.deepEqual(exception.filter, ['docs/index.md'])
  assert.deepEqual(await check('## 正文\n\n内容。\n', { ...options.config, ...exception.config }), [])
})

test('lint shares source scope, rejects excluded or empty explicit selections', async () => {
  const files = await sourceFiles()
  assert.ok(files.includes('.agents/skills/jzmedia-docs/SKILL.md'))
  assert.ok(files.includes('android-tv/README.md'))
  // 根 AGENTS.md 仅存于本地工作区（不入库），不再要求出现在源集合中。
  assert.ok(files.every(file => !/(?:node_modules|docs\/private|\.vitepress|\/build\/)/.test(file)))
  assert.deepEqual(selectFiles(files, ['android-tv/README.md']), ['android-tv/README.md'])
  assert.ok(selectFiles(files, ['android-tv']).every(file => file.startsWith('android-tv/')))
  for (const target of ['docs/private', '../outside.md', 'missing.md', '--unknown']) {
    assert.throws(() => selectFiles(files, [target]), /未找到|不支持/)
  }
})
