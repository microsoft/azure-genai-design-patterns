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
---
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

---
### Pattern 3: (Reduce Greedy Problem)
---
#### Approach

Encourage Generative AI models reasoning capabilities to reduce the greedy problem and Hallucination. 

#### Implementation

AS LLM reasoining capabilities are growing, Hallucinations are decreasing. We need to encourge the model to re-think and re-correct by asking model to break the resolution in steps

for example to solve the below question, Example(1) prompt leads to greedy problem and hallucination, howover Example(2) prompt design asks the model to think, re-correct and analyze resulting in relevant, consistent or factual outcome

**Example - 1 : Prompt leading to Greedy Problem**

When i was 6, my sister was half my age. Noew I'm 70 how old is my sister?

**Example 2 : Prompt without Greedy problem forcing the model to reason and think**

When i was 6, my sister was half my age. Noew I'm 70 how old is my sister? Provide step by step analysis

---
 ### Pattern 4: (Role Based Prompt Variations)
 ---
#### Approach

Encourage Generative AI models to assign a role while answering the prompts, this approach leads to personalzied output, please see below two examples where outputs are focused on assumed role

#### Implementation

A specific role assignemnt helps to reduce hallucination. 

**Role : French Revolution time period Travel Guide**

I want you to act as a travel guide from France at the time of the French Revolution. I will write you my location and you will suggest a place to visit near my location. In some cases, I will also give you the type of places I will visit. I also want you to provide the names of important people from places you suggest. You will also suggest places of similar type that are close to my first location. My first suggestion request is: “I am in Paris and I want to visit the homes of important French politicians.


Q:   I am in Paris and want to visit the president house

Ans: I apologize, as I mentioned earlier, there was no President of France during the French Revolution as we know it today. If you are interested in visiting important government buildings, you can visit the Hôtel de Ville in Paris, which served as the seat of the Paris Commune during the Revolution. It is a beautiful building with impressive architecture and a rich history. Another important government building that you can visit is the Palais Bourbon, which was built for the Duchess of Bourbon


**Role : French Travel Guide**

I want you to act as a travel guide from France. I will write you my location and you will suggest a place to visit near my location. In some cases, I will also give you the type of places I will visit. I also want you to provide the names of important people from places you suggest. You will also suggest places of similar type that are close to my first location. My first suggestion request is: “I am in Paris and I want to visit the homes of important French politicians.


Q:    I am in Paris and want to visit the president house

Ans:  If you want to visit the house of the President of France, you can visit the Élysée Palace, which is the official residence of the President of the French Republic. Located in the 8th arrondissement of Paris, this palace is a historical monument that dates back to the 18th century. You can take a guided tour of the palace and its gardens

#### Performance

The Hallucination resolution will lead to relevant, consistent or factual outcome aligned to the role who is asking the question

#### Strengths

The mitigation of Hallucination by model by avoiding  non relevant, inconsistent or contradictory outcome.

#### Limitations


