class InvalidSecret(ValueError):
    """Raised when encrypted data cannot be authenticated or decoded."""


class GameError(RuntimeError):
    """Base class for stable errors returned by the game API boundary."""


class AccountError(RuntimeError):
    """Base class for stable account-service errors."""


class AccountNotFound(AccountError):
    def __init__(self) -> None:
        super().__init__("account not found")


class AccountIdentityConflict(AccountError):
    """The authoritative game account identity does not match this record."""

    def __init__(self) -> None:
        super().__init__("account identity conflict")


class AccountDisabled(AccountError):
    def __init__(self) -> None:
        super().__init__("account disabled")


class AccountPaused(AccountError):
    def __init__(self) -> None:
        super().__init__("account paused")


class AccountRemoved(AccountError):
    def __init__(self) -> None:
        super().__init__("account removed")


class AuthenticationRequired(AccountError):
    def __init__(self) -> None:
        super().__init__("authentication required")


class PolicyUnavailable(AccountError):
    def __init__(self) -> None:
        super().__init__("policy unavailable")


class ReconciliationRequired(AccountError):
    def __init__(self) -> None:
        super().__init__("mutation reconciliation required")


class PlanPreconditionFailed(AccountError):
    def __init__(self) -> None:
        super().__init__("plan precondition failed")


class SessionRejected(GameError):
    pass


class ContractChanged(GameError):
    pass


class InventoryFull(GameError):
    pass


class InsufficientResource(GameError):
    def __init__(self, metadata: dict[str, object] | None = None) -> None:
        self.metadata = metadata or {}
        super().__init__("insufficient resource")


class GameConflict(GameError):
    def __init__(self, code: str | None = None) -> None:
        self.code = code
        super().__init__(code or "game conflict")


class GameRateLimited(GameError):
    def __init__(self, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__("game rate limited")


class AmbiguousMutation(GameError):
    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"ambiguous mutation: {operation}")


class GameUnavailable(GameError):
    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"game unavailable: {operation}")


class GameSchemaMismatch(GameError):
    def __init__(self, operation: str, metadata: dict[str, object]) -> None:
        self.operation = operation
        self.metadata = metadata
        super().__init__(f"unexpected response schema: {operation}")


class GameHttpError(GameError):
    def __init__(self, operation: str, metadata: dict[str, object]) -> None:
        self.operation = operation
        self.metadata = metadata
        super().__init__(f"game HTTP error: {operation}")
