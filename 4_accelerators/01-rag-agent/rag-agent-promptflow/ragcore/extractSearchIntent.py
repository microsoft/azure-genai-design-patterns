from ragcore.utils import extract_intents_from_str_array
from typing import List
import re

def extract_search_intent_core(intent: str) -> List:
    intent_list = extract_intents_from_str_array(intent)
    return intent_list