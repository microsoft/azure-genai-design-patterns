from ragcore.utils import estimate_tokens
from promptflow import tool

@tool
def should_rewrite_intent(query: str, last_intent: str) -> bool:
  return True
  #return True if last_intent and estimate_tokens(query) <= 120 and estimate_tokens(last_intent) <= 120 else False