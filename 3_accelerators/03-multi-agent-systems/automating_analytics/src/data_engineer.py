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
from base_tool import ChatGPT_Handler, SQL_Query
system_message="""
You are a data engineer to help retrieve data by writing python code to query data from DBMS based on request. 
You generally follow this process:
1. You first need to identify the list of usable tables 
2. From the question, you decide on which tables are needed to accquire data
3. Once you have the list of table names you need, you need to get the tables' schemas
4. Then you can formulate your SQL query
5. Check your data 
6. Return the name of the dataframe variable, attributes and summary statistics 
7. Do not write code for more than 1 thought step. Do it one at a time.

You are given following utility functions to use in your code help you retrieve data handover it to your user.
    1. get_table_names(): a python function to return the list of usable tables. From this list, you need to determine which tables you are going to use.
    2. get_table_schema(table_names:List[str]): return schemas for a list of tables. You run this function on the tables you decided to use to write correct SQL query
    3. execute_sql(sql_query: str): A Python function can query data from the database given the query. 
        - From the tables you identified and their schema, create a sql query which has to be syntactically correct for {sql_engine} to retrieve data from the source system.
        - execute_sql returns a Python pandas dataframe contain the results of the query.
    4. print(): use print() if you need to observe data for yourself. 
    5. save("name", data): to persist dataset for later use
Here is a specific <<Template>> to follow:
"""

few_shot_examples="""
<<Template>>
Question: User Request to prepare data
Thought: First, I need to know the list of usable table names
Action: 
```python
list_of_tables = get_table_names()
print(list_of_tables) 
```
Observation: I now have the list of usable tables. 
Thought: I now choose some tables from the list of usable tables . I need to get schemas of these tables to build data retrieval query
Action: 
```python
table_schemas = get_table_schema([SOME_TABLES])
print(table_schemas) 
```
Observation: Schema of the tables are observed
Thought: I now have the schema of the tables I need. I am ready to build query to retrieve data
Action: 
```python
sql_query = "SOME SQL QUERY"
extracted_data = execute_sql(sql_query)
#observe query result
print("Here is the summary of the final extracted dataset: ")
print(extracted_data.describe())
#save the data for later use
save("name_of_dataset", extracted_data)
```
Observation: extracted_data seems to be ready
Final Answer: Hey, data scientist, here is name of dataset, attributes and summary statistics
<<Template>>
"""
extract_patterns=[('python',r"```python\n(.*?)```")]
class SQL_Data_Preparer(ChatGPT_Handler):

    def __init__(self,sql_engine, st,db_path=None, dbserver=None, database=None, db_user=None,db_password=None,**kwargs) -> None:
        super().__init__(extract_patterns=extract_patterns, **kwargs)
        if sql_engine =="sqlserver":
            #TODO: Handle if there is not a driver here
            self.sql_query_tool = SQL_Query(driver='ODBC Driver 17 for SQL Server',dbserver=dbserver, database=database, db_user=db_user ,db_password=db_password)
        else:
            self.sql_query_tool = SQL_Query(db_path=db_path)

        global system_message, few_shot_examples
        # table_names = sql_query_tool.get_table_schema()
        formatted_system_message = f"""
        {system_message.format(sql_engine=sql_engine)}
        {few_shot_examples}
        """
        self.conversation_history =  [{"role": "system", "content": formatted_system_message}]
        self.st = st
    def run(self, question: str, show_code,show_prompt,st) -> str:
        import pandas as pd
        st.write(f"Request to prepare data: {question}")
        def get_table_names():
            return self.sql_query_tool.get_table_names()
        def get_table_schema(table_names:List[str]):
            return self.sql_query_tool.get_table_schema(table_names)

        def execute_sql(query):
            return self.sql_query_tool.execute_sql_query(query)
        def display(data):
            if type(data) is PlotlyFigure:
                st.plotly_chart(data)
            elif type(data) is MatplotFigure:
                st.pyplot(data)
            else:
                st.write(data)
        def load(name):
            return self.st.session_state[name]
        def save(name, data):
            self.st.session_state[name]= data

        def observe(name, data):
            try:
                data = data[:10] # limit the print out observation to 15 rows
            except:
                pass
            self.st.session_state[f'observation:{name}']=data

        max_steps = 15
        count =1

        user_question= f"Question: {question}"
        new_input=""
        error_msg=""
        while count<= max_steps:
            llm_output,next_steps = self.get_next_steps(user_question= user_question, assistant_response =new_input, stop=["Observation:"])
            
            user_question=""
            if llm_output=='OPENAI_ERROR':
                st.write("Error Calling Azure Open AI, probably due to service limit, please start over")
                break
            elif llm_output=='WRONG_OUTPUT_FORMAT': #just have open AI try again till the right output comes
                count +=1
                continue
            new_input= "" #forget old history
            run_ok =True
            for output in next_steps:
                comment= output.get("comment","")
                new_input += comment
                python_code = output.get("python","")
                new_input += python_code
                if len(python_code)>0:
                    old_stdout = sys.stdout
                    sys.stdout = mystdout = StringIO()

                    # if show_code:
                    #     st.write("Code")
                    #     st.code(python_code)
                    try:
                        exec(python_code, locals())
                        sys.stdout = old_stdout
                        std_out = str(mystdout.getvalue())
                        if len(std_out)>0:
                            new_input +="\nObservation:\n"+ std_out 
                            # print(new_input)                  
                    except Exception as e:
                        new_input +="\nObservation: Encounter following error:"+str(e)+"\nIf the error is about python bug, fix the python bug, if it's about SQL query, double check that you use the corect tables and columns name and query syntax, can you re-write the code?"
                        sys.stdout = old_stdout
                        run_ok = False
                        error_msg= str(e)
                # if output.get("text_after") is not None and show_code:
                #     st.write(output["text_after"])
            if show_prompt:
                self.st.write("Prompt")
                self.st.write(self.conversation_history)

            if not run_ok:
                st.write(f"encountering error: {error_msg}, \nI will now retry")

            count +=1
            if "Final Answer:" in llm_output:
                final_output= output.get("comment","")+output.get("text_after","")+output.get("text_after","")
                return final_output
            if count>= max_steps:
                st.write("I am sorry, I cannot handle the question, please change the question and try again")

        

        

