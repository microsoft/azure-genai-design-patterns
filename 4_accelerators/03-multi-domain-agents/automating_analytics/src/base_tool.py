import openai
import string
import ast
import sqlite3
from datetime import timedelta
import os
import pandas as pd
import numpy as np
import random
from urllib import parse
import re
import json
from sqlalchemy import create_engine  
import sqlalchemy as sql
from plotly.graph_objects import Figure as PlotlyFigure
from matplotlib.figure import Figure as MatplotFigure
import time
from typing import List
import sys
from io import StringIO

def validate_output( llm_output,extracted_output):
    valid = False
    if "Final Answer:" in llm_output:
        return True
    for output in extracted_output:
        if len(output.get("python",""))!=0 or len(output.get("request_to_data_engineer",""))!=0:
            return True
    if (llm_output == "OPENAI_ERROR"):
        valid = True
    

    return valid
class ChatGPT_Handler: #designed for chatcompletion API
    def __init__(self, extract_patterns=None,gpt_deployment=None,max_response_tokens=None,token_limit=None,temperature=None ) -> None:
        self.max_response_tokens = max_response_tokens
        self.token_limit= token_limit
        self.gpt_deployment=gpt_deployment
        self.temperature=temperature
        self.extract_patterns = extract_patterns
        # self.conversation_history = []
    def _call_llm(self,prompt, stop):
        response = openai.ChatCompletion.create(
        engine=self.gpt_deployment, 
        messages = prompt,
        temperature=self.temperature,
        max_tokens=self.max_response_tokens,
        stop=stop
        )
        try:
            llm_output = response['choices'][0]['message']['content']
        except:
            llm_output=""

        return llm_output
    def extract_code_and_comment(self,entire_input, python_codes):
        # print("entire_input: \n", entire_input)
        remaing_input = entire_input
        comments=[]
        for python_code in python_codes:
            temp_python_code = "```python\n"+python_code+"```"
            text_before = remaing_input.split(temp_python_code)[0]
            comments.append(text_before)
            remaing_input = remaing_input.split(temp_python_code)[1]
        return comments, remaing_input
    def extract_output(self, text_input):
            # print("text_input\n",text_input)
            outputs=[]
            for pattern in self.extract_patterns: 
                if "python" in pattern[1]:

                    python_codes = re.findall(pattern[1], text_input, re.DOTALL)
                    comments, text_after= self.extract_code_and_comment(text_input, python_codes)
                    # print("text_after ", text_after)
                    for comment, code in zip(comments, python_codes):
                        outputs.append({"python":code, "comment":comment})
                    outputs.append({"text_after":text_after})
                elif "request_to_data_engineer" in pattern[1]:
                    request = re.findall(pattern[1], text_input, re.DOTALL)
                    if len(request)>0:
                        outputs.append({"request_to_data_engineer":request[0]})

            return outputs
    def get_next_steps(self, user_question, assistant_response, stop):
        if len(self.conversation_history)>2:
            self.conversation_history.pop() #removing old history

        if len(user_question)>0:
            self.conversation_history.append({"role": "user", "content": user_question})
        if len(assistant_response)>0:
            self.conversation_history.append({"role": "assistant", "content": assistant_response})
        n=0
        try:
            llm_output = self._call_llm(self.conversation_history, stop)

        except Exception as e:
            if "maximum context length" in str(e):
                print(f"Context length exceeded")
                return "OPENAI_ERROR",""  
            time.sleep(8) #sleep for 8 seconds
            while n<5:
                try:
                    llm_output = self._call_llm(self.conversation_history, stop)
                except Exception as e:
                    n +=1

                    print(f"error calling open AI, I am retrying 5 attempts , attempt {n}")
                    time.sleep(8) #sleep for 8 seconds
                    print(str(e))

            llm_output = "OPENAI_ERROR"     
             
    
        outputs = self.extract_output(llm_output)
        if len(llm_output)==0:
            return "",[]
        if not validate_output(llm_output, outputs): #wrong output format
            llm_output = "WRONG_OUTPUT_FORMAT"
        return llm_output,outputs

class SQL_Query(ChatGPT_Handler):
    def __init__(self, system_message="",data_sources="",db_path=None,driver=None,dbserver=None, database=None, db_user=None ,db_password=None, **kwargs):
        super().__init__(**kwargs)
        if len(system_message)>0:
            self.system_message = f"""
            {data_sources}
            {system_message}
            """
        self.sql_engine =os.environ.get("SQL_ENGINE","sqlite")
        self.database=database
        self.dbserver=dbserver
        self.db_user = db_user
        self.db_password = db_password
        self.db_path= db_path #This is the built-in demo using SQLite
        
        self.driver= driver
        
    def execute_sql_query(self, query, limit=10000):  
        if self.sql_engine == 'sqlserver': 
            connecting_string = f"Driver={{ODBC Driver 17 for SQL Server}};Server=tcp:{self.dbserver},1433;Database={self.database};Uid={self.db_user};Pwd={self.db_password}"
            params = parse.quote_plus(connecting_string)

            engine = create_engine("mssql+pyodbc:///?odbc_connect=%s" % params)
        else:
            engine = create_engine(f'sqlite:///{self.db_path}')  


        result = pd.read_sql_query(query, engine)
        result = result.infer_objects()
        for col in result.columns:  
            if 'date' in col.lower():  
                result[col] = pd.to_datetime(result[col], errors="ignore")  
  
        if limit is not None:  
            result = result.head(limit)  # limit to save memory  
  
        # session.close()  
        return result  
    def get_table_schema(self, table_names:List[str]):

        # Create a comma-separated string of table names for the IN operator  
        table_names_str = ','.join(f"'{name}'" for name in table_names)  
        # print("table_names_str: ", table_names_str)
        
        # Define the SQL query to retrieve table and column information 
        if self.sql_engine== 'sqlserver': 
            sql_query = f"""  
            SELECT C.TABLE_NAME, C.COLUMN_NAME, C.DATA_TYPE, T.TABLE_TYPE, T.TABLE_SCHEMA  
            FROM INFORMATION_SCHEMA.COLUMNS C  
            JOIN INFORMATION_SCHEMA.TABLES T ON C.TABLE_NAME = T.TABLE_NAME AND C.TABLE_SCHEMA = T.TABLE_SCHEMA  
            WHERE T.TABLE_TYPE = 'BASE TABLE'  AND C.TABLE_NAME IN ({table_names_str})  
            """  
        elif self.sql_engine=='sqlite':
            sql_query = f"""    
            SELECT m.name AS TABLE_NAME, p.name AS COLUMN_NAME, p.type AS DATA_TYPE  
            FROM sqlite_master AS m  
            JOIN pragma_table_info(m.name) AS p  
            WHERE m.type = 'table'  AND m.name IN ({table_names_str}) 
            """  
        else:
            raise Exception("unsupported SQL engine, please manually update code to retrieve database schema")

        # Execute the SQL query and store the results in a DataFrame  
        df = self.execute_sql_query(sql_query, limit=None)  
        output=[]
        # Initialize variables to store table and column information  
        current_table = ''  
        columns = []  
        
        # Loop through the query results and output the table and column information  
        for index, row in df.iterrows():
            if self.sql_engine== 'sqlserver': 
                table_name = f"{row['TABLE_SCHEMA']}.{row['TABLE_NAME']}"  
            else:
                table_name = f"{row['TABLE_NAME']}" 

            column_name = row['COLUMN_NAME']  
            data_type = row['DATA_TYPE']   
            if " " in table_name:
                table_name= f"[{table_name}]" 
            column_name = row['COLUMN_NAME']  
            if " " in column_name:
                column_name= f"[{column_name}]" 

            # If the table name has changed, output the previous table's information  
            if current_table != table_name and current_table != '':  
                output.append(f"table: {current_table}, columns: {', '.join(columns)}")  
                columns = []  
            
            # Add the current column information to the list of columns for the current table  
            columns.append(f"{column_name} {data_type}")  
            
            # Update the current table name  
            current_table = table_name  
        
        # Output the last table's information  
        output.append(f"table: {current_table}, columns: {', '.join(columns)}")
        output = "\n ".join(output)
        return output
    def get_table_names(self):
        
        # Define the SQL query to retrieve table and column information 
        if self.sql_engine== 'sqlserver': 
            sql_query = """  
            SELECT DISTINCT C.TABLE_NAME  
            FROM INFORMATION_SCHEMA.COLUMNS C  
            JOIN INFORMATION_SCHEMA.TABLES T ON C.TABLE_NAME = T.TABLE_NAME AND C.TABLE_SCHEMA = T.TABLE_SCHEMA  
            WHERE T.TABLE_TYPE = 'BASE TABLE' 
            """  
        elif self.sql_engine=='sqlite':
            sql_query = """    
            SELECT DISTINCT m.name AS TABLE_NAME 
            FROM sqlite_master AS m  
            JOIN pragma_table_info(m.name) AS p  
            WHERE m.type = 'table' 
            """  
        else:
            raise Exception("unsupported SQL engine, please manually update code to retrieve database schema")

        df = self.execute_sql_query(sql_query, limit=None)  
        
        output=[]
        
        # Loop through the query results and output the table and column information  
        for index, row in df.iterrows():
            if self.sql_engine== 'sqlserver': 
                table_name = f"{row['TABLE_SCHEMA']}.{row['TABLE_NAME']}"  
            else:
                table_name = f"{row['TABLE_NAME']}" 

            if " " in table_name:
                table_name= f"[{table_name}]" 
            output.append(table_name)  
        
        return output








    