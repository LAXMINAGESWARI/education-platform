# Text-to-SQL progress stream

Text-to-SQL binding of the shared progress envelope in
[`progress-events.md`](./progress-events.md). This document does not redefine that
envelope. It specifies the teacher ask stream: endpoint, auth, stage order, what
must not be emitted, reconnect, and timeouts.

Job progress for generation and ingest already uses `modules/progress/sse.py`:
`text/event-stream`, `SSE_HEADERS` (`Cache-Control: no-cache`,
`Connection: keep-alive`, `X-Accel-Buffering: no`), and a comment heartbeat
`b": heartbeat\n\n"` every `HEARTBEAT_SECONDS` (15). The frontend reads it with
`fetch` plus `Authorization: Bearer` and a `ReadableStream` (`frontend/src/api/client.ts`
`apiStream`, `frontend/src/api/progress.ts` `readSse`). Native `EventSource` cannot
set that header, so this stream does not use it. Comment lines are already ignored
by `parseSseBlock`. This endpoint reuses that framing and the 15 s heartbeat. It
does not reuse the progress subject's snapshot/`Last-Event-ID` replay. An ask
stores its own row, described under Reconnect.

`POST /text-to-sql/ask` is unchanged: teacher role, `AskIn` (`question` only), and
the two-field `AskOut` (`natural_answer`, `confidence`). Streaming
is a separate endpoint and runs only when `TEXT_TO_SQL_PIPELINE` is `lean`. When
the flag is `legacy`, `POST /text-to-sql/ask/stream` returns 404 with a detail
that names `POST /api/v1/text-to-sql/ask`, and the client uses that endpoint.

## Endpoint

`POST /api/v1/text-to-sql/ask/stream`

- Auth: the teacher allow-list from `require_role("teacher")`, the same gate as
  `/text-to-sql/ask` (`Requires one of: teacher`). The handler resolves the principal
  with `get_current_user_detached` so the request database session is released before
  the first frame. Identity (`user_id`, `user_role`, `institution_id`) comes only from
  the verified JWT.
- Success response: `200`, `Content-Type: text/event-stream`, the same `SSE_HEADERS`
  as `modules/progress/router.py`. Auth failures stay ordinary JSON (401/403)
  before the stream starts.
- Body: the `AskIn` question, plus `request_id` (client-generated UUID string).
  `request_id` is required here and only here. It is the envelope `subject_id`.
  `/ask` does not accept it.

```json
{"question": "How many students do I teach?", "request_id": "2f1c0c2e-7b1a-4e0a-9c3d-1a2b3c4d5e6f"}
```

## Events

Data frames use the existing SSE shape: `event: <name>\ndata: <json>\n\n` with
compact JSON. The heartbeat is the existing comment line, not a data event.

### `progress`

Shared envelope from `progress-events.md`. For this stream every field is fixed
as follows:

| Field | Value |
| --- | --- |
| `v` | `1` |
| `kind` | `"text_to_sql"` |
| `subject_id` | the request's `request_id` |
| `seq` | monotonic integer starting at 1, increasing by 1 for each `progress` event on this response |
| `stage` | `checking` \| `understanding` \| `writing_query` \| `validating` \| `running` \| `summarising` |
| `status` | `running` \| `done` \| `failed` |
| `percent`, `current`, `total`, `unit` | always `null` (an ask has no countable unit) |
| `message` | a short fixed user-facing string, or `null`. Never SQL, schema text, table or column names, row values, guard scores, error categories, or template names |
| `updated_at` | ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`) |

`seq` is per `progress` event. `answer_delta`, `table`, `final`, `refusal`, and
`error` do not carry `seq`. Order is the order of frames on this response.
Heartbeats do not advance `seq`.

Stage copy (the only strings `message` may use):

| Stage | `running` message | `done` / `failed` |
| --- | --- | --- |
| `checking` | `"Checking your question"` | `null` |
| `understanding` | `"Understanding your question"` | `null` |
| `writing_query` | `"Writing a query"` | `null` |
| `validating` | `"Checking the query"` | `null` |
| `running` | `"Looking up the data"` | `null` |
| `summarising` | `"Writing the answer"` | `null` |

### `answer_delta`

Token text from the GPT compose path only.

```json
{"text": "Aisha "}
```

### `table`

One event for the deterministic small-result path (at most 20 rows and 5
columns). `columns` are readable labels. `rows` are the cell values in that
column order, already coerced to JSON scalars.

```json
{"columns": ["Full name", "Mastery percent"], "rows": [["Aisha Rahman", 82.0]]}
```

### `final`

The same two fields as `AskOut`, so the client can render the stream the same
way as `/ask`. Table names are not included.

```json
{"natural_answer": "...", "confidence": "high"}
```

`confidence` is JSON `null` when `/ask` would return null.

### `refusal`

```json
{"message": "I can only help with questions about your school's students, classes, attendance, or curriculum data. Try asking something in that scope."}
```

`message` is the `natural_answer` `honest_refusal` already produces for `/ask`
(the fixed category string, including the audit fail-closed wording). It is not
`state["error"]`, not a guard score, and not an error category name.

### `error`

```json
{"message": "That took too long to answer — please try again."}
```

Used for the same cases `/ask` turns into HTTP 504 and HTTP 500. `message` is
`_TIMEOUT_ANSWER` or `_UNEXPECTED_FAILURE_ANSWER` from `text_to_sql/router.py`.
The stream does not include a status code, a stack, or the exception text.

### Heartbeat

`": heartbeat\n\n"`, emitted when 15 s pass with no other frame, matching
`modules/progress/sse.py` and `HEARTBEAT_SECONDS`.

## Ordering

1. As soon as the lean run starts, emit `progress` `checking` / `running` and
   `understanding` / `running`. Speculative SQL generation may run beside the
   guards and the intent router. The guard is the batched Jev call plus, when
   Jev passes, the same GPT injection and off-topic checks the legacy nodes
   run. `checking` / `done` is emitted only after those have passed. Until
   then, `checking` and `understanding` / `running` are the only `progress`
   stages emitted. No `writing_query`, no `answer_delta`, no `table`. No SQL
   executes.
2. Guard refusal: `progress` `checking` / `failed`, then exactly one `refusal`,
   then the stream closes. Do not emit `understanding` `done`, `writing_query`,
   `validating`, `running`, `summarising`, `table`, `answer_delta`, or `final`.
   Cancel or discard the intent and generation tasks. Nothing they produced is
   written into an event.
3. Guards pass: `checking` / `done`, then `understanding` / `done` once intent
   routing has finished.
4. Template or free-form SQL: `writing_query` `running` then `done` (even when
   speculative generation already finished; the events wait until step 3). Then
   `validating` `running`, then `validate_sql` and `apply_role_scope`. On
   success, `validating` / `done`. On refusal, `validating` / `failed`, one
   `refusal`, close.
5. `running` / `running`, then `execute_sql`. The audit row is written
   immediately after a successful execute, before `sanity_check` and before any
   `table`, `answer_delta`, or `final`. If that write fails, the stream emits
   `running` / `failed` and one `error` frame carrying the fail-closed message,
   and it does not emit a data frame. On an execution refusal, `running` /
   `failed`, one `refusal`, close.
6. The at-risk service path skips `writing_query` and `validating` (there is no
   SQL). It still waits for both guard verdicts, then uses `running` for the
   service read. The audit row is written immediately after that read succeeds,
   on the same fail-closed rule as execute.
7. After the audit write succeeds, `sanity_check` runs. On success, `running` /
   `done`. On refusal, `running` / `failed`, one `refusal`, close. The executed
   query was already audited, so this refusal does not write a second row.
8. `summarising` / `running`. A small deterministic table emits one `table`
   event and no `answer_delta`. The GPT compose path emits one or more
   `answer_delta` events and no `table`. Then `summarising` / `done` and one
   `final`. None of those frames are emitted before the audit write succeeds.
9. Heartbeats may appear between any of the frames above. They are not ordered
   relative to pipeline stages except that they only fire on a 15 s quiet gap.

A refusal or an unexpected failure yields exactly one `refusal` or one `error`,
and no frame after it. `final` is not sent on those paths. An audit fail-closed
result is an `error` frame, not a `refusal`, and it carries no table, delta, or
final. `/ask` still withholds the answer in `natural_answer` when the same
write fails.

## What must not leak

These rules are enforced by which frames the generator is allowed to yield, not
by filtering a free-form payload.

- Nothing derived from SQL text, the schema catalog, table or column names, or
  result rows is emitted before both guard verdicts have passed and, on a SQL
  path, `validate_sql` and `apply_role_scope` have both succeeded.
- `table` rows and `answer_delta` text (the compose view of those rows) are
  emitted only after `execute_sql`, the audit write, and `sanity_check` have
  all finished successfully. The at-risk path uses the same rule with the
  service result in place of `execute_sql`. An audit write failure emits one
  `error` and no data frame.
- No guard probability, no `INJECTION_BLOCKED` / `OFF_TOPIC_REJECTED` / other
  category names, no raw SQL, no template name, and no queried-table line appear
  in any frame. `/ask` does not return SQL, and this stream matches that.

## Reconnect

`Last-Event-ID` is ignored. The progress module uses it as a fingerprint of a
generation snapshot (`modules/progress/subscribe.py`). This stream does not
use that log. It stores one row in `text_to_sql_ask_runs`, keyed by
`(user_id, request_id)`, for `TEXT_TO_SQL_STREAM_TTL_SECONDS` (10 minutes).
The row holds the progress snapshot and the terminal frames (`table`, `final`,
`refusal`, `error`). It does not store raw `query_result` rows beyond the
`table` frame `/ask` already renders. The row is in Postgres so any API pod
can read it.

A client that drops and posts the same body again:

- After the run is `done` and the row has not expired: the server sends the
  stored terminal frames and does not start another run. SQL does not execute
  again.
- While the first run is still `running`: the server sends the progress
  snapshot already stored, then comment heartbeats, and polls the row on that
  heartbeat interval. When the first run stores its terminal frames, this
  response sends them. It does not start a second run.
- A different user with the same `request_id` is a different key and gets
  their own run.
- After the row expires, a new post may claim it and run again.

A dropped connection cancels speculative work (intent routing, schema load, and
SQL generation) and waits until those tasks have stopped, so they do not keep a
database connection. The same cancellation runs when a guard refuses or raises.
The audit row for a query that already executed is written immediately after
`execute_sql`, before any data frame, so a drop after that write is already
audited. `audit_log` still runs exactly once for that execution. The producer
then finishes compose and stores the terminal frames when it is already past
the speculative stage. A client disconnect is logged at INFO with no traceback.

## Timeouts

| Limit | Value | On expiry |
| --- | --- | --- |
| Heartbeat quiet gap | 15 s | one comment heartbeat; the run continues |
| Whole ask, same as `/ask` `_GRAPH_TIMEOUT_SECONDS` | 60 s | one `error` with the 504 detail string, then close |
| `execute_sql` `statement_timeout` | 15 s | unchanged; surfaces as today's execution refusal |
| Jev guard and gateway guard (`JEV_GUARD_TIMEOUT_SECONDS`) / SQL generate / compose | 8 s / 20 s / 30 s | today's fail-closed guard or honest refusal; no extra app-level transport retry |

15 s comment heartbeats stay under Cloudflare's 100 s idle proxy timeout.

## Compose streaming

`ai_platform.gateway.api.complete(..., stream=True)` buffers provider chunks and
returns one `Completion`. The GPT compose path on this endpoint needs a stream
variant that yields text deltas and writes one `llm_gateway_calls` row at the
end, with the same `UsageRecord` shape as `complete`. Each delta is one
`answer_delta`. The deterministic table path does not call the model.

## Policy chat

`POST /api/v1/chats/{conversation_id}/messages/stream` takes
`{ "content", "request_id" }` and streams the policy graph. `kind` is
`policy_chat`. Stages are `checking` (injection and scope), `searching`
(retrieval, no model), and `summarising` (the answer). `answer_delta` is the
answer text. `final` is `{ "content", "citations" }` plus the saved user
message, assistant message, and context. `table` is not emitted. `refusal`,
`error`, and the 15 s heartbeat are. A repeat POST with the same `request_id`
replays the saved turn and does not insert another message. Replay follows
`chat_messages.reply_to_message_id`. A legacy assistant row has no link: replay
uses the next assistant reply and stops at the next question. Ordering uses
`created_at` from the database clock, with the question first when the
timestamps tie. The in-process stream run cache is keyed by user, conversation,
and `request_id`. The text-to-SQL stages `writing_query`, `validating`, and
`running` are not used. The answer
step calls `complete_stream` for `policy_chat.answer` when the gateway is on.
The frame encoder and heartbeat are the text-to-SQL ones.
