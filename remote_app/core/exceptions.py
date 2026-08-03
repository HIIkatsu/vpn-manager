class ServiceError(Exception):
    pass


class NotFoundError(ServiceError):
    pass


class PaymentActivationError(ServiceError):
    pass
