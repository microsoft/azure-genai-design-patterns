# Agent class
### responsbility definition: expertise, scope, conversation script, style 
from openai import AzureOpenAI
import os
from pathlib import Path  
import json
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential  
from azure.search.documents import SearchClient  
import inspect

from azure.search.documents.models import (

    QueryAnswerType,
    QueryCaptionType,
    QueryType,
    VectorizedQuery,
)

env_path = Path('..') / '.env'
load_dotenv(dotenv_path=env_path)
emb_engine = os.getenv("AZURE_OPENAI_EMB_DEPLOYMENT_2")

client = AzureOpenAI(
  api_key=os.environ.get("AZURE_OPENAI_API_KEY_2"),  
  api_version="2023-12-01-preview",
  azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT_2")
)
chat_engine =os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_1")

chat_client = AzureOpenAI(
  api_key=os.environ.get("AZURE_OPENAI_API_KEY_1"),  
  api_version="2023-12-01-preview",
  azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT_1")
)


#azcs implementation
service_endpoint = os.getenv("AZURE_SEARCH_SERVICE_ENDPOINT") 
index_name = os.getenv("AZURE_SEARCH_INDEX_NAME") 
index_name = index_name.strip('"')

key = os.getenv("AZURE_SEARCH_ADMIN_KEY") 
key = key.strip('"')

# @retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
# Function to generate embeddings for title and content fields, also used for query embeddings
def get_embedding(text, model=emb_engine):
   text = text.replace("\n", " ")
   return client.embeddings.create(input = [text], model=model).data[0].embedding

credential = AzureKeyCredential(key)
azcs_search_client = SearchClient(service_endpoint, index_name =index_name , credential=credential)
def display_product_info(product_ids, language="English"):  
    display_columns = ["id", "name_en", "price", "size", "description_en", "best_for_en"]
    if language == "Vietnamese":
        display_columns = ["id", "name_vi", "price", "size", "description_vi", "best_for_vi"]
    elif language == "Japanese":
        display_columns = ["id", "name_ja", "price", "size", "description_ja", "best_for_ja"]
    elif language == "Chinese":
        display_columns = ["id", "name_zh", "price", "size", "description_zh", "best_for_zh"]
    elif language == "Spanish":
        display_columns = ["id", "name_es", "price", "size", "description_es", "best_for_es"]
    table_rows = ""  
    product_ids = product_ids.split(",")  
    for product_id in product_ids:  
        result = azcs_search_client.get_document(key=product_id)  
        table_rows += f"""  
        <tr>  
            <td colspan="{len(display_columns)}">  
                <img id="image_{result['id']}" src="" alt="Loading image..." />  
            </td>  
        </tr>  
        """  
  
    html_table = f"<table border='1'>{table_rows}</table>"  
    # html_table = f"<table border='1'><tr>{''.join([f'<th>{header}</th>' for header in display_columns])}</tr>{table_rows}</table>"  

    return html_table  

def search_product(search_query, filter=None, language="English"):    
    vector = VectorizedQuery(vector=get_embedding(search_query), k_nearest_neighbors=3, fields="descriptionVector")    
    display_columns = ["id", "name_en", "price", "size", "description_en", "best_for_en"]
    if language == "Vietnamese":
        display_columns = ["id", "name_vi", "price", "size", "description_vi", "best_for_vi"]
    elif language == "Japanese":
        display_columns = ["id", "name_ja", "price", "size", "description_ja", "best_for_ja"]
    elif language == "Chinese":
        display_columns = ["id", "name_zh", "price", "size", "description_zh", "best_for_zh"]
    elif language == "Spanish":
        display_columns = ["id", "name_es", "price", "size", "description_es", "best_for_es"]
    search_parameters = {    
        "search_text": search_query,    
        "vector_queries": [vector],    
        "query_type": QueryType.SEMANTIC,    
        "semantic_configuration_name": 'my-semantic-config',    
        "query_caption": QueryCaptionType.EXTRACTIVE,    
        "query_answer": QueryAnswerType.EXTRACTIVE,    
        "top": 3    
    }    
    if filter:  
        print("filter ", filter)  
        search_parameters["select"] = display_columns
        search_parameters["filter"] = filter  
  
    results = azcs_search_client.search(**search_parameters)  
        
    table_headers = display_columns    
    table_rows = ""    
        
    for result in results:    
        table_rows += f"<tr><td>{result['id']}</td><td>{result[display_columns[1]]}</td><td>{result['price']}</td><td>{result['size']}</td><td>{result[display_columns[4]]}</td><td>{result[display_columns[5]]}</td></tr>"    
        
    html_table = f"<table><tr>{''.join([f'<th>{header}</th>' for header in table_headers])}</tr>{table_rows}</table>"    
        
    return html_table  


def check_args(function, args):
    sig = inspect.signature(function)
    params = sig.parameters

    # Check if there are extra arguments
    for name in args:
        if name not in params:
            return False
    # Check if the required arguments are provided 
    for name, param in params.items():
        if param.default is param.empty and name not in args:
            return False

    return True

class Smart_Agent():
    """
    Agent that can use other agents and tools to answer questions.

    Args:
        persona (str): The persona of the agent.
        tools (list): A list of {"tool_name":tool} that the agent can use to answer questions. Tool must have a run method that takes a question and returns an answer.
        stop (list): A list of strings that the agent will use to stop the conversation.
        init_message (str): The initial message of the agent. Defaults to None.
        engine (str): The name of the GPT engine to use. Defaults to "gpt-35-turbo".

    Methods:
        llm(new_input, stop, history=None, stream=False): Generates a response to the input using the LLM model.
        _run(new_input, stop, history=None, stream=False): Runs the agent and generates a response to the input.
        run(new_input, history=None, stream=False): Runs the agent and generates a response to the input.

    Attributes:
        persona (str): The persona of the agent.
        tools (list): A list of {"tool_name":tool} that the agent can use to answer questions. Tool must have a run method that takes a question and returns an answer.
        stop (list): A list of strings that the agent will use to stop the conversation.
        init_message (str): The initial message of the agent.
        engine (str): The name of the GPT engine to use.
    """


    def __init__(self, persona,functions_spec, functions_list, name=None, init_message=None, engine =chat_engine, language="English"):
        if init_message is not None:
            init_hist =[{"role":"system", "content":persona.format(language=language)}, {"role":"assistant", "content":init_message}]
        else:
            init_hist =[{"role":"system", "content":persona.format(language=language)}]

        self.init_history =  init_hist
        self.engine = engine
        self.name= name
        self.language = language

        self.functions_spec = functions_spec
        self.functions_list= functions_list
        
    # @retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
    def run(self, user_input, conversation=None):
        if user_input is None: #if no input return init message
            return self.init_history, self.init_history[1]["content"]
        if conversation is None: #if no history return init message
            conversation = self.init_history.copy()
        if (conversation[0]["role"] == "system") and (len(conversation[0]["content"]) == 0):
            conversation[0]["content"] = self.init_history[0]["content"]
        conversation.append({"role": "user", "content": user_input})
        request_help = False
        max_runs = 10
        count=0
        while True and count<max_runs:
            count+=1
            response = chat_client.chat.completions.create(
                model=self.engine, # The deployment name you chose when you deployed the GPT-35-turbo or GPT-4 model.
                messages=conversation,
            tools=self.functions_spec,
            # response_format={ "type": "json_object" },
            tool_choice='auto',
            max_tokens=300,

            )
            
            response_message = response.choices[0].message
            if response_message.content is None:
                response_message.content = ""

            tool_calls = response_message.tool_calls
            

            # Step 2: check if GPT wanted to call a function
            if  tool_calls:
                # print(response_message)
                conversation.append(response_message)  # extend conversation with assistant's reply
                for tool_call in tool_calls:
                    function_name = str(tool_call.function.name)
                    print("Recommended Function call:")
                    print(function_name)
                    print()
                
                    # Step 3: call the function
                    # Note: the JSON response may not always be valid; be sure to handle errors
                                    
                    # verify function exists
                    if function_name not in self.functions_list:
                        # raise Exception("Function " + function_name + " does not exist")
                        conversation.pop()
                        continue
                    function_to_call = self.functions_list[function_name]
                    
                    # verify function has correct number of arguments
                    function_args = json.loads(tool_call.function.arguments)
                    function_args['language'] = self.language

                    if check_args(function_to_call, function_args) is False:
                        # raise Exception("Invalid number of arguments for function: " + function_name)
                        conversation.pop()
                        continue

                    try:
                        function_response = str(function_to_call(**function_args))
                    except Exception as e:
                        print(e)
                        conversation.pop()
                        continue

                    # print(function_response)
                    print()
                
                    conversation.append(
                        {
                            "tool_call_id": tool_call.id,
                            "role": "tool",
                            "name": function_name,
                            "content": function_response,
                        }
                    )  # extend conversation with function response
                    

                continue
            else:
                break #if no function call break out of loop as this indicates that the agent finished the research and is ready to respond to the user
        conversation.append(response_message)
        assistant_response = response_message.content

        return request_help, conversation, assistant_response
                
PERSONA = """
You are Maya, an voice enabled AI Shiseido products sales assistant at a store with a computer screen. 
You try to sell products to customers who are international visitors and may speak different languages.
You identified that the customer you're serving speaks {language}.
Engage in the verbal conversation in {language} with the customer to understand their needs. Ask the customer about their skin type and concerns.
Use the internal product catalog search tool to find information and recommend to customer.
When there's an interest from the customer, you can use display_product_info to show details of the products on the TV screen.
Be very concise, do not use more than 2 sentences to respond. Make use of the TV screen to illustrate details.
"""

AVAILABLE_FUNCTIONS = {
            "search_product": search_product,
            "display_product_info": display_product_info


        } 

FUNCTIONS_SPEC= [  
    {
        "type":"function",
        "function":{

        "name": "display_product_info",
        "description": "Display information about products on the TV screen for the user to see.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_ids": {
                    "type": "string",
                    "description": "comma separated list of valid product ids to display information about"
                }


            },
            "required": ["product_id"],
        },
    }},
    {
        "type":"function",
        "function":{

        "name": "search_product",
        "description": "Searches product catalog for information. It's internal tool. Customer can't see the search results. Use display_product_info to show the results to the customer if needed",
        "parameters": {
            "type": "object",
            "properties": {
                "search_query": {
                    "type": "string",
                    "description": "The search query to use to search the product catalog"
                },
                "filter": {
                    "type": "string",
                    "description": "The filter to use to search the product catalog. Only price and size filters are supported.Only use quantity without unit. Example: price lt 1000 and size gt 30"
                }

            },
            "required": ["search_query"],
        },
    }},

]  