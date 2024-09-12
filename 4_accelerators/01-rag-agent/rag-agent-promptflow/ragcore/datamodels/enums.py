from enum import Enum

class QueryType(Enum):
    SIMPLE = 'simple'
    SEMANTIC = 'semantic'
    VECTOR = 'vector'
    VECTOR_SIMPLE_HYBRID = 'vector_simple_hybrid'
    VECTOR_SEMANTIC_HYBRID = 'vector_semantic_hybrid'
    
class DataSourceType(Enum):
    ACS = 'acs'
    COSMOS = 'cosmos'