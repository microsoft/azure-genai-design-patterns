from promptflow import tool
from typing import List

@tool
def should_query_data_source(search_intent: List) -> str:
  return len(search_intent) > 0