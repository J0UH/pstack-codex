# Optional Grok Bot webhook bridge

`scripts/grok_bot.py` is the Codex host bridge for one step of the upstream `make-bot-ui` skill: "Host the page on this computer". It POSTs one JSON object from this machine to a Grok Bot webhook routine with the sender key held on this machine. It is optional. Nothing in the core Codex/Fable workflow imports it or depends on it.

## Two different Grok surfaces

- **Grok Build** is the coding CLI. `docs/grok.md` covers it as a supervised, disposable worker for direct coding and analysis. It has no routines, no webhooks and no sender keys.
- **Grok Bot** is the desktop app with a persistent cloud computer. Routines, the routine panel, the webhook URL, the sender key and the secure secret entry all live in that app. Work for the Bot is delegated through the app UI as app management, not as a Grok Build coding task.

No Codex-native Bot API is invented here. Codex has no `update_state`, no `SendToUser` secret card and no `[routine]` wake turn. The Bot-specific parts use the supported app UI, operated by the user or an authorized coordinator through its computer-use tools. A separate diagnostic Bot was reached this way, created a paused routine, and returned a verified screenshot of a public page on its cloud browser. This bridge implements only the documented outbound POST.

## What the upstream skill requires and where each part lives

| Upstream step | Where it happens under Codex |
| --- | --- |
| Create the webhook routine with `update_state` | In the Grok Bot app. The routine prompt must treat the POST body as untrusted data. |
| Copy the URL from the routine panel | The user copies it from the panel and may paste it in chat or into the config. |
| Request the sender key with a `secret-request` card | In the Grok Bot app's native secure secret request. That UI currently asks for `label` and `name`, not the upstream `connector` and `field`. The user enters the key there. Never ask for the key in chat, and never accept it in chat if offered. |
| Store `{url, key}` on the server | `url` goes in the config file. The key goes in a 0600 file or an environment variable that only the sender process sees. The config never contains the key. |
| POST with the documented headers, 8 s, one try | `scripts/grok_bot.py send` or `send_event`. |
| Probe once with a harmless payload before saying the UI is live | `scripts/grok_bot.py probe`. |
| Append failed JSON to a local log; drain it from the routine | The bridge appends to a local 0600 file. Draining is not provided (see below). |
| Tailscale, page hosting, wake handling | Outside this bridge. |

## Current evidence

The parent session has live UI proof that a webhook routine was created in the Grok Bot app and left paused. The sender key was not obtained, and no webhook was fired. Direct app delegation also produced an observed screenshot of `example.com` on the Bot computer. Therefore:

- No real POST has been made to `api2.cursor.sh` by this code. All transport evidence comes from an injected opener and a loopback HTTP server on 127.0.0.1.
- Whether the live routine returns exactly HTTP 200 on wake, and what its response looks like, is unobserved.
- The end-to-end workflow (page click, local POST, routine wake, bot action) remains unverified. Do not describe it as working.

There are still blockers before the full skill can be claimed: obtaining the key through the app's secure entry, an accepted live probe, and a failure-queue bridge the routine can actually reach.

## Config

One JSON object. Unknown keys are rejected, including `expected_host`.

```json
{
  "url": "https://api2.cursor.sh/automations/webhook/<id-from-the-routine-panel>",
  "key_file": "/absolute/path/to/sender.key",
  "queue_path": "/absolute/path/to/failed-webhook-events.jsonl",
  "probe_payload": {"action": "probe"}
}
```

- `url` is required. Only `https://api2.cursor.sh/automations/webhook/<id>` is accepted: exact host, no port, no userinfo, no query, no fragment, no extra path. There is no host override in the config, the API or the CLI.
- Exactly one of `key_file` or `key_env`. `key_file` is an absolute path to a regular file owned by the current user with mode 0600 containing one line of printable ASCII. `key_env` names an environment variable of the sender process.
- `queue_path` defaults to `failed-webhook-events.jsonl` next to the config file. Its directory must already exist. When the config is passed as a dict instead of a file, `queue_path` is required.
- `probe_payload` must be explicit before using `probe`. Choose an action that the actual routine prompt is known to ignore; no universally harmless action is invented. Normal sends do not require this field.
- `queue_path`, `key_file` and the config file must be three different files. This is checked by path when the config is loaded and by inode when files are opened.

## CLI

```text
python3 scripts/grok_bot.py check --config /abs/bot.json
python3 scripts/grok_bot.py probe --config /abs/bot.json
python3 scripts/grok_bot.py send  --config /abs/bot.json --payload-file /abs/event.json
python3 scripts/grok_bot.py send  --config /abs/bot.json --stdin
```

`check` makes no network call. It validates the config and URL, confirms the key is readable without printing it, and inspects the failure queue. `probe` and `send` print one JSON result. The key and the URL are never accepted as arguments, and unknown flags are reported by name without echoing their values.

Exit codes: 0 accepted, 1 rejected/redirect/network/internal, 2 invalid config, payload, queue or unavailable key, 124 timeout.

## What a send does, in order

1. Re-validate the config at the send boundary, including a caller-supplied dict, against the documented host. Anything else stops here with `invalid_config` before the key is read.
2. Validate and encode the payload: one non-empty JSON object, JSON-native values only, no bytes, at most 64 KiB, nesting at most 16 deep.
3. Open the failure queue for append with `O_NOFOLLOW` and `O_NONBLOCK`, creating it 0600 if missing, and check the open descriptor: regular file, owned by the current user, no group/other bits, not the config file. An existing 0644 queue, a symlink, a directory or a missing directory stops here with `invalid_queue`. Nothing is chmodded, truncated or created except a missing queue file.
4. Read the key. `key_file` is opened with `O_NOFOLLOW` and checked on the same descriptor it is read from. A key file that is the same inode as the queue or the config stops the send.
5. Refuse a payload that contains the key. Dict keys and string values are inspected before JSON escaping, and the encoded bytes are checked too. Such an event is neither sent nor queued.
6. POST once with `Content-Type: application/json`, `Authorization: Bearer <key>`, `X-Automation-Key: <key>`, an 8 s socket timeout, TLS verification, no proxy and every redirect refused.
7. Accept exactly HTTP 200. Any other status, including 201 or 204, is `rejected` and unconfirmed. The response body and headers are never read or recorded.
8. On `rejected`, `redirect_refused`, `network_error`, `timeout` or `secret_unavailable`, append the exact encoded event bytes as one line to the queue. Probe payloads are never queued.

Failure messages are fixed phrases plus an exception class name. No transport exception text, response body, response header or key fragment reaches the result, stdout or stderr. A final pass scrubs the result if the key were ever present, which the tests treat as an internal error.

## Result fields

`status`, `exit_code`, `probe`, `url`, `routine_id`, `host_policy` (always `documented_default`), `method`, `headers_sent` (names only), `timeout_seconds`, `timeout_note`, `attempts`, `retry` (always false), `redirects_followed` (always false), `body_bytes`, `body_sha256`, `http_status`, `http_accepted`, `bot_completion_verified` (always false), `completion_note`, `secret_source`, `queued`, `queue_path`, `errors`, `warnings`, `started_at`, `ended_at`, `elapsed_seconds`.

`http_accepted` means the routine was woken. It says nothing about what the bot did afterwards. That is observed in the Grok Bot app.

## Failure queue and the drain gap

The queue is one encoded event per line, the same JSON that was POSTed, in a 0600 file on this Mac. That satisfies "append the same JSON to a local log".

The upstream skill then says to drain that log from the routine. The routine runs on the Bot's cloud computer, which cannot read a file on this Mac. Nothing here gives it that access. Before the full workflow can be claimed, an explicit and accessible failure bridge is needed, for example a tailnet-reachable endpoint on this machine that the routine can call, with its own authorization. That bridge is not designed or built here, and this bridge never retries on its own.

When the key is unavailable the event is still queued, because there is no key to compare it against. Keep the payload free of secrets by construction.

## Check report and doctor contract

`check_config` (and the `check` CLI) returns: `schema` (`pstack-codex/grok-bot-check/1`), `config_path`, `config_valid`, `url`, `routine_id`, `host_policy` (`documented_default` once the config is valid), `secret_source`, `secret_available`, `queue_path`, `queue_usable`, `queue_entries`, `network_called` (always false), `errors`, `warnings`. `queue_usable` is new. Readiness means `config_valid`, `secret_available` and `queue_usable` are all true; the CLI exits 2 otherwise.

The `doctor.py` in this checkout does not import this module. Its Grok Bot component reports the app bundle only and points at the `check` command as a separate explicit action. Any future doctor integration should read the keys above and treat `queue_usable` as part of readiness.

Removed from the previous draft: `expected_host`, the `queue` subcommand, the queue record wrapper (`schema`, `attempt_id`, status, URL), `attempt_id`, `response_body_bytes` and `response_body_sha256`. The send result keeps `schema`, `status`, `http_status`, `http_accepted` and `probe` unchanged.

## Limits

- The 8 s value is urllib's socket timeout. It bounds the connect and each read separately. DNS resolution is not covered, and it is not a hard whole-attempt deadline. `elapsed_seconds` reports what actually happened.
- Ownership checks refuse files owned by other users, but that path could not be exercised in tests without root. Directory ownership is not checked; the queue's directory is the config author's decision. If another user controls that directory they can deny service, but the descriptor checks and inode comparison still prevent writing into a symlink target, a foreign file, the key file or the config.
- POSIX only (`O_NOFOLLOW`, `getuid`).
- No test proves anything about the live routine. See "Current evidence".

## Tests

```text
python3 -m unittest discover -s tests -p test_grok_bot.py -v
```

Regression classes reproduce the review findings against synthetic keys and an injected opener: host override, queue handling (0644, symlink, hardlink, directory, missing parent, short writes), key-bearing payloads, error-message leakage with short, long and quote-containing keys, and non-200 acceptance. A loopback server verifies real header delivery, redirect refusal, timeout classification and that the response body is not awaited. Synthetic keys are chosen so that no 8-character window of a key appears in any other fixture text, which lets the tests fail on a leaked fragment rather than only on the whole value.

## Optional cloud-computer handoff

Use Grok Bot only when the task benefits from its persistent cloud computer or needs Bot-native facilities. Pass a bounded, authorized task and verify the returned artifact. Local Codex paths, browser sessions and credentials are not automatically present on that computer. Do not infer the Bot's selected model or count it as an exact-model reviewer without independent evidence. Bots in one account share a cloud computer, so separate Bots are not substitutes for per-lane isolated VMs. [Grok Bot overview](https://docs.x.ai/grok-bot/overview).

A Bot-native workflow can run its page/server and failure queue on that computer under the original skill. A sender hosted on the local Mac needs a separately verified way for the routine to reach its failure queue; this helper does not silently claim that bridge exists.
