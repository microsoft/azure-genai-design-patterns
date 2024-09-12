from promptflow import tool
from typing import List, Dict, Any
from flowUtils import transform_retrieval_response
from ragcore.chunkDocuments import chunk_documents_core
from ragcore.utils import query_type_map, data_source_map

# Query Types:
# - Keyword
# - Semantic
# - Vector
# - Hybrid (vector + keyword)
# - Hybrid + semantic

# Data Source Types:
# - Azure AI Search
# - Cosmos

@tool
def chunk_documents(results: List[List[Dict[str, Any]]], max_tokens: int, queries: List, top_k: int, data_source: str, query_type: str) -> List:
  # Check if Query Type is valid
  if query_type not in query_type_map:
    raise Exception(f"Invalid Query Type: {query_type}")
  
  # Check if Data Source Type is valid
  if data_source not in data_source_map:
    raise Exception(f"Invalid Data Source: {data_source}")
  transformed_results = transform_retrieval_response(results, queries)
  
  return chunk_documents_core(transformed_results, max_tokens, top_k, data_source_map[data_source], query_type_map[query_type])