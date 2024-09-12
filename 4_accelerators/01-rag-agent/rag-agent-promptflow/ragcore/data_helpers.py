"""Data helpers for ragcore."""
import os
from typing import List


def get_files_recursively(directory_path: str) -> List[str]:
    """Gets all files in the given directory recursively.
    Args:
        directory_path (str): The directory to get files from.
    Returns:
        List[str]: List of file paths.
    """
    file_paths = []
    for dirpath, _, files in os.walk(directory_path):
        for file_name in files:
            file_path = os.path.join(dirpath, file_name)
            file_paths.append(file_path)
    return file_paths
