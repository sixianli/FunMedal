const BRIDGE = "/Applications/FunMedalBridge.app/Contents/MacOS/funmedal-bridge"
const DROPS = [
  ["the prompt was removed from the queue", "cleared"],
  ["the session was stopped", "stopped"],
]
let listening = false

function outcome(entered) {
  if (!entered.drop) return "submitted"
  const known = DROPS.find(([words]) => entered.drop.includes(words))
  return known ? known[1] : "refused"
}

async function deliver($, bridge, line) {
  let heard
  try {
    heard = JSON.parse(line)
  } catch {
    return
  }
  if (typeof heard.say !== "string" || !Number.isInteger(heard.clip)) return
  let result = "refused"
  try {
    result = outcome(await $.prompt.submit({ text: heard.say, asUser: true }))
  } catch {}
  try {
    await $.process.run([bridge, "said", "--clip", String(heard.clip), "--result", result], { timeoutMs: 5000 })
  } catch {}
}

async function listen($, bridge) {
  let pending = ""
  for await (const piece of $.process.spawn({ argv: [bridge, "listen"] })) {
    if (piece.stream !== "stdout") continue
    pending += piece.text
    let cut = pending.indexOf("\n")
    while (cut >= 0) {
      void deliver($, bridge, pending.slice(0, cut))
      pending = pending.slice(cut + 1)
      cut = pending.indexOf("\n")
    }
  }
}

export function register(on) {
  on("session.start", async ($, e, next) => {
    const started = await next(e)
    if (!listening) {
      listening = true
      const bridge = (await $.env.get("HOME")) + BRIDGE
      void listen($, bridge)
        .catch(() => {})
        .finally(() => {
          listening = false
        })
    }
    return started
  })
}
