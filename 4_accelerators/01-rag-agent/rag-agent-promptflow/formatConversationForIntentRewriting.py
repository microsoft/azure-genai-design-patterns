from promptflow import tool
from typing import List
from ragcore.formatConversationForIntentRewriting import format_rewrite_intent_inputs_core

@tool
def format_rewrite_intent_inputs(query: str, history: List, max_tokens: int) -> object:
  return format_rewrite_intent_inputs_core(query, history, max_tokens)