import streamlit as st  
from streamlit_extras.add_vertical_space import add_vertical_space  
sys.path.append("..")  
from agents.smart_agent import Agent_Orchestrator  
from agents.tools import add_to_cache, redis_get, redis_set  
import sys  
import os  
import json  
import uuid  
from plotly.graph_objects import Figure as PlotlyFigure  
from matplotlib.figure import Figure as MatplotFigure  
import pandas as pd  
import asyncio  
  
  
# Function to transform tools into the desired format  
st.set_page_config(layout="wide", page_title="Smart Analytic Copilot Demo Application using LLM")  
  
styl = f"""<style>  
    .stTextInput {{  
      position: fixed;  
      bottom: 3rem;  
    }}  
</style>"""  
st.markdown(styl, unsafe_allow_html=True)  
  
MAX_HIST = 3  
  
# Sidebar contents  
with st.sidebar:  
    st.title('Analytic AI Copilot')  
    st.markdown(''' ''')  
    add_vertical_space(5)  
    if st.button('Clear Chat'):  
        if 'history' in st.session_state:  
            st.session_state['history'] = []  
        if 'display_data' in st.session_state:  
            st.session_state['display_data'] = {}  
        if 'session_id' in st.session_state:  
            del st.session_state['session_id']  
    st.markdown("""  
        ### Sample Questions:  
        1. What were the total sales for each year available in the database?  
        2. Who are the top 5 customers by order volume, and what is the total number of orders for each?  
        3. What are the top 10 most popular products based on quantity sold?  
        4. What are the total sales broken down by country?  
    """)  
    st.write('')  
    st.write('')  
    st.write('')  
    st.markdown('#### Created by James N., 2024')  
  
    if 'session_id' in st.session_state:  
        session_id = st.session_state['session_id']  
    else:  
        session_id = str(uuid.uuid4())  
    st.session_state['session_id'] = session_id  
  
    if 'history' not in st.session_state:  
        st.session_state['history'] = []  
    if 'input' not in st.session_state:  
        st.session_state['input'] = ""  
    if 'question_count' not in st.session_state:  
        st.session_state['question_count'] = 0  
    if 'solution_provided' not in st.session_state:  
        st.session_state['solution_provided'] = False  
    if 'display_data' not in st.session_state:  
        st.session_state['display_data'] = {}  
  
agent_runner = Agent_Orchestrator(session_id=session_id)  
user_input = st.chat_input("You:")  
history = st.session_state['history']  
display_data = st.session_state['display_data']  
question_count = st.session_state['question_count']  
data = None  
  
if len(history) > 0:  
    # Purging history  
    removal_indices = []  
    idx = 0  
    running_question_count = 0  
    start_counting = False  # Flag to start including history items in the removal_indices list  
    for message in history:  
        idx += 1  
        message = dict(message)  
        if message.get("role") == "user":  
            running_question_count += 1  
            start_counting = True  
        if start_counting and (question_count - running_question_count >= MAX_HIST):  
            removal_indices.append(idx - 1)  
        elif question_count - running_question_count < MAX_HIST:  
            break  
    for index in removal_indices:  
        del history[index]  
    question_count = 0  
    for message in history:  
        message = dict(message)  
        if message.get("role") == "user":  
            question_count += 1  
        if message.get("role") != "system" and message.get("role") != "tool" and message.get("name") is None and len(message.get("content")) > 0:  
            with st.chat_message(message["role"]):  
                st.markdown(message["content"])  
        elif message.get("role") == "tool":  
            data_item = display_data.get(message.get("tool_call_id"), None)  
            if data_item is not None:  
                if type(data_item) is PlotlyFigure:  
                    st.plotly_chart(data_item)  
                elif type(data_item) is MatplotFigure:  
                    st.pyplot(data_item)  
                elif type(data_item) is pd.DataFrame:  
                    st.dataframe(data_item)  
else:  
    history, agent_response = asyncio.run(agent_runner.run(user_input=None))  
    with st.chat_message("assistant"):  
        st.markdown(agent_response)  
    user_history = []  
  
if user_input:  
    st.session_state['solution_provided'] = False  
    st.session_state['feedback'] = False  
    with st.chat_message("user"):  
        st.markdown(user_input)  
        code, history, agent_response, data = asyncio.run(agent_runner.run(user_input=user_input, conversation=history))  
        viz_output = redis_get('data' + session_id)  
        if viz_output is not None:  
            if type(viz_output) is PlotlyFigure:  
                print("display chart")  
                st.plotly_chart(viz_output)  
            else:  
                st.write(viz_output)  
    with st.chat_message("assistant"):  
        if agent_response:  
            st.markdown(agent_response)  
            st.session_state['solution_provided'] = True  
            st.session_state['code'] = code  
            st.session_state['answer'] = agent_response  
            st.session_state['question'] = user_input  
            feedback = st.checkbox("That was a good answer", key="feedback")  
    if data is not None:  
        st.session_state['display_data'] = data  
    st.session_state['history'] = history  
st.session_state['question_count'] = question_count  
if st.session_state['solution_provided']:  
    feedback = st.session_state.get("feedback", False)  
    if feedback:  
        print("Feedback received:", feedback)  
        code = st.session_state['code']  
        question = st.session_state['question']  
        answer = st.session_state['answer']  
        if len(code) > 0 and len(question) > 0:  
            print("adding to cache")  
            add_to_cache(question, code, answer)  
