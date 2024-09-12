from enum import Enum
from ragcore.datamodels.enums import QueryType, DataSourceType
from ragcore.chunking.search_chunkers.base_chunker import BaseDocumentChunker
from ragcore.chunking.search_chunkers.acs_text_chunker import ACSTextChunker
from ragcore.chunking.search_chunkers.acs_vector_chunker import ACSVectorChunker
from ragcore.chunking.search_chunkers.cosmos_chunker import CosmosChunker

class ChunkerFactory:
    @staticmethod
    def create_chunker(data_source_type: DataSourceType, query_type: QueryType) -> BaseDocumentChunker:
        if data_source_type == DataSourceType.ACS.value and query_type in [QueryType.SIMPLE.value, QueryType.SEMANTIC.value]:
            return ACSTextChunker(query_type)
        elif data_source_type == DataSourceType.ACS.value and query_type in [QueryType.VECTOR.value, QueryType.VECTOR_SIMPLE_HYBRID.value, QueryType.VECTOR_SEMANTIC_HYBRID.value]:
            return ACSVectorChunker(query_type)
        elif data_source_type == DataSourceType.COSMOS.value and query_type == QueryType.VECTOR.value:
            return CosmosChunker()
        else:
            raise Exception("Please select a valid Chunker configuration")