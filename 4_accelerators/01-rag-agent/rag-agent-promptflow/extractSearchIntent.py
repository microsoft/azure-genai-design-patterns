from promptflow import tool
from typing import List
from ragcore.extractSearchIntent import extract_search_intent_core

@tool
def extract_search_intent(intent) -> List:
    return extract_search_intent_core(intent)
