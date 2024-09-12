"""Data class for Search Result for any retriever

Classes:
    SearchResult: Data class for Search result from any retriever
"""
from dataclasses import dataclass
from typing import List, Optional, Generic, TypeVar, Dict, ClassVar
from typing_extensions import TypeAlias

T = TypeVar("T")
Latency: TypeAlias = Dict[str, float]

@dataclass
class SearchResult(Generic[T]):
    """ Data class for Search result from any retriever"""
    
    top_k: List[T]
    "The top k results from the retriever"

    query: Optional[str] = None
    "The query used to retrieve the results"
    
    latency: Optional[Latency] = None
    "latency of the search"

    DEFAULT_LATENCY: ClassVar[Latency] = {
        "tpromptLatency": -1.0,
        "vectorSearchLatency": -1.0,
    }
    "default value for latency"

    def __post_init__(self):
        if self.latency is None:
            self.latency = self.DEFAULT_LATENCY
