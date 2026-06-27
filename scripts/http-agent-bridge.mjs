#!/usr/bin/env node
// http-agent-bridge.mjs — thin HTTP bridge between the Trader Intel web app
// (hosted on Cloudflare Pages) and the local OpenClaw gateway.
//
// Cloudflare can't reach 127.0.0.1:18789, so this sits at a publicly-exposed
// Tailscale Funnel URL and wraps the `openclaw agent` CLI.
//
// Handles two routes:
//   POST /api/trader-run  — fire-and-forget agent/pipeline run (Run button)
//   POST /api/trader-chat — run an agent turn and stream response as SSE
//
// Usage:
//   node ~/.openclaw/scripts/http-agent-bridge.mjs
//   PORT=18790 node ~/.openclaw/scripts/http-agent-bridge.mjs
//
// Then expose via Tailscale (see README in this file's header comments).

import http from 'node:http'
import { spawn } from 'node:child_process'
import { readFileSync } from 'node:fs'

const PORT   = Number(process.env.PORT || 18790)
const TOKEN  = (process.env.BRIDGE_TOKEN || readFileSync('/home/aaron/.openclaw/credentials/gateway-token', 'utf8')).trim()
const OPENCLAW = process.env.OPENCLAW_BIN || 'openclaw'

const AGENTS = ['overseer','researcher','quant','critic','trader','risk','executor','archivist','developer','dwight','montra-pm']

// --- auth ---
function authed(req) {
  const h = req.headers['authorization'] || ''
  return h.replace(/^Bearer\s+/i, '') === TOKEN
}

// --- read POST body ---
async function body(req) {
  let raw = ''
  for await (const c of req) raw += c
  try { return JSON.parse(raw || '{}') } catch { return {} }
}

// --- SSE helpers ---
function sseHeaders() {
  return { 'content-type': 'text/event-stream; charset=utf-8', 'cache-control': 'no-store', connection: 'keep-alive', 'access-control-allow-origin': '*' }
}
const frame = (res, obj) => { try { res.write(`data: ${JSON.stringify(obj)}\n\n`) } catch {} }

// --- run openclaw agent, stream SSE ---
function runAgentSSE(res, { agent, message, sessionId }) {
  frame(res, { type: 'thinking', agent, text: 'Sending to agent…' })

  const args = ['agent', '--agent', agent, '-m', message]
  if (sessionId) args.push('--session-id', sessionId)

  const proc = spawn(OPENCLAW, args, {
    env: { ...process.env, PATH: `/usr/local/bin:/usr/bin:/bin:${process.env.PATH || ''}` },
  })

  let out = '', err = ''
  proc.stdout.on('data', c => { out += c })
  proc.stderr.on('data', c => { err += c })

  proc.on('close', code => {
    const text = out.trim()
    if (code !== 0 || !text) {
      frame(res, { type: 'error', message: err.trim() || `Agent exited with code ${code}` })
    } else {
      frame(res, { type: 'message', agent, role: 'agent', text, ts: new Date().toISOString() })
    }
    frame(res, { type: 'done' })
    try { res.end() } catch {}
  })

  proc.on('error', e => {
    frame(res, { type: 'error', message: `Failed to start agent: ${e.message}` })
    frame(res, { type: 'done' })
    try { res.end() } catch {}
  })

  return proc
}

// --- HTTP server ---
const srv = http.createServer(async (req, res) => {
  res.setHeader('access-control-allow-origin', '*')
  res.setHeader('access-control-allow-methods', 'POST, OPTIONS')
  res.setHeader('access-control-allow-headers', 'content-type, authorization, accept')

  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return }

  if (!authed(req)) {
    res.writeHead(401, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ ok: false, error: 'Unauthorized' }))
    return
  }

  const rawUrl = req.url || '/'
  const url = rawUrl.split('?')[0]
  console.log(`[bridge] ${req.method} ${url}`)

  if (req.method !== 'POST') {
    res.writeHead(404, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ ok: false, error: 'Not found' }))
    return
  }

  const data = await body(req)

  // Tailscale Funnel strips the path prefix, so both /api/trader-run and
  // /api/trader-chat arrive here as POST /. Route on body content:
  //   message present  → chat (SSE streaming)
  //   no message       → run (fire-and-forget)
  const isChat = typeof data.message === 'string' && data.message.trim().length > 0

  // --- /api/trader-run (fire-and-forget, Run button) ---
  if (!isChat && (url === '/api/trader-run' || url === '/trader-run' || url === '/')) {
    const target = AGENTS.includes(String(data.target || '').trim().toLowerCase()) ? String(data.target || '').trim().toLowerCase() : null
    const agent  = (!target || target === 'run_all') ? 'overseer' : target
    const msg    = (!target || target === 'run_all')
      ? '[PIPELINE-TRIGGER] Run the full trading pipeline now. Execute the deterministic core (trader-pass-deterministic.sh), inventory the DB, and complete all mandatory steps per your system prompt. Narrate the results to Telegram when done.'
      : `[PIPELINE-TRIGGER] Run your agent pass now. Complete all steps in your system prompt and report back.`

    // Detach — caller gets an immediate ack, agent runs in background.
    const proc = spawn(OPENCLAW, ['agent', '--agent', agent, '-m', msg, '--session-id', `web-run-${Date.now()}`], {
      detached: true, stdio: 'ignore',
      env: { ...process.env, PATH: `/usr/local/bin:/usr/bin:/bin:${process.env.PATH || ''}` },
    })
    proc.unref()

    res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' })
    res.end(JSON.stringify({ ok: true, target: data.target || 'run_all', dispatched_agent: agent }))
    return
  }

  // --- /api/trader-chat (streaming SSE) ---
  if (isChat) {
    const target  = String(data.target || '').trim().toLowerCase()
    const agent   = AGENTS.includes(target) ? target : 'overseer'
    const message = String(data.message || '').trim().slice(0, 8000)
    const session = String(data.session_id || '').replace(/[^a-zA-Z0-9-_]/g, '').slice(0, 64) || `web-${Date.now()}`

    if (!message) {
      res.writeHead(400, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ ok: false, error: 'Empty message' }))
      return
    }

    res.writeHead(200, sseHeaders())
    const proc = runAgentSSE(res, { agent, message, sessionId: session })

    // Kill agent if client disconnects
    req.on('close', () => { try { proc.kill() } catch {} })
    return
  }

  res.writeHead(400, { 'content-type': 'application/json' })
  res.end(JSON.stringify({ ok: false, error: 'Unknown request — expected message (chat) or target (run)' }))
})

srv.listen(PORT, () => {
  console.log(`[bridge] listening on http://127.0.0.1:${PORT}`)
  console.log(`[bridge] POST /api/trader-run  — fire-and-forget run`)
  console.log(`[bridge] POST /api/trader-chat — SSE agent chat`)
})

process.on('SIGTERM', () => srv.close())
process.on('SIGINT',  () => { srv.close(); process.exit(0) })
