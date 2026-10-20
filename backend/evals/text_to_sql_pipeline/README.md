# Text-to-SQL pipeline comparison

Runs one golden file through the legacy graph and the lean pipeline and prints
accuracy, latency, LLM calls, and tokens. `--dry-run` makes no model or database
call.

```bash
cd backend
uv run python -m evals.text_to_sql_pipeline.compare --dry-run
uv run python -m evals.text_to_sql_pipeline.compare \
  --golden tests/fixtures/text_to_sql_golden.yaml --dry-run
```

A live run uses the local `OPENROUTER_API_KEY` and the local Postgres in
`backend/.env`. It refuses to start unless all three of these are set. Do not
point `DATABASE_URL` at production.

```bash
TEXT_TO_SQL_BENCH_CONFIRM=1 \
TEXT_TO_SQL_BENCH_USER_ID=<teacher-uuid> \
TEXT_TO_SQL_BENCH_INSTITUTION_ID=<institution-uuid> \
uv run python -m evals.text_to_sql_pipeline.compare --live
```

Leave `TEXT_TO_SQL_PIPELINE` unset. The harness sets `pipeline` on each state, so
legacy stays on `chat_completion` and lean uses the gateway in the same process.

Accuracy is `expect_rows` when that key is present, otherwise a
whitespace-insensitive `expect_sql` substring of `validated_sql`, otherwise
whether the two pipelines returned the same rows. Legacy time-to-first-token
equals time-to-answer, because `/ask` returns one body. The lean stream fills
both once `POST /text-to-sql/ask/stream` is measured.

Switch over when lean ties or beats legacy on accuracy, is faster at p50, and
the guard eval below shows no regression.

## Guard eval

The 160-row set is `evals/guard/datasets/chat_guard_eval_v1.json`. The data
surface is the text-to-SQL injection and off-topic checks. `--data-guard-batch`
sends those two Jev questions in one `post_decisions` per row. Without the flag
each check is its own call, which is what the existing call-count test locks.
Do not run `--live` against a production OpenRouter project.

```bash
cd backend
uv run python scripts/eval_chat_guard.py --surfaces data --dry-run
uv run python scripts/eval_chat_guard.py --surfaces data --dry-run --data-guard-batch
uv run python scripts/eval_chat_guard.py --surfaces data --live --max-usd 2.00
uv run python scripts/eval_chat_guard.py --surfaces data --live --max-usd 2.00 \
  --data-guard-batch
```
