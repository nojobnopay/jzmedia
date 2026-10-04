import { createServer } from 'node:http'
import { createReadStream, existsSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import { site, normalizeBase } from './site-config.mjs'

const mimeTypes = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json', '.svg': 'image/svg+xml',
  '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.mp4': 'video/mp4',
  '.vtt': 'text/vtt; charset=utf-8', '.woff2': 'font/woff2', '.ico': 'image/x-icon',
}
export const defaultDist = site.outDir

export function helpCsp(root) {
  const { scriptHashes } = JSON.parse(readFileSync(path.join(root, 'csp-hashes.json'), 'utf8'))
  return `default-src 'self'; script-src 'self' ${scriptHashes.map(hash => `'${hash}'`).join(' ')}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'self'`
}

export function createHelpServer(root = defaultDist, base = site.base) {
  base = normalizeBase(base)
  const csp = helpCsp(root)
  return createServer((request, response) => {
    response.setHeader('Content-Security-Policy', csp)
    response.setHeader('X-Content-Type-Options', 'nosniff')
    if (!['GET', 'HEAD'].includes(request.method)) { response.writeHead(405).end(); return }
    let pathname
    try { pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname) }
    catch { response.writeHead(400).end(); return }
    if (base !== '/' && (pathname === '/' || pathname === base.slice(0, -1))) {
      response.writeHead(308, { Location: base }).end(); return
    }
    let relative = pathname.startsWith(base) ? pathname.slice(base.length) : ''
    if (!relative || relative.endsWith('/')) relative += 'index.html'
    const candidate = path.resolve(root, relative)
    const allowed = pathname.startsWith(base) && candidate.startsWith(root + path.sep) &&
      !relative.split('/').some(part => part.startsWith('.')) && relative !== 'csp-hashes.json'
    let file = allowed && existsSync(candidate) && statSync(candidate).isFile() ? candidate : null
    let status = 200
    if (!file) { file = path.join(root, '404.html'); status = 404 }
    if (!existsSync(file)) { response.writeHead(404).end('Not found'); return }
    const size = statSync(file).size
    const extension = path.extname(file)
    response.setHeader('Content-Type', mimeTypes[extension] || 'application/octet-stream')
    response.setHeader('Cache-Control', extension === '.html' ? 'no-cache' : 'public, max-age=3600')
    response.setHeader('Accept-Ranges', 'bytes')
    let start = 0; let end = size - 1
    if (status === 200 && request.headers.range) {
      const range = /^bytes=(\d*)-(\d*)$/.exec(request.headers.range)
      if (!range || (!range[1] && !range[2])) { response.writeHead(416, { 'Content-Range': `bytes */${size}` }).end(); return }
      if (!range[1]) start = Math.max(0, size - Number(range[2]))
      else { start = Number(range[1]); end = range[2] ? Math.min(end, Number(range[2])) : end }
      if (start > end || start >= size) { response.writeHead(416, { 'Content-Range': `bytes */${size}` }).end(); return }
      status = 206
      response.setHeader('Content-Range', `bytes ${start}-${end}/${size}`)
    }
    response.setHeader('Content-Length', end - start + 1)
    response.writeHead(status)
    if (request.method === 'HEAD' || size === 0) response.end()
    else createReadStream(file, { start, end }).pipe(response)
  })
}
