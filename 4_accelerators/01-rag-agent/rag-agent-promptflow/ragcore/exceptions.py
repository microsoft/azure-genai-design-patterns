class UnsupportedFormatError(Exception):
    """Exception raised when a format is not supported by a parser."""

    def init(self, message: str):
        self.message = message
        self.code = 400