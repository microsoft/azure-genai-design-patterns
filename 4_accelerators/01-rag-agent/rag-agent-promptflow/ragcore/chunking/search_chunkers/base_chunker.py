""" Base class for Document Chunker

Classes:
    BaseDocumentChunker: Base class for Document Chunker
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Dict, Any
from ragcore.datamodels import Document
from ragcore.datamodels.search_result import SearchResult

class BaseDocumentChunker(ABC):

    def __init__(self):
        pass

    @staticmethod
    @abstractmethod
    def chunk_results(results: List[SearchResult], max_tokens: int, top_k: int) -> List[List[Document]]:
        pass