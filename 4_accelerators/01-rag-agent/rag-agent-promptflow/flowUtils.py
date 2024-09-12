from typing import List, Dict, Any

def transform_retrieval_response(retrieval_responses: List[List[Dict[str, Any]]], queries: List) -> List[Dict[str, Any]]:
    transformed_retrieval_responses = list()
    for query, query_retrieval_responses in zip(queries, retrieval_responses):
        transformed_mapping = dict()
        transformed_query_responses = [transform_raw_retrieval_doc(raw_doc) for raw_doc in query_retrieval_responses]
        transformed_mapping["top_k"] = transformed_query_responses
        transformed_mapping["query"] = query
        transformed_retrieval_responses.append(transformed_mapping)
    return transformed_retrieval_responses

def transform_raw_retrieval_doc(raw_doc: Dict[str, Any]) -> Dict[str, Any]:
    transformed_doc = dict()
    transformed_doc['text'] = raw_doc['text']
    transformed_doc['score'] = raw_doc['score']
    transformed_metadata = dict()
    field_to_extract_metadata_from = None
    if 'additional_fields' in raw_doc:
        field_to_extract_metadata_from = raw_doc['additional_fields']  
    elif 'metadata' in raw_doc:
        field_to_extract_metadata_from = raw_doc['metadata'] 
    if field_to_extract_metadata_from:
        transformed_metadata['id'] = field_to_extract_metadata_from.get('id', '')
        transformed_metadata['title'] = field_to_extract_metadata_from.get('title', '')
        transformed_metadata['url'] = field_to_extract_metadata_from.get('url', '')
        transformed_metadata['filepath'] = field_to_extract_metadata_from.get('filepath', '')
        transformed_metadata['contentVector'] = field_to_extract_metadata_from.get('contentVector')
        transformed_metadata['meta_json_string'] = field_to_extract_metadata_from.get('meta_json_string')
        transformed_metadata['@search.score'] = field_to_extract_metadata_from.get('@search.score')
        transformed_metadata['@search.reranker_score'] = field_to_extract_metadata_from.get('@search.reranker_score')
        transformed_metadata['@search.highlights'] = field_to_extract_metadata_from.get('@search.highlights')
        transformed_metadata['@search.captions'] = field_to_extract_metadata_from.get('@search.captions')
        transformed_metadata['captions'] = field_to_extract_metadata_from.get('captions')
        transformed_metadata['answers'] = field_to_extract_metadata_from.get('answers')

    transformed_doc['metadata'] = transformed_metadata
    return transformed_doc
