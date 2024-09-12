"Utilities for text processing."
import os
import re

from typing import Tuple


def cleanup_content(content: str) -> str:
    """Cleans up the given content using regexes
    Args:
        content (str): The content to clean up.
    Returns:
        str: The cleaned up content.
    """
    output = re.sub(r"\n{3,}", "\n\n", content)
    output = re.sub(r"[^\S\n]{3,}", "  ", output)
    output = re.sub(r"-{3,}", "---", output)

    return output.strip()


def get_content_and_file_name(file_path: str) -> Tuple[str, str]:
    """Gets the content and file name from the given file path.
    Args:
        file_path (str): The file path
    Returns:
        Tuple[str, str]: The content and file name.
    """
    with open(file_path, "r") as f:
        content = f.read()
    file_name = os.path.basename(file_path)
    return content, file_name
