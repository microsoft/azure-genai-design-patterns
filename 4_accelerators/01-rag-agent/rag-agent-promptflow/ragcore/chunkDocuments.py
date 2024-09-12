from typing import List, Dict, Any
from ragcore.datamodels.document import Document
from ragcore.datamodels.enums import QueryType
from ragcore.datamodels.search_result import SearchResult
from ragcore.chunking.search_chunkers.acs_utils import result_to_dataclass
from ragcore.chunking.search_chunkers.cosmos_chunker import CosmosChunker
from ragcore.chunking.search_chunkers.chunker_factory import ChunkerFactory, DataSourceType
from ragcore.chunking.utils import documents_to_json_dict

def parse_results(results: List[Dict[str, Any]], data_source_type: DataSourceType) -> List[SearchResult]:
  """
  Parse the results from the search service.
  @param results: List of retrieved documents (Search Results in JSON format)
  @param data_source_type: Value can be "acs" or "cosmos"
  @return: List of search results
  """
  search_results: List[SearchResult] = []
  for result in results:

    docs: List = []

    if data_source_type == DataSourceType.ACS.value:
      docs = [result_to_dataclass(doc) for doc in result["top_k"]]
    elif data_source_type == DataSourceType.COSMOS.value:
      docs = [CosmosChunker.result_to_dataclass(result) for result in result["top_k"]]
        
    else:
      docs = result["top_k"]
      
    search_result = SearchResult(
      query=result["query"],
      top_k=docs
    )
    search_results.append(search_result)
  return search_results

def chunk_documents_core(results: List[Dict[str, Any]], max_tokens: int, top_k: int, data_source_type: DataSourceType, query_type: QueryType) -> List[List[Dict[str, Any]]]:
  """
  Chunk retrieved documents.
  @param result: List of retrieved documents
  @param max_tokens: Number of max tokens per chunk
  @param query: current user query
  @param top_k: number of search results
  @param data_source_type: Value can be "acs" or "cosmos"
  @param query_type: Value can be "simple", "semantic", "vector", "vector_simple_hybrid" or "vector_semantic_hybrid"

  @return: List of normalized documents
  """
  chunker = ChunkerFactory.create_chunker(data_source_type, query_type)
  parsed_results: List[SearchResult] = parse_results(results, data_source_type)
  
  chunked_documents: List[List[Document]] = chunker.chunk_results(results=parsed_results, max_tokens=max_tokens, top_k=top_k)

  return documents_to_json_dict(chunked_documents)