class TransientError(Exception):
    """Rate limit, timeout or 5xx from an external service; the task should retry later."""


class PipelineError(Exception):
    """Permanent problem with this document or configuration; retrying won't help."""
