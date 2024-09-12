""" Data class for Search results from ACS

Classes:
    SearchResultsACSSemantic: Data class for Search results from ACS
    Metadata: Data class for metadata in SearchResultsACS
"""

from dataclasses import dataclass
from typing import Optional, List

@dataclass
class Metadata(object):
    search_score: Optional[float] = None
    search_reranker_score: Optional[float] = None
    search_highlights: Optional[List[str]] = None
    filepath: Optional[str] = None
    url: Optional[str] = None
    chunk_id: Optional[str] = None
    title: Optional[str] = None

@dataclass
class SearchResultsACS(object):
    metadata: Metadata
    score: float = 0
    text: str = ""
