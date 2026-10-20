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
does not reuse the progress subject's snapshot/`Last-Event-ID` replay, because an
ask is not a durable Postgres snapshot.

`POST /text-to-sql/ask` is unchanged: teacher role, `AskIn` (`question` only), and
the three-field `AskOut` (`natural_answer`, `confidence`, `provenance`). Streaming
is a separate endpoint and always runs the lean pipeline.

## Endpoint

`POST /api/v1/text-to-sql/ask/stream`

- Auth: `require_role("teacher")`, the same gate as `/text-to-sql/ask`. Identity
  (`user_id`, `user_role`, `institution_id`) comes only from the verified JWT.
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

The same three fields as `AskOut`, so the client can render the stream the same
way as `/ask`.

```json
{"natural_answer": "...", "confidence": "high", "provenance": "Queried: student_profiles."}
```

`confidence` and `provenance` are JSON `null` when `/ask` would return null.

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
   guards and the intent router. Until both guard verdicts have passed, those
   two stages are the only `progress` stages emitted. No `writing_query`, no
   `answer_delta`, no `table`.
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
5. `running` / `running`, then `execute_sql` and `sanity_check`. On success,
   `running` / `done`. On refusal, `running` / `failed`, one `refusal`, close.
6. The at-risk service path skips `writing_query` and `validating` (there is no
   SQL). It still waits for both guard verdicts, then uses `running` for the
   service read and `sanity_check`.
7. `summarising` / `running`. A small deterministic table emits one `table`
   event and no `answer_delta`. The GPT compose path emits one or more
   `answer_delta` events and no `table`. Then `summarising` / `done` and one
   `final`.
8. Heartbeats may appear between any of the frames above. They are not ordered
   relative to pipeline stages except that they only fire on a 15 s quiet gap.

A refusal or an unexpected failure yields exactly one `refusal` or one `error`,
and no frame after it. `final` is not sent on those paths. `audit_log` still
runs before the stream closes, and an audit fail-closed refusal replaces the
answer the same way `/ask` does (one `refusal`, not an `error`).

## What must not leak

These rules are enforced by which frames the generator is allowed to yield, not
by filtering a free-form payload.

- Nothing derived from SQL text, the schema catalog, table or column names, or
  result rows is emitted before both guard verdicts have passed and, on a SQL
  path, `validate_sql` and `apply_role_scope` have both succeeded.
- `table` rows and `answer_delta` text (the compose view of those rows) are
  emitted only after `execute_sql` and `sanity_check` have both finished
  successfully. The at-risk path uses the same rule with the service result in
  place of `execute_sql`.
- No guard probability, no `INJECTION_BLOCKED` / `OFF_TOPIC_REJECTED` / other
  category names, no raw SQL, and no template name appear in any frame. `/ask`
  does not return SQL, and this stream matches that.
- `provenance` on `final` may name tables, because `AskOut.provenance` already
  does, and only after the checks above.

## Reconnect

`Last-Event-ID` is ignored. The progress module uses it as a fingerprint of a
durable snapshot (`modules/progress/subscribe.py`); this stream keeps no event
log and cannot replay `seq`. A dropped connection is restarted by the client:
the same `POST` body, including the same `request_id`. The server runs the
question again. It does not resume `seq` and it does not cache the previous
answer. `request_id` is copied into the audit payload so the two runs can be
correlated.

## Timeouts

| Limit | Value | On expiry |
| --- | --- | --- |
| Heartbeat quiet gap | 15 s | one comment heartbeat; the run continues |
| Whole ask, same as `/ask` `_GRAPH_TIMEOUT_SECONDS` | 60 s | one `error` with the 504 detail string, then close |
| `execute_sql` `statement_timeout` | 15 s | unchanged; surfaces as today's execution refusal |
| Gateway guard / SQL generate / compose (lean) | 8 s / 20 s / 30 s | today's fail-closed guard or honest refusal; no extra app-level transport retry |

15 s comment heartbeats stay under Cloudflare's 100 s idle proxy timeout.

## Compose streaming

`ai_platform.gateway.api.complete(..., stream=True)` buffers provider chunks and
returns one `Completion`. The GPT compose path on this endpoint needs a stream
variant that yields text deltas and writes one `llm_gateway_calls` row at the
end, with the same `UsageRecord` shape as `complete`. Each delta is one
`answer_delta`. The deterministic table path does not call the model.

## Policy chat

Not implemented here. The same frame family fits; the text-to-SQL stage enum
does not. A policy stream would set `kind` to the policy value from
`progress-events.md` and use stages `checking` (injection and scope),
`searching` (retrieval, no model), and `summarising` (the answer). `answer_delta`
fits the GPT answer. `final` would be the policy message (content and citations),
not `AskOut`. `table` does not apply. `refusal`, `error`, and the 15 s heartbeat
do. The follow-up is a `POST` stream beside `POST /chats/{id}/messages` that
maps those nodes onto that stage enum and streams `policy_chat.answer` through
the same gateway stream variant. It should share the encoder and heartbeat, and
it should not reuse `writing_query`, `validating`, or `running`.
