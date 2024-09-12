import re
from dataclasses import fields
from ragcore.datamodels.document import Document
from ragcore.datamodels.search_results_acs import SearchResultsACS, Metadata
from typing import Set, List, Dict, Any

FIELD_GUESSES = {
    "title": {"title"},
    "url": {"url", "uri", "link", "document_link"},
    "filepath": {"filepath", "filename"},
    "metadata": {"metadata"}
}

TITLE_REGEX = re.compile(r"[tT]itle: (.*)\n")

def parse_results_list(results: List[Dict[str, Any]]) -> List[SearchResultsACS]:
    """Transforms results into SearchResultsACS dataclass instances.
    @param results: List of search results
    """
    parsed_results: List[SearchResultsACS] = []
    for [i, result] in enumerate(results):
        parsed_result = result_to_dataclass(result)
        parsed_result.metadata.chunk_id = i
        parsed_results.append(parsed_result)

    return parsed_results

def extract_title_from_content(content: str) -> str:
    search = TITLE_REGEX.search(content)
    if search and search.group(1):
        return search.group(1).strip()
    return ""

def normalize_content_fields(obj: Dict[str, Any]) -> Dict[str, Any]:
    guessed_fields = dict()
    # Loop through all fields from the object

    for item_key in obj.keys():
        original_name = True
        item = obj.get(item_key)
        # It item is dictionary, make recursive call
        if isinstance(item, dict):
            item = normalize_content_fields(item)
        # Loop through map of field guesses, and try to find any field name that needs to be normalized
        for field_type in FIELD_GUESSES:
            # When field is matched, save the original value with the normalized name
            if item_key in FIELD_GUESSES[field_type]:
                guessed_fields[field_type] = item
                original_name = False
        # If name didn't need normalization, use original one
        if original_name == True:
            guessed_fields[item_key] = item
    return guessed_fields

def result_to_dataclass(result: Dict[str, Any]) -> SearchResultsACS:
        # Normalize field names
        result = normalize_content_fields(result)

        # Extract 1st level of properties
        metadata = None
        score = ""
        text = ""
        if isinstance(result, dict):
            metadata = result.get("metadata")
            score = result.get("score", 0)
            text = result.get("text", "")
        
        # Try to extract metadata properties
        search_score = None
        search_reranker_score = None
        search_highlights = None
        filepath = None
        url = None
        chunk_id = None
        title = None
        if isinstance(metadata, dict):
            search_score = metadata.get("@search.score", None)
            search_reranker_score = metadata.get("@search.reranker_score", None)
            search_highlights = metadata.get("@search.highlights", None)
            filepath = metadata.get("filepath", None)
            url = metadata.get("url", None)
            chunk_id = metadata.get("chunk_id", None)
            title = metadata.get("title", None)

        if not title and text:
            title = extract_title_from_content(text)

        metadata = Metadata(
            search_score=search_score, 
            search_reranker_score=search_reranker_score, 
            search_highlights=search_highlights, 
            filepath=filepath, 
            url=url, 
            chunk_id=chunk_id,
            title=title
        )

        formatted_result = SearchResultsACS(metadata=metadata, score=score, text=text)
        return formatted_result

def result_to_document(result: SearchResultsACS) -> Document:

    return Document(
        chunk_id=result.metadata.chunk_id,
        content=result.text,
        score=result.score,
        title=result.metadata.title,
        filepath=result.metadata.filepath,
        url=result.metadata.url
    )