from pathlib import Path  
import json  
import os  
from openai import AzureOpenAI  
import yaml  
from .tools import (  
    check_args,  
    get_cache,  
    transform_tools,  
    execute_python_code,  
    retrieve_context,  
    redis_get,  
    redis_set  
)  
from tenacity import retry, wait_random_exponential, stop_after_attempt  
import pandas as pd  
from dotenv import load_dotenv  
import inspect  
  
env_path = Path('./') / 'secrets.env'  
load_dotenv(dotenv_path=env_path)  
  
MAX_ERROR_RUN = 3  
MAX_RUN_PER_QUESTION = 10  
MAX_QUESTION_TO_KEEP = 3  
MAX_QUESTION_WITH_DETAIL_HIST = 1  
  
chat_engine1 = os.getenv("AZURE_OPENAI_DEPLOYMENT1")  
chat_engine2 = os.getenv("AZURE_OPENAI_DEPLOYMENT2")  
client = AzureOpenAI(  
    api_key=os.environ.get("AZURE_OPENAI_API_KEY"),  
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),  
    azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),  
)  
  
max_conversation_len = 5  # Set the desired value of k  
  
def clean_up_history(history, max_q_with_detail_hist=1, max_q_to_keep=2):  
    question_count = 0  
    removal_indices = []  
    for idx in range(len(history) - 1, 0, -1):  
        message = dict(history[idx])  
        if message.get("role") == "user":  
            question_count += 1  
            if question_count >= max_q_with_detail_hist and question_count < max_q_to_keep:  
                if message.get("role") != "user" and message.get("role") != "assistant" and len(message.get("content")) == 0:  
                    removal_indices.append(idx)  
        if question_count >= max_q_to_keep:  
            removal_indices.append(idx)  
    for index in removal_indices:  
        del history[index]  
  
def reset_history_to_last_question(history):  
    for i in range(len(history) - 1, -1, -1):  
        message = dict(history[i])  
        if message.get("role") == "user":  
            break  
        history.pop()  
  
class Smart_Agent:  
    def __init__(self, persona, functions_spec, functions_list, name=None, engine=chat_engine2):  
        self.persona = persona  
        self.engine = engine  
        self.name = name  
        self.functions_spec = functions_spec  
        self.functions_list = functions_list  
  
    async def run(self, session_id, conversation):  
        execution_error_count = 0  
        response_message = None  
        data = {}  
        execution_context = {}  
        run_count = 0  
        code = ""  
        switch_role = False  
  
        while True:  
            if switch_role:  
                break  
            if run_count >= MAX_RUN_PER_QUESTION:  
                reset_history_to_last_question(conversation)  
                print(f"Need to move on from this question due to max run count reached ({run_count} runs)")  
                response_message = {"role": "assistant", "content": "I am unable to answer this question at the moment, please ask another question."}  
                break  
            if execution_error_count >= MAX_ERROR_RUN:  
                reset_history_to_last_question(conversation)  
                print(f"resetting history due to too many errors ({execution_error_count} errors) in the code execution")  
                execution_error_count = 0  
  
            response = client.chat.completions.create(  
                model=self.engine,  # The deployment name you chose when you deployed the GPT-35-turbo or GPT-4 model.  
                messages=conversation,  
                tools=self.functions_spec,  
                tool_choice='auto',  
                temperature=0.2,  
            )  
            run_count += 1  
            response_message = response.choices[0].message  
            if response_message.content is None:  
                response_message.content = ""  
            tool_calls = response_message.tool_calls  
  
            if tool_calls:  
                conversation.append(response_message)  # extend conversation with assistant's reply  
                for tool_call in tool_calls:  
                    function_name = tool_call.function.name  
                    print("Recommended Function call:")  
                    print(function_name)  
                    print()  
                    if function_name == "get_additional_context":  
                        switch_role = True  
                        run_count = 0  
                        break  
                    if function_name not in self.functions_list:  
                        print(("Function " + function_name + " does not exist, retrying"))  
                        function_response = "Function " + function_name + " does not exist"  
                    else:  
                        function_to_call = self.functions_list[function_name]  
                        try:  
                            function_args = json.loads(tool_call.function.arguments)  
                        except json.JSONDecodeError as e:  
                            print(e)  
                            conversation.pop()  
                            break  
                        if function_name == "execute_python_code":  
                            function_args["session_id"] = session_id  
                        if check_args(function_to_call, function_args) is False:  
                            print("check arg failed")  
                            conversation.pop()  
                            break  
                        if function_name == "execute_python_code":  
                            function_response = await function_to_call(**function_args)  
                            print("done execute python code ,", function_response)  
                            data_output = redis_get('data' + session_id)  
                            if data_output is not None:  
                                data[tool_call.id] = data_output  
                            if "error" in function_response:  
                                execution_error_count += 1  
                                print("error")  
                            else:  
                                code = function_args["python_code"]  
                        else:  
                            function_response = str(function_to_call(**function_args))  
                        print()  
                    conversation.append(  
                        {  
                            "tool_call_id": tool_call.id,  
                            "role": "tool",  
                            "name": function_name,  
                            "content": function_response,  
                        }  
                    )  
                    continue  
            else:  
                break  
  
        conversation.append(response_message)  
        assistant_response = dict(response_message).get('content')  
        return switch_role, code, assistant_response, data  
  
def transform_tools(tools):  
    transformed_tools = []  
    for tool in tools:  
        transformed_tool = {  
            "type": "function",  
            "function": {  
                "name": tool['name'],  
                "description": tool['description'],  
                "parameters": tool.get('parameters', {})  
            }  
        }  
        transformed_tools.append(transformed_tool)  
    return transformed_tools  
  
def load_entity(file_path, entity_name):  
    with open(file_path, 'r') as file:  
        data = yaml.safe_load(file)  
    for entity in data:  
        if entity.get('name') == entity_name:  
            return entity  
    return None  
  
class Agent_Orchestrator:  
    def __init__(self, session_id, init_message="Hello, I am your AI Analytic Assistant, what question do you have？", active_agent=0):  
        coder1 = load_entity('src/agents/prompts.yaml', 'coder1')  
        coder2 = load_entity('src/agents/prompts.yaml', 'coder2')  
        if coder1:  
            coder1_functions_spec = transform_tools(coder1.get('tools', []))  
            coder1_functions = {  
                "execute_python_code": execute_python_code,  
                "retrieve_context": retrieve_context  
            }  
        if coder2:  
            coder2_functions_spec = transform_tools(coder2.get('tools', []))  
            coder2_functions = {  
                "execute_python_code": execute_python_code,  
                "retrieve_context": retrieve_context  
            }  
        agent1 = Smart_Agent(persona=coder1.get("persona"), functions_list=coder1_functions, functions_spec=coder1_functions_spec)  
        agent2 = Smart_Agent(persona=coder2.get("persona"), functions_list=coder2_functions, functions_spec=coder2_functions_spec)  
        self.agents = [agent1, agent2]  
        self.session_id = session_id  
        init_history = redis_get(self.session_id)  
        if init_history:  
            self.conversation = init_history  
        if init_message is not None:  
            self.conversation = [{"role": "system", "content": self.agents[active_agent].persona}, {"role": "assistant", "content": init_message}]  
        else:  
            self.conversation = [{"role": "system", "content": self.agents[active_agent].persona}]  
        self.active_agent = active_agent  
  
    def switch_persona(self, similiar_question=None):  
        if self.active_agent == 0 or similiar_question is not None:  
            if similiar_question is not None:  
                new_system_message = {"role": "system", "content": self.agents[1].persona + "\n Here are similiar answered questions with solutions: \n" + similiar_question}  
                self.conversation[0] = new_system_message  
                self.active_agent = 1  
                print("Giving similiar solutions context to coder2")  
        elif self.active_agent == 1:  
            print("Switching persona to coder1")  
            new_system_message = {"role": "system", "content": self.agents[0].persona}  
            self.conversation[0] = new_system_message  
            self.active_agent = 0  
  
    async def run(self, user_input, conversation=None):  
        if user_input is None:  # if no input return init message  
            return self.conversation, self.conversation[1]["content"]  
        if conversation is not None:  # if no history return init message  
            self.conversation = conversation  
        similiar_question = get_cache(user_input)  
        if self.active_agent == 0:  
            if len(similiar_question) > 0:  
                self.switch_persona(similiar_question)  
        else:  
            if len(similiar_question) > 0:  
                self.switch_persona(similiar_question)  # updating coder 2 with similiar questions  
            else:  
                self.switch_persona()  # no similiar questions, switch to coder 1  
        self.conversation.append({"role": "user", "content": user_input})  
        clean_up_history(self.conversation, max_q_with_detail_hist=MAX_QUESTION_WITH_DETAIL_HIST, max_q_to_keep=MAX_QUESTION_TO_KEEP)  
        switch_persona, code, assistant_response, data = await self.agents[self.active_agent].run(self.session_id, self.conversation)  
        if switch_persona:  
            self.switch_persona()  
            reset_history_to_last_question(self.conversation)  
            switch_persona, code, assistant_response, data = await self.agents[self.active_agent].run(self.session_id, self.conversation)  
        redis_set(self.session_id, self.conversation)  
        return code, self.conversation, assistant_response, data  
  
