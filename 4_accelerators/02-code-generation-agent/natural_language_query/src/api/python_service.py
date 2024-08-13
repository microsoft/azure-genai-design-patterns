import os  
import json  
import redis  
import uuid  
import pandas as pd  
from pathlib import Path  
from sqlalchemy import create_engine  
import contextlib  
import sys  
from io import StringIO  
from dotenv import load_dotenv  
from openai import AzureOpenAI  
from tenacity import retry, wait_random_exponential, stop_after_attempt  
from plotly.graph_objects import Figure as PlotlyFigure  
from matplotlib.figure import Figure as MatplotFigure  
import base64  
import pickle  
import inspect  
from fastapi import FastAPI, HTTPException  
from pydantic import BaseModel  
  
# Load environment variables  
env_path = Path('.') / 'secrets.env'  
load_dotenv(dotenv_path=env_path)  
  
# Redis configuration  
AZURE_REDIS_ENDPOINT = os.getenv("AZURE_REDIS_ENDPOINT")  
AZURE_REDIS_KEY = os.getenv("AZURE_REDIS_KEY")  
redis_client = redis.StrictRedis(host=AZURE_REDIS_ENDPOINT, port=6380, password=AZURE_REDIS_KEY, ssl=True)  
  
# Redis functions  
def redis_set(key, value):  
    redis_client.set(key, base64.b64encode(pickle.dumps(value)))  
  
def redis_get(key):  
    value = redis_client.get(key)  
    return pickle.loads(base64.b64decode(value)) if value else None  
  
# Azure OpenAI configuration  
client = AzureOpenAI(  
    api_key=os.environ.get("AZURE_OPENAI_API_KEY"),  
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),  
    azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT"),  
)  
  
  
# SQLAlchemy configuration  
sqllite_db_path = os.environ.get("SQLITE_DB_PATH", "data/northwind.db")  
engine = create_engine(f'sqlite:///{sqllite_db_path}')  
  
# FastAPI application  
app = FastAPI()  
  
# Request model  
class ExecutionRequest(BaseModel):  
    assumptions: str  
    goal: str  
    python_code: str  
    session_id: str  
  
  
@app.post("/execute/")  
async def execute_code(request: ExecutionRequest):  
    execution_context = {}  # Initialize execution context  
  
    def execute_sql_query(sql_query, limit=100):  
        result = pd.read_sql_query(sql_query, engine)  
        result = result.infer_objects()  
        for col in result.columns:  
            if 'date' in col.lower():  
                result[col] = pd.to_datetime(result[col], errors="ignore")  
        return result  
  
    def reduce_dataframe_size(df):  
        max_str_length = 100  
        max_list_length = 3  
        reduced_df = pd.DataFrame()  
        for column in df.columns:  
            if df[column].dtype == object or df[column].dtype == str:  
                reduced_df[column] = df[column].apply(lambda x: reduce_cell(x, max_str_length, max_list_length))  
            else:  
                reduced_df[column] = df[column]  
        return reduced_df  
  
    def reduce_cell(cell, max_str_length, max_list_length):  
        try:  
            data = json.loads(cell)  
            if isinstance(data, list):  
                data = truncate_list(data, max_list_length)  
            return json.dumps(data)  
        except (json.JSONDecodeError, TypeError):  
            return str(cell)[:max_str_length]  
  
    def truncate_list(lst, max_list_length):  
        truncated = lst[:max_list_length]  
        for i, item in enumerate(truncated):  
            if isinstance(item, dict):  
                truncated[i] = truncate_dict(item, max_list_length)  
            elif isinstance(item, list):  
                truncated[i] = truncate_list(item, max_list_length)  
        return truncated  
  
    def truncate_dict(dct, max_list_length):  
        for key, value in dct.items():  
            if isinstance(value, list):  
                dct[key] = truncate_list(value, max_list_length)  
            elif isinstance(value, dict):  
                dct[key] = truncate_dict(value, max_list_length)  
        return dct  
  
    def show_to_user(data):  
        redis_set('data' + request.session_id, data)  # Store data in Redis  
        if type(data) is PlotlyFigure or type(data) is MatplotFigure:  
            pass  
        elif type(data) is pd.DataFrame:  
            data = data.head(30)  
            data = reduce_dataframe_size(data)  
            redis_set('data_from_display_' + request.session_id, data.to_markdown(index=False, disable_numparse=True))  
        else:  
            redis_set('data_from_display_' + request.session_id, str(data))  
  
    if 'execute_sql_query' not in execution_context:  
        execution_context['execute_sql_query'] = execute_sql_query  
    if 'show_to_user' not in execution_context:  
        execution_context['show_to_user'] = show_to_user  
  
    # Define a context manager to redirect stdout and stderr  
    @contextlib.contextmanager  
    def captured_output():  
        new_out, new_err = StringIO(), StringIO()  
        old_out, old_err = sys.stdout, sys.stderr  
        try:  
            sys.stdout, sys.stderr = new_out, new_err  
            yield sys.stdout, sys.stderr  
        finally:  
            sys.stdout, sys.stderr = old_out, old_err  
  
    # Use the context manager to capture output  
    with captured_output() as (out, err):  
        try:  
            exec(request.python_code, execution_context)  
        except Exception as e:  
            if hasattr(e, 'message'):  
                print(f"{type(e)}: {e.message}", file=sys.stderr)  
            else:  
                print(f"{type(e)}: {e}", file=sys.stderr)  
  
    # Retrieve the captured output and errors  
    stdout = out.getvalue()  
    stderr = err.getvalue()  
    new_input = ""  
  
    if len(stdout) > 0:  
        new_input += "\n" + stdout  
        return {"output": new_input}  
  
    if len(stderr) > 0:  
        new_input += "\n" + stderr  
        return {"output": new_input}  
  
    data_display = redis_get("data_from_display_" + request.session_id)  
    if data_display:  
        return {"output": data_display}  
  
    return {"output": "The graph for the data was displayed to the user."}  
  
if __name__ == "__main__":  
    import uvicorn  
    uvicorn.run(app, host="0.0.0.0", port=8000)  
