class IsoTpValidationError(ValueError):
    """Base error for semantically invalid ISO-TP data or state."""


class IsoTpFrameParseError(IsoTpValidationError):
    """Raised when raw CAN data is not a valid ISO-TP frame."""


class IsoTpReassemblyError(IsoTpValidationError):
    """Raised when ISO-TP frames cannot form a valid payload."""


class IsoTpSegmentationError(IsoTpValidationError):
    """Raised when a payload cannot be segmented into ISO-TP frames."""


class IsoTpTransportError(Exception):
    """Base error for failures exposed by the ISO-TP transport API."""


class IsoTpPayloadError(IsoTpTransportError):
    """Raised when an outgoing payload cannot be transported."""


class IsoTpTimeoutError(IsoTpTransportError):
    pass


class IsoTpProtocolError(IsoTpTransportError):
    pass


class IsoTpFlowControlError(IsoTpTransportError):
    pass


class IsoTpCanError(IsoTpTransportError):
    pass
