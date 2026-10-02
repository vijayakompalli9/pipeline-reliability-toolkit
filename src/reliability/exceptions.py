"""Custom exceptions. Each maps to a distinct failure mode an operator cares about."""


class ReliabilityError(Exception):
    """Base class for all toolkit errors."""


class ContractError(ReliabilityError):
    """A contract, reconciliation, or lineage YAML file is missing or invalid."""


class DataSourceError(ReliabilityError):
    """A data file could not be found or loaded into DuckDB."""


class CircuitOpenError(ReliabilityError):
    """Raised by the circuit breaker to block a downstream load."""

    def __init__(self, message: str, decision: object | None = None) -> None:
        super().__init__(message)
        self.decision = decision


class SummarizerError(ReliabilityError):
    """An incident summarizer provider failed or is misconfigured."""
