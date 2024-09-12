"""This module contains the parsers for the different types of files that can be parsed by the program.

Files:
    parser_factory.py: Parser factory class.
    parsers.py: Parser classes.
"""
from ragcore.parsers.parser_factory import ParserFactory
from ragcore.parsers.parsers import HTMLParser, TextParser, MarkdownParser