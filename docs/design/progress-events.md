# Progress events

One event shape for material upload, topic generation, text-to-SQL, and policy chat.
Upload and generation ride the existing job-progress SSE (`/api/v1/progress/versions/{id}`
and `/api/v1/progress/runs/{id}`). Text-to-SQL and policy chat are request-scoped streams
that use the same stage envelope plus a few terminal payloads.

The snapshot event already on the job stream stays. `progress` is an additional event.
Clients that only understand `snapshot` keep working.

## Envelope

SSE event name: `progress`.

```json
{
  "v": 1,
  "kind": "upload",
  "subject_id": "8f1c0c2e-1b2a-4d5e-9c00-1a2b3c4d5e6f",
  "seq": 4,
  "stage": "embedding",
  "status": "running",
  "percent": 70,
  "current": 5,
  "total": 10,
  "unit": "chunk",
  "message": "Embedding 5 of 10 chunks.",
  "updated_at": "2026-10-20T10:15:03+00:00"
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `v` | `1` | Schema version. Unknown versions are ignored. |
| `kind` | `upload` \| `generation` \| `text_to_sql` \| `policy_chat` | Which stage enum applies. |
| `subject_id` | string | Version id, run id, or the caller's `request_id`. |
| `seq` | int | Monotonic per subject, starting at 1. Never reset except by a new subject. |
| `stage` | string | A value from that kind's public stage list. |
| `status` | `running` \| `done` \| `failed` | `done` and `failed` are terminal for that subject. |
| `percent` | int 0–100 or null | Overall percent, not the percent inside one stage. Null when unknown or failed. |
| `current` | int or null | Numerator for the stage. Null when the stage has no counter. |
| `total` | int or null | Denominator. Null when unknown. Zero is treated as unknown. |
| `unit` | `page` \| `chunk` or null | What `current` / `total` count. |
| `message` | string or null | Short text safe to show a person. No stack traces, SQL, or filesystem paths. |
| `updated_at` | ISO-8601 timestamp | When this state was stored. |

Every `progress` event is the full current state, not a delta. A client that missed
earlier `seq` values renders this payload as-is.

## Job stream (upload and generation)

Wire format, alongside the existing snapshot:

```text
id: version:<uuid>:<12-hex>
event: snapshot
data: { ...version or run snapshot... }

id: version:<uuid>:<12-hex>
event: progress
data: { ...envelope... }

: heartbeat

event: close
data: {"reason":"terminal"}
```

Rules:

- The snapshot event is unchanged: same name, same previous fields. Upload snapshots
  gain stage columns (`stage`, `stage_seq`, `stage_current`, `stage_total`,
  `stage_message`, `stage_updated_at`). Old clients ignore the new fields.
- `progress` is sent only when the snapshot has a stage. It uses the same `id` as the
  snapshot it was built from. `Last-Event-ID` dedupes the pair.
- On connect, the server reads the current row and emits that one snapshot and that
  one `progress` event. It does not replay earlier stages. There is no gap and no
  history log.
- If `Last-Event-ID` equals the current id, nothing is sent until the row changes.
- A terminal snapshot (`ready`, `failed`, `published`, and the generation terminal
  phases) is followed by `event: close` with `{"reason":"terminal"}`.
- Heartbeats stay comment frames (`: heartbeat`) every 15 seconds. They are not
  `progress` events and they do not advance `seq`.

Durability: for `upload` and `generation`, `seq` and the stage live on the row that
the snapshot read already loads. A crashed worker does not leave a client at
"parsing 40%" after the lease is gone (see [Stale stages](#stale-stages)).

## Upload stages

Public enum: `received`, `parsing`, `chunking`, `embedding`, `extracting_topics`,
`done`, `failed`.

| Stage | Status | What the worker is doing | Counts |
| --- | --- | --- | --- |
| `received` | running | File is stored and the job is queued or has just been claimed. | null |
| `parsing` | running | Docling is converting the PDF. | null today; see below |
| `chunking` | running | Text is split into chunks. | `current` = `total` = chunk count, `unit` = `chunk`, once splitting has finished |
| `embedding` | running | Chunk vectors are being computed. | `current` of `total` chunks, `unit` = `chunk` |
| `extracting_topics` | running | Section headings already on the chunks are noted. This does not call a model. | `current` = chunks that have a heading, `total` = chunk count, `unit` = null |
| `done` | done | Version is `ready`. | null, `percent` = 100 |
| `failed` | failed | Ingest stopped. | null, `percent` = null, `message` = the friendly reason |

`subject_id` is the source-material version id or the knowledge-document version id.

### Parsing has no page counter

Docling 2.119 `DocumentConverter.convert` takes the source, headers, `raises_on_error`,
`max_num_pages`, `max_file_size`, and `page_range`. It has no progress callback, and
the installed package exposes no per-page hook. `parsing` therefore sends
`current` = null, `total` = null, `unit` = null. If a later Docling exposes a page
callback, the same stage will send `unit` = `page` and "page X of Y" without a
schema change.

### Percent weights

Overall percent uses fixed stage weights. The weight is the width of that stage's
band, not a guess of wall-clock time.

| Stage | Weight | Band |
| --- | ---: | --- |
| `received` | 0 | 0 |
| `parsing` | 40 | 0–40 |
| `chunking` | 10 | 40–50 |
| `embedding` | 40 | 50–90 |
| `extracting_topics` | 10 | 90–100 |
| `done` | | 100 |
| `failed` | | null |

When `current` and `total` are both positive:

```text
percent = round(band_start + weight * min(1, current / total))
```

Clamped to 0–100. When either count is null or `total` is 0, `percent` is the start
of the band (`parsing` stays at 0 for the whole conversion). `done` is 100. `failed`
is null so a failure does not look complete.

Worked example: embedding 5 of 10 chunks is `round(50 + 40 * 0.5) = 70`.

### Friendly failures

`message` on `failed` is chosen from the failure category. `failure_reason` on the
snapshot may still carry the short admin diagnostic (for example Docling's own
sentence). The progress message never includes a traceback, an exception class, a
SQL statement, or a path.

| Category | When | Message |
| --- | --- | --- |
| `missing_file` | Blob key or file is gone | The uploaded file could not be found. |
| `empty` | Conversion produced no text | No readable text was found in this document. |
| `unreadable` | Parse failed | The document could not be read. |
| `index` | Embedding or vector write failed | The document could not be indexed. |
| `not_linked` | Material, institution, or target is missing | This upload is missing required information. |
| `interrupted` | Attempts were used up | Processing was interrupted and could not finish. |
| `unknown` | Anything else | The upload could not be processed. |

### Writes and throttling

Stage changes are stored on the version row (`stage`, `stage_current`, `stage_total`,
`stage_message`, `stage_updated_at`, `stage_seq`) and committed on their own, so a
subscriber can see them before the chunk transaction commits. Chunk and vector
writes stay in one transaction.

A stage boundary always writes. Inside a stage, a write happens when the overall
percent has moved by at least 5 points, or at least 1 second has passed since the
previous write. Each write increments `seq`, sets `updated_at`, and fires the
existing Postgres wake. The 15-second heartbeat is unchanged.

## Stale stages

A lease that expired or was released must not keep showing the dead worker's stage.
`reset_stage_on_requeue` sets:

- `stage` = `received`
- `status` = `running` (the version stays `processing`)
- counts = null
- `percent` = 0
- `message` = `Restarting...`
- `seq` = previous + 1

It runs from `workers/lease.py` `claim()`, and from the version snapshot reader on
every read. Both use the same predicate:

- the job is `running` and `locked_until` is null or already past, or
- the job is `queued` and `attempts >= 1`

A first queue (`attempts == 0`) stays on `File received.` A `ready` or `failed`
version is left alone. Repeating the reset while the message is already
`Restarting...` does not burn another `seq`.

This branch does not merge PR #200 and does not vendor `release_lease`.
On `cursor/k3s-api-db-pool-f39d` at `2a00407` (same function in the later
Postgres test commit `ecd2db0`), the release updates a row only when `id`,
`status = running`, `attempts`, and the last-written `locked_until` all match.
A miss is logged and leaves the row unchanged. A graceful SIGTERM refunds the
attempt in that same update. #200 still includes the pool and deploy work and
is sequenced after #197.

`reset_stage_on_requeue` stays a separate function. Call it in that release
transaction only when the update rowcount is 1, before commit. A miss must not
call it, or it would clear the stage another worker is writing. Until that call
exists, claim and the snapshot reader still clear a stage for an expired
running lease or a queued job whose attempt was kept (`attempts >= 1`). A
refunded first attempt (`queued`, `attempts == 0`, `locked_until` null) looks
like a first queue to those paths, so they leave the stage in place.

## Generation stages

`kind` = `generation`. `subject_id` is the run id. Stages are the existing run
phases: `indexing`, `outlining`, `outline_review`, `generating`, `qa_review`,
`published`, `failed`, `discarded`.

| Phase | `status` |
| --- | --- |
| `published` | `done` |
| `failed`, `discarded` | `failed` |
| anything else | `running` |

`percent`, `current`, `total`, and `unit` are null until generation has its own
weights. `message` is the run's `failure_reason` only when `status` is `failed`,
and only after that string has been passed through the same "no path, no traceback"
rule. This document defines the event. The upload work emits `progress` for version
subjects; generation keeps publishing its snapshot until it writes the same envelope.

## Text-to-SQL

`kind` = `text_to_sql`. The stream is request-scoped. `subject_id` is the
`request_id` the client sends. Nothing is stored for replay. A reconnect starts a
new ask with the same `request_id`; the server does not resume `seq`.

Stages, in order, mapped from the graph the ask endpoint already runs:

| Stage | Graph nodes |
| --- | --- |
| `checking` | `injection_guard`, `question_validator` |
| `understanding` | `intent_router`, `load_schema`, `link_schema` |
| `writing_query` | `generate_sql` |
| `validating` | `validate_sql`, `apply_role_scope`, `resolve_at_risk`, `sanity_check` |
| `running` | `execute_sql` |
| `summarising` | `compose_answer`, `audit_log` |

`percent` may stay null. `status` is `running` until a terminal event below.

Additional SSE events on this stream only:

| Event | `data` |
| --- | --- |
| `answer_delta` | `{ "text": "..." }` — append to the answer; not a full replacement |
| `table` | `{ "columns": ["..."], "rows": [["..."]] }` — the query result grid |
| `final` | The same fields as `AskOut`: `natural_answer`, `confidence`, `provenance` |
| `refusal` | `{ "message": "..." }` — one sentence, safe to show |

`final` and `refusal` end the stream. They are not `progress` events and they do
not use the stage enum.

## Policy chat

`kind` = `policy_chat`. Same request-scoped rules as text-to-SQL, including
reconnect-by-reask with the same `request_id`.

| Stage | Graph nodes |
| --- | --- |
| `checking` | `injection_guard`, `question_validator` |
| `searching` | `retrieve` |
| `summarising` | `summarize` |

Additional SSE events:

| Event | `data` |
| --- | --- |
| `answer_delta` | `{ "text": "..." }` |
| `final` | `{ "content": "...", "citations": [{ "id", "label", "excerpt" }] }` — the assistant message, not `AskOut` |
| `refusal` | `{ "message": "..." }` |

Policy chat does not emit `table`.
