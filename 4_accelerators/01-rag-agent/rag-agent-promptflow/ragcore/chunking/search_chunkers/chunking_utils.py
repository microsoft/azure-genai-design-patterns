
from typing import List, Tuple
from copy import deepcopy as dc
from collections import defaultdict
from ragcore.utils import get_tfidf_sim_scores
from ragcore.parsers.parser_factory import ParserFactory
from ragcore.datamodels.document import Document
from ragcore.utils import TokenEstimator
from ragcore.chunking.text_chunker import TextChunker

SENTENCE_ENDINGS = [".", "!", "?"]
WORDS_BREAKS = list(
    reversed([",", ";", ":", " ", "(", ")", "[", "]", "{", "}", "\t", "\n"]))

parser_factory = ParserFactory()
TEXT_CHUNKER = TextChunker(version="v2")
TOKEN_ESTIMATOR = TokenEstimator()

def update_doc_score(doc: Document, score: float):
    """Update the score of the document with the given value"""
    doc.score = score
    return doc

def combine_docs_with_scores(docs: List[Document], scores: List[float]) -> List[Document]:
    updated_docs = []
    for doc, score in zip(docs, scores):
        updated_doc = doc
        updated_doc.score = score
        updated_docs.append(updated_doc)
    return updated_docs

# TODO: Probably delete highlight tags
def chunk_onthefly_with_highlights(results: List[Document], sim_scores: List[float], highlight_list: List[str],
                                   highlight_tag: str, highlight_tag_end: str,
                                   max_chunk_size: int = 1050, top_k: int = 5) -> List[Document]:
    """

    @param results: List of search result from teh source
    @param highlight_list: List of highlight strings
    @param highlight_tag: tag like <HIGH> to denote start of a highlight
    @param highlight_tag_end: tag like </HIGH> to denote start of a highlight
    @param max_chunk_size: Size of chunks
    @return: SearchResult after resolving chunking on the fly
    """
    if results is None or len(results) == 0:
        return []
    if not highlight_list or len(highlight_list) != len(results):
        raise Exception("Please provide a highlight list with highligthed content for each top K result.")
    if not highlight_tag or not highlight_tag_end:
        raise Exception("Please provide a start and end tag for the highlight in your highlight_list")
    if not sim_scores or len(sim_scores) != len(results):
        raise Exception("Please provide sim scores for each document")
    potential_docs = []
    is_any_doc_chunked = False
    filepath_chunk_id_dict = defaultdict(int)
    for doc, sim_score, higlighted_text_org in zip(results, sim_scores, highlight_list):
        content = doc.content
        num_tokens = TOKEN_ESTIMATOR.estimate_tokens(content)
        base_highlight_count = higlighted_text_org.count(highlight_tag)
        original_metadata = (
            f"orignal document size={num_tokens}. Scores={sim_score}"
            f"Org Highlight count={base_highlight_count}."
        )
        base_chunkid = 0
        if doc.filepath:
            base_chunkid += filepath_chunk_id_dict[doc.filepath]
        num_chunks = 0
        if num_tokens <= max_chunk_size or len(higlighted_text_org) == 0:
            doc.metadata = {"chunking": original_metadata}
            doc.chunk_id = f"{base_chunkid}"
            potential_docs.append(
                (doc, base_highlight_count, num_tokens, sim_score)
            )
            num_chunks += 1
        else:
            is_any_doc_chunked = True
            if higlighted_text_org:
                num_tokens_highlights = TOKEN_ESTIMATOR.estimate_tokens(higlighted_text_org)
                if num_tokens_highlights < max_chunk_size:
                    new_doc = dc(doc)
                    new_doc.content = higlighted_text_org
                    new_doc.metadata = {"chunking": (
                            original_metadata
                            + f"Filtering to highlight size={num_tokens_highlights}"
                    )}
                    new_doc.chunk_id = f"{base_chunkid}"
                    # we havent changed non-highlighted result.
                    potential_docs.append(
                        (
                            new_doc,
                            base_highlight_count,
                            num_tokens_highlights,
                            sim_score,
                        )
                    )
                    num_chunks += 1
                else:
                    chunks = TEXT_CHUNKER.chunk_content(
                        content=higlighted_text_org,  # TODO: move to org text later
                        num_tokens=max_chunk_size,
                        file_name=doc.filepath
                    )
                    for cidx, chunk in enumerate(chunks):
                        new_doc = dc(doc)
                        new_doc.content = chunk.content.replace(highlight_tag, "").replace(highlight_tag_end, "")
                        this_chunk_highlights = chunk.content.count(highlight_tag)
                        num_tokens_chunk = TOKEN_ESTIMATOR.estimate_tokens(new_doc.content)
                        if num_tokens_chunk < 2:
                            # skip empty chunks
                            continue
                        new_doc.metadata = {"chunking": (
                                original_metadata
                                + f"Filtering to chunk no. {cidx}/Highlights={this_chunk_highlights}"
                                  f" of size={num_tokens_chunk}"
                        )}
                        new_doc.chunk_id = str(base_chunkid + cidx)
                        potential_docs.append(
                            (
                                new_doc,
                                this_chunk_highlights,
                                num_tokens_chunk,
                                sim_score,
                            )
                        )
                        num_chunks += 1
        if doc.filepath:
            filepath_chunk_id_dict[doc.filepath] += num_chunks
    if is_any_doc_chunked:
        # scale highlight count with semantic score/BM25score             
        potential_docs = sorted(
            potential_docs, key=lambda tup: tup[1] * tup[3] / tup[2] if tup[2]>0 else 0, reverse=True
        )
        docs_with_simscores = [(tup[0], float(tup[1] * tup[3] / tup[2]) if tup[2]>0 else 0) for tup in potential_docs]
    else:
        return results # send base results without modification

    sorted_docs = sorted(docs_with_simscores, key=lambda tup: tup[1], reverse=True)[:top_k]
    final_docs = [tup[0] for tup in sorted_docs]
    final_sim_scores = [tup[1] for tup in sorted_docs]
    return combine_docs_with_scores(final_docs, final_sim_scores) # Update docs with sim scores

def chunk_onthefly(query: str, results: List[Document], sim_scores: List[float], max_chunk_size:int, top_k: int) -> List[Document]:
    """
    Do chunking on the fly without using hit highlights.
    @param result:
    @param max_chunk_size:
    @return:
    """
    if not results:
        return []
    if not sim_scores or len(sim_scores) != len(results):
        sim_scores = [1.0]*len(results) # we dont use sim scores for this right now so we can just assume to be 1.0 if not available
    if not query:
        raise Exception("Please provide a query to compare against for reranking on the fly chunks")
    
    potential_docs: List[Tuple[Document, float, int]] = []
    filepath_chunk_id_dict = defaultdict(int)

    for doc, sim_score in zip(results, sim_scores):
        content = doc.content
        num_tokens = TOKEN_ESTIMATOR.estimate_tokens(content)
        base_chunkid = 0
        if doc.filepath:
            base_chunkid += filepath_chunk_id_dict[doc.filepath]
        if num_tokens <= max_chunk_size:
            doc.chunk_id = f"{base_chunkid}"
            potential_docs.append((doc, sim_score, num_tokens))
        else:
            chunks = TEXT_CHUNKER.chunk_content(
                content=doc.content,
                num_tokens=max_chunk_size,
                file_name=doc.filepath
            )
            for cidx, chunk in enumerate(chunks):
                new_doc = dc(doc)
                new_doc.content = chunk.content
                new_doc.chunk_id = str(base_chunkid + cidx)
                num_tokens_chunk = TOKEN_ESTIMATOR.estimate_tokens(new_doc.content)
                potential_docs.append((new_doc, sim_score, num_tokens_chunk))

    if len(potential_docs) == len(results):
        return results
    sim_scores = get_tfidf_sim_scores(query, [tup[0].content for tup in potential_docs])

    new_docs: List[Tuple[Document, float, int]] = []
    for tfidf_score, tup in zip(sim_scores, potential_docs):
        new_docs.append((tup[0], tfidf_score, tup[2]))

    top_docs_with_scores = sorted(new_docs, key=lambda tup: tup[1], reverse=True)[:top_k]

    return [Document(
        content=tup[0].content,
        chunk_id=tup[0].chunk_id,
        title=tup[0].title,
        filepath=tup[0].filepath,
        url=tup[0].url,
        metadata=tup[0].metadata,
        score=tup[1]
    ) for tup in top_docs_with_scores]