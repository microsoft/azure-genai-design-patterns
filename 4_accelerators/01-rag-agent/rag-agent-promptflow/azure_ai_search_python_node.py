from dotenv import load_dotenv, find_dotenv
import os

from promptflow import tool
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient

# The inputs section will change based on the arguments of the tool function, after you save the code
# Adding type to arguments and return value will help the system show the types properly
# Please update the function name/signature per need
@tool
def azure_ai_search_tool(chat_output_str: str) -> str:

    # Load the .env file
    load_dotenv(find_dotenv())
    # Replace with your Azure Cognitive Search service details
    endpoint = os.getenv('AI_SEARCH_ENDPOINT')
    index_name = os.getenv('AI_SEARCH_INDEX_NAME')
    api_key = os.getenv('AI_SEARCH_API_KEY')

    # Create a SearchClient
    search_client = SearchClient(endpoint=endpoint,
                                index_name=index_name,
                                credential=AzureKeyCredential(api_key))

    # Convert the chat_output_str to a dictionary
    chat_output_str = eval(chat_output_str)

    # Unpack the chat_output_str for the search query
    search_elements = chat_output_str['modified_search_queries']

    # Generate Python code for each dictionary using list comprehension
    function_calls = [
    "search_client.search(" + ", ".join(
        f'{key}="{value}"' if isinstance(value, str) else f'{key}={value}'
        for key, value in query.items()
    ) + ")"
    for query in search_elements
    ]

    # Initialize the results list
    results = []

    # Print the generated function calls
    for function_call in function_calls:
        print(function_call)

        # Run the search
        results += eval(function_call)

    return results
