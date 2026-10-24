"""Safe errors shared by the bootstrap and later domain modules."""


class AppError(Exception):
    """Messages and codes must be static, never copied from upstream exceptions."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
