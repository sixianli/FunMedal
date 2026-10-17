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

export function register(on) {
  on("session.start", async ($, e, next) => {
    const started = await next(e)
    const dir = await $.env.get("FUNMEDAL_MODTEST_DIR")
    if (!dir) return started
    await record($, dir, { event: "mod.start" })
    void listen($, dir)
    return started
  })
}
