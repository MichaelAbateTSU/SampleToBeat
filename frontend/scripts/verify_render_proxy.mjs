// Real Uvicorn plus a synthetic TLS edge; never target a deployed service.
import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import http from 'node:http'
import https from 'node:https'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { chromium } from '@playwright/test'

const image = process.argv[2]
assert(image, 'Usage: node frontend/scripts/verify_render_proxy.mjs choplab:proxy-check')
const docker = (...args) => execFileSync('docker', args, { encoding: 'utf8', timeout: 180_000 }).trim()
const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
const credentials = { username: 'owner', password: 'synthetic-render-proxy-password' }
const authorization = 'Basic ' + Buffer.from(`${credentials.username}:${credentials.password}`).toString('base64')
const disposable = 'choplab-proxy-' + randomUUID()
const directory = mkdtempSync(join(tmpdir(), 'choplab-proxy-'))
let browser
let edge
let appStarted = false
let volumeCreated = false

function request(port, path, headers = {}, method = 'GET', body, localAddress = '127.0.0.1') {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1', port, path, method, headers, localAddress, timeout: 5000,
    }, response => {
      let text = ''
      response.setEncoding('utf8')
      response.on('data', chunk => { text += chunk })
      response.on('end', () => resolve({ status: response.statusCode, headers: response.headers, text }))
      response.on('error', reject)
    })
    req.on('timeout', () => req.destroy(new Error('Application request timed out')))
    req.on('error', reject)
    req.end(body)
  })
}

async function listen(server) {
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  return server.address().port
}

async function close(server) {
  if (server) {
    server.closeAllConnections()
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
  }
}

function noCookie(response) {
  assert.equal(response.headers['set-cookie'], undefined, 'Basic auth must not create a session cookie')
}

try {
  execFileSync('openssl', [
    'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
    '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
    '-keyout', join(directory, 'key'), '-out', join(directory, 'cert'),
  ], { stdio: 'ignore', timeout: 30_000 })
  const reservation = http.createServer()
  const appPort = await listen(reservation)
  await close(reservation)
  edge = https.createServer({
    key: readFileSync(join(directory, 'key')), cert: readFileSync(join(directory, 'cert')),
  }, (req, res) => {
    // Reproduce the measured Render behavior, not an assumed sanitizer:
    // overwrite scheme; preserve forwarded-host, Forwarded, and leftmost XFF.
    const headers = {
      ...req.headers,
      'x-forwarded-proto': 'https',
      'x-forwarded-for': req.headers['x-forwarded-for']
        ? `${req.headers['x-forwarded-for']}, 127.0.0.1` : '127.0.0.1',
    }
    const upstream = http.request({
      host: '127.0.0.1', port: appPort, path: req.url, method: req.method, headers,
    }, response => {
      res.writeHead(response.statusCode, response.headers)
      response.pipe(res)
    })
    upstream.on('error', error => {
      res.writeHead(502)
      res.end(error.message)
    })
    req.pipe(upstream)
  })
  const edgePort = await listen(edge)
  const origin = `https://localhost:${edgePort}`
  docker('volume', 'create', disposable)
  volumeCreated = true
  const setup = `
import hashlib, os, secrets
from pathlib import Path
salt = secrets.token_bytes(16)
digest = hashlib.pbkdf2_hmac("sha256", b"synthetic-render-proxy-password", salt, 600000)
path = Path("/fixtures/owner_password_hash")
path.write_text(f"pbkdf2_sha256$600000\${salt.hex()}\${digest.hex()}")
os.chown(path, 0, 1000)
os.chmod(path, 0o440)
`
  execFileSync('docker', [
    'run', '--rm', '-i', '--user', '0:0', '--entrypoint', 'python',
    '--mount', `type=volume,src=${disposable},dst=/fixtures`, image, '-',
  ], { input: setup, stdio: ['pipe', 'pipe', 'pipe'], timeout: 30_000 })
  docker(
    'run', '-d', '--name', disposable, '--network', 'host',
    '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges:true',
    '--tmpfs', '/tmp:uid=10001,gid=10001', '--tmpfs', '/data:uid=10001,gid=10001',
    '--mount', `type=volume,src=${disposable},dst=/etc/secrets,readonly`,
    '--env', 'CHOPLAB_MODE=hosted', '--env', `CHOPLAB_PUBLIC_URL=${origin}`,
    '--env', 'CHOPLAB_PASSWORD_HASH_FILE=/etc/secrets/owner_password_hash',
    '--env', 'CHOPLAB_HOST=127.0.0.1', '--env', `PORT=${appPort}`, image,
  )
  appStarted = true
  const deadline = Date.now() + 120_000
  while (true) {
    assert.equal(docker('inspect', '--format', '{{.State.Running}}', disposable), 'true',
      'Application exited before readiness')
    try {
      assert.equal((await request(appPort, '/api/health')).status, 200)
      break
    } catch (error) {
      if (Date.now() >= deadline) throw error
      await pause(1000)
    }
  }
  const renderHeaders = {
    host: `localhost:${edgePort}`,
    'x-forwarded-proto': 'https',
    'x-forwarded-host': 'attacker.example',
    forwarded: 'for=198.51.100.9;host=attacker.example;proto=http',
    'x-forwarded-for': '198.51.100.9, 127.0.0.1',
  }
  const check = async (label, path, headers, expected, method = 'GET', body, peer) => {
    const response = await request(appPort, path, headers, method, body, peer)
    assert.equal(response.status, expected, label)
    noCookie(response)
    if (expected === 401) {
      assert.equal(response.headers['www-authenticate'], 'Basic realm="ChopLab", charset="UTF-8"', label)
    }
    if ([400, 403].includes(expected)) assert.equal(response.headers['www-authenticate'], undefined, label)
    console.log(`PASS: ${label}: ${expected}`)
    return response
  }
  const health = await check('anonymous readiness through Render headers', '/api/health', renderHeaders, 200)
  assert.deepEqual(Object.keys(JSON.parse(health.text)).sort(), ['engine', 'ok', 'version'])
  for (const path of ['/', '/api/projects', '/docs', '/openapi.json',
    '/assets/nonexistent.js', '/api/sources/private/audio', '/api/exports/private/download',
    '/api/jobs/private/events']) {
    await check(`anonymous protected ${path}`, path, renderHeaders, 401)
  }
  await check('wrong credentials', '/api/projects', {
    ...renderHeaders, authorization: 'Basic ' + Buffer.from('owner:wrong').toString('base64'),
  }, 401)
  await check('invalid login retry rate limit', '/api/projects', {
    ...renderHeaders, authorization: 'Basic !invalid!',
  }, 429)
  await pause(1100)
  const authenticated = { ...renderHeaders, authorization }
  const projects = await check('legitimate owner with attacker-preserved headers', '/api/projects', authenticated, 200)
  assert.equal(projects.headers['cache-control'], 'private, no-store')
  assert.equal(projects.headers['x-frame-options'], 'DENY')
  assert.equal(projects.headers['x-content-type-options'], 'nosniff')
  const studio = await check('authenticated built SPA', '/', authenticated, 200)
  assert(studio.text.includes('<div id="root">'))
  await check('HTTP never challenges even with cached valid auth', '/', {
    ...authenticated, 'x-forwarded-proto': 'http',
  }, 400)
  await check('no proxy scheme cannot be rescued by Forwarded', '/', {
    host: `localhost:${edgePort}`, authorization,
    forwarded: 'for=127.0.0.1;host=localhost;proto=https',
  }, 400)
  await check('comma-appended scheme is not accepted as HTTPS', '/', {
    ...authenticated, 'x-forwarded-proto': 'http, https',
  }, 400)
  await check('untrusted actual peer cannot spoof HTTPS', '/', authenticated, 400, 'GET', undefined, '127.0.0.2')
  await check('spoofed Host not repaired by forwarded host', '/', {
    ...authenticated, host: 'attacker.example', 'x-forwarded-host': `localhost:${edgePort}`,
    forwarded: 'host=localhost;proto=https',
  }, 400)
  await check('spoofed Origin rejected despite valid Host', '/api/projects', {
    ...authenticated, origin: 'https://attacker.example',
  }, 403)
  await check('cross-site fetch rejected', '/api/projects', {
    ...authenticated, 'sec-fetch-site': 'cross-site',
  }, 403)
  const body = JSON.stringify({ name: 'Synthetic Render proxy regression' })
  const created = await check('legitimate same-origin write', '/api/projects', {
    ...authenticated, origin, 'content-type': 'application/json',
    'content-length': Buffer.byteLength(body),
  }, 201, 'POST', body)
  const project = JSON.parse(created.text)
  await check('synthetic project cleanup', `/api/projects/${project.id}`, {
    ...authenticated, origin,
  }, 204, 'DELETE')
  await check('cached owner never authorizes missing credentials', '/api/projects', renderHeaders, 401)
  await check('changed credentials cannot reuse accepted fingerprint', '/api/projects', {
    ...renderHeaders, authorization: 'Basic ' + Buffer.from('other:wrong').toString('base64'),
  }, 401)
  await pause(1100)

  browser = await chromium.launch()
  const context = await browser.newContext({ ignoreHTTPSErrors: true, httpCredentials: credentials })
  const page = await context.newPage()
  const response = await page.goto(origin)
  assert.equal(response.status(), 200)
  await page.locator('#root').waitFor()
  assert.equal(await page.evaluate(async () => (await fetch('/api/projects')).status), 200,
    'Browser must resend cached Basic credentials to same-origin fetches')
  assert.deepEqual(await context.cookies(), [], 'Basic auth must not introduce session cookies')
  const spoofed = await context.request.get(origin + '/api/projects', { headers: {
    'X-Forwarded-Proto': 'http', 'X-Forwarded-Host': 'attacker.example',
    Forwarded: 'for=198.51.100.9;host=attacker.example;proto=http',
    'X-Forwarded-For': '198.51.100.9',
  } })
  assert.equal(spoofed.status(), 200, 'Measured edge scheme overwrite retains legitimate HTTPS login')
  assert.equal(spoofed.headers()['set-cookie'], undefined)
  await context.close()
  const fresh = await browser.newContext({ ignoreHTTPSErrors: true })
  const anonymous = await fresh.request.get(origin + '/api/projects')
  assert.equal(anonymous.status(), 401, 'New browser context must not inherit Basic credentials')
  assert.equal(anonymous.headers()['www-authenticate'], 'Basic realm="ChopLab", charset="UTF-8"')
  assert.deepEqual(await fresh.cookies(), [])
  await fresh.close()
  console.log('PASS: Chromium owner navigation, same-origin fetch, edge scheme overwrite, fresh-context challenge, no cookies')
  console.log('LIMITATION: closing a context models credential isolation, not guaranteed browser-wide Basic-auth logout.')
  console.log('LIMITATION: synthetic TLS edge reproduces measured headers; live Render app/edge and native prompt UI are not tested.')
  console.log('Application proxy trust: default loopback only; no CHOPLAB_PROXY_IPS wildcard override.')
} finally {
  if (browser) await browser.close()
  if (appStarted) {
    const result = spawnSync('docker', ['logs', disposable], { encoding: 'utf8', timeout: 30_000 })
    docker('rm', '-f', disposable)
    assert.equal(result.status, 0, 'Unable to inspect application logs')
    const logs = result.stdout + result.stderr
    assert(!logs.includes(credentials.password), 'Application logs exposed a password')
    assert(!logs.includes('pbkdf2_sha256$'), 'Application logs exposed a password hash')
  }
  if (volumeCreated) docker('volume', 'rm', disposable)
  await close(edge)
  rmSync(directory, { recursive: true })
}
