import os  
import json  
import redis  
import uuid  
import pandas as pd  
from pathlib import Path  
from sqlalchemy import create_engine  
from azure.core.credentials import AzureKeyCredential  
from azure.search.documents import SearchClient  
from azure.search.documents.models import VectorizedQuery  
import contextlib  
import sys  
from io import StringIO  
from dotenv import load_dotenv  
from openai import AzureOpenAI  
from tenacity import retry, wait_random_exponential, stop_after_attempt  
from plotly.graph_objects import Figure as PlotlyFigure  
from matplotlib.figure import Figure as MatplotFigure  
from fastapi import HTTPException
import base64  
import pickle  
import inspect  
import httpx  # Add this import  
  
env_path = Path('./') / 'secrets.env'  
load_dotenv(dotenv_path=env_path)  
  
# Redis configuration  
AZURE_REDIS_ENDPOINT = os.getenv("AZURE_REDIS_ENDPOINT")  
AZURE_REDIS_KEY = os.getenv("AZURE_REDIS_KEY")  
redis_client = redis.StrictRedis(host=AZURE_REDIS_ENDPOINT, port=6380, password=AZURE_REDIS_KEY, ssl=True)  
PYTHON_SERVICE_URL = os.getenv("PYTHON_SERVICE_URL", "http://localhost:8000")  

def redis_set(key, value):  
    redis_client.set(key, base64.b64encode(s=pickle.dumps(obj=value)))  
  
def redis_get(key):  
    value = redis_client.get(key)  
    return pickle.loads(base64.b64decode(value)) if value else None  
  
# Azure OpenAI configuration  
client = AzureOpenAI(  
    api_key=os.environ.get("AZURE_OPENAI_API_KEY"),  
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),  
    azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),  
)  
chat_engine2 = os.getenv("AZURE_OPENAI_DEPLOYMENT2")  
embedding_model = os.getenv("AZURE_OPENAI_EMB_DEPLOYMENT")  
  
# Azure Search configuration  
search_service = os.getenv("AZURE_SEARCH_SERVICE_ENDPOINT")  
service_endpoint = f"https://{search_service}.search.windows.net/"  
index_name = os.getenv("AZURE_SEARCH_INDEX_NAME")  
key = os.getenv("AZURE_SEARCH_ADMIN_KEY")  
credential = AzureKeyCredential(key)  
azcs_search_client = SearchClient(service_endpoint, index_name=index_name, credential=credential)  
  
sqllite_db_path = os.environ.get("SQLITE_DB_PATH", "../../data/northwind.db")  
engine = create_engine(f'sqlite:///{sqllite_db_path}')  
  
def get_embedding(text):  
    text = text.replace("\n", " ")  
    return client.embeddings.create(input=[text], model=embedding_model).data[0].embedding  
  
def add_to_cache(question, code, answer):  
    experience = {  
        "id": str(uuid.uuid4()),  
        "question": question,  
        "code": code,  
        "questionVector": get_embedding(question),  
        "answer": answer  
    }  
    azcs_search_client.upload_documents(documents=[experience])  
  
def get_cache(question):  
    vector = VectorizedQuery(vector=get_embedding(question), k_nearest_neighbors=3, fields="questionVector")  
    results = azcs_search_client.search(  
        search_text=question,  
        vector_queries=[vector],  
        select=["question", "code", "answer"],  
        top=2  
    )  
    text_content = ""  
    for result in results:  
        if result['@search.score'] >= float(os.getenv("SEMANTIC_HIT_THRESHOLD")):  
            text_content += f"###Question: {result['question']}\n###Solution:\n {result['code']}\n"  
    return text_content  
  
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
  
async def execute_python_code(assumptions, goal, python_code, session_id):  
    url = f"{PYTHON_SERVICE_URL}/execute/"  # Change this if your FastAPI service is running on a different host/port  
    print("url ", url)
    payload = {  
        "assumptions": assumptions,  
        "goal": goal,  
        "python_code": python_code,  
        "session_id": session_id  
    }  
  
    async with httpx.AsyncClient() as client:  
        response = await client.post(url, json=payload)  
        if response.status_code == 200:  
            result = response.json()  
            print("result of python code execution ", result)
            return result["output"]  
        else:  
            raise HTTPException(status_code=response.status_code, detail=response.text)  
  
def get_additional_context():  
    pass  
  
@retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))  
def retrieve_context(business_concepts):  
    # Load the metadata file  
    with open(os.getenv("META_DATA_FILE", "data/metadata.json"), "r") as file:  
        data = json.load(file)  
    # Extract values from the loaded data  
    analytic_scenarios = data.get("analytic_scenarios", {})  
    scenario_list = [(scenario[0], scenario[1]['description']) for scenario in analytic_scenarios.items()]  
    # create scenario_list_md which is the a markdown table with column headers 'Scenario' and 'Description' from scenario_list  
    scenario_list_md = ""  
    for scenario in scenario_list:  
        scenario_list_md += f"| {scenario[0]} | {scenario[1]} |\n"  
    # add headers 'Scenario' and 'Description' to scenario_list_md  
    scenario_list_md = f"| Scenario | Description |\n| --- | --- |\n{scenario_list_md}"  
    sys_msg = f"""  
    You are an AI assistant that helps people find information.  
    You are given business concept(s) and you need to identify which one or several business analytic scenario(s) below are relevant to the them.  
    <<analytic_scenarios>>  
    {scenario_list_md}  
    <</analytic_scenarios>>  
    Output your response in json format with the following structure:  
       {{  
        "scenarios": [  
            {{  
                "scenario_name": "...", # name of the scenario.  
            }}  
        ]  
    }}  
    """  
    response = client.chat.completions.create(  
        model=chat_engine2,  # The deployment name you chose when you deployed the GPT-35-turbo or GPT-4 model.  
        messages=[{"role": "system", "content": sys_msg}, {"role": "user", "content": business_concepts}],  
        response_format={"type": "json_object"}  
    )  
    response_message = response.choices[0].message.content.strip()  
    scenario_names = json.loads(response_message)["scenarios"]  
    scenario_names = [scenario["scenario_name"] for scenario in scenario_names]  
    # Extract values from the loaded data  
    analytic_scenarios = data.get("analytic_scenarios", {})  
    scenario_list = [scenario[0] for scenario in analytic_scenarios.items()]  
    if not set(scenario_names).issubset(set(scenario_list)):  
        raise Exception("You provided invalid scenario name(s), please check and try again")  
    scenario_tables = data.get("scenario_tables", {})  
    scenario_context = "Following tables might be relevant to the question: \n"  
    all_tables = data.get("tables", {})  
    all_relationships = data.get("table_relationships", {})  
    all_relationships = {(relationship[0], relationship[1]): relationship[2] for relationship in all_relationships}  
    tables = set()  
    for scenario_name in scenario_names:  
        tables.update(scenario_tables.get(scenario_name, []))  
    for table in tables:  
        scenario_context += f"- table_name: {table} - description: {all_tables[table]['description']} - columns: {all_tables[table]['columns']}\n"  
    table_pairs = [(table1, table2) for table1 in tables for table2 in tables if table1 != table2]  
    relationships = set()  
    for table_pair in table_pairs:  
        relationship = all_relationships.get(table_pair, None)  
        if relationship:  
            relationships.add((table_pair[0], table_pair[1], relationship))  
    scenario_context += "\nTable relationships: \n"  
    for relationship in relationships:  
        scenario_context += f"- {relationship[0]}, {relationship[1]}:{relationship[2]}\n"  
    scenario_context += "\nFollowing rules might be relevant: \n"  
    for scenario_name in scenario_names:  
        scenario_context += f"- {scenario_name}: {str(analytic_scenarios[scenario_name]['rules'])}\n"  
    return scenario_context  
