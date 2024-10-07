import sys
import codecs
import json
import os
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from dotenv import find_dotenv, load_dotenv
from openai import AzureOpenAI
from promptflow.core import tool

# Set default encoding to UTF-8
# sys.stdout = codecs.getwriter("utf-8")#(sys.stdout.detach())
# sys.stderr = codecs.getwriter("utf-8")#(sys.stderr.detach())


@tool
def azure_ai_search_tool(
    chat_output_str: str,
    top: int,
    query_type: str,
    semantic_configuration_name: str,
    openai_embedding_model: str,
    aoai_api_version: str,
    dimensions: int,
) -> str:
    # Load the .env file
    load_dotenv(find_dotenv())

    # Create the SearchClient
    search_client = SearchClient(
        endpoint=os.getenv("AI_SEARCH_ENDPOINT"),
        index_name=os.getenv("AI_SEARCH_INDEX_NAME"),
        credential=AzureKeyCredential(os.getenv("AI_SEARCH_API_KEY")),
    )

    # Create the Azure OpenAI Client
    aoai_client = AzureOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=aoai_api_version,
    )

    # Print chat_output_str for debugging
    print("Original chat_output_str:", chat_output_str)

    # Safely parse the fixed chat_output_str to a dictionary
    try:
        chat_output_dict = json.loads(chat_output_str)
        print("chat_output_dict: ", chat_output_dict)
    except json.JSONDecodeError as e:
        print(f"JSON decode error in chat_output_str: {e}")
        return str(e)

    # Unpack the chat_output_dict for the search query
    search_elements = chat_output_dict.get("modified_search_queries", [])
    print(
        "search_elements object prior to adding user search parameters: ",
        search_elements,
    )

    # Initialize the results list
    results = []

    # Generate and run the search query for each element
    for query in search_elements:
        # Overwrite / write key values in the query based on user requirements
        query["top"] = top
        query["query_type"] = query_type  # Ensure 'query_type' is valid
        query["semantic_configuration_name"] = semantic_configuration_name

        print("search_elements before embeddings call: ", search_elements)

        # Generate the semantic query with proper formatting
        try:
            query["semantic_query"] = (
                aoai_client.embeddings.create(
                    input=query["search_text"],
                    model=openai_embedding_model,
                    dimensions=dimensions,
                )
                .data[0]
                .embedding
            )
        except Exception as e:
            print(f"Error generating semantic query: {e}")
            continue

        # Build the function call
        function_call = (
            "search_client.search("
            + ", ".join(
                f'{key}="{value}"' if isinstance(value, str) else f"{key}={value}"
                for key, value in query.items()
            )
            + ")"
        )

        # Print the generated function call
        print(
            "Output of function_call (this should perform the search!!!!!!!!!!\n",
            "###############################################\n",
            function_call,
        )

        # Run the search and append results
        try:
            results += eval(function_call)
            print("Results after performing search:", results)
        except Exception as e:
            print(f"Error executing search query: {e}")

    print("Results being sent to the next node: ", results)
    return json.dumps(results, ensure_ascii=False)
