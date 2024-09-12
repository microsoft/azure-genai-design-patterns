from promptflow import tool
from typing import List
from ragcore.formatReplyInputs import format_generate_reply_inputs_core

@tool
def format_generate_reply_inputs(query: str, history: List, chunks: List, max_conversation_tokens: int, max_tokens: int) -> object:
  return format_generate_reply_inputs_core(query, history, chunks, max_conversation_tokens, max_tokens)
