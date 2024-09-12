"""Data class for normalized Document

Classes:
    Document: Data class for normalized Document

"""
from typing import Optional
from dataclasses import dataclass

@dataclass
class Document(object):
    """
        Data class for chunked documents
    """
    content: str
    "Document content"
    chunk_id: str = None
    "Chunk Id"
    score: float = None
    "Similarity score"
    title: Optional[str] = None
    "Document title"
    filepath: Optional[str] = None
    "Document filepath"
    url: Optional[str] = None
    "Document URL"
    metadata: Optional[dict] = None
    "Document generated metadata"

    def __eq__(self, other) -> bool:
        if not isinstance(other, Document):
            return False
        
        return self.content == other.content and self.title == other.title and self.filepath == other.filepath