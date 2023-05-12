# Minimizing Hallucination in Truth Grounded Scenarios

## Use Cases



The Large language models are used to drive customer insights on use cases not limited to Summarization of text, Automated response generation, Content Generation, Code generation, Code Documentation, Semnatic Search with Embeddings, Information Discovery and Knowledge mining. These uses cases drive business value across various vertical use cases and industries including legal, retail, customer services, call center, entertainment, financial, banking and travel industries. 
Prompt input governance, control and best practices play a significant role in the model output quality and in controlling the model Hallucination

## Challenges

Hallunications is one of major challenge due to its impact on prompt responses quality and relevance as, models produce ourputs that are not based on facts or differ contextualy from the required outcome, these responses can be minor deviations to desired outcome or can produce completely false or divergent outputs.

Huge amounts of data which is prone to noise and errors and can lead to bias,ansence of all the information context in the data, unclear Input prompts can  mislead the model to generate non relevant, inconsistent or contradictory outcome hallucinations.


## Solution Patterns

---
### Pattern 1: (Prompt Governance)
---
#### Approach


An enterprise level prompts governance startegy and process development

#### Implementation

A Prompt Governance structure and document with Do's, Dont's, examples should be created, the document will focus on creating, precise and clear input prompt intructions and guradrails along with prompts secure development methodologies.

for example open ended prompts are one of major causes of Hallucination so, for a company who provides historical events, instaed of askign open ended promprs like "what happened in WW1", you may like to ask , "Summarize the significant events in WW1 including causes of conflit"


#### Performance

The focused input prompts with clear instrcutions will lead to a summarized and focused answer which uers will like to see leading to token usage, cost, customer experience and costs optimization

#### Strengths

The mitigation of Hallucination by model by avoiding  non relevant, inconsistent or contradictory outcome.

#### Limitations

[ when does this approach fails or is not recommended ]

---
### Pattern 2: (Model Parameters Optimization)
#### Approach

Optimize Generative AI models parameters like Temperature, Top_p optimziation to guide the output to align to your business servieces goals and also to reduce Hallucination. 
#### Implementation

Update parameter settings of model which optimize model response generation capabilities, for example optimizing Tempretature parameter which controls the randomness of output. 
A high Temperature value wil lead model to more Hallucination, use lower temperature for something like fact-based QA to encourage more factual and concise responses. 

#### Performance

The optimized Temperature parameter will lead to actual and concise response leading to token usage, cost, customer experience and costs optimization

#### Strengths

The mitigation of Hallucination by model by avoiding  non relevant, inconsistent or contradictory outcome.

#### Limitations

This is an anti-pattern for use cases where you will like moodel to be creative and focus less on concise resposnes.

### Pattern 3: (Model Reasoning and Greedy Problem)
#### Approach

Encourage Generative AI models reasoning capabilities to reduce the greedy problem and Hallucination. 

#### Implementation

AS LLM reasoining capabilities are growing, Hallucinations are decreasing. We need to encourge the model to re-think and re-correct by asking model to break the resolution in steps

for example to solve the below question, scenario(1) prompt leads to greedy problem and hallucination, howover Scenario(2) prompt design asks the model to think, re-correct and analyze

Prompt with Greedy Problem

1)When i was 6, my sister was half my age. Noew I'm 70 how old is my sister?

Prompt without Greedy problem forcing the model to reason and think

2)When i was 6, my sister was half my age. Noew I'm 70 how old is my sister? Provide step by step analysis

 

#### Performance

The Greedy problem resolution will lead to actual and concise response

#### Strengths

The mitigation of Hallucination by model by avoiding  non relevant, inconsistent or contradictory outcome.

#### Limitations


