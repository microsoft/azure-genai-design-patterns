from dataclasses import fields, dataclass
from ragcore.datamodels.document import Document
from typing import Dict, Any, List

def dataclass_to_dict(obj: dataclass) -> Dict[str, Any]:
    return {field.name: getattr(obj, field.name) for field in fields(obj)}

def results_list_to_docs_list(obj: List[List[Dict[str, Any]]]) -> List[List[Document]]:
    """Transforms json results to Document objects"""
    docs_list: List[List[Document]] = []
    for list in obj:
        docs_list.append([Document(**doc) for doc in list])
    return docs_list