import re
import time
from typing import List, Dict, Any
from ragcore.datamodels.document import Document
from ragcore.utils import get_tfidf_sim_scores
from ragcore.chunking.search_chunkers.acs_utils import result_to_document
from ragcore.chunking.search_chunkers.chunking_utils import chunk_onthefly, chunk_onthefly_with_highlights, update_doc_score
from ragcore.chunking.search_chunkers.base_chunker import BaseDocumentChunker
from ragcore.datamodels.enums import QueryType
from ragcore.datamodels.search_results_acs import SearchResultsACS
from ragcore.datamodels.search_result import SearchResult

class ACSTextChunker(BaseDocumentChunker):
    def __init__(self, query_type: QueryType):
        self.query_type = query_type
        # We need to expose these two properties in the constructor arguments
        self._content_field_separator: str = "\n"
        self._content_fields: List[str] = ["content"]

    def chunk_results(self, results: List[SearchResult], max_tokens: int, top_k: int) -> List[List[Document]]:
        if not results:
            # Dont do anything for empty results
            return []
        
        docs: List[List[Document]] = []
        
        for search_result in results:
            doc_list: List[Document] = []
            sim_scores = []
            highlight_list = []
            use_highlights = True

            result_docs: List[SearchResultsACS] = search_result.top_k
            query = search_result.query

            for result in result_docs:
                # When using hybrid search
                if result.metadata.search_score:
                    sim_score = result.metadata.search_score
                    model_sim_score = result.metadata.search_reranker_score
                    if model_sim_score:
                        sim_score = model_sim_score/4.0
                else:
                    sim_score = result.score
                sim_scores.append(sim_score)
                # # first parse the original doc
                doc = result_to_document(result)
                doc_list.append(doc)

                highlights = result.metadata.search_highlights

                if not highlights:
                    higlighted_text_org = ""
                    use_highlights = False
                else:
                    higlighted_text_org = self._content_field_separator.join(
                        [
                            ".".join(highlights[content_col]).strip() for content_col in self._content_fields
                            if content_col in highlights
                        ]
                    ).strip()
                highlight_list.append(higlighted_text_org)

            if use_highlights:
                chunked_results = chunk_onthefly_with_highlights(
                    results=doc_list, 
                    sim_scores=sim_scores,
                    max_chunk_size=max_tokens,
                    highlight_tag="<HIGH>",
                    highlight_tag_end="</HIGH>",
                    highlight_list=highlight_list,
                    top_k=top_k
                )
            else:
                chunked_results = chunk_onthefly(query=query, results=doc_list, sim_scores=sim_scores, max_chunk_size=max_tokens, top_k=top_k)

            if self.query_type == QueryType.SIMPLE.value:
                # Either we do on the fly chunking or just original document, we need sim scores
                # For on the fly chunking we need to use the chunked docs for similarity scores
                t1 = time.perf_counter()
                sim_scores = get_tfidf_sim_scores(
                    query=query,
                    document_list=[f"{doc.title}\n{doc.content}" for doc in chunked_results],
                    ngram=2
                )
                # DISCLAIMER: We add 0.2 score to ensure we dont filter documents in range [0.1, 0.3].
                # Correct filter threshold is pending for tfidf search. Since defualt therhsold is 0.3 this makes it 0.1 for simple search.
                chunked_results = [update_doc_score(doc, min(0.2+score, 1.0)) for doc, score in zip(chunked_results, sim_scores)]
                print(f"Sim Scores: {[doc.score for doc in chunked_results]} calculated in {(time.perf_counter()-t1)*1000:.3f}ms")

            docs.append(chunked_results)
        return docs
