"""ERP business logic (procure-to-pay, order-to-cash, inventory) shared by the API, SOAP and agent."""


class ERPError(ValueError):
    """A business-rule violation or a lookup that found nothing. Message is user-facing."""
