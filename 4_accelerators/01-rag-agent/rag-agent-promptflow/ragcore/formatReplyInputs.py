from typing import List
from ragcore.utils import clip_text, make_valid_json, get_conversation_text, TokenEstimator
import json

MAX_TURN_TOKENS = 1000
TOKEN_ESTIMATOR = TokenEstimator()

def format_generate_reply_inputs_core(query: str, history: List, chunks: List, max_conversation_tokens: int, max_tokens: int) -> object:
  # Construct query text respecting token limits.
  query = clip_text(query, max_tokens) if max_tokens > 10 else ''
  max_tokens -= TOKEN_ESTIMATOR.estimate_tokens(text=query)

  # Construct conversation text respecting token limits.
  conversation = get_conversation_text(history, max_conversation_tokens, MAX_TURN_TOKENS)
  max_tokens -= TOKEN_ESTIMATOR.estimate_tokens(text=conversation)

  # Construct documentation text respecting token limits.
  documentation = get_documentation_text(chunks, max_tokens) if max_tokens > 10 else ''

  return {
    'query': query,
    'conversation': conversation,
    'documentation': documentation
  }

def get_documentation_text(chunks: List, max_tokens: int) -> str:
  # Convert chunks into JSON string.
  chunks_list = []
  for chunk_index in range(len(chunks)):
    chunk = chunks[chunk_index]
    chunk_text = {
      f'[doc{chunk_index + 1}]': {
        'title': chunk.get('title', ''),
        'content': chunk['content']
      }
    }
    chunks_list.append(chunk_text)
  docs_obj = { 'retrieved_documents': chunks_list }
  docs_text = json.dumps(docs_obj)

  # Clip string and verify still valid JSON.
  docs_text = clip_text(docs_text, max_tokens)
  docs_text = make_valid_json(docs_text)
  return docs_text
