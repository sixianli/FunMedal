let seq = 0
let mine = null

async function config($) {
  const cfg = JSON.parse(await $.fs.read($.plugin.root + "/check.json"))
  const phase = (await $.fs.read(cfg.base + "/phase.txt")).trim()
  return { ...cfg, out: cfg.base + "/" + phase }
}

async function record($, out, fields) {
  seq += 1
  const t = await $.clock.now()
  const name = out + "/mod-events/" + String(t) + "-" + String(seq).padStart(4, "0") + ".json"
  await $.fs.write(name, JSON.stringify({ t: t / 1000, seq, ...fields }))
}

function parseStatus(stdout) {
  try {
    const s = JSON.parse(stdout)
    return { ok: Boolean(s.ok), pid: s.pid ?? null, card: s.card ?? null, authorization: s.authorization ?? null }
  } catch {
    return { ok: false, text: stdout.slice(0, 200) }
  }
}

async function status($, cfg) {
  const r = await $.process.run([cfg.bridge, "status"], { timeoutMs: 5000 })
  return { exitCode: r.exitCode, ...parseStatus(r.stdout), stderr: r.stderr.slice(0, 200) }
}

async function owner($, out) {
  const file = out + "/owner.json"
  for (let i = 0; i < 30; i++) {
    if (await $.fs.exists(file)) {
      try {
        return JSON.parse(await $.fs.read(file)).session_id
      } catch {}
    }
    await $.clock.sleep(1000)
  }
  return null
}

async function spawnedParent($) {
  const child = $.process.spawn({ argv: ["/bin/sh", "-c", 'echo "$PPID"'] })
  let text = ""
  for await (const chunk of child) if (chunk.stream === "stdout") text += chunk.text
  return Number(text.trim()) || null
}

async function spawnedStatus($, cfg) {
  const child = $.process.spawn({ argv: [cfg.bridge, "status"] })
  let text = ""
  for await (const chunk of child) if (chunk.stream === "stdout") text += chunk.text
  return parseStatus(text)
}

async function checks($, cfg, session) {
  const out = cfg.out
  await record($, out, { event: "mod.start", ...session })
  await record($, out, { event: "spawn.parent", claude_pid: await spawnedParent($) })
  const before = await status($, cfg)
  await record($, out, { event: "run.status.before", ...before })
  if (!before.ok) {
    const t0 = await $.clock.now()
    const opened = await $.process.run(["/usr/bin/open", "-g", cfg.app, "--args", "serve"], { timeoutMs: 10000 })
    await record($, out, { event: "open.called", exitCode: opened.exitCode, stderr: opened.stderr.slice(0, 300) })
    let after = null
    for (let i = 0; i < 20; i++) {
      await $.clock.sleep(1000)
      after = await status($, cfg)
      if (after.ok) break
    }
    await record($, out, { event: "run.status.after_open", seconds: ((await $.clock.now()) - t0) / 1000, ...after })
  }
  await record($, out, { event: "spawn.status", ...(await spawnedStatus($, cfg)) })
  let card = null
  for (let i = 0; i < 30; i++) {
    card = await status($, cfg)
    if (card.card === "connected") break
    await $.clock.sleep(1000)
  }
  await record($, out, { event: "card.status", ...card })
  await record($, out, { event: "checks.done" })
}

export function register(on) {
  on("session.start", async ($, e, next) => {
    const started = await next(e)
    let cfg = null
    try {
      cfg = await config($)
    } catch {
      return started
    }
    void (async () => {
      const id = await $.session.id()
      const claimed = await owner($, cfg.out)
      if (claimed !== id) return
      mine = cfg
      const session = { session_id: id, version: await $.session.version(), surfaces: await $.session.surfaces() }
      try {
        await checks($, cfg, session)
      } catch (err) {
        await record($, cfg.out, { event: "checks.failed", error: String(err) })
      }
    })()
    return started
  })

  on("session.attach", async ($, e, next) => {
    if (mine) await record($, mine.out, { event: "session.attach", surfaces: await $.session.surfaces() })
    return next(e)
  })

  on("turn.complete", async ($, e, next) => {
    if (mine) await record($, mine.out, { event: "turn.complete", reason: e.reason, aborted: e.isAborted })
    return next(e)
  })

  on("tool.check", async ($, e, next) => {
    const result = await next(e)
    if (mine) {
      try {
        await record($, mine.out, { event: "tool.check", tool: e.tool, decision: result.decision })
      } catch {}
    }
    return result
  })
}
