import chassis


class _StatusError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def test_connection_errors_are_transient():
    assert chassis.classify_error(Exception("connection reset")) == "transient"


def test_rate_limit_and_server_errors_are_transient():
    assert chassis.classify_error(_StatusError("too many requests", 429)) == "transient"
    assert chassis.classify_error(_StatusError("bad gateway", 502)) == "transient"


def test_bad_model_is_a_model_error():
    exc = _StatusError("deepseek/nonexistent is not a valid model ID", 400)
    assert chassis.classify_error(exc) == "model"
    exc404 = _StatusError("No endpoints found for model", 404)
    assert chassis.classify_error(exc404) == "model"


def test_other_400s_are_invalid_request():
    exc = _StatusError(
        "Messages with role 'tool' must be a response to a preceding message with 'tool_calls'",
        400,
    )
    assert chassis.classify_error(exc) == "invalid_request"


def test_404_without_model_mention_is_invalid_request():
    assert chassis.classify_error(_StatusError("not found", 404)) == "invalid_request"
