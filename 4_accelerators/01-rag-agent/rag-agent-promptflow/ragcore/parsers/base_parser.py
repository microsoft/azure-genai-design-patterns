"""Parsers can take a file content (and optionally file name) and parse it to create a 
Document.
"""
from abc import ABC, abstractmethod
import os
from ragcore.datamodels.document import Document

from typing import List, Optional


class BaseParser(ABC):
    """A parser parses content to produce a document."""

    @abstractmethod
    def parse(self, content: str, file_name: Optional[str] = None) -> Document:
        """Parses the given content.
        Args:
            content (str): The content to parse.
            file_name (str): The file name associated with the content.
        Returns:
            Document: The parsed document.
        """
        pass

    def parse_file(self, file_path: str) -> Document:
        """Parses the given file.
        Args:
            file_path (str): The file to parse.
        Returns:
            Document: The parsed document.
        """
        with open(file_path, "r") as f:
            return self.parse(f.read(), os.path.basename(file_path))

    def parse_directory(self, directory_path: str) -> List[Document]:
        """Parses the given directory.
        Args:
            directory_path (str): The directory to parse.
        Returns:
            List[Document]: List of parsed documents.
        """
        documents = []
        for file_name in os.listdir(directory_path):
            file_path = os.path.join(directory_path, file_name)
            if os.path.isfile(file_path):
                documents.append(self.parse_file(file_path))
        return documents
