# Minimizing Hallucination in Truth Grounded Scenarios

Hallunications are prompt responses that are not based on facts or differ contextually and the outputs can be minor deviations to completely false or divergent outputs, understanding the Types, Key factors and mitigation strategies for the Hallucinations help us to generate the accurate output aligned to organization standards and principles




# Types of Hallucinations
 **Response Variability**
 
 LLM's have a tendency to generate responses that differ from earlier responses
 

**Reponse conflict/Discrepancy from input Prompt**

 LLM generates a response which differs and conflicts from input prompt
 
 for example : Write a positive review of product A and model returns "Product A is low quality and after sales service is bad.”

**Factual errors** 

 Models Hallucinate by providing a nonfactual answer which have never happened or never existed 

**Nonsensical/Irrelevant/information Hallucinations**

 Models Hallucinate by providing non-relevant information to the question. 
 for example 
 
 What is the capital of UK
 Capital of UK is London and London is a great musician as well


# Key Hallucination Factors

**Data Quality** 

 LLM models are trained on huge amounts of data. Such large amount of data may contain  noise and errors and can lead to, bias, secondly training data may not contain all the information context ,leading to generalization problem in LLMs. 

**Input Context**
 
  Unclear Input prompts can confuse or mislead the model to generate non relevant, inconsistent or contradictory outcome.


# Hallucination Mitigation best practices
**Input Prompts**

 A focused, precise, and clear input prompt will likely generate accurate information 
 
 for example instead of asking "what happened in WW1", you can ask , "Summarize the 
 significant events in WW1 including causes of conflict"

**LLM parameters**

  These are achieved by updating parameter settings of model, these settings effect the model response generation capabilities, for example Temperature parameter which   
  controls the randomness of output. A high Temperature value can create more Hallucination, we might want to use lower temperature for something like fact-based QA to 
  encourage more factual and concise responses. For poem generation or other creative tasks, it might be beneficial to increase temperature.

**OneShot, Few-Shot Prompting**

 In Single Shot and Few-Shot prompting, in addition to task description of desired output we provide Model several examples, this prompting reduced Hallucination in 
 tasks which need very focused output format or desired output styles

**Role based Prompting**

 A specific role assignment helps to reduce hallucination. For example, you can say in your prompt: "you are one of the best historians in the world" or "you are a 
 brilliant professor," followed by your question. 

**Greedy Problem**

 AS LLM reasoning capabilities are growing the greedy problem and Hallucinations are decreasing, however we need to ask the model to solve the problem by asking to 
 provide the steps

 for example: 

 When I was 6, my sister was half my age. Now I'm 70 how old is my sister?
 Prompt with asking the model to reason and think by asking it to divide the problem in steps.

When I was 6, my sister was half my age. Now I'm 70 how old is my sister? Provide step by step analysis.
