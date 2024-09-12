import tiktoken
from typing import List, Dict, Any, Generator, Optional, Tuple
from ragcore.utils import TokenEstimator
from ragcore.datamodels.datamodels_utils import dataclass_to_dict
from ragcore.datamodels.document import Document
from ragcore.parsers.parser_factory import ParserFactory
from ragcore.chunking import MarkdownTextSplitter, CSVChunker, PdfChunker, PythonCodeTextSplitter, RecursiveCharacterTextSplitter

SENTENCE_ENDINGS = [".", "!", "?"]
WORDS_BREAKS = list(
    reversed([",", ";", ":", " ", "(", ")", "[", "]", "{", "}", "\t", "\n"]))

parser_factory = ParserFactory()
TOKEN_ESTIMATOR = TokenEstimator()

def copy_if_present(dest, source, name):
    if source and name in source:
        dest[name] = source[name]

def documents_to_json_dict(chunks: List[List[Document]]) -> List[List[Dict[str, Any]]]:
    json_list: List[List[Dict[str, Any]]] = []
    for chunk_list in chunks:
        json_list.append([dataclass_to_dict(chunk) for chunk in chunk_list])
    return json_list

def chunk_content_helper_v2(
        content: str, file_format: str, file_name: Optional[str],
        token_overlap: int,
        num_tokens: int = 256,
        use_fr: bool = False
) -> Generator[Tuple[str, int, Document], None, None]:
    parser = parser_factory(file_format, use_fr)
    doc = parser.parse(content, file_name=file_name)
    if file_format == "markdown":
        splitter = MarkdownTextSplitter(keep_separator=False)
        chunked_content_list = splitter.split_text(content, token_limit=num_tokens, chunk_overlap=token_overlap)    # chunk the original content
        for chunked_content, chunk_size in merge_chunks_serially(chunked_content_list, num_tokens):
            chunk_doc = parser.parse(chunked_content, file_name=file_name)
            chunk_doc.title = doc.title
            yield chunk_doc.content, chunk_size, chunk_doc
    elif file_format in {'csv'} and file_name:
        splitter = CSVChunker()
        chunked_docs = splitter.split_text(content, file_name, token_limit=num_tokens)
        for chunk_doc in chunked_docs:
            chunk_size = TOKEN_ESTIMATOR.estimate_tokens(chunk_doc.content)
            yield chunk_doc.content, chunk_size, chunk_doc
    elif file_format == "pdf" and use_fr:
        splitter = PdfChunker()
        chunked_content_list = splitter.split_text(doc.content, token_limit=num_tokens, chunk_overlap=token_overlap)
        for chunked_content in chunked_content_list:
            chunk_size = TOKEN_ESTIMATOR.estimate_tokens(chunked_content)
            yield chunked_content, chunk_size, doc
    else:
        if file_format == "python":
            splitter = PythonCodeTextSplitter(keep_separator=False)
            chunked_content_list = splitter.split_text(doc.content, token_limit=num_tokens, chunk_overlap=token_overlap)
        else:
            splitter = RecursiveCharacterTextSplitter(
                separators=SENTENCE_ENDINGS + WORDS_BREAKS, keep_separator=False)
            chunked_content_list = splitter.split_text(doc.content, token_limit=num_tokens, chunk_overlap=token_overlap)
        
        for chunked_content in chunked_content_list:
            chunk_size = TOKEN_ESTIMATOR.estimate_tokens(chunked_content)
            yield chunked_content, chunk_size, doc


def chunk_content_helper_v1(
        content: str, file_format: str, file_name: Optional[str],
        token_overlap: int,
        num_tokens: int = 256,
        use_fr: bool = False
) -> Generator[Tuple[str, int, Document], None, None]:
    """
    chunk the string into chunks of num_tokens
    : param s: the parsed original sentence to be chunked based on the size of num_tokens
    : param num_tokens: number of tokens per chunk
    : return: a list of chunks
    """
    parser = parser_factory(file_format)
    doc = parser.parse(content, file_name=file_name)
    enc = tiktoken.get_encoding("gpt2")
    this_chunk = ""
    this_chunksize = 0
    for line in doc.content.split("\n"):
        line_size = len(enc.encode(line, allowed_special="all"))
        if this_chunksize + line_size > num_tokens:
            yield this_chunk, this_chunksize, doc
            this_chunk = ""
            this_chunksize = 0
        this_chunk += f"\n{line}"
        this_chunksize += line_size
    if len(this_chunk) > 0:
        yield this_chunk, this_chunksize, doc
    
def merge_chunks_serially(chunked_content_list: List[str], num_tokens: int) -> Generator[Tuple[str, int], None, None]:
    # TODO: solve for token overlap
    current_chunk = ""
    total_size = 0
    for chunked_content in chunked_content_list:
        chunk_size = TOKEN_ESTIMATOR.estimate_tokens(chunked_content)
        if total_size > 0:
            new_size = total_size + chunk_size
            if new_size > num_tokens:
                yield current_chunk, total_size
                current_chunk = ""
                total_size = 0
        total_size += chunk_size
        current_chunk += chunked_content
    if total_size > 0:
        yield current_chunk, total_size