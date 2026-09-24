class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, retryable: bool = False):
        self.code, self.message, self.status, self.retryable = code, message, status, retryable
        super().__init__(code)


class ProviderError(AppError):
    def __init__(self, code, message, *, uncertain=False, retryable=False):
        super().__init__(code, message, 502, retryable)
        self.uncertain = uncertain


def error_dict(error, request_id=None):
    return dict(code=error.code, message=error.message, request_id=request_id, retryable=error.retryable)
