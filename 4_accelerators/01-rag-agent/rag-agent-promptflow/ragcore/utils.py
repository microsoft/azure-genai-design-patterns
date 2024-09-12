import tiktoken
from bs4 import BeautifulSoup, Comment
import json
import re
import os
from typing import List, Optional, Union
from langdetect import detect
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from difflib import SequenceMatcher

tokenizer = tiktoken.get_encoding('gpt2')
SENTENCE_ENDINGS = ["\.", "!", "\?"]
WORDS_BREAKS = list(reversed([",", ";", ":", " ", "\(", "\)", "\[", "\]", "\{", "\}", "\t", "\n"]))

query_type_map = {
  'Keyword': 'simple',
  'Semantic': 'semantic',
  'Vector': 'vector',
  'Hybrid (vector + keyword)': 'vector_simple_hybrid',
  'Hybrid + semantic': 'vector_semantic_hybrid'
}

data_source_map = {
  'Azure AI Search': 'acs',
  'Cosmos': 'cosmos',
}

# Load stop words.
All_STOP_WORDS = {}
local_dir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(local_dir, "stopwords.jsonl"), encoding="utf-8") as f:
    lines = f.readlines()
    for line in lines:
        item = json.loads(line, strict=False)
        All_STOP_WORDS[item["lang"]] = item["stopwords"]

def sanitize_citation_content(content: str) -> str:
    try:
        # borrow from https://stackoverflow.com/questions/16861/sanitising-user-input-using-python
        rjs = r'[\s]*(&#x.{1,7})?'.join(list('javascript:'))
        rvb = r'[\s]*(&#x.{1,7})?'.join(list('vbscript:'))
        re_scripts = re.compile('(%s)|(%s)' % (rjs, rvb), re.IGNORECASE)
        validTags = 'p i strong b u a h1 h2 h3 pre br img button'.split()
        validAttrs = 'href src width height'.split()
        soup = BeautifulSoup(content, features="html.parser")
        for comment in soup.findAll(string=lambda text: isinstance(text, Comment)):
            # Get rid of comments
            comment.extract()
        for tag in soup.findAll(True):
            if tag.name not in validTags:
                tag.hidden = True
            attrs = tag.attrs
            tag.attrs = {}
            if not attrs:
                continue
            if isinstance(attrs, dict):
                attrs = attrs.items()
                for attr, val in attrs:
                    if attr in validAttrs:
                        val = re_scripts.sub('', val) # Remove scripts (vbs & js)
                        tag.attrs[attr] = val
            elif isinstance(attrs, list):
                for attr in attrs:
                    if attr in validAttrs:
                        val = re_scripts.sub('', attr)
                        tag.attrs[attr] = val

        return soup.renderContents().decode('utf8')
    except Exception as e:
        return content

def clip_text(text: str, max_tokens: int) -> str:
    return tokenizer.decode(tokenizer.encode(text, allowed_special='all')[:max_tokens])

def make_valid_json(text: str) -> str:
    # Verify matching terminators.
    stack = []
    pos = 0
    while pos < len(text):
        char = text[pos]
        if char == '{':
            stack.append('}')
        elif char == '[':
            stack.append(']')
        elif char == '"':
            stack.append('"')
            pos = pos + 1
            while pos < len(text):
                if text[pos] == '"' and text[pos - 1] != '\\':
                    stack.pop()
                    break
                pos = pos + 1
        elif stack and char == stack[-1]:
            stack.pop()
        pos = pos + 1
    
    # Verify doesn't end with a backslash.
    if text and text[-1] == '\\':
        text = text[:-1]
    
    # Add missing terminators.
    for char in reversed(stack):
        text = text + char
    return text

def strip_quotes_symmetric(text):
    while text.startswith(("'", '"')) and text.endswith(("'", '"')) and len(text) >= 2:
        text = text[1:-1]
    return text

def convert_string_to_list(list_string: str) -> List[str]:
    # convert string type list "["what is Azure", "what is virtual machine"]" to list of strings
    string_list = list()
    try:
        string_list = json.loads(list_string)
    except Exception as e1:
        list_string = bytes(list_string, "utf-8").decode("unicode_escape")
        try:
            string_list = json.loads(list_string)
        except Exception as e2:
            string_list = list_string
    if isinstance(string_list, str):
        string_list = [list_string]
    if not isinstance(string_list, list):
        string_list = [str(string_list)]
    string_list = [strip_quotes_symmetric(str(s)) for s in string_list]
    return string_list

def get_conversation_text(history: List, max_tokens: int, max_turn_tokens: int, max_turns: int = 0) -> str:
  conversation = ''
  turns = 0
  estimator = TokenEstimator()
  for turn in reversed(history):
    reply_content = clip_text(turn['outputs']['reply'], max_turn_tokens)
    reply_text = f'assistant:\n{reply_content}\n\n'
    max_tokens -= estimator.estimate_tokens(text=reply_text)
    turns = turns + 1
    if max_tokens <= 0 or (turns > max_turns and max_turns > 0):
      break
    conversation = reply_text + conversation
    query_content = clip_text(turn['inputs']['query'], max_turn_tokens)
    query_text = f'user:\n{query_content}\n\n'
    max_tokens -= estimator.estimate_tokens(text=query_text)
    turns = turns + 1
    if max_tokens <= 0 or (turns > max_turns and max_turns > 0):
      break
    conversation = query_text + conversation
  return conversation.strip()

def cleanup_content(content: str) -> str:
    output = re.sub(r"\n{2,}", "\n", content)
    output = re.sub(r"[^\S\n]{2,}", " ", output)
    output = re.sub(r"-{2,}", "--", output)
    return output.strip()

def get_tfidf_sim_scores(
        query: str,
        document_list: List[str],
        min_reply_chars: int = 0,
        ngram: int = 2
) -> List[float]:
    if not document_list:
        return []
    if not query:
        return [0.0]*len(document_list)

    query_lang = lang_detector(query)
    doc_langs = set([lang_detector(doc) for doc in document_list])

    if query_lang is None or len(doc_langs) != 1 or query_lang not in doc_langs:
        return [1.0]*len(document_list) # return high scores by default

    if (query.count(" ") > min_reply_chars) and (query_lang not in ['zh', 'ja', 'ko', 'th', 'vi']):
        if query_lang not in All_STOP_WORDS:
            query_lang = "en"
        if ngram == 1:
            vocabulary = set(query.split())
        else:
            tmp_vectorizer = TfidfVectorizer(
                decode_error='ignore',
                analyzer='word',
                stop_words=All_STOP_WORDS[query_lang],
                ngram_range=(1, ngram))
            try:
                tmp_vectorizer.fit_transform([query])
            except ValueError:
                return [1.0]*len(document_list) # return high scores if all words in query are stopwords
            vocabulary = tmp_vectorizer.get_feature_names_out()

        vectorizer = TfidfVectorizer(
            decode_error='ignore',
            analyzer='word',
            stop_words=All_STOP_WORDS[query_lang],
            ngram_range=(1, ngram),
            vocabulary=vocabulary
        )
        doc_sentences = document_list + [query]
        # Compute TF-IDF vectors for all sentences
        tfidf_matrix = vectorizer.fit_transform(doc_sentences)
        # Compute cosine similarity between the small string and all document sentences
        similarities = cosine_similarity(tfidf_matrix[-1:], tfidf_matrix[:-1])
        return similarities[0].tolist()
    else:
        query = clean_stopwords(query, query_lang)
        sim_scores = []
        for doc in document_list:
            doc_cleaned = clean_stopwords(doc, query_lang)
            matching_ratio = sequence_match_ratio(query, doc_cleaned) # add a default buffer score of 0.1 to reduce filtered docs
            sim_scores.append(matching_ratio)
        return sim_scores

def lang_detector(text: str) -> Optional[str]:
    MAX_CHAR_FOR_DETECTION = 500
    text_for_detection = text[:MAX_CHAR_FOR_DETECTION]
    try:
        lang = detect(text_for_detection)
    except Exception as e:
        print(f"Error in detecting language: {e} - Setting it as None")
        return None
    return lang

def clean_stopwords(s: str, detectedLang: str="") -> str:
    non_space_separated_langs = ['zh', 'ja', 'ko', 'th', 'vi']
    if detectedLang not in non_space_separated_langs:
        s_arr = s.split()
    else:
        s_arr = list(s)
    if detectedLang in All_STOP_WORDS:
        s_cleaned = [w for w in s_arr if w not in All_STOP_WORDS[detectedLang]]
        if detectedLang not in non_space_separated_langs:
            return " ".join(s_cleaned)
        else:
            return "".join(s_cleaned)
    else:
        return s

def sequence_match_ratio(s1, s2):
    if not s1 or not s2:
        return 1.0
    # Create SequenceMatcher object with input sequences
    seq_matcher = SequenceMatcher(None, s1, s2)

    # Get matching blocks
    match_blocks = seq_matcher.get_matching_blocks()

    # Calculate total matching length
    match_length = sum([match.size for match in match_blocks])

    # Calculate ratio wrt shorter sequence
    shorter_length = min(len(s1), len(s2))

    # Return ratio
    return match_length / shorter_length

def extract_intents_from_str_array(str_array: str) -> List[str]:
    intents = []
    i = 0

    # There needs to be an open square bracket in order to extract intents
    start = str_array.find('[')
    if start == -1:
        return intents
    else:
        i = start

    # Iterate whole array until all intents are found 
    while i < len(str_array):
        # Assume string will start with double quotes
        is_double_quotes = True

        # Omit characters that don't correspond to the string start
        while str_array[i] == '[' or str_array[i] == ',' or str_array[i] == ' ' or str_array[i] == '"' or str_array[i] == "'" or str_array[i] == '\\':
            
            # Check if string starts with single quote instead of double
            if str_array[i] == "'":
                is_double_quotes = False
            
            i += 1

            # Break if end of string
            if i == len(str_array):
                break

        if i == len(str_array) or str_array[i] == ']':
            break
        
        # Set single or double quote character as string delimiter
        quote_char = '"'
        if is_double_quotes == False:
            # Single quote
            quote_char = "'"

        # Find string element
        intent = ""
        while i < len(str_array) and str_array[i] != quote_char and str_array[i] != "," and str_array[i] != "[" and str_array[i] != "]":
            # Skip backslash
            if str_array[i] != '\\':
                intent += str_array[i]
            i += 1
            
        # If string element not empty, add to intents array
        if len(intent) > 0 :
            # Delete any duplicated single/double quotes or blank spaces around the string
            intents.append(intent.strip("'\" "))

    return intents

def convert_escaped_to_posix(escaped_path):
    windows_path = escaped_path.replace("\\\\", "\\")
    posix_path = windows_path.replace("\\", "/")
    return posix_path

class TokenEstimator(object):
    GPT2_TOKENIZER = tiktoken.get_encoding("gpt2")
    CHATGPT_TOKENIZER = tiktoken.get_encoding("cl100k_base")

    def estimate_tokens(self, text: Union[str, List]) -> int:
        if isinstance(text, str):
            return len(self.GPT2_TOKENIZER.encode(text, allowed_special="all"))
        else:
            # https://github.com/openai/openai-cookbook/blob/main/examples/How_to_count_tokens_with_tiktoken.ipynb
            tokens_per_message = 4  # every message follows <|start|>{role/name}\n{content}<|end|>\n
            tokens_per_name = -1    # if there's a name, the role is omitted
            num_tokens = 0
            for message in text:
                num_tokens += tokens_per_message
                for key, value in message.items():
                    num_tokens += len(self.CHATGPT_TOKENIZER.encode(value))
                    if key == "name":
                        num_tokens += tokens_per_name
            num_tokens += 3  # every reply is primed with <|start|>assistant<|message|>
            return num_tokens

    def construct_tokens_with_size(self, tokens: str, numofTokens: int) -> str:
        newTokens = self.GPT2_TOKENIZER.decode(
            self.GPT2_TOKENIZER.encode(tokens, allowed_special="all")[:numofTokens]
        )
        return newTokens