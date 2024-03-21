# Agent class
### responsbility definition: expertise, scope, conversation script, style 
from openai import AzureOpenAI
from datetime import datetime  
import os
from pathlib import Path  
import json
from concurrent.futures import ThreadPoolExecutor, as_completed  
from dotenv import load_dotenv
from azure.core.credentials import AzureKeyCredential  
from azure.search.documents import SearchClient  
import inspect

env_path = Path('.') / 'secrets.env'
load_dotenv(dotenv_path=env_path)
chat_engine =os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT")
client = AzureOpenAI(
  api_key=os.environ.get("AZURE_OPENAI_API_KEY"),  
  api_version="2023-12-01-preview",
  azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
)
def create_or_update_plan(new_plan):
    return new_plan
def update_migration_plan(existing_content, new_content):  
    """  
    Update the existing notebook content with new content.  
  
    :param existing_content: The existing content of the notebook.  
    :param new_content: The new content to add to the notebook.  
    :return: The updated notebook content.  
    """  
    # Identify the start of the notebook section  
    notebook_start = existing_content.find('## Migration Plan:')  
      
    # Check if the notebook section is found  
    if notebook_start == -1:  
        # The notebook section doesn't exist, return the original content  
        return existing_content  
      
    # Extract the content before and after the notebook section  
    before_notebook = existing_content[:notebook_start]  
      
    updated_content = before_notebook.strip() +"\n## Migration Plan:\n"+ new_content.strip()    
      
    return updated_content  
  
def save_converted_code(converted_code, file_name, migrated_code_directory):
    os.makedirs(migrated_code_directory, exist_ok=True)
    with open(os.path.join(migrated_code_directory,file_name), 'w', encoding="utf-8") as file:
        file.write(converted_code)
        
    print("code saved to file: ", file_name)
    
def read_code_file(file_name, source_directory="../Views"):
    with open(os.path.join(source_directory,file_name), 'r') as file:
        code_content = file.read()
    return code_content


def gpt_stream_wrapper(response):
    for chunk in response:
        chunk_msg= chunk['choices'][0]['delta']
        chunk_msg= chunk_msg.get('content',"")
        yield chunk_msg

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

MIGRATION_PLANNING_PROMPT = """
You are a code conversion AI assistant helping with creating a step by step action plan to convert a Sitecore MVC application to React. 
The application comprises multiple components and pages, each with its intricacies and dependencies. 
You are provided with the  findings from the source files analysis.
You need to identify the dependencies among the components and pages, as well as any common functionalities they share.
The action plan should include the following sections:
- Overview: A concise description of the migration's scope, objectives, and process.
- Components: An inventory of Sitecore components, including details such as data models, templates, JavaScript libraries, and Sitecore APIs used.
- Pages: An inventory of Sitecore pages, noting the same types of key details as for components.
- Dependencies: A catalog of dependencies among the components and pages, highlighting any potential challenges for the migration.
- Common Functionalities: An enumeration of functionalities that are ubiquitous across components and pages, which might be abstracted or centralized during the migration.
- Sequence of conversion: a sequence of conversion activities for the conversion team to follow.
For every component and page, remember to cite the source file name with full extension to facilitate the migration process.
Below is the accumulated information from the code files reviewed. Use it to create the action plan.
## Source files analysis:
{notebook}
 
"""
MIGRATION_ANALYSIS_PROMPT = """
You are a code migration expert tasked with migrating a large Sitecore MVC application to React. 
The application comprises multiple components and pages, each with its intricacies and dependencies. 
Currently, you are in the information collection and analysis phase. As you review each component and page, you will document the key information that will be useful in the migration plan. 
Review the contents of the code file '{file_name}' and analyze the code to identify the following:
- Overview: A brief description of the component or page, including its purpose and functionality.
- Dependencies: A list of dependencies and interactions with other components or pages.
- Data Models: Details about the data models used in the component or page.
- Templates: The templates used in the component or page.
- JavaSccript Libraries: The JavaScript libraries and frameworks used in the component or page.
- Sitecore APIs: The Sitecore APIs used in the component or page.
## Code content:
{code_content}
"""

CODE_MIGRATION_PROMPT = """
You are a code conversion expert tasked with migrating a  Sitecore MVC application to React. 
The application comprises multiple components and pages, each with its intricacies and dependencies. 
You are given an initial migration plan by your analysis team. Start by reviewing and discussing the plan with your user to incorporate any additional information or changes.
If you made changes to the plan, you will need to update the plan with the new content by calling the function update_migration_plan.
Then offer the user to start the actual migration process.
From the plan, pick the task one by one. If it's code conversion of a file, read the content of the file by calling the read_code_file function, analyze it and perform the conversion.
Return the converted code content together with any comments or analysis you have made to user.
Once they are satisfied with the conversion, persist the changes using function save_converted_code and move to the next task. 
## Migration Plan:
{migration_plan}
"""

def list_files(directory, max_files=10):
    """ 
    List the files in the specified directory. 
  
    :param directory: The directory to list the files.  
    :return: A list of files in the specified directory.  
    """  
    all_files = [f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f))]
    # manual files selection
    # return ['FeatureList.cshtml', '_PersonalizationPopup.cshtml', 'AboutComponent.cshtml', 'BenefitsSection.cshtml']
    return all_files[:max_files]
def get_llm_response(system_prompt, user_prompt, max_tokens=500):
    #read the content of the file
    response = client.chat.completions.create(
        model=chat_engine, # The deployment name you chose when you deployed the GPT-35-turbo or GPT-4 model.
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        max_tokens=max_tokens,
    )
    # assuming the function update_notebook is called, get the response from the function
    response_message = response.choices[0].message.content

    return response_message

  
def get_analysis(file_name, file_content):  
    # Assuming MIGRATION_ANALYSIS_PROMPT and get_llm_response are defined elsewhere  
    analysis = get_llm_response(MIGRATION_ANALYSIS_PROMPT.format(file_name=file_name, code_content=file_content),   
                                "Following the structure you're instructed, what are the key findings from the code file?")  
    return f"###file name: {file_name}\n{analysis}"  
  
def create_migration_plan(directory, max_files=10):  
    code_files = list_files(directory, max_files)  # Assuming list_files is defined elsewhere  
    notebook = ""  
      
    # Using ThreadPoolExecutor to run get_llm_response calls in parallel  
    with ThreadPoolExecutor() as executor:  
        # Create a dictionary to hold future submissions  
        future_to_file = {executor.submit(get_analysis, file, open(os.path.join(directory, file), "r").read()): file for file in code_files}  
          
        for future in as_completed(future_to_file):  
            file = future_to_file[future]  
            try:  
                analysis_result = future.result()  
                notebook += "\n\n" + analysis_result  
            except Exception as exc:  
                print(f'File {file} generated an exception: {exc}')  
      
    # Generate the migration plan based on the accumulated notebook  
    migration_plan = get_llm_response(MIGRATION_PLANNING_PROMPT.format(notebook=notebook),   
                                      "Following the structure you're instructed, what is the migration plan based on the accumulated findings?", max_tokens=1000)  
    return migration_plan, notebook  

    
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


    def __init__(self, persona,functions_spec, functions_list, name=None, init_message=None, engine =chat_engine, source_directory="../Views", migrated_code_directory="converted_code"):
        if init_message is not None:
            init_hist =[{"role":"system", "content":persona}, {"role":"assistant", "content":init_message}]
        else:
            init_hist =[{"role":"system", "content":persona}]

        self.init_history =  init_hist
        self.persona = persona
        self.engine = engine
        self.name= name
        self.source_directory= source_directory
        self.migrated_code_directory= migrated_code_directory
        self.functions_spec = functions_spec
        self.functions_list= functions_list
    def update_persona(self, new_persona):
        self.persona = new_persona
        self.init_history[0]["content"] = new_persona
    # @retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
    def run(self, user_input, conversation=None):
        if user_input is None: #if no input return init message
            return self.init_history, self.init_history[1]["content"]
        if conversation is None: #if no history return init message
            conversation = self.init_history.copy()
        conversation.append({"role": "user", "content": user_input})
        print("conversation: ", conversation)
        request_help = False
        loop_count= 0
        while True:
            loop_count+=1
            if loop_count>10:
                break
            response = client.chat.completions.create(
                model=self.engine, # The deployment name you chose when you deployed the GPT-35-turbo or GPT-4 model.
                messages=conversation,
            tools=self.functions_spec,
            tool_choice='auto',
            max_tokens=600,

            )
            
            response_message = response.choices[0].message
            if response_message.content is None:
                response_message.content = ""

            tool_calls = response_message.tool_calls
            

            # print("assistant response: ", response_message.content)
            # Step 2: check if GPT wanted to call a function
            if  tool_calls:
                conversation.append(response_message)  # extend conversation with assistant's reply
                for tool_call in tool_calls:
                    function_name = tool_call.function.name
                    print("Recommended Function call:")
                    print(function_name)
                    print()
                
                    # Step 3: call the function
                    # Note: the JSON response may not always be valid; be sure to handle errors
                                    
                    # verify function exists
                    # if function_name not in self.functions_list:
                    #     # raise Exception("Function " + function_name + " does not exist")
                    #     conversation.pop()
                    #     continue
                    function_to_call = self.functions_list[function_name]
                    
                    # verify function has correct number of arguments
                    try:
                        function_args = json.loads(tool_call.function.arguments)
                    except:
                        conversation.pop()
                        continue


                    if function_name == "save_converted_code":
                        function_args["migrated_code_directory"] = self.migrated_code_directory

                    if function_name == "read_code_file":
                        function_args["source_directory"] = self.source_directory

                    if check_args(function_to_call, function_args) is False:
                        # raise Exception("Invalid number of arguments for function: " + function_name)
                        conversation.pop()
                        continue

                    
                    # print("beginning function call")
                    function_response = str(function_to_call(**function_args))

                    if function_name=="update_migration_plan": #scenario where the agent asks for help
                        current_system_message = conversation[0]['content']
                        new_system_message = update_migration_plan(current_system_message, function_response)
                        # print("new system message: ", new_system_message)
                        conversation[0]['content'] = new_system_message
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
                

AVAILABLE_FUNCTIONS = {
            # "search_knowledgebase": search_knowledgebase,
            "update_migration_plan": update_migration_plan,
            "save_converted_code": save_converted_code,
            "read_code_file": read_code_file,


        } 

FUNCTIONS_SPEC= [  
    {
        "type":"function",
        "function":{

        "name": "update_migration_plan",
        "description": "Update the migration plan with new content",
        "parameters": {
            "type": "object",
            "properties": {
                "updated_migration_plan": {
                    "type": "string",
                    "description": "New or changed content of the migration plan"
                },

            },
            "required": ["updated_migration_plan"],
        },
    }},
    {
        "type":"function",
        "function":{

        "name": "save_converted_code",
        "description": "Write converted code to a file",
        "parameters": {
            "type": "object",
            "properties": {
                "converted_code": {
                    "type": "string",
                    "description": "The converted code to save"
                },
                "file_name": {
                    "type": "string",
                    "description": "file name with extension to save the code to. Example: 'index.js'"
                },


            },
            "required": ["converted_code", "file_name"],
        },
    }},
    {
        "type":"function",
        "function":{

        "name": "read_code_file",
        "description": "Read the content of a code file",
        "parameters": {
            "type": "object",
            "properties": {
                "file_name": {
                    "type": "string",
                    "description": "file_name to read the content from"
                },
            },
            "required": ["file_name"],
        },
    }},

]  


