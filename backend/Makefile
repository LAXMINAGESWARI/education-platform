.PHONY: test-fast test-changed test-lf test-shard-durations

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)

# Local loop: skip ML-marked tests, one worker per core, stop on the first failure.
test-fast:
	uv run pytest -m "not ml" -n auto --dist loadfile --no-cov -x -q

test-changed:
	$(ROOT)/scripts/test-changed.sh

test-lf:
	uv run pytest --lf -n auto --dist loadfile

# Rewrite tests/shard_durations.txt from a full fast-suite run.
test-shard-durations:
	$(ROOT)/scripts/record-shard-durations.py
