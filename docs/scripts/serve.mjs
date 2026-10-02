import { createHelpServer } from './server.mjs'

const option = (name, fallback) => {
  const index = process.argv.indexOf(name)
  return index < 0 ? fallback : process.argv[index + 1]
}
const port = Number(option('--port', '4174'))
const host = option('--host', '127.0.0.1')
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('无效端口')
const server = createHelpServer()
server.listen(port, host, () => console.log(`文档预览：http://${host}:${port}/help/`))
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.close(() => process.exit(0)))
