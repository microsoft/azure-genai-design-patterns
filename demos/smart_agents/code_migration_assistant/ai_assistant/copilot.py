import streamlit as st
from streamlit_extras.add_vertical_space import add_vertical_space

from agents import CODE_MIGRATION_PROMPT, AVAILABLE_FUNCTIONS, FUNCTIONS_SPEC, Smart_Agent, create_migration_plan
import sys
import time
import random
import os
from pathlib import Path  
import json
functions = FUNCTIONS_SPEC.copy()

if "migration_plan" in st.session_state:
    migration_plan = st.session_state['migration_plan']
else:
    migration_plan = ""
agent = Smart_Agent(persona=CODE_MIGRATION_PROMPT.format(migration_plan=migration_plan),functions_list=AVAILABLE_FUNCTIONS, functions_spec=functions, init_message="Hi James, this is Maya, your AI migration assistant. Let's start by running the analysis and creating a migration plan. Please click on the 'Start Analysis' button to begin the process. You will see the migration plan once the analysis is complete. Please review it and let me know if you're good with the plan.")



st.set_page_config(layout="wide",page_title="Code migration copilot- A demo of intelligent code migration agent with OpenAI")
styl = """
<style>
        section[data-testid="stSidebar"] {
            width: 900px !important; # Set the width to your desired value
        }
</style>
"""
st.markdown(styl, unsafe_allow_html=True)

with st.sidebar:
    st.title('Code Migration Copilot')
    st.markdown("AI Copilot to help with code migration.")
    source_folder =st.text_input("Source folder", key="source_folder", value="../Views")
    if st.button("Start Analysis"):
        with st.spinner("Analyzing..."):
            migration_plan, code_analysis=create_migration_plan(source_folder, max_files=5)
            st.session_state['code_analysis'] = code_analysis
            st.session_state['migration_plan'] = migration_plan
    if "migration_plan" in st.session_state:
        migration_plan = st.session_state['migration_plan'] 
        st.markdown(migration_plan)
        if "history" in st.session_state:
            st.session_state['history'][0]["content"] = CODE_MIGRATION_PROMPT.format(migration_plan=migration_plan)


    if 'history' not in st.session_state:
        st.session_state['history'] = []
    if 'input' not in st.session_state:
        st.session_state['input'] = ""
    if 'code_display' in st.session_state:
        code = st.session_state['code_display']
        st.title('Code viewer')
        st.code(code, language='javascript')

    if st.button('Restart'):

        if 'history' in st.session_state:
            st.session_state['history'] = []
        if 'migration_plan' in st.session_state:
            st.session_state['migration_plan'] = None
    

    st.write('Created by James N')

user_input= st.chat_input("You:")

## Conditional display of AI generated responses as a function of user provided prompts
history = st.session_state['history']
      
if len(history) > 0:
    for message in history:
        message = dict(message)
        if message.get("role") != "system" and message.get("role") != "tool" and message.get("name") is None and len(message.get("content")) > 0:
            with st.chat_message(message["role"]):
                    st.markdown(message["content"])
else:
    history, agent_response = agent.run(user_input=None)
    with st.chat_message("assistant"):
        st.markdown(agent_response)
if user_input:
    with st.chat_message("user"):
        st.markdown(user_input)
    query_used, history, agent_response = agent.run(user_input=user_input, conversation=history)
    with st.chat_message("assistant"):
        st.markdown(agent_response)

st.session_state['history'] = history
