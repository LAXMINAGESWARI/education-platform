"""Chat-guard eval.

``python -m evals.guard.run`` (or ``scripts/eval_chat_guard.py``) scores the
versioned dataset. Policy chat off-topic is GPT scope (``gpt_scope``), after
regex, one Jev injection call, and the short-message check.
"""
