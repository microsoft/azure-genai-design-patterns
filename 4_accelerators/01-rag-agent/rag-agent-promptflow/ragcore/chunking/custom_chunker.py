import io
import math
import os
import re
from abc import ABC, abstractmethod
from typing import Any, Tuple, Union, Dict, Generator, Iterable, List, Optional

import numpy as np
import pandas as pd

from ragcore.datamodels.document import Document
from ragcore.utils import TokenEstimator

class CustomChunker(ABC):

    @abstractmethod
    def split_text(self, text: str, filename: str, token_limit: int = 1024) -> List[Document]:
        "split text into documents"

class CSVChunker(CustomChunker):
    def split_text(self, text: str, filename: str, token_limit: int = 1024) -> List[Document]:
        out_docs = []
        chunk = []
        filename = get_filename_from_filepath(filename)
        current_tokens = 0
        chunk_counter = 0
        TOKEN_ESTIMATOR = TokenEstimator()
        header_tokens = sum(TOKEN_ESTIMATOR.estimate_tokens(col) for col in pd.read_csv(io.StringIO(text), nrows=1).columns)
        approx_token_limit = token_limit - 256 if token_limit > 384 else token_limit
        chunked_csv = pd.read_csv(io.StringIO(text), chunksize=1024)
        for i, chunk_df in enumerate(chunked_csv):

            print(f"Done num rows={i * 1024}")
            for _, row in chunk_df.iterrows():
                md_row = row_to_markdown(row)
                row_tokens = TOKEN_ESTIMATOR.estimate_tokens(md_row)
                chunk_df = pd.DataFrame(chunk)

                if current_tokens + row_tokens + header_tokens > approx_token_limit and chunk:
                    summaries = summarize_chunk(chunk_df)
                    content_for_doc = ""
                    content_for_doc = write_summary_to_file(summaries, content=content_for_doc)
                    content_for_doc = write_chunk_to_file(chunk, str(chunk_counter), content=content_for_doc)
                    fname=f"{filename}_chunk_{chunk_counter}.txt"
                    out_docs.append(
                        Document(content=content_for_doc, filepath=fname, title=content_for_doc.split("\n")[0].strip()))
                    chunk = []
                    current_tokens = 0
                    chunk_counter += 1

                chunk.append(row)
                current_tokens += row_tokens

        # Write any remaining rows in the last chunk
        if chunk:
            chunk_df = pd.DataFrame(chunk)
            summaries = summarize_chunk(chunk_df)
            content_for_doc = ""
            content_for_doc = write_summary_to_file(summaries, content=content_for_doc)
            content_for_doc = write_chunk_to_file(chunk, str(chunk_counter), content=content_for_doc)
            fname = f"{filename}_chunk_{chunk_counter}.txt"
            out_docs.append(
                Document(content=content_for_doc, filepath=fname, title=content_for_doc.split("\n")[0].strip()))
        return out_docs

   
class PdfChunker(CustomChunker):
    
    def extract_caption(self, text):
        separator = self._separators[-1]
        for _s in self._separators:
            if _s == "":
                separator = _s
                break
            if _s in text:
                separator = _s
                break
        
        # Now that we have the separator, split the text
        if separator:
            lines = text.split(separator)
        else:
            lines = list(text)
        
        # remove empty lines
        lines = [line for line in lines if line!='']
        caption = ""
        
        if len(text.split(f"<{self._pdf_headers['title']}>"))>1:
            caption +=  text.split(f"<{self._pdf_headers['title']}>")[-1].split(f"</{self._pdf_headers['title']}>")[0]
        if len(text.split(f"<{self._pdf_headers['sectionHeading']}>"))>1:
            caption +=  text.split(f"<{self._pdf_headers['sectionHeading']}>")[-1].split(f"</{self._pdf_headers['sectionHeading']}>")[0]
        
        caption += "\n"+ lines[-1].strip()

        return caption

    def split_text(self, text: str, token_limit: int = 1024, chunk_overlap: int = 128) -> List[str]:
        self._table_tags = {
            "table_open": "<table>", 
            "table_close": "</table>", 
            "row_open":"<tr>"
        }
        self._pdf_headers = {
        "title": "h1",
        "sectionHeading": "h2"
        }
        SENTENCE_ENDINGS = [".", "!", "?"]
        WORDS_BREAKS = list(
            reversed([",", ";", ":", " ", "(", ")", "[", "]", "{", "}", "\t", "\n"]))
        self._separators = (SENTENCE_ENDINGS + WORDS_BREAKS) or ["\n\n", "\n", " ", ""]
        TOKEN_ESTIMATOR = TokenEstimator()
        self._length_function = TOKEN_ESTIMATOR.estimate_tokens
        self._chunk_size = token_limit
        self._chunk_overlap = chunk_overlap
        self._noise = 50 # tokens to accommodate differences in token calculation, we don't want the chunking-on-the-fly to inadvertently chunk anything due to token calc mismatch


        start_tag = self._table_tags["table_open"]
        end_tag = self._table_tags["table_close"]
        splits = text.split(start_tag)
        
        final_chunks = self.chunk_rest(splits[0]) # the first split is before the first table tag so it is regular text
        
        table_caption_prefix = ""
        if len(final_chunks)>0:
            table_caption_prefix += self.extract_caption(final_chunks[-1]) # extracted from the last chunk before the table
        for part in splits[1:]:
            table, rest = part.split(end_tag)
            table = start_tag + table + end_tag 
            minitables = self.chunk_table(table, table_caption_prefix)
            final_chunks.extend(minitables)

            if rest.strip()!="":
                text_minichunks = self.chunk_rest(rest)
                final_chunks.extend(text_minichunks)
                table_caption_prefix = self.extract_caption(text_minichunks[-1])
            else:
                table_caption_prefix = ""
            
        final_final_chunks = [chunk for chunk, chunk_size in self.merge_chunks_serially(final_chunks, self._chunk_size)]

        return final_final_chunks
    
    def chunk_rest(self, item):
        separator = self._separators[-1]
        for _s in self._separators:
            if _s == "":
                separator = _s
                break
            if _s in item:
                separator = _s
                break
        chunks = []
        if separator:
            splits = item.split(separator)
        else:
            splits = list(item)
        _good_splits = []
        for s in splits:
            if self._length_function(s) < self._chunk_size - self._noise:
                _good_splits.append(s)
            else:
                if _good_splits:
                    merged_text = self._merge_splits(_good_splits, separator)
                    chunks.extend(merged_text)
                    _good_splits = []
                other_info = self.chunk_rest(s)
                chunks.extend(other_info)
        if _good_splits:
            merged_text = self._merge_splits(_good_splits, separator)
            chunks.extend(merged_text)
        return chunks
    
    def chunk_table(self, table, caption):
        if self._length_function("\n".join([caption, table])) < self._chunk_size - self._noise:
            return ["\n".join([caption, table])]
        else:
            headers = ""
            if re.search("<th.*>.*</th>", table):
                header = re.search("<th.*>.*</th>", table) # extract the header out. Opening tag may contain rowspan/colspan
                if header is not None:
                    headers += header.group() 
            splits = table.split(self._table_tags["row_open"]) #split by row tag
            tables = []
            current_table = caption + "\n"
            for part in splits:
                if len(part)>0:
                    if self._length_function(current_table + self._table_tags["row_open"] + part) < self._chunk_size: # if current table length is within permissible limit, keep adding rows
                        if part not in [self._table_tags["table_open"], self._table_tags["table_close"]]: # need add the separator (row tag) when the part is not a table tag
                            current_table += self._table_tags["row_open"]
                        current_table += part
                        
                    else:
                        
                        # if current table size is beyond the permissible limit, complete this as a mini-table and add to final mini-tables list
                        current_table += self._table_tags["table_close"]
                        tables.append(current_table)

                        # start a new table
                        current_table = "\n".join([caption, self._table_tags["table_open"], headers])
                        if part not in [self._table_tags["table_open"], self._table_tags["table_close"]]:
                            current_table += self._table_tags["row_open"]
                        current_table += part

            
            # TO DO: fix the case where the last mini table only contain tags
            
            if not current_table.endswith(self._table_tags["table_close"]):
                
                tables.append(current_table + self._table_tags["table_close"])
            else:
                tables.append(current_table)
            return tables
        
    def merge_chunks_serially(self, chunked_content_list: List[str], num_tokens: int) -> Generator[Tuple[str, int], None, None]:
        # TODO: solve for token overlap
        current_chunk = ""
        total_size = 0
        for chunked_content in chunked_content_list:
            chunk_size = self._length_function(chunked_content)
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

    def _merge_splits(self, splits: Iterable[str], separator: str) -> List[str]:
        # We now want to combine these smaller pieces into medium size
        # chunks to send to the LLM.
        separator_len = self._length_function(separator)

        docs = []
        current_doc: List[str] = []
        total = 0
        for d in splits:
            _len = self._length_function(d)
            if (
                total + _len + (separator_len if len(current_doc) > 0 else 0)
                > self._chunk_size
            ):
                if total > self._chunk_size:
                    print(
                        f"Created a chunk of size {total}, "
                        f"which is longer than the specified {self._chunk_size}"
                    )
                if len(current_doc) > 0:
                    doc = self._join_docs(current_doc, separator)
                    if doc is not None:
                        docs.append(doc)
                    # Keep on popping if:
                    # - we have a larger chunk than in the chunk overlap
                    # - or if we still have any chunks and the length is long
                    while total > self._chunk_overlap or (
                        total + _len + (separator_len if len(current_doc) > 0 else 0)
                        > self._chunk_size
                        and total > 0
                    ):
                        total -= self._length_function(current_doc[0]) + (
                            separator_len if len(current_doc) > 1 else 0
                        )
                        current_doc = current_doc[1:]
            current_doc.append(d)
            total += _len + (separator_len if len(current_doc) > 1 else 0)
        doc = self._join_docs(current_doc, separator)
        if doc is not None:
            docs.append(doc)
        return docs
    
    def _join_docs(self, docs: List[str], separator: str) -> Optional[str]:
        text = separator.join(docs)
        text = text.strip()
        if text == "":
            return None
        else:
            return text

class RecursiveCharacterTextSplitter(CustomChunker):
    def __init__(self, separators=None, keep_separator=True, is_separator_regex=False, **kwargs: Any):
        self._separators = separators or ["\n\n", "\n", " ", ""]
        self._keep_separator = keep_separator
        self._is_separator_regex = is_separator_regex

    def _split_text(self, text: str, token_limit: int = 1024, chunk_overlap: int = 0, separators: List[str] = []) -> List[str]:
        final_chunks = []
        self._chunk_size = token_limit
        self._chunk_overlap = chunk_overlap

        separator = separators[-1]
        TOKEN_ESTIMATOR = TokenEstimator()
        self._length_function = TOKEN_ESTIMATOR.estimate_tokens
        new_separators = []
        for i, _s in enumerate(separators):
            _separator = _s if self._is_separator_regex else re.escape(_s)
            if _s == "":
                separator = _s
                break
            if re.search(_separator, text):
                separator = _s
                new_separators = separators[i + 1 :]
                break
        _separator = separator if self._is_separator_regex else re.escape(separator)
        splits = self._split_text_with_regex(text, _separator, self._keep_separator)

        # Now go merging things, recursively splitting longer texts.
        _good_splits = []
        _separator = "" if self._keep_separator else separator
        for s in splits:
            if self._length_function(s) < self._chunk_size:
                _good_splits.append(s)
            else:
                if _good_splits:
                    merged_text = self._merge_splits(_good_splits, _separator)
                    final_chunks.extend(merged_text)
                    _good_splits = []
                if not new_separators:
                    for i in range(math.ceil(len(s)/self._chunk_size)):
                        snippet = s[i: i + self._chunk_size]
                        final_chunks.append(snippet)
                else:
                    other_info = self._split_text(s, token_limit=self._chunk_size, chunk_overlap=self._chunk_overlap, separators=new_separators)
                    final_chunks.extend(other_info)
        if _good_splits:
            merged_text = self._merge_splits(_good_splits, _separator)
            final_chunks.extend(merged_text)
        
        return final_chunks

    def split_text(self, text: str, token_limit: int = 1024, chunk_overlap: int = 0) -> List[str]:
        return self._split_text(text, token_limit=token_limit, chunk_overlap=chunk_overlap, separators=self._separators)

    def _split_text_with_regex(self, 
        text: str, separator: str, keep_separator: bool
    ) -> List[str]:
        # Now that we have the separator, split the text
        if separator:
            if keep_separator:
                # The parentheses in the pattern keep the delimiters in the result.
                _splits = re.split(f"({separator})", text)
                splits = [_splits[i] + _splits[i + 1] for i in range(1, len(_splits), 2)]
                if len(_splits) % 2 == 0:
                    splits += _splits[-1:]
                splits = [_splits[0]] + splits
            else:
                splits = re.split(separator, text)
        else:
            splits = list(text)
        return [s for s in splits if s != ""]


    def _merge_splits(self, splits: Iterable[str], separator: str) -> List[str]:
        # We now want to combine these smaller pieces into medium size
        # chunks to send to the LLM.
        separator_len = self._length_function(separator)

        docs = []
        current_doc: List[str] = []
        total = 0
        for d in splits:
            _len = self._length_function(d)
            if (
                total + _len + (separator_len if len(current_doc) > 0 else 0)
                > self._chunk_size
            ):
                if total > self._chunk_size:
                    print(
                        f"Created a chunk of size {total}, "
                        f"which is longer than the specified {self._chunk_size}"
                    )
                if len(current_doc) > 0:
                    doc = self._join_docs(current_doc, separator)
                    if doc is not None:
                        docs.append(doc)
                    # Keep on popping if:
                    # - we have a larger chunk than in the chunk overlap
                    # - or if we still have any chunks and the length is long
                    while total > self._chunk_overlap or (
                        total + _len + (separator_len if len(current_doc) > 0 else 0)
                        > self._chunk_size
                        and total > 0
                    ):
                        total -= self._length_function(current_doc[0]) + (
                            separator_len if len(current_doc) > 1 else 0
                        )
                        current_doc = current_doc[1:]
            current_doc.append(d)
            total += _len + (separator_len if len(current_doc) > 1 else 0)
        doc = self._join_docs(current_doc, separator)
        if doc is not None:
            docs.append(doc)
        return docs
    
    def _join_docs(self, docs: List[str], separator: str) -> Optional[str]:
        text = separator.join(docs)
        text = text.strip()
        if text == "":
            return None
        else:
            return text


class MarkdownTextSplitter(RecursiveCharacterTextSplitter):
    """Attempts to split the text along Markdown-formatted headings."""
    def __init__(self, **kwargs: Any) -> None:
        """Initialize a MarkdownTextSplitter."""
        separators = [
                # First, try to split along Markdown headings (starting with level 2)
                "\n#{1,6} ",
                # Note the alternative syntax for headings (below) is not handled here
                # Heading level 2
                # ---------------
                # End of code block
                "```\n",
                # Horizontal lines
                "\n\\*\\*\\*+\n",
                "\n---+\n",
                "\n___+\n",
                # Note that this splitter doesn't handle horizontal lines defined
                # by *three or more* of ***, ---, or ___, but this is not handled
                "\n\n",
                "\n",
                " ",
                "",
            ]
        super().__init__(separators=separators, **kwargs)

class PythonCodeTextSplitter(RecursiveCharacterTextSplitter):
    """Attempts to split the text along Python syntax."""
    def __init__(self, **kwargs: Any) -> None:
        """Initialize a PythonCodeTextSplitter."""
        separators = [
                # First, try to split along class definitions
                "\nclass ",
                "\ndef ",
                "\n\tdef ",
                # Now split by the normal type of lines
                "\n\n",
                "\n",
                " ",
                "",
            ]
        super().__init__(separators=separators, **kwargs)

def row_to_markdown(row: pd.Series) -> str:
    """Convert a pandas Series (row) to a Markdown table row."""
    return "| " + " | ".join(row.map(str)) + " |"


def summarize_chunk(chunk: pd.DataFrame) -> Dict[str, Union[Tuple[float, float], str, List[str]]]:
    """
    Summarize a given chunk's columns. For numerical columns,
    provide range. For categorical columns, provide unique values.
    """
    summaries = {}

    for column in chunk.columns:
        series = chunk[column]
        if np.issubdtype(series.dtype, np.number):
            unique_values = series.dropna().unique()
            if len(unique_values) < 5:
                summaries[column] = unique_values.tolist()
            else:
                summaries[column] = (series.min(), series.max())

        # Handle datetime columns - but not used unless we allow infer but that will be slow
        elif np.issubdtype(series.dtype, np.datetime64) or np.issubdtype(series.dtype, np.timedelta64):
            summaries[column] = (series.min(), series.max())
        elif series.dtype == 'bool':
            true_count = series.sum()  # True is 1 and False is 0
            false_count = len(series) - true_count
            summaries[column] = f"True: {true_count}, False: {false_count}"
        else:
            unique_values = series.unique()
            if len(unique_values) > 10:
                summaries[
                    column] = f"{len(unique_values)} unique values. " \
                              f"Few Random examples: {','.join([str(x) for x in unique_values[:5]])}"
            else:
                summaries[column] = unique_values.tolist()

    return summaries


def get_filename_from_filepath(filepath):
    return os.path.basename(filepath).split(".")[0]


def get_summary_str(summaries: Dict[str, Union[Tuple[float, float], str, List[str]]]):
    out = ""
    for column, summary in summaries.items():
        out += f"- **{column}**: {summary}\n"
    return out


def write_summary_to_file(summaries: Dict[str, Union[Tuple[float, float], str, List[str]]], content: str) -> str:
    """Write a chunk's summary to a file."""
    content += "#### Table chunk Summary\n\n"
    content += get_summary_str(summaries)
    content += "\n\n"
    return content


def get_header_str(chunk: List[pd.Series]) -> str:
    return "| " + " | ".join(chunk[0].index) + " |"

def write_chunk_to_file(chunk: List[pd.Series], chunk_counter: str, content: str) -> str:
    """Write a chunk (list of rows) to a file as a Markdown table."""
    super_header = f"#### Chunk no. {chunk_counter}"
    header = get_header_str(chunk)
    separator = "| --- " * len(chunk[0].index) + "|"
    content += (super_header + "\n")
    content += (header + "\n")
    content += (separator + "\n")
    for row in chunk:
        content += (row_to_markdown(row) + "\n")
    content += "\n\n"
    return content
