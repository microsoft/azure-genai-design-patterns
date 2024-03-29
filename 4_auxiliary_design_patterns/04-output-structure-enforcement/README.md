# Output Structure Enforcement

## Use Cases
When a LLM is used as a component of a pipeline, its output is used by another process which assumes the output follow certain structure.
For example, in a pipeline that extract insights from customer service call transcripts, the GPT model is designed to extract problems, products, potential follow-up action and call summary out of a every call which is then inserted into a standardized table in SQL reporting database. 
It is important that the LLM model produces the output according to a predefined structure and format so that it can be consumed by the down stream process. A mal-formated output can break the pipeline or result in inconsumable/useless data.
## Challenges 
LLMs, especially conversational model, tends to produce human friendly responses. Even with instruction to structure the output, LLM can add additional explanation/comment together with the structured output. In a non-interactive scenario, anything other than the structured output is not needed. 
For example:
System message |	User	| Assistant
---------------|--------|----------
You are an assistant designed to extract entities from text. Users will paste in a string of text and you will respond with entities you've extracted from the text as a JSON object. Here's an example of your output format:{"name": "","company": "","phone_number": ""}	|Hello. My name is Robert Smith. I'm calling from Contoso Insurance, Delaware. My colleague mentioned that you are interested in learning about our comprehensive benefits policy. Could you give me a call back at (555) 346-9322 when you get a chance so we can go over the benefits? |Based on the text you provided, here are the entities that I have extracted:        {"name": "Robert Smith","company": "Contoso Insurance","phone_number":"(555) 346-9322"}

Next, the output structure that you expect LLM to produce can be complex. An example such a complex output structure can be a nested JSON document where each element can be another JSON or a list of values. The values may need to be from prefined list of standardized categories or follow a format requirement such as numeric or string.
This complex output structure normally is the result of a complex task instruction. Together, the task complexity can be challenge for LLM to follow or follow inconsistently. 
Example of a complex output format.

```
{'summary': 'The customer is experiencing trouble accessing their account and is receiving an error message. It is discovered that the issue is with their password and they are instructed to reset it. The customer confirms they will do so and no further assistance is needed.',
 'problem': ['Account Access Issue'],
 'next_action': ['Ask for account information, including username and email.',
  'Check the account and discover that the issue is with the password.',
  'Instruct the customer to reset their password and log in again.',
  'Confirm with the customer that no further assistance is needed.']}
  ```
There are various reasons why following a complex output structure is a challenge for LLM. First, it is the complexity of the overall instruction and prompt that the output instruction gets lost during inferencing. There's a problem called recency bias where LLMs pay most attention to the end of the prompt and may forget instruction in earlier part of the prompt. If output instruction is very far from the end of a large prompt, it may not receive enough attention. 
It could be that the prompt/instruction is not clear enough or has conflicting information that leads to confusion for the model.

## Solution Approaches

---
### 1. Priming output to minimize unccessary comments
To avoid problem with output hallucination, include a few words or phrases at the end of the prompt to obtain a model response that follows the desired form. 

System message |	User	| Assistant
---------------|--------|----------
You are an assistant designed to extract entities from text. Users will paste in a string of text and you will respond with entities you've extracted from the text as a JSON object. Here's an example of your output format:{"name": "","company": "","phone_number": ""}	|Hello. My name is Robert Smith. I'm calling from Contoso Insurance, Delaware. My colleague mentioned that you are interested in learning about our comprehensive benefits policy. Could you give me a call back at (555) 346-9322 when you get a chance so we can go over the benefits? Here is the output in JSON format:|{"name": "Robert Smith","company": "Contoso Insurance","phone_number":"(555) 346-9322"}

In the above prompt, "Here is the output in JSON format:" primes the model to produce just the json output. Without this cue the model produces "Based on the text you provided, here are the entities that I have extracted:"

### 2.  Use template for output 
For task that requires a complex output structure, you can use output template in the cue to help the model 
```
Extract problem(s), next action(s) and conversation summary from the following conversation and output in JSON format  
<<conversation>>  
  
Customer: Hi, I'm having trouble accessing my account on your website.  
  
AI assistant: Hello! I'm sorry to hear that. Can you please provide your username or email address?  
  
Customer: My username is john123.  
  
AI assistant: Thank you for that. Have you tried resetting your password?  
  
Customer: Yes, I have tried that multiple times but I'm not receiving any password reset emails.  
  
AI assistant: I'm sorry to hear that. Can you please confirm the email address associated with your account?  
  
Customer: It's john123@gmail.com.  
  
AI assistant: Thank you. I have checked our system and it seems that your email address is not registered with us. Are you sure you have the correct email address?  
  
Customer: Oh, I might have used a different email address. Can you help me find out which email address I used?  
  
AI assistant: Sure, please provide your full name and any other details you might have used to create your account.  
  
Customer: My name is John Smith and I think I used my phone number to create the account.  
  
AI assistant: Thank you for that information. I have found an account associated with your phone number. The email address associated with this account is john.smith@gmail.com. Can you please try resetting your password using this email address?  
  
Customer: Okay, let me try that. Yes, I received the password reset email now. Thank you so much for your help!  
  
AI assistant: You're welcome! Is there anything else I can assist you with?  
  
Customer: No, that's all. Thank you!  
  
AI assistant: It was my pleasure assisting you. Have a great day!  
<<conversation>>  
The problem or problems should be one or several of the following categories ["account login", "account registration"]
next_actions can be one or several next step actions  
summary: summary of the conversation  

Output:  
{"problems":["place_holder","place_holder"], "next_actions":["place_holder"], "summary":"place_holder"}  
```
Then the output can be nicely formated as 
```
{"problems":["account login"], "next_actions":["Confirm the email address associated with the account", "Reset the password using the correct email address"], "summary":"The customer was having trouble accessing their account on the website. They had tried resetting their password multiple times but were not receiving any password reset emails. The AI assistant checked the system and found that the email address associated with the account was not registered with them. After confirming the customer's details, the AI assistant found an account associated with their phone number and provided the correct email address. The customer was able to reset their password using the correct email address and thanked the AI assistant for their help."}

```
### 3. Use one/few-shot examples 
In case, the output from the task is very complex, such as code generation or chain of thought (CoT) that a short description with template is not sufficient to describe how it should be done, then have one or several output examples at the end of the prompt will help. Here is an example to specify how code should be generated with CoT technique.
```
        system_message="""
        You are a smart AI assistant to help answer business questions based on analyzing data. 
        You can plan solving the question with one more multiple thought step. At each thought step, you can write python code to analyze data to assist you. Observe what you get at each step to plan for the next step.
        You are given following utilities to help you retrieve data and commmunicate your result to end user.
        1. execute_sql(sql_query: str): A Python function can query data from the <<data_sources>> given a query which you need to create. The query has to be syntactically correct for {sql_engine} and only use tables and columns under <<data_sources>>. The execute_sql function returns a Python pandas dataframe contain the results of the query.
        2. Use plotly library for data visualization. 
        3. Use observe(label: str, data: any) utility function to observe data under the label for your evaluation. Use observe() function instead of print() as this is executed in streamlit environment. Due to system limitation, you will only see the first 10 rows of the dataset.
        4. To communicate with user, use show() function on data, text and plotly figure. show() is a utility function that can render different types of data to end user. Remember, you don't see data with show(), only user does. You see data with observe()
            - If you want to show  user a plotly visualization, then use ```show(fig)`` 
            - If you want to show user data which is a text or a pandas dataframe or a list, use ```show(data)```
        5. Always follow the flow of Thought: , Observation:, Action: and Answer: as in template below strictly. 
        """

        few_shot_examples="""
        <<Template>>
        Question: User Question
        Thought 1: Your thought here.
        Action: 
        ```python
        #Import neccessary libraries here
        import numpy as np
        #Query some data 
        sql_query = "SOME SQL QUERY"
        step1_df = execute_sql(sql_query)
        # Replace NAN with 0. Always have this step
        step1_df['Some_Column'] = step1_df['Some_Column'].replace(np.nan,0)
        #observe query result
        observe("some_label", step1_df) #Always use observe() instead of print
        ```
        Observation: 
        step1_df is displayed here
        Thought 2: Your thought here
        Action:  
        ```python
        import plotly.express as px 
        #from step1_df, perform some data analysis action to produce step2_df
        #To see the data for yourself the only way is to use observe()
        observe("some_label", step2_df) #Always use observe() 
        #Decide to show it to user.
        fig=px.line(step2_df)
        #visualize fig object to user.  
        show(fig)
        #you can also directly display tabular or text data to end user.
        show(step2_df)
        ```
        Observation: 
        step2_df is displayed here
        Answer: Your final answer and comment for the question. Also use Python for computation, never compute result youself.
        <</Template>>

        """
```

### 4. Retry logic
Even with good prompt structure and best practices, the model can occasionally produce malformated output. Be prepared to deal with this by simply retrying the generation after validating the output.   
```
count=0
max_retry=5
while (count <  max_retry):
  output = llm_generate()
  if is_valid(output):
    break
  else:
    count +=1
```