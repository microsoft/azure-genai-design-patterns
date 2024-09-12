""" Data class for Search results from Cosmos using Vector search

Classes:
    SearchResultsCosmosVector: Data class for Search results from Cosmos using Vector search
"""

from dataclasses import dataclass
from typing import Optional, Any, Dict

@dataclass
class SearchResultsCosmosVector(object):
    chunk_id: str = None,
    id: str = None
    title: str = ""
    content: str = ""
    filepath: str = ""
    url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None