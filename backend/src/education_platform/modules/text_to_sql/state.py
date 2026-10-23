"""LangGraph wire-format state for the text-to-SQL pipeline.

`TextToSQLState` mirrors the pattern in `modules/assistant/graph.py`'s `GraphState`:
a loose, `total=False` TypedDict, since LangGraph nodes return partial updates that
get merged into the running state rather than replacing it wholesale.
"""

from __future__ import annotations

from typing import Any, Final, TypedDict

# retry_count starts at 0 (set by whatever builds the initial state) and is capped
# here; graph.py's routing checks against this when deciding retry vs. honest_refusal.
MAX_RETRIES: Final[int] = 3


# --- state["error"] class convention ------------------------------------------------
#
# Every node that sets state["error"] must format it with `format_error()` below, using
# exactly one of the category constants in `_ERROR_CATEGORIES`. This is the single
# documented contract for classifying a failure — nodes that produce an error and nodes
# that consume one (audit_log, honest_refusal, compose_answer) all key off the same fixed
# prefixes instead of each inventing their own wording to grep for. `error_category()` is
# the matching consumer-side helper; use it rather than hand-rolling another
# `.startswith(...)` check.
#
# This is deliberately just a handful of string constants plus two tiny functions, not a
# structured error type — `state["error"]` stays `str | None`. A real typed error object
# would be the next step if/when fields beyond a category + message turn out to be
# needed; not warranted yet. (This module started with three categories — LLM_ERROR,
# VALIDATION_ERROR, ROLE_VIOLATION — then EXECUTION_ERROR and AUDIT_ERROR each earned
# their own slot rather than folding into an existing one; SCHEMA_ERROR is the third such
# addition, for the same reason. A new category is cheap precisely because the contract
# is this small, not a sign the count is meant to stay fixed at whatever it happened to
# be.)

SCHEMA_ERROR: Final[str] = "SCHEMA_ERROR"
"""load_schema couldn't produce a usable schema_context at all — schema_catalog.md is
missing/unreadable, or the excluded-content filter's own post-check failed (over-stripped
content, a leaked excluded table name, ...). No SQL was even attempted; this is a
deployment/configuration problem with the catalog file itself, not the database being
unreachable (EXECUTION_ERROR) or anything about the question or an authored query. Gets
its own category rather than folding into EXECUTION_ERROR for the same reason AUDIT_ERROR
got its own in Task 10 rather than folding into it: each category here documents exactly
one raising node, and load_schema failing before any SQL exists is a genuinely different
pipeline stage from execute_sql failing to run a real, valid, authorized query. Raised by
load_schema, and only by load_schema."""

LLM_ERROR: Final[str] = "LLM_ERROR"
"""The LLM/API call itself failed — network error, auth failure, empty completion,
rate limit, missing OPENROUTER_API_KEY. No SQL/answer was produced at all; this is not
the model producing bad output, so there is nothing to hand back to it as a correction
in a retry prompt. Raised by generate_sql today."""

VALIDATION_ERROR: Final[str] = "VALIDATION_ERROR"
"""The LLM produced SQL, but it was rejected — bad syntax, a disallowed statement type,
or a reference to a table/column not present in schema_context. The rejection reason
belongs in the next retry's prompt (see generate_sql's retry handling). Raised by
validate_sql."""

ROLE_VIOLATION: Final[str] = "ROLE_VIOLATION"
"""The SQL is fine on its own terms, but apply_role_scope determined the asking
user_role isn't allowed to run or see it — a policy rejection, not a correctness one.
Raised by apply_role_scope."""

INJECTION_BLOCKED: Final[str] = "INJECTION_BLOCKED"
"""The *question itself* was refused before any SQL was even attempted — a heuristic
regex match, or an LLM classifier call, judged it a prompt-injection/jailbreak attempt
("ignore previous instructions", "you are now an admin", ...). Deliberately not
ROLE_VIOLATION: that category means real SQL existed and was rejected on authorization
grounds; this one means generation was never attempted at all, the same "refuse for the
right reason" distinction this pipeline already draws everywhere else (see
apply_role_scope's own ordering of its checks). This is a cost/UX layer, not a security
boundary — apply_role_scope's identity-only row scoping is what actually keeps a
successfully-generated adversarial query safe regardless of whether this category ever
fires; skipping this check entirely (this classifier being unavailable, or a phrasing it
doesn't recognize) degrades to today's behavior, not to an unsafe one. Raised by
injection_guard, and only by injection_guard — the first node in the graph, before
load_schema."""

OFF_TOPIC_REJECTED: Final[str] = "OFF_TOPIC_REJECTED"
"""The question wasn't malicious — `INJECTION_BLOCKED` is the category for that — it just
has nothing to do with a school's students, grades, attendance, or curriculum data at all
("what's the weather", "write me a poem"). Distinct from `INJECTION_BLOCKED` for the same
reason that category is distinct from `ROLE_VIOLATION`: a different fact about *why*
generation was never attempted, worth a different audit signal rather than lumping every
pre-generation refusal into one bucket. Raised by `question_validator`, and only by
`question_validator` — the node right after `injection_guard`. This was briefly folded
into injection_guard's own classifier call instead (one combined prompt returning both
`injection` and `off_topic`, to save a second OpenRouter round-trip), then measured and
reverted: the combined call produced a 100%-reproducible false positive on an ordinary
question ("What subject do I teach?") that a separate, off-topic-only call got right every
time — see question_validator.py's own docstring for the full comparison and numbers. Like
`INJECTION_BLOCKED`, this is a cost/UX layer, not a security boundary — `apply_role_scope`
doesn't care whether a question was ever "about" school data; it only ever restricts a
query that was actually generated."""

EXECUTION_ERROR: Final[str] = "EXECUTION_ERROR"
"""The SQL was valid (Task 5) and authorized (Task 6), but the database itself failed to
run it — a timeout, a connection failure, a constraint violation the earlier checks
couldn't have predicted. Not something generate_sql can fix by rewriting SQL, so this
routes to honest_refusal like the other two, never back into the retry loop. The detail
attached here is always a generic, user-safe message — never the raw driver/Postgres
error text, which can carry schema/constraint names or query fragments; the real detail
goes to logging and state["audit_entry"] instead. Raised by execute_sql."""

AUDIT_ERROR: Final[str] = "AUDIT_ERROR"
"""A real answer was legitimately produced, but the audit record for it could not be
persisted (a DB error/connection issue in audit_log itself, not in anything upstream).
Per this project's charter, role-based access and the audit trail are equally committed
guarantees, not one scoped down for the other's convenience — so this is a fail-closed
category: audit_log overwrites what would have been a served answer with a refusal rather
than let one reach the caller with no corresponding audit record. Raised by audit_log,
and only by audit_log; every other category above means no trustworthy answer existed to
protect in the first place, this one means an answer existed and was deliberately
withheld anyway."""

_ERROR_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        SCHEMA_ERROR,
        LLM_ERROR,
        VALIDATION_ERROR,
        ROLE_VIOLATION,
        EXECUTION_ERROR,
        AUDIT_ERROR,
        INJECTION_BLOCKED,
        OFF_TOPIC_REJECTED,
    }
)


def format_error(category: str, detail: str) -> str:
    """Build a `state["error"]` value with the shared category prefix. Always use this
    to set state["error"] rather than formatting the string by hand, so the prefix
    convention can't drift (typo'd colon/spacing, category not in `_ERROR_CATEGORIES`).
    """
    if category not in _ERROR_CATEGORIES:
        raise ValueError(
            f"unknown error category {category!r}; must be one of {sorted(_ERROR_CATEGORIES)}"
        )
    return f"{category}: {detail}"


def error_category(error: str | None) -> str | None:
    """The category a `state["error"]` value was formatted with, or `None` if `error`
    is unset or wasn't produced by `format_error()` (e.g. legacy/foreign text).
    """
    if error is None:
        return None
    category, _, _rest = error.partition(": ")
    return category if category in _ERROR_CATEGORIES else None


class TextToSQLState(TypedDict, total=False):
    """State threaded node to node through the text-to-SQL graph (see graph.py)."""

    # --- Input, set once at graph invocation ---
    question: str  # the user's natural-language question
    pipeline: str  # "lean" selects the lean prompt, gateway, and one repair
    user_id: str
    user_role: str  # one of: "admin", "teacher", "student", "parent" — drives apply_role_scope
    institution_id: str  # same sourcing discipline as user_id/user_role: populated once,
    # upstream, from the verified JWT/session — never from the question, LLM output, or
    # any other untrusted input. apply_role_scope pins every sensitive-table read to this
    # tenant, for every role including admin.
    schema_context: str  # schema catalog markdown, loaded by load_schema from schema_catalog.md
    query_source: str | None  # "template" (apply_role_scope skips its AST rewrite —
    # the template author already wrote the row-scoping logic into the template SQL) or
    # None/unset for the default free-form path (generate_sql's LLM output), which gets
    # the full apply_role_scope rewrite. No template caller exists yet; this is here for
    # a future template-based task.
    intent_route: str  # "template" or "free_form", selected after question validation
    intent: str | None
    intent_confidence: float | None
    intent_parameters: dict[str, Any]
    answer_criterion: str | None  # a template's own short, teacher-facing phrase for a
    # policy-defined criterion that isn't obvious from the number/list alone (e.g.
    # "mastery below 60% or attendance below 80%") — set by intent_router from the
    # matched template's optional `criterion` field, None for free_form (that path has no
    # single fixed definition to name) or for a template with no `criterion` declared.
    # compose_answer appends it, verbatim, to the deterministic single-scalar answer path
    # only — never handed to the LLM summarization path, which already free-phrases and
    # could instead paraphrase or drop it the same way Task 9's own fix was needed for.

    zero_result_is_valid: bool  # set by intent_router from the matched template's
    # `zero_is_valid` flag. True means "no rows / a zero count is a genuine answer for this
    # template" (e.g. no student below a stated threshold): sanity_check then skips its
    # zero_rows / zero_valued_aggregate downgrades. Unset/False for free_form, whose zero is
    # ambiguous between "nothing matches" and "scoping silently excluded everything".
    empty_answer: str | None  # the template's own plain sentence for a zero-row result
    # (`empty_answer` in intent_templates.yaml, already formatted with the validated
    # parameters); compose_answer uses it instead of the generic "couldn't find any records".

    zero_probe_sql: dict[str, str]  # reference queries (existing governed templates) the
    # router attaches for templates whose zero result needs corroborating:
    # `visible_students` (count_my_students) for `zero_is_valid` templates,
    # `taught_subjects` (my_subjects) for templates that take a `subject`. execute_sql runs
    # them only when the main result is empty/zero, in the same RLS-scoped transaction.
    zero_probe_results: dict[str, Any] | None  # their answers: {"visible_students": int,
    # "taught_subjects": [str, ...]}; a probe that failed is simply absent, and None means
    # no probe ran (the result wasn't zero). sanity_check / compose_answer read this.

    # --- SQL generation / validation ---
    generated_sql: str | None  # raw SQL produced by generate_sql, before validation
    validated_sql: str | None  # SQL that passed validate_sql; only set once safe to run
    retry_count: int  # number of generate_sql attempts so far; default 0, capped at MAX_RETRIES

    # --- Execution ---
    query_result: list[dict[str, Any]] | None  # rows returned by execute_sql
    result_row_count: int | None

    # --- Output ---
    natural_answer: str | None  # final natural-language answer composed by compose_answer
    confidence: str | None  # one of: "high", "medium", "low"
    provenance: str | None  # human-readable trace of what was queried

    # --- Error handling / audit ---
    error: str | None  # set by any node that fails
    audit_entry: dict[str, Any] | None  # structured record written by audit_log
    pipeline_metrics: dict[str, int]  # timings copied off the meter for the bench
    stream_request_id: str | None  # client request id for the progress stream; absent on /ask
