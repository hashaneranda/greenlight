class DomainError(Exception):
    """A business rule was broken. The message is safe to show to the user."""


class NotPermitted(DomainError):
    pass


class InvalidRequest(DomainError):
    pass


class InvalidState(DomainError):
    pass


class NoApproverAvailable(DomainError):
    pass
