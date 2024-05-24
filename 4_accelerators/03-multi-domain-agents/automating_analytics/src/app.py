import streamlit as st
import os
import pandas as pd
import sys
import numpy as np
import plotly.express as px
import plotly.graph_objs as go
from data_engineer import SQL_Data_Preparer
from data_scientist import Data_Analyzer
import openai
import streamlit as st  

from dotenv import load_dotenv

from pathlib import Path  # Python 3.6+ only

env_path = Path('.') / 'secrets.env'
load_dotenv(dotenv_path=env_path)


openai.api_type = "azure"
openai.api_version = "2023-03-15-preview" 
max_response_tokens = 1250
token_limit= 4096
temperature=0

sqllite_db_path= os.environ.get("SQLITE_DB_PATH","data/northwind.db")


faq_dict = {  
    "ChatGPT": [  
        "Show me daily revenue trends in 2016 across product categories",  
        "Is that true that top 20% customers generate 80% revenue in 2016? What's their percentage of revenue contribution?",  
        "Which products have most seasonality in sales quantity in 2016?",  
        "Which customers are most likely to churn?", 
        "What is the impact of discount on sales? What's optimal discount rate?" 
    ],  
    "GPT-4": [  
        "Predict monthly revenue for next 6 months starting from June-2018. Do not use Prophet.",  
        "What is the impact of discount on sales? What's optimal discount rate?" ,  
    ]  
}  
st.sidebar.title('Data Analysis Assistant')

col1, col2  = st.columns((3,1)) 
def save_setting(setting_name, setting_value):  
    """  
    Function to save the setting information to session  
    """  
    st.session_state[setting_name] = setting_value  
  
def load_setting(setting_name, default_value=''):  
    """  
    Function to load the setting information from session  
    """  
    if  os.environ.get(setting_name) is not None:
        return os.environ.get(setting_name)
    if setting_name not in st.session_state:  
        st.session_state[setting_name] = default_value  
    return st.session_state[setting_name]  

chatgpt_deployment = load_setting("AZURE_OPENAI_CHATGPT_DEPLOYMENT","gpt-35-turbo")  
gpt4_deployment = load_setting("AZURE_OPENAI_GPT4_DEPLOYMENT","gpt-35-turbo")  
endpoint = load_setting("AZURE_OPENAI_ENDPOINT","https://resourcenamehere.openai.azure.com/")  
api_key = load_setting("AZURE_OPENAI_API_KEY")  
sql_engine = load_setting("SQL_ENGINE","sqlite")
dbserver = load_setting("SQL_SERVER")
database = load_setting("SQL_DATABASE")
db_user = load_setting("SQL_USER")
db_password = load_setting("SQL_PASSWORD")

with st.sidebar:  
    with st.expander("Settings"):
        chatgpt_deployment = st.text_input("ChatGPT deployment name:", value=chatgpt_deployment)  
        gpt4_deployment = st.text_input("GPT-4 deployment name (if not specified, default to ChatGPT's):", value=gpt4_deployment) 
        if gpt4_deployment=="":
            gpt4_deployment= chatgpt_deployment 
        endpoint = st.text_input("Azure OpenAI Endpoint:", value=endpoint)  
        api_key = st.text_input("Azure OpenAI Key:", value=api_key, type="password")

        save_setting("AZURE_OPENAI_CHATGPT_DEPLOYMENT", chatgpt_deployment)  
        save_setting("AZURE_OPENAI_GPT4_DEPLOYMENT", gpt4_deployment)  
        save_setting("AZURE_OPENAI_ENDPOINT", endpoint)  
        save_setting("AZURE_OPENAI_API_KEY", api_key)  


        sql_engine = st.selectbox('SQL Engine',["sqlite", "sqlserver"])  
        if sql_engine =="sqlserver":
            dbserver = st.text_input("SQL Server:", value=dbserver)  
            database = st.text_input("SQL Server Database:", value=database)  
            db_user = st.text_input("SQL Server db_user:", value=db_user)  
            db_password = st.text_input("SQL Server Password:", value=db_password, type="password")

        save_setting("SQL_ENGINE", sql_engine)  
        save_setting("SQL_SERVER", dbserver)  
        save_setting("SQL_DATABASE", database) 
        save_setting("SQL_USER", db_user)   
        save_setting("SQL_PASSWORD", db_password)  

    gpt_engine = st.selectbox('GPT Model', ["ChatGPT", "GPT-4"])  
    if gpt_engine == "ChatGPT":  
        gpt_engine = chatgpt_deployment  
        faq = faq_dict["ChatGPT"]  
    else:  
        gpt_engine = gpt4_deployment  
        faq = faq_dict["GPT-4"]  
    option = st.selectbox('FAQs',faq)  

    if gpt_engine!="":
    
        sql_engine = load_setting("SQL_ENGINE")
        dbserver = load_setting("SQL_SERVER")  
        database = load_setting("SQL_DATABASE")
        db_user = load_setting("SQL_USER")
        db_password = load_setting("SQL_PASSWORD")
        data_preparer = SQL_Data_Preparer(sql_engine=sql_engine,st=st,dbserver=dbserver,db_path=sqllite_db_path, database=database, db_user=db_user ,db_password=db_password,  
                            gpt_deployment=gpt_engine,max_response_tokens=max_response_tokens,token_limit=token_limit,
                            temperature=temperature)  
        
        analyzer = Data_Analyzer(st=st,gpt_deployment=gpt_engine,max_response_tokens=max_response_tokens,token_limit=token_limit,
                            temperature=temperature)  

    show_code = st.checkbox("Show code", value=False)  
    show_prompt = st.checkbox("Show prompt", value=False)

    question = st.text_area("Ask me a question", option)
    openai.api_key = api_key
    openai.api_base = endpoint
  
    if st.button("Submit"):  
        if chatgpt_deployment=="" or endpoint=="" or api_key=="":
            col1.error("You need to specify Open AI Deployment Settings!", icon="🚨")
        else:
            for key in st.session_state.keys():
                if "AZURE_OPENAI" not in key and "settings" and "SQL" not in key : 
                    del st.session_state[key]  

            analyzer.run(question,data_preparer,show_code,show_prompt, col1)  