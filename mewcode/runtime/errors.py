from __future__ import annotations


class RuntimeCompositionError(Exception):
    """A scenario could not be resolved or assembled."""


class ScenarioValidationError(RuntimeCompositionError):
    """A scenario definition is malformed."""


class CapabilityUnavailableError(RuntimeCompositionError):
    """A required scenario capability is unavailable."""
