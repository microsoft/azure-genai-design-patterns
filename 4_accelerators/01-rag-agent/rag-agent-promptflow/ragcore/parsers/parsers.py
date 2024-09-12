"Contains concrete implementations of parsers for different formats."
import markdown
from typing import Optional
from bs4 import BeautifulSoup
import ast
from ragcore.datamodels.document import Document
from ragcore.parsers.base_parser import BaseParser
from ragcore.parsers.text_utils import cleanup_content
from ragcore.utils import TokenEstimator


class MarkdownParser(BaseParser):
    """Parses Markdown content."""

    def __init__(self) -> None:
        super().__init__()
        self._html_parser = HTMLParser()

    def parse(self, content: str, file_name: Optional[str] = None) -> Document:
        """Parses the given content.
        Args:
            content (str): The content to parse.
            file_name (str): The file name associated with the content.
        Returns:
            Document: The parsed document.
        """

        # Convert markdown to HTML to get the title, but use the original markdown content as the document content
        html_content = markdown.markdown(content, extensions=['fenced_code', 'toc', 'tables', 'sane_lists'])

        output = self._html_parser.parse(html_content, file_name)

        return Document(content=cleanup_content(content), title=output.title)


class HTMLParser(BaseParser):
    """Parses HTML content."""
    TITLE_MAX_TOKENS = 64
    NEWLINE_TEMPL = "<NEWLINE_TEXT>"

    def __init__(self) -> None:
        super().__init__()
        self.token_estimator = TokenEstimator()

    def parse(self, content: str, file_name: Optional[str] = None) -> Document:
        """Parses the given content.
        Args:
            content (str): The content to parse.
            file_name (str): The file name associated with the content.
        Returns:
            Document: The parsed document.
        """
        soup = BeautifulSoup(content, 'html.parser')

        # Extract the title
        title = ''
        if soup.title and soup.title.string:
            title = soup.title.string
        else:
            # Try to find the first <h1> tag
            h1_tag = soup.find('h1')
            if h1_tag:
                title = h1_tag.get_text(strip=True)
            else:
                h2_tag = soup.find('h2')
                if h2_tag:
                    title = h2_tag.get_text(strip=True)
        if title is None or title == '':
            # if title is still not found, guess using the next string
            try:
                title = next(soup.stripped_strings)
                title = self.token_estimator.construct_tokens_with_size(title, self.TITLE_MAX_TOKENS)
            except StopIteration:
                title = file_name
        # preserve the HTML as it is without doing any post processing
        result = content
        if title is None:
            title = ''  # ensure no 'None' type title
        return Document(content=cleanup_content(result), title=str(title))


class TextParser(BaseParser):
    """Parses text content."""
    TITLE_MAX_TOKENS = 64
    def __init__(self) -> None:
        super().__init__()
        self.token_estimator = TokenEstimator()

    def _get_first_alphanum_line(self, content: str) -> Optional[str]:
        title = None
        for line in content.splitlines():
            if any([c.isalnum() for c in line]):
                title = line.strip()
                break
        return title

    def _get_first_line_with_property(
            self, content: str, property: str = "title: "
    ) -> Optional[str]:
        title = None
        for line in content.splitlines():
            if line.startswith(property):
                title = line[len(property):].strip()
                break
        return title

    def parse(self, content: str, file_name: Optional[str] = None) -> Document:
        """Parses the given content.
        Args:
            content (str): The content to parse.
            file_name (str): The file name associated with the content.
        Returns:
            Document: The parsed document.
        """
        title = self._get_first_line_with_property(
            content
        ) or self._get_first_alphanum_line(content)
        if title:
            title = self.token_estimator.construct_tokens_with_size(title, self.TITLE_MAX_TOKENS)
        return Document(content=cleanup_content(content), title=title or file_name)


class PythonParser(BaseParser):
    def _get_topdocstring(self, text):
        tree = ast.parse(text)
        docstring = ast.get_docstring(tree)  # returns top docstring
        return docstring

    def parse(self, content: str, file_name: Optional[str] = None) -> Document:
        """Parses the given content.
        Args:
            content (str): The content to parse.
            file_name (str): The file name associated with the content.
        Returns:
            Document: The parsed document.
        """
        docstring = self._get_topdocstring(content)
        if docstring:
            title = f"{file_name}: {docstring}"
        else:
            title = file_name
        return Document(content=content, title=title)

    def __init__(self) -> None:
        super().__init__()


class CSVParser(BaseParser):
    def parse(self, content: str, file_name: Optional[str] = None) -> Document:
        return Document(content=content)