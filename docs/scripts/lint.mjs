import { execFile } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'
import { main as markdownlint } from 'markdownlint-cli2'

export const repoRoot = fileURLToPath(new URL('../../', import.meta.url))
const execute = promisify(execFile)

export async function sourceFiles() {
  // Share the link checker's pruning, including skills and private/symlink rules.
  const { stdout } = await execute('python3', [path.join(repoRoot, 'scripts/check_docs_links.py'), '--list'],
    { cwd: repoRoot, encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 })
  return JSON.parse(stdout)
}

export function selectFiles(files, targets = []) {
  if (!targets.length) return files
  const selected = new Set()
  for (const target of targets) {
    if (target.startsWith('-')) throw new Error(`不支持的选项：${target}`)
    const relative = path.relative(repoRoot, path.resolve(repoRoot, target)).split(path.sep).join('/')
    const matches = files.filter(file => !relative || file === relative || file.startsWith(`${relative}/`))
    if (!matches.length) throw new Error(`未找到检查范围内的 Markdown：${target}`)
    for (const file of matches) selected.add(file)
  }
  return [...selected].sort()
}

export async function run(args = process.argv.slice(2)) {
  const fix = args.includes('--fix')
  const targets = args.filter(arg => arg !== '--fix')
  const files = selectFiles(await sourceFiles(), targets)
  if (!files.length) throw new Error('未找到源 Markdown，拒绝将空检查视为通过')
  const result = await markdownlint({
    directory: repoRoot,
    argv: [...files.map(file => `:${file}`), ...(fix ? ['--fix'] : [])],
    logMessage: message => { if (!message.startsWith('Finding:')) console.log(message) },
    logError: console.error,
  })
  // 格式通过后提醒页首 version/reviewed 是否需要随正文更新；只提醒，不改变退出码。
  try {
    await metaReminders(targets.length ? files : [])
  } catch (error) {
    console.error(`元信息提醒检查失败（不影响本次 lint 结果）：${error.message}`)
  }
  return result
}

export async function metaReminders(files = []) {
  const script = path.join(repoRoot, 'scripts/check_docs_meta.py')
  const { stdout } = await execute('python3', files.length ? [script, ...files] : [script],
    { cwd: repoRoot, encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 })
  if (stdout.trim()) console.log(stdout.trim())
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.exitCode = await run() }
  catch (error) { console.error(error.message); process.exitCode = 2 }
}
