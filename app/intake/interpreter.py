from datetime import date
from typing import Protocol

from app.models import StructuredIntent


class InterpreterError(RuntimeError):
    """An interpreter failed without allowing unvalidated output downstream."""


class InterpreterConfigurationError(InterpreterError):
    pass


class InterpreterResponseError(InterpreterError):
    pass


class IntentInterpreter(Protocol):
    """Provider-neutral natural-language boundary."""

    def interpret(
        self,
        message: str,
        reference_date: date | None = None,
    ) -> StructuredIntent:
        """Return one fully validated description of the user's intent."""
        ...
