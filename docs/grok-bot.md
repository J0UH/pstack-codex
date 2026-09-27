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
| Append failed JSON to a local log; drain it from the routine | The sender appends to a local 0600 file. `scripts/grok_failure_bridge.py` lets the routine claim and acknowledge those entries over a separately authenticated endpoint (see below). |
| Tailscale, page hosting, wake handling | Outside this bridge. |

## Current evidence

The initial September 18 checkpoint proved app handoff and paused-routine creation only. The September 27 [host-bridge acceptance record](../evidence/host-bridge-acceptance.json) adds real webhook and queue access:

- The existing diagnostic routine received a real POST with HTTP 200 and returned the exact harmless probe nonce in the Bot UI.
- A second webhook woke a cloud routine that claimed two synthetic failed events from this Mac through a temporary authenticated HTTPS endpoint, acknowledged both, and confirmed the next claim was empty. Server request logs and SQLite receipts independently recorded all four operations. The original JSONL remained unchanged.
- The routine did not post its own completion reply for the queue run; its later report confirmed the counts and empty final claim. This is proof of the bounded queue exercise, not arbitrary Bot task completion or a full page-click workflow.
- The original routine instruction was restored exactly and left paused. Temporary services were stopped and the local copy of the sender key was removed. No real credential value is in the published evidence.

Each installation still needs its own sender key, routine, consumer token and reachable TLS front end. Those are configuration prerequisites; the sender and queue bridge are now implemented and live-tested within the scope above.

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
- `queue_path` defaults to `failed-webhook-events.jsonl` next to the config file. Its directory must already exist, be owned by the sender user and have no group/other write bits. When the config is passed as a dict instead of a file, `queue_path` is required.
- `probe_payload` must be explicit before using `probe`. Choose an action that the actual routine prompt is known to ignore; no universally harmless action is invented. Normal sends do not require this field.
- `queue_path`, `key_file` and the config file must be three different files. This is checked by path when the config is loaded and by inode when files are opened.

## CLI

```text
python3 scripts/grok_bot.py check --config /abs/bot.json
python3 scripts/grok_bot.py probe --config /abs/bot.json
python3 scripts/grok_bot.py send  --config /abs/bot.json --payload-file /abs/event.json
python3 scripts/grok_bot.py send  --config /abs/bot.json --stdin
```

`check` makes no network call. It validates the config and URL, confirms the key is readable without printing it, and inspects the failure queue. `probe` and `send` print one JSON result. The key and the URL are never accepted as arguments. Unknown arguments and invalid choices produce fixed errors without echoing their values.

Exit codes: 0 accepted, 1 rejected/redirect/network/internal, 2 invalid config, payload, queue or unavailable key, 124 timeout.

## What a send does, in order

1. Re-validate the config at the send boundary, including a caller-supplied dict, against the documented host. Anything else stops here with `invalid_config` before the key is read.
2. Validate and encode the payload: one non-empty JSON object, JSON-native values only, no bytes, at most 64 KiB, nesting at most 16 deep.
3. Open the failure queue for read and append with `O_NOFOLLOW` and `O_NONBLOCK`, creating it 0600 if missing, and check the open descriptor: regular file, owned by the current user, no group/other bits, not the config file. An existing 0644 queue, a symlink, a directory or a missing directory stops here with `invalid_queue`. Nothing is chmodded, truncated or created except a missing queue file.
4. Read the key. `key_file` is opened with `O_NOFOLLOW` and checked on the same descriptor it is read from. A key file that is the same inode as the queue or the config stops the send.
5. Refuse a payload that contains the key. Dict keys and string values are inspected before JSON escaping, and the encoded bytes are checked too. Such an event is neither sent nor queued.
6. POST once with `Content-Type: application/json`, `Authorization: Bearer <key>`, `X-Automation-Key: <key>`, an 8 s socket timeout, TLS verification, no proxy and every redirect refused.
7. Accept exactly HTTP 200. Any other status, including 201 or 204, is `rejected` and unconfirmed. The response body and headers are never read or recorded.
8. On `rejected`, `redirect_refused`, `network_error`, `timeout` or `secret_unavailable`, append the exact encoded event bytes as one line to the queue. Probe payloads are never queued.

   The append holds an exclusive `flock` on the queue from the first byte through `fsync`, waiting at most 10 s for another sender (otherwise `queue_append_failed`). Senders that use this module therefore never interleave records, even when the kernel accepts a record in several short writes. Writers that do not take the lock, such as another program appending to the same file, are not excluded. If the file does not end in a newline because a writer died mid-record, the append first writes one newline. The fragment then stays a single malformed line and the new record stays whole. The record's own bytes never change. On macOS, if two sends race to create a missing queue file, the one that loses retries the same checked open once instead of refusing the send.

Failure messages are fixed phrases plus an exception class name. No transport exception text, response body, response header or key fragment reaches the result, stdout or stderr. A final pass scrubs the result if the key were ever present, which the tests treat as an internal error.

## Result fields

`status`, `exit_code`, `probe`, `url`, `routine_id`, `host_policy` (always `documented_default`), `method`, `headers_sent` (names only), `timeout_seconds`, `timeout_note`, `attempts`, `retry` (always false), `redirects_followed` (always false), `body_bytes`, `body_sha256`, `http_status`, `http_accepted`, `bot_completion_verified` (always false), `completion_note`, `secret_source`, `queued`, `queue_path`, `errors`, `warnings`, `started_at`, `ended_at`, `elapsed_seconds`.

`http_accepted` means the routine was woken. It says nothing about what the bot did afterwards. That is observed in the Grok Bot app.

## Failure queue and the failure bridge

The queue is one encoded event per line, the same JSON that was POSTed, in a 0600 file on this Mac. That satisfies "append the same JSON to a local log". The sender never retries on its own, and nothing below changes the sender.

The upstream skill then says to drain that log from the routine. The routine runs on the Bot's cloud computer, which cannot read a file on this Mac. `scripts/grok_failure_bridge.py` is the opt-in bridge for that step. Status: implemented, regression-tested and exercised from a real cloud routine with synthetic queued events; see the acceptance record above.

- **Endpoints.** `POST /v1/failures/claim` with `{"limit": n, "lease_seconds": s}` leases up to `n` entries and returns each as `{entry_id, claim_id, claim_count, body_sha256, event}`, where `event` is the exact JSON object from the log line and `body_sha256` hashes that line. `POST /v1/failures/ack` with `{"acks": [{"entry_id", "claim_id"}]}` marks entries handled. `POST /v1/failures/release` with `{"releases": [...]}` returns them early. `GET /v1/failures/status` reports counts.
- **Non-lossy.** The bridge opens the log read-only with the sender's own `open_queue` checks, never truncates, rewrites or deletes it, and imports only complete newline-terminated lines into its private SQLite state. A partial line that the sender is still writing waits for its newline. Malformed lines, including lines nested deeper than the sender's 16-level limit, are counted and never served; they stay in the log. A shrunken or rewritten log is refused rather than guessed.
- **Claim is not delete.** A claim is a lease. An entry that is not acknowledged returns after its lease with a new `claim_id` and a higher `claim_count`, so delivery to the routine is at least once. An ack with an old `claim_id` is `stale_claim`. The routine should treat `entry_id` or `body_sha256` as its idempotency key.
- **Ack is not Bot completion.** An ack is the routine's own statement that it handled the entry. Every ack and status response carries `bot_completion_verified: false`. HTTP 200 from the bridge means the request was processed, nothing more.
- **Separate credential.** The routine authenticates with a consumer bearer token from its own 0600 file, created with `new-token`. It is refused if it is the sender key file, the queue, the sender config or the bridge config, by path or by inode. The bridge never reads the sender key. The event bridge's ingest token is a third, unrelated credential.
- **Reachability.** The server binds only `127.0.0.1` or `::1`. The routine reaches it only through an operator-configured TLS reverse proxy or tailnet front end, for example Tailscale Serve forwarding HTTPS to the loopback port. The routine stores the consumer token in the Bot's own secure secret entry, never in chat or in the routine prompt.

Config ([example](../examples/grok-failure-bridge.example.json)):

```json
{
  "sender_config": "/absolute/path/to/bot.json",
  "state_dir": "/absolute/private/failure-bridge-state",
  "listen_host": "127.0.0.1",
  "listen_port": 8788,
  "consumer_token_file": "/absolute/private/failure-consumer.token",
  "default_lease_seconds": 300,
  "max_lease_seconds": 3600,
  "max_claim": 20
}
```

```text
python3 scripts/grok_failure_bridge.py new-token --path /abs/failure-consumer.token
python3 scripts/grok_failure_bridge.py check  --config /abs/failures.json
python3 scripts/grok_failure_bridge.py serve  --config /abs/failures.json
python3 scripts/grok_failure_bridge.py status --config /abs/failures.json
```

`check` is offline and never reads the sender key. `serve` runs in the foreground until `SIGTERM`, `SIGINT` or `SIGHUP`. `state_dir` must be a 0700 directory owned by the operator.

A routine that drains the queue calls claim, handles each `event` as untrusted data under its own prompt, acks what it finished, and releases or leaves the rest to expire. This routine-side behavior is a suggested contract; it has not run on a real Bot.

When the key is unavailable the event is still queued, because there is no key to compare it against. Keep the payload free of secrets by construction.

## Check report and doctor contract

`check_config` (and the `check` CLI) returns: `schema` (`pstack-codex/grok-bot-check/1`), `config_path`, `config_valid`, `url`, `routine_id`, `host_policy` (`documented_default` once the config is valid), `secret_source`, `secret_available`, `queue_path`, `queue_usable`, `queue_entries`, `network_called` (always false), `errors`, `warnings`. `queue_usable` is new. Readiness means `config_valid`, `secret_available` and `queue_usable` are all true; the CLI exits 2 otherwise.

The `doctor.py` in this checkout does not import this module. Its Grok Bot component reports the app bundle only and points at the `check` command as a separate explicit action. Any future doctor integration should read the keys above and treat `queue_usable` as part of readiness.

Removed from the previous draft: `expected_host`, the `queue` subcommand, the queue record wrapper (`schema`, `attempt_id`, status, URL), `attempt_id`, `response_body_bytes` and `response_body_sha256`. The send result keeps `schema`, `status`, `http_status`, `http_accepted` and `probe` unchanged.

## Limits

- The 8 s value is urllib's socket timeout. It bounds the connect and each read separately. DNS resolution is not covered, and it is not a hard whole-attempt deadline. `elapsed_seconds` reports what actually happened.
- Ownership checks refuse files owned by other users, but that path could not be exercised in tests without root. The queue directory is required to be owned by the sender and not writable by group or others and held open while the queue is opened relative to its descriptor. These checks do not provide containment against a privileged process or other code running as the same user.
- POSIX only (`O_NOFOLLOW`, `getuid`).
- No test proves anything about the live routine. See "Current evidence".

## Tests

```text
python3 -m unittest discover -s tests -p test_grok_bot.py -v
python3 -m unittest tests.test_grok_failure_bridge -v
```

The failure-bridge suite produces real queue lines through `send_event` with a failing injected opener, then checks exact-JSON claims, an unchanged log, lease expiry and stale acks, release, partial, malformed and overly nested lines, concurrent senders and consumers with no loss, scoped HTTP authentication (the sender key is refused as a consumer token), a refused 0644 queue left untouched, credential separation by path and inode, and that the sender key is never read.

Regression classes reproduce the review findings against synthetic keys and an injected opener: host override, queue handling (0644, symlink, hardlink, directory, missing parent, short writes, two concurrent senders forced into 37-byte writes through the real `_write_all`, a crashed partial trailing record, a lock that is never released), key-bearing payloads, error-message leakage with short, long and quote-containing keys, and non-200 acceptance. A loopback server verifies real header delivery, redirect refusal, timeout classification and that the response body is not awaited. Synthetic keys are chosen so that no 8-character window of a key appears in any other fixture text, which lets the tests fail on a leaked fragment rather than only on the whole value.

## Optional cloud-computer handoff

Use Grok Bot only when the task benefits from its persistent cloud computer or needs Bot-native facilities. Pass a bounded, authorized task and verify the returned artifact. Local Codex paths, browser sessions and credentials are not automatically present on that computer. Do not infer the Bot's selected model or count it as an exact-model reviewer without independent evidence. Bots in one account share a cloud computer, so separate Bots are not substitutes for per-lane isolated VMs. [Grok Bot overview](https://docs.x.ai/grok-bot/overview).

A Bot-native workflow can run its page/server and failure queue on that computer under the original skill. A sender hosted on the local Mac uses the failure bridge above, whose routine-side reachability was proved for the bounded diagnostic setup above. Each new deployment still needs its own reachability check.
