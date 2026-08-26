"""Typed domain errors used to fail safely and explain skipped trades."""


class GoldFlowError(Exception):
    """Base exception for expected application failures."""


class ConfigurationError(GoldFlowError):
    """Raised when safety-sensitive configuration is invalid."""


class BrokerError(GoldFlowError):
    """Raised when a broker operation cannot be completed safely."""


class BrokerUnavailableError(BrokerError):
    """Raised when the broker session or terminal is unavailable."""


class BrokerOperationUnsupported(BrokerError):
    """Raised when an adapter cannot perform a requested capability."""


class SymbolResolutionError(BrokerError):
    """Raised when a canonical instrument cannot be resolved safely."""


class RiskRejectedError(GoldFlowError):
    """Raised when a non-bypassable risk rule rejects an order."""


class DuplicateOrderError(RiskRejectedError):
    """Raised when order idempotency or position limits detect a duplicate."""


class InvalidStateTransition(GoldFlowError):
    """Raised when a trade attempts an invalid state-machine transition."""


class AuthenticationError(GoldFlowError):
    """Raised when API authentication or authorization fails."""
