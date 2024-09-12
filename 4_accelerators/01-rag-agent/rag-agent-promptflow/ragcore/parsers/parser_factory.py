"Parser factory for using parsers"
from typing import List
from ragcore.parsers.base_parser import BaseParser
from ragcore.parsers.parsers import (
    HTMLParser,
    MarkdownParser,
    TextParser,
    PythonParser,
    CSVParser
)
from ragcore.exceptions import UnsupportedFormatError


class ParserFactory:
    def __init__(self):
        self._parsers = {
            "html": HTMLParser(),
            "text": TextParser(),
            "markdown": MarkdownParser(),
            "python": PythonParser(),
            "csv": CSVParser()
        }

    @property
    def supported_formats(self) -> List[str]:
        "Returns a list of supported formats"
        return list(self._parsers.keys())

    def __call__(self, file_format: str, use_fr: bool = False) -> BaseParser:
        parser = self._parsers.get(file_format, None)
        if file_format == "pdf":
            if use_fr:
                parser = self._parsers.get("html", None)
            else:
                parser = self._parsers.get("text", None)
        if parser is None:
            raise UnsupportedFormatError(f"{file_format} is not supported")

        return parser
