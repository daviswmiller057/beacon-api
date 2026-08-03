class InteractionError(RuntimeError):
    pass


class UnsupportedIntentError(InteractionError):
    pass


class InteractionTaskNotFound(InteractionError):
    pass


class AmbiguousTaskError(InteractionError):
    pass
