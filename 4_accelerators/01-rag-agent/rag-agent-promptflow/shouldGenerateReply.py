from promptflow import tool
from typing import List

@tool
def should_generate_reply(queries: List[str], chunks: List) -> bool:
  return True
  #return True if chunks or not queries else False