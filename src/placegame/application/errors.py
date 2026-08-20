class ApplicationError(RuntimeError):
    """A stable, safe application-layer error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class PlanInProgress(ApplicationError):
    def __init__(self) -> None:
        super().__init__("plan_in_progress")


class GameContractChanged(ApplicationError):
    def __init__(self) -> None:
        super().__init__("game_contract_changed")


class IdleReconciliationRequired(ApplicationError):
    def __init__(self) -> None:
        super().__init__("reconciliation_required")
