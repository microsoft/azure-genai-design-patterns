from typing import List, Dict, Any
from ragcore.datamodels.document import Document
from ragcore.chunking.search_chunkers.base_chunker import BaseDocumentChunker
from ragcore.datamodels.search_result import SearchResult
from ragcore.chunking.search_chunkers.chunking_utils import chunk_onthefly, get_tfidf_sim_scores, update_doc_score
from ragcore.datamodels.search_results_cosmos_vector import SearchResultsCosmosVector

class CosmosChunker(BaseDocumentChunker):
    def chunk_results(self, results: List[SearchResult[SearchResultsCosmosVector]], max_tokens: int, top_k: int) -> List[List[Document]]:
        
        docs: List[List[Document]] = []

        for search_results in results:
            doc_list: List[Document] = []
            sim_scores: List[float] = []
            
            result_docs = search_results.top_k
            query = search_results.query

            for result in result_docs:
                doc = self._result_to_document(result)
                doc_list.append(doc)
                sim_scores.append(doc.score)

            # chunk on the fly
            chunked_result_doc = chunk_onthefly(query=query, results=doc_list, sim_scores=sim_scores, max_chunk_size=max_tokens, top_k=top_k) 
                
            # get similarity scores since cosmos vector search didn't return it
            sim_scores = get_tfidf_sim_scores(
                query= query,
                document_list=[f"{doc.title}\n{doc.content}" for doc in chunked_result_doc],
                ngram = 2)
            
            # DISCLAIMER: Add 0.2 score to ensure we dont filter documents in range [0.1, 0.3].
            # Correct filter threshold is pending for Cosmos vector search. 
            # Since defualt therhsold is 0.3 this makes it 0.1 for simple search.
            sim_scores = [min(0.2+score, 1.0) for score in sim_scores]

            # Update the scores of the documents
            chunked_results = [update_doc_score(doc, score) for doc, score in zip(chunked_result_doc, sim_scores)]        
                
            docs.append(chunked_results)
        return docs
    
    @staticmethod
    def result_to_dataclass(result: Dict[str, Any]) -> SearchResultsCosmosVector:
        """Convert the result to a dataclass object representing the result from Cosmos Vector search"""
        return SearchResultsCosmosVector(
            id=result.get("id", None),
            chunk_id=result.get("chunk_id", None),
            title=result.get("title", ""),
            content=result.get("content", ""),
            filepath=result.get("filepath", ""),
            url=result.get("url", None),
            metadata=result.get("metadata", None)
        )
    
    def _result_to_document(self, result: SearchResultsCosmosVector) -> Document:
        """Convert the Cosmos Vector search result to a standard Document"""
        return Document(
            content=result.content,
            title=result.title,
            filepath=result.filepath,
            url=result.url,
            metadata=result.metadata,
            score=1.0
        )