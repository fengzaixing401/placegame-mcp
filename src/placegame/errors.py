class InvalidSecret(ValueError):
    """Raised when encrypted data cannot be authenticated or decoded."""


class GameError(RuntimeError):
    """Base class for stable errors returned by the game API boundary."""


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

    @classmethod
    def from_redacted_response(cls, response) -> "InsufficientResource":
        from placegame.game.client import redact_response_metadata

        return cls(redact_response_metadata(response))


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
