"""Failures from a decisions-model call. These never include the API key."""


class JudgeError(RuntimeError):
    """The decisions call failed or the response was not usable."""


class JudgePaymentRequired(JudgeError):
    """HTTP 402. OpenRouter credits are exhausted or billing is blocked."""


class JudgeRateLimited(JudgeError):
    """HTTP 429 remained after the retry budget."""


class JudgeResponseError(JudgeError):
    """The body was missing a question or returned an unexpected answer type."""
