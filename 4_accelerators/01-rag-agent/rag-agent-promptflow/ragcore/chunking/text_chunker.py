"""Text chunker module.

Classes:
    TextChunker: Text chunker class.
"""
import os
import json
from typing import List, Optional
from tqdm import tqdm
from ragcore.exceptions import UnsupportedFormatError
from ragcore.parsers import ParserFactory
from ragcore.chunking.utils import chunk_content_helper_v1, chunk_content_helper_v2
from ragcore.data_helpers import get_files_recursively
from ragcore.utils import convert_escaped_to_posix
from ragcore.datamodels.document import Document

class TextChunker:
    """Text chunker class.

    Attributes:
        supported_formats (List[str]): List of supported file formats.
    """
    FILE_FORMAT_DICT = {
        "md": "markdown",
        "txt": "text",
        "html": "html",
        "shtml": "html",
        "htm": "html",
        "py": "python",
        "csv": "csv",
        "pdf": "pdf",
    }

    def __init__(self, version: str = "v1", extensions_to_process: Optional[List[str]] = None) -> None:
        """Initializes the text chunker.
        Args:
            version (str): The version of the chunker. v1 is the old version. v2 is the new version.
            extensions_to_process (List[str]): The extensions to process. If None, all extensions are processed. If not None, only the extensions in the list are processed.
        """
        self._parser_factory = ParserFactory()
        if not extensions_to_process:
            self.extensions_to_process = set(self.FILE_FORMAT_DICT.keys())
        else:
            all_formats_requested = {
                file_extension: self.FILE_FORMAT_DICT.get(file_extension, None)
                for file_extension in extensions_to_process
            }
            self.extensions_to_process = set([
                ext for ext, ext_format in all_formats_requested.items()
                if ext_format is not None
            ])
            extensions_to_ignore = [
                ext for ext, ext_format in all_formats_requested.items()
                if ext_format is None
            ]
            if len(extensions_to_ignore) > 0:
                print("Not able to process the fllowing types:",
                      extensions_to_ignore)
        if version == "v1" and "py" in self.extensions_to_process:
            self.extensions_to_process.remove("py")
            print(
                "remove python file support. Not supported by v1 chunker. Please use version='v2'")
        if len(self.extensions_to_process) == 0:
            raise Exception("Chunker will not process any file. Please provide one of these filetypes",
                            list(self.FILE_FORMAT_DICT.keys()))
        if version == "v1":
            self.chunk_fn = chunk_content_helper_v1
        else:
            self.chunk_fn = chunk_content_helper_v2

    def _get_file_format(self, file_name: str) -> Optional[str]:
        """Gets the file format from the file name.
        Returns None if the file format is not supported.
        Args:
            file_name (str): The file name.
        Returns:
            str: The file format.
        """

        # in case the caller gives us a file path
        file_name = os.path.basename(file_name)
        file_extension = file_name.split(".")[-1]
        if file_extension not in self.extensions_to_process:
            return None
        return self.FILE_FORMAT_DICT.get(file_extension, None)

    @property
    def supported_formats(self) -> List[str]:
        return self._parser_factory.supported_formats

    def chunk_content(
        self,
        content: str,
        file_name: Optional[str] = None,
        url: Optional[str] = None,
        ignore_errors: bool = True,
        num_tokens: int = 256,
        min_chunk_size: int = 0,
        token_overlap: int = 0,
        use_fr: bool = False
    ) -> List[Document]:
        """Chunks the given content. If ignore_errors is true, returns None
         in case of an error
        Args:
            content (str): The content to chunk.
            file_name (str): The file name. used for title, file format detection.
            url (str): The url. used for title.
            ignore_errors (bool): If true, ignores errors and returns None.
            num_tokens (int): The number of tokens in each chunk.
            min_chunk_size (int): The minimum chunk size below which chunks will be filtered.
            token_overlap (int): The number of tokens to overlap between chunks.
        Returns:
            List[Document]: List of chunked documents.
        """

        try:
            if file_name is None:
                file_format = "text"
            else:
                file_format = self._get_file_format(file_name)
                if file_format is None:
                    file_format = "text"

            chunked_context = self.chunk_fn(
                content=content,
                file_name=file_name,
                file_format=file_format,
                num_tokens=num_tokens,
                token_overlap=token_overlap,
                use_fr=use_fr
            )
            chunks = []
            for chunk, chunk_size, doc in chunked_context:
                if chunk_size >= min_chunk_size:
                    chunks.append(
                        Document(
                            content=chunk,
                            title=doc.title,
                            url=url,
                        )
                    )

        except UnsupportedFormatError as e:
            if ignore_errors:
                return []
            else:
                raise e
        except Exception as e:
            if ignore_errors:
                return []
            else:
                raise e
        return chunks

    def chunk_file(
        self,
        file_path: str,
        ignore_errors: bool = True,
        num_tokens=256,
        min_chunk_size=10,
        url: Optional[str] = None,
        token_overlap: int = 0
    ) -> List[Document]:
        """Chunks the given file.
        Args:
            file_path (str): The file to chunk.
        Returns:
            List[Document]: List of chunked documents.
        """
        file_name = os.path.basename(file_path)
        file_format = self._get_file_format(file_name)
        if not file_format:
            if ignore_errors:
                return []
            else:
                raise UnsupportedFormatError(f"{file_name} is not supported")

        with open(file_path, "r", encoding="utf8") as f:
            content = f.read()
        return self.chunk_content(
            content=content,
            file_name=file_name,
            ignore_errors=ignore_errors,
            num_tokens=num_tokens,
            min_chunk_size=min_chunk_size,
            url=url,
            token_overlap=max(0, token_overlap)
        )

    def chunk_directory(
        self,
        directory_path: str,
        ignore_errors: bool = True,
        num_tokens: int = 256,
        min_chunk_size: int = 10,
        url_prefix: Optional[str] = None,
        token_overlap: int = 0
    ) -> List[Document]:
        """
        Chunks the given directory recursively
        Args:
            directory_path (str): The directory to chunk.
            ignore_errors (bool): If true, ignores errors and returns None.
            num_tokens (int): The number of tokens to use for chunking.
            min_chunk_size (int): The minimum chunk size.
            url_prefix (str): The url prefix to use for the files. If None, the url will be None. If not None, the url will be url_prefix + relpath. 
                              For example, if the directory path is /home/user/data and the url_prefix is https://example.com/data, 
                              then the url for the file /home/user/data/file1.txt will be https://example.com/data/file1.txt
            token_overlap (int): The number of tokens to overlap between chunks.
        Returns:
            List[Document]: List of chunked documents.
        """
        chunks = []
        for file_path in tqdm(get_files_recursively(directory_path)):
            if os.path.isfile(file_path):
                # get relpath
                url_path = None
                rel_file_path = os.path.relpath(file_path, directory_path)
                if url_prefix:
                    url_path = url_prefix + rel_file_path
                    url_path = convert_escaped_to_posix(url_path)
                try:
                    result = self.chunk_file(
                        file_path,
                        ignore_errors=ignore_errors,
                        num_tokens=num_tokens,
                        min_chunk_size=min_chunk_size,
                        url=url_path,
                        token_overlap=token_overlap
                    )
                    for chunk_idx, chunk_doc in enumerate(result):
                        chunk_doc.filepath = rel_file_path
                        chunk_doc.metadata = json.dumps({"chunk_id": str(chunk_idx)})
                    chunks.extend(chunks)
                except Exception as e:
                    if not ignore_errors:
                        raise
                    # There's no logging yet
                    # logging.warning(f"File ({file_path}) failed with ", e)

        return chunks
