from typing import List, Dict, Tuple, Any
from ragcore.datamodels import Document, dataclass_to_dict, results_list_to_docs_list

def select_chunks_core(results: List[List[Dict[str, Any]]], top_k: int, min_score: float = None) -> List:
  RRF_CONST = 60
  chunk_rrf: Dict[str, Tuple[float, Document]] = {}

  results: List[List[Document]] = results_list_to_docs_list(results)

  # Loop through results from each query
  for list in results:
    # The default value for score was set to 1 in the previous version of the code for this method. 
    # Although, service doesn't has that default value, it is not necessary to set it here.
    sorted_chunks: List[Document] = sorted(list, key=lambda x: x.score, reverse=True)
    for [rank, chunk] in enumerate(sorted_chunks, start=1):
      if min_score and chunk.score < min_score:
        continue
      chunk_key = ( chunk.title, chunk.filepath, chunk.content )
      rrf = chunk_rrf.get(chunk_key, (0, None))[0] + 1.0 / (rank + RRF_CONST)
      chunk_rrf[chunk_key] = (rrf, chunk )

  sorted_by_rrf = sorted(chunk_rrf.items(), key=lambda x: x[1][0], reverse=True)
  
  # The structure of the results should follow the structure of the Document dataclass
  new_results: List[Dict[str, Any]] = []
  
  for _, (rrf, chunk) in sorted_by_rrf:
    # Convert Document to dictionaries
    new_chunk = dataclass_to_dict(chunk)
    new_chunk['score'] = rrf
    new_results.append(new_chunk)
    if len(new_results) >= top_k:
      break
  return new_results