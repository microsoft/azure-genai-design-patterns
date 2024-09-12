from typing import List, Dict, Any
from enum import Enum
from ragcore.datamodels.document import Document
from ragcore.datamodels.search_result import SearchResult
from ragcore.chunking.search_chunkers.acs_utils import result_to_document
from ragcore.datamodels.search_results_acs import SearchResultsACS
from ragcore.datamodels.enums import QueryType
from ragcore.chunking.search_chunkers.base_chunker import BaseDocumentChunker
from ragcore.chunking.search_chunkers.chunking_utils import chunk_onthefly, update_doc_score
from ragcore.utils import get_tfidf_sim_scores

class ACSVectorChunker(BaseDocumentChunker):

    def __init__(self, query_type: QueryType):
        self.query_type = query_type

    def chunk_results(self, results: List[SearchResult], max_tokens: int, top_k: int) -> List[List[Document]]:
        
        docs: List[List[Document]] = []
        
        for search_result in results:
            doc_list: List[Document] = []

            result_docs: List[SearchResultsACS] = search_result.top_k
            query = search_result.query

            for result in result_docs:
                sim_score = result.metadata.search_score
                model_sim_score = result.metadata.search_reranker_score
                if model_sim_score:
                    sim_score = model_sim_score / 4.0
                doc = result_to_document(result)
                doc.score = sim_score
                doc_list.append(doc)
            
            print("Num docs before fusion:", len(doc_list))
            doc_list = sorted(doc_list, key=lambda x: x.score, reverse=True)[:top_k]   
            sim_scores: List[float] = [doc.score for doc in doc_list]  
        
            chunked_results = chunk_onthefly(
                query=query,
                results=doc_list,
                sim_scores=sim_scores,
                max_chunk_size=max_tokens,
                top_k=top_k
            )

            # get similarity scores since hybrid semantic search is not being used
            if self.query_type in [QueryType.VECTOR.value, QueryType.VECTOR_SIMPLE_HYBRID.value]:
                sim_scores = get_tfidf_sim_scores(
                    query=query,
                    document_list=[f"{doc.title}\n{doc.content}" for doc in chunked_results],
                    ngram=2
                )
                # DISCLAIMER: We add 0.2 score to ensure we dont filter documents in range [0.1, 0.3].
                # Correct filter threshold is pending for vector search. Since defualt therhsold is 0.3 this makes it 0.1 for simple search.
                sim_scores = [min(0.2+score, 1.0) for score in sim_scores]
                chunked_results = [update_doc_score(doc, score) for doc, score in zip(chunked_results, sim_scores)]

            docs.append(chunked_results)
        return docs
