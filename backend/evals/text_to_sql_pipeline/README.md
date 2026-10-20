# Text-to-SQL pipeline comparison

Runs one golden file through the legacy graph and the lean pipeline and prints
accuracy, latency, LLM calls, and tokens. `--dry-run` builds the real prompts,
counts tokens, and GETs the public OpenRouter model list for prices. It does
not call a model and it does not open a database. `--max-usd` (default `2.00`)
is printed on the dry-run. A live run refuses before any model call when the
worst case (both pipelines, pipeline retries, and transport retries) is over
that cap.

```bash
cd backend
uv run python -m evals.text_to_sql_pipeline.compare --dry-run
uv run python -m evals.text_to_sql_pipeline.compare \
  --golden tests/fixtures/text_to_sql_golden.yaml --dry-run --max-usd 2.00
```

## Golden Bench School

`tests/fixtures/text_to_sql_golden.yaml` holds about forty teacher questions.
`expect_rows` is the result of the hand-written SQL in
`evals/text_to_sql_pipeline/references.py` against a deterministic school, not
a model answer. The school is Golden Bench School. Meera Krishnan
(`meera.bench@golden.school`, password `demo1234`) teaches Grade 6 section 6A
Mathematics and Science. Noura Al Hashimi teaches section 6B. Questions about
6B, Noura's students, or a score that exists only in 6B must come back empty
for Meera. Out-of-scope questions set `expect_refused`.

The loader deletes only that institution. Point `DATABASE_URL` at a local
migrated database. `backend/.env.example` uses database `education`. The test
suite uses `education_test` when `DATABASE_URL` is unset. Do not point this at
production.

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://education:education@localhost:5432/education_test \
  uv run python -m evals.text_to_sql_pipeline.seed --write-golden
```

`--write-golden` rewrites the yaml from the reference SQL. A live compare can
load the same school and fill the teacher ids:

```bash
TEXT_TO_SQL_BENCH_CONFIRM=1 \
DATABASE_URL=postgresql+asyncpg://education:education@localhost:5432/education_test \
  uv run python -m evals.text_to_sql_pipeline.compare --live --load-fixture --max-usd 2.00
```

Without `--load-fixture`, set the three bench variables yourself. A live run
uses the local `OPENROUTER_API_KEY`. Do not start one until the dry-run USD
line has been approved.

Leave `TEXT_TO_SQL_PIPELINE` unset. The harness sets `pipeline` on each state, so
legacy stays on `chat_completion` and lean uses the gateway in the same process.

Accuracy is `expect_rows` when that key is present (values are compared after
normalising decimals), and `expect_refused` requires an error and no rows.
Otherwise a whitespace-insensitive `expect_sql` substring of `validated_sql` is
used, otherwise whether the two pipelines returned the same rows. Legacy
time-to-first-token equals time-to-answer, because `/ask` returns one body. The
lean stream fills both once `POST /text-to-sql/ask/stream` is measured.

Switch over when lean ties or beats legacy on accuracy, is faster at p50, and
the guard eval below shows no regression.

## Guard eval

The 160-row set is `evals/guard/datasets/chat_guard_eval_v1.json`. The data
surface is the text-to-SQL injection and off-topic checks. `--data-guard-batch`
sends those two Jev questions in one `post_decisions` per row. Without the flag
each check is its own call, which is what the existing call-count test locks.
Do not run `--live` against a production OpenRouter project.

`--dry-run` prints the existing call estimate and a second block priced from the
actual request bodies and the public model list, including a worst case with
Jev and GPT transport retries. It does not call a model.

```bash
cd backend
uv run python scripts/eval_chat_guard.py --dry-run
uv run python scripts/eval_chat_guard.py --surfaces data --dry-run
uv run python scripts/eval_chat_guard.py --surfaces data --dry-run --data-guard-batch
uv run python scripts/eval_chat_guard.py --surfaces data --live --max-usd 2.00
uv run python scripts/eval_chat_guard.py --surfaces data --live --max-usd 2.00 \
  --data-guard-batch
```
