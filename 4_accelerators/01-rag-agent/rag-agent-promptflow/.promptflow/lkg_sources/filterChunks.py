from promptflow import tool
from typing import List
from ragcore.filterChunks import select_chunks_core

@tool
def select_chunks(results: List, top_k: int, min_score: float = None) -> List:
  return select_chunks_core(results, top_k, min_score)