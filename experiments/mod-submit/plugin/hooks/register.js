let seq = 0

async function record($, dir, fields) {
  seq += 1
  const t = await $.clock.now()
  const name = dir + "/mod-events/" + String(t) + "-" + String(seq).padStart(4, "0") + ".json"
  await $.fs.write(name, JSON.stringify({ t: t / 1000, seq, ...fields }))
}

async function submit($, dir, text) {
  await record($, dir, { event: "submit.called", text })
  try {
    const result = await $.prompt.submit({ text, asUser: true })
    await record($, dir, { event: "submit.resolved", text, dropped: Boolean(result.drop) })
  } catch (err) {
    await record($, dir, { event: "submit.rejected", text, error: String(err) })
  }
}

async function listen($, dir) {
  const child = $.process.spawn({
    argv: ["/bin/sh", "-c", 'echo "$PPID $$" > "$1/listener.pid"; exec /usr/bin/tail -n 0 -F "$1/inbox.txt"', "listener", dir],
  })
  let pending = ""
  try {
    for await (const chunk of child) {
      if (chunk.stream !== "stdout") continue
      pending += chunk.text
      let cut = pending.indexOf("\n")
      while (cut >= 0) {
        const line = pending.slice(0, cut).trim()
        pending = pending.slice(cut + 1)
        if (line) void submit($, dir, line)
        cut = pending.indexOf("\n")
      }
    }
    await record($, dir, { event: "listener.ended" })
  } catch (err) {
    await record($, dir, { event: "listener.failed", error: String(err) })
  }
}

async function claimListener($, dir, sessionId) {
  const claim = dir + "/listener.session"
  if (await $.fs.exists(claim)) {
    const owner = (await $.fs.read(claim)).trim()
    if (owner !== sessionId) return owner
  }
  await $.fs.write(claim, sessionId)
  return null
}

export function register(on) {
  on("session.start", async ($, e, next) => {
    const started = await next(e)
    const dir = await $.env.get("FUNMEDAL_MODTEST_DIR")
    if (!dir) return started
    const session = {
      session_id: await $.session.id(),
      cwd: await $.session.cwd(),
      surfaces: await $.session.surfaces(),
    }
    const owner = await claimListener($, dir, session.session_id)
    if (owner) {
      await record($, dir, { event: "mod.start.skipped", owner, ...session })
      return started
    }
    await record($, dir, { event: "mod.start", ...session })
    void listen($, dir)
    return started
  })

  on("turn.start", async ($, e, next) => {
    const dir = await $.env.get("FUNMEDAL_MODTEST_DIR")
    if (dir) await record($, dir, { event: "turn.start", text: e.text, turn_id: e.turnId })
    return next(e)
  })

  on("turn.complete", async ($, e, next) => {
    const dir = await $.env.get("FUNMEDAL_MODTEST_DIR")
    if (dir) await record($, dir, { event: "turn.complete", reason: e.reason, aborted: e.isAborted })
    return next(e)
  })

  on("tool.check", async ($, e, next) => {
    const result = await next(e)
    const dir = await $.env.get("FUNMEDAL_MODTEST_DIR")
    if (dir) await record($, dir, { event: "tool.check", tool: e.tool, decision: result.decision })
    return result
  })
}
