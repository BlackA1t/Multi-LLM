class MultiLLMError(Exception):
    """Base error safe to display after secret redaction."""


class PolicyDenied(MultiLLMError):
    pass


class AuthenticationError(MultiLLMError):
    pass


class ReauthenticationRequired(AuthenticationError):
    """The renewable session is unusable; explicit user sign-in is required."""


class CredentialStoreError(MultiLLMError):
    """Credential Manager operation failed without exposing secret material."""


class CredentialIntegrityError(CredentialStoreError):
    """A chunked credential failed manifest, size, encoding, or digest checks."""


class QuotaExceeded(MultiLLMError):
    def __init__(self, message: str, *, retry_after: str | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderResponseError(MultiLLMError):
    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class ModelMismatch(ProviderResponseError):
    pass
