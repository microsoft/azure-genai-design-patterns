from typing import List
from ragcore.utils import get_conversation_text, clip_text, TokenEstimator

MAX_TURN_TOKENS = 500
MAX_TURNS = 2
TOKEN_ESTIMATOR = TokenEstimator()

def format_rewrite_intent_inputs_core(query: str, history: List, max_tokens: int) -> object:
  # Add user query to end.
  query = format_query(query)
  max_tokens -= TOKEN_ESTIMATOR.estimate_tokens(query)

  # Add conversation above it.
  conversation = get_conversation_text(history, max_tokens, MAX_TURN_TOKENS, MAX_TURNS) + "\n\n" + query
  return conversation

def format_query(query: str) -> str:
  query_content = clip_text(query, MAX_TURN_TOKENS)
  return f'user:\nCurrent user question:\n{query_content}\n\n'