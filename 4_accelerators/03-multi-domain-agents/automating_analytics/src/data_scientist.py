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
from base_tool import ChatGPT_Handler
system_message="""
You are data scientist to help answer business questions by writing python code to analyze and draw business insights.
You have the help from a data engineer who can retrieve data from source system according to your request.
The data engineer make data you would request available as a pandas dataframe variable that you can use. 
You are given following utility functions to use in your code help you retrieve data and visualize your result to end user.
    1. display(): This is a utility function that can render different types of data to end user. 
        - If you want to show  user a plotly visualization, then use ```display(fig)`` 
        - If you want to show user data which is a text or a pandas dataframe or a list, use ```display(data)```
    2. print(): use print() if you need to observe data for yourself. 
Remember to format Python code query as in ```python\n PYTHON CODE HERE ``` in your response.
Only use display() to visualize or print result to user. Only use plotly for visualization.
Please follow the <<Template>> below:
"""
few_shot_examples="""
<<Template>>
Question: User Question
Thought: First, I need to accquire the data needed for my analysis
Action: 
```request_to_data_engineer
Prepare a dataset with customers, categories and quantity, for example
```
Observation: Name of the dataset and description 
Thought: Now I can start my work to analyze data 
Action:  
```python
import pandas as pd
import numpy as np
#load data provided by data engineer
step1_df = load("name_of_dataset")
# Fill missing data
step1_df['Some_Column'] = step1_df['Some_Column'].replace(np.nan, 0)
#use pandas, statistical analysis or machine learning to analyze data to answer  business question
step2_df = step1_df.apply(some_transformation)
print(step2_df.head(10)) 
```
Observation: step2_df data seems to be good
Thought: Now I can show the result to user
Action:  
```python
import plotly.express as px 
fig=px.line(step2_df)
#visualize fig object to user.  
display(fig)
#you can also directly display tabular or text data to end user.
display(step2_df)
```
... (this Thought/Action/Observation can repeat N times)
Final Answer: Your final answer and comment for the question
<<Template>>

"""
extract_patterns=[('request_to_data_engineer',r"```request_to_data_engineer\n(.*?)```"),('python',r"```python\n(.*?)```")]


class Data_Analyzer(ChatGPT_Handler):

    def __init__(self, st,**kwargs) -> None:
        super().__init__(extract_patterns=extract_patterns,**kwargs)
        formatted_system_message = f"""
        {system_message}
        {few_shot_examples}
        """
        self.conversation_history =  [{"role": "system", "content": formatted_system_message}]
        self.st = st
    def run(self, question: str, data_preparer, show_code,show_prompt,st) -> any:
        import pandas as pd
        st.write(f"User: {question}")
        def display(data):
            if type(data) is PlotlyFigure:
                st.plotly_chart(data)
            elif type(data) is MatplotFigure:
                st.pyplot(data)
            else:
                st.write(data)
        def load(name):
            return self.st.session_state[name]
        def persist(name, data):
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
            if len(next_steps)>0:
                request = next_steps[0].get("request_to_data_engineer", "")
                if len(request)>0:
                    data_output =data_preparer.run(request,show_code,show_prompt,st)
                    if data_output is not None: #Data is returned from data engineer
                        new_input= "Observation: this is the output from data engineer\n"+data_output
                        continue
                    else:
                        st.write("I am sorry, we cannot accquire data from source system, please try again")
                        break

            for output in next_steps:
                comment= output.get("comment","")
        
                if len(comment)>0 and show_code:
                    st.write(output["comment"])
                    
                new_input += comment
                python_code = output.get("python","")
                new_input += python_code
                if len(python_code)>0:
                    old_stdout = sys.stdout
                    sys.stdout = mystdout = StringIO()

                    if show_code:
                        st.write("Code")
                        st.code(python_code)
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
                if output.get("text_after") is not None and show_code:
                    st.write(output["text_after"])
            if show_prompt:
                self.st.write("Prompt")
                self.st.write(self.conversation_history)

            if not run_ok:
                st.write(f"encountering error: {error_msg}, \nI will now retry")

            count +=1
            if "Final Answer:" in llm_output or len(llm_output)==0:
                final_output= output.get("comment","")+output.get("text_after","")+output.get("text_after","")
                return final_output
            if count>= max_steps:
                st.write("I am sorry, I cannot handle the question, please change the question and try again")
        

        

