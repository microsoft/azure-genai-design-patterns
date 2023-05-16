# Minimizing Hallucination in Truth Grounded Scenarios

## Use Cases



The Large language models are used to drive customer insights on use cases not limited to Summarization of text, Automated response generation, Content Generation, Code generation, Code Documentation, Semnatic Search with Embeddings, Information Discovery and Knowledge mining. These uses cases drive business value across various  use cases and industries including legal, retail, customer services, call center, entertainment, financial, banking and travel industries. 
Prompt input governance, control and best practices play a significant role in the model Hallucination

## Challenges

Hallunications is one of major challenge due to its impact on prompt responses quality and relevance, model completions that are not based on facts or differ contextually from the required outcome, these responses can be minor deviations to desired outcome or can produce completely false or divergent outputs.

Huge amounts of data the LLM are trained on can contain noise, errors, bias and lack of information context. Unclear input prompts can mislead the model to hallucinate leading to non-relevant, inconsistent or contradictory outcomes.


## Solution Patterns

---
### Pattern 1: (Prompt Governance)
---
#### Approach


An enterprise level prompts governance startegy and process development which is aligned to organziation streatgic context

#### Implementation

A Prompt Governance structure and document should be drafted including Do's, Dont's, and examples of business services use cases.The document must focus on creating precise and clear input prompt intructions, context and prompts secure prompt methodologies.Open ended prompts are one of major causes of Hallucination.

**Example- 1: Open Ended Prompts**

What events happened in World War 1

**Example- 2: Concise, Focused Prompts**

Summarize the significant events in World War 1 including causes of conflit and the countries most impacted by the war


#### Performance

The focused input prompts with clear instrcutions will lead to a summarized and focused answers avoiding high token usage with lower hits to Open AI end point, enhancing customer experience.

#### Strengths

This approach mitigates Hallucination by avoiding  non relevant, inconsistent or contradictory outcomes.

#### Limitations

Avoid using concise promots for use caes like conent genaration where you want the completions to be generalized and craetive.  

---
### Pattern 2: (Model Parameters Optimization)
---
#### Approach

Optimize Generative AI models parameters like Temperature to reduce Hallucination. 

#### Implementation

Update Temperature parameter settings to optimize model response generation capabilities.Tempretature parameter controls randomness in the parameter completions. Lowering the temperature produces more repetitive and deterministic responses.

Increasing the temperature creates more unexpected or creative responses, while lower temperature creates factual and concise responses. 

**Example- 1: Higher Temperature Value**

Q: Contoso made a sale for $50. The raw materials cost $10 and the labor was $20. What was the profit on the sale?
A: The total cost was $10 + $20. Revenue was $50. Profit is revenue - total cost so the profit was $20

Q: Fabrikam sold 100 widgets for $10 each. However, 30 of the widgets were returned for a full refund and 1 with 30% refund. How much money did Fabrikam make, provide step by step instructions? 
A:
Step 1: Calculate the total sales
Total sales = 100 x $10 = $1,000

Step 2: Calculate the refund
Full refund on 30 widgets = 30 x $10 = $300
Partial refund on 1 widget = 1 x $10 x 0.7 = $7
Total refund = $300 + $7 = $307

Step 3: Calculate the net profits
Net profit = Total sales - Total refund
Net profit = $1,000 - $307 = $693

**Example- 2: Lower Temperarture Value**
Q: Contoso made a sale for $50. The raw materials cost $10 and the labor was $20. What was the profit on the sale?
A: The total cost was $10 + $20. Revenue was $50. Profit is revenue - total cost so the profit was $20

Q: Fabrikam sold 100 widgets for $10 each. However, 30 of the widgets were returned for a full refund and 1 with 30% refund. How much money did Fabrikam make, provide step by step instructions? 
A: Step 1: Calculate the total revenue from the sale of the 100 widgets: 100 x $10 = $1,000

Step 2: Calculate the total refund amount: 30 x $10 = $300 + (1 x $10 x 0.7) = $7

Step 3: Calculate the total profit: $1,000 - $307 = $693


#### Performance

The optimized Temperature parameter will lead to actual and concise response leading to enhanced customer experience and costs optimization

#### Strengths

The mitigation of Hallucination by model by avoiding  non relevant, inconsistent or contradictory outcome.

#### Limitations

Lowering Temperature is an anti-pattern for use cases where you will like moodel to be creative and focus less on concise resposnes.

---
### Pattern 3: (Reduce Greedy Problem)
---
#### Approach

Encourage Generative AI models reasoning capabilities to reduce the greedy problem and Hallucination. 

#### Implementation

AS LLM reasoning capabilities are growing, Hallucinations are decreasing. We need to encourge the model to re-think and re-correct by encoursging the model to think and analyse the outcome

for example to solve the below question, Example(1) prompt leads to greedy problem and hallucination, howover Example(2) prompt design asks the model to think, re-correct and analyze resulting in relevant, consistent and factual outcome

**Example - 1 : Prompt leading to Greedy Problem**

When i was 6, my sister was half my age. Noew I'm 70 how old is my sister?

**Example 2 : Prompt without Greedy problem forcing the model to reason and think**

When i was 6, my sister was half my age. Noew I'm 70 how old is my sister? Provide step by step analysis

---
 ### Pattern 4: (Role Based Prompt Variations)
 ---
#### Approach

Encourage Generative AI models to take a role while answering the prompts, this approach leads to personalzied output.

#### Implementation

A specific role assignemnt helps to reduce hallucination. The below example generates very different results based on role assignment  

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

The personalization of conent and mitigation of Hallucination by model by assuming the role leads to avoiding  non relevant, inconsistent or contradictory outcome.

#### Limitations

This is an anti pattern for use cases where we need model to generate genralized and non-personalized outcomes




