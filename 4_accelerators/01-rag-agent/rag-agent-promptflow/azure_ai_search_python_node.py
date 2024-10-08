import json
from azure.identity import DefaultAzureCredential
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.ai.ml import MLClient
from openai import AzureOpenAI
from promptflow.core import tool

@tool
def azure_ai_search_tool(
    chat_output_str: str,
    subscription_id: str,
    resource_group: str,
    workspace_name: str,
    ai_search_index_name: str,
    top: int,
    query_type: str,
    semantic_configuration_name: str,
    openai_embedding_model: str,
    aoai_api_version: str,
    dimensions: int,
) -> str:
    
    # Create an MLClient to get the connections
    ml_client = MLClient(DefaultAzureCredential(), subscription_id, resource_group, workspace_name)
    # Retrieve AI Studio Connections for API Base URL and Keys for AI Search and AOAI Services
    AI_SEARCH_CONNECTION = ml_client.connections.get(name="AI_SEARCH_CONNECTION", populate_secrets=True)
    AOAI_CONNECTION = ml_client.connections.get(name="AOAI_CONNECTION", populate_secrets=True)

    # Create the SearchClient - this gets used in function_call
    search_client = SearchClient(
        endpoint=AI_SEARCH_CONNECTION.api_base,
        index_name=ai_search_index_name,
        credential=AzureKeyCredential(AI_SEARCH_CONNECTION.api_key),
    )

    # Create the Azure OpenAI Client
    aoai_client = AzureOpenAI(
        azure_endpoint=AOAI_CONNECTION.api_base,
        api_key=AOAI_CONNECTION.api_key,
        api_version=aoai_api_version,
    )

    # Load the chat_output_str (should be the JSON entity and topic tracking object) into a dictionary
    try:
        chat_output_dict = json.loads(chat_output_str)
    except json.JSONDecodeError as e:
        print(f"JSON decode error in chat_output_str: {e}")
        return str(e)

    # Unpack the chat_output_dict and extract  for the search query
    search_elements = chat_output_dict.get("modified_search_queries", [])

    # Initialize the results list
    results = []

    # Generate and run the search query for each element
    for query in search_elements:
        # Overwrite / write key values in the query based on user requirements
        query["top"] = top
        query["query_type"] = query_type  # Ensure 'query_type' is valid
        query["semantic_configuration_name"] = semantic_configuration_name

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

        # Build the function call to submit the query to AI Search
        function_call = (
            "search_client.search("
            + ", ".join(
                f'{key}="{value}"' if isinstance(value, str) else f"{key}={value}"
                for key, value in query.items()
            )
            + ")"
        )

        # Run the search and append results
        try:
            results += eval(function_call)
            print("Results after performing search:", results)
        except Exception as e:
            print(f"Error executing search query: {e}")

    return json.dumps(results, ensure_ascii=False)
