
# Large Document Summarization

## Use Cases

Document Summarization can be used to summarize Call Center Log Analytics, Customer and Agent Communications, Legal Documents and Contracts, blogs, and social media feeds. Summarizing these documents drive business value in various industries including legal, retail, customer services, call center, entertainment, financial, banking and travel industries. 


## Challenges

Large Documents (Input text) summarization has below Challenges

**1 -** Input text size and token limitations in GPT models 

**2-** Summarization of Summarization leads to information loss due to multiple chunks summarizations


## Solution Patterns

---
### Pattern 1: (Summary of Summaries with Langchain Refine Pattern)
---
#### Approach

The common approach is to split large documents into chunks do summarization on each chunk, and later group the summarized output of each chunk into a final chunk and, finally, summarize the final chunk.



 
#### Implementation

We will use "Refine" pattern in langchain library to summarize large documents, refine pattern has the capability to keep relevant context while summarizing multiple chunks. Refine pattern approach works as below.

##### Step 1: Divide the large documents into chunks, the chunk sizes can be derived based on LLM token size limit
##### Step 2: Let's assume we have three chunks created, we will pass the Chunk 1 to model for summarization and will get a summarized text of chunk 1
##### Step 3: We will pass the Chunk2 to model along with summarized text of Chunk1 and will get the summarized text
##### Step 4: We will repeat the process and will pass the Chunk3 to model along with summarized text of step 3

If we look at the steps above, we can see that each step gets the context of the previous step summary, thus keeping the context. Please refer to below GitHub examples

**Langchain Example**

Please refer to below GitHub repositories for langchain summarization examples 

**1 -** https://github.com/microsoft/OpenAIWorkshop/blob/main/scenarios/powerapp_and_python/python/Langchain_Summarization.ipynb 

**2 -** https://github.com/hwchase17/langchain/tree/master/langchain/chains/summarize


#### Performance

This approach cannot process the chunks in parallel and is sequential as every step feeds to next step, which results in slow processing as compared to parallel chunk processing approach like MapReduce

#### Strengths

Maintenance of context in the summarization without loss of information 

#### Limitations

This approach is dependent on ordering of document chunks as information flows from first chunk and flows downward.

---



### Pattern 2: (Abstractive and Extractive Summarization)
---
#### Approach

It is highly recommended that depending on the use case scenario, we request the GPT model to consider either Extractive or Abstractive or mix of both summarization approach. Extractive summarization creates almost identical sentences from the original text to generate the summary, while Abstractive summarization generates new content in summary while considering all of context in the input text. This approach can be used after summarizing the chunk following the Pattern # 1




#### Implementation

Selection of correct Summarization approach to drive Abstractive or Extractive Summarization 


**Example- 1: Abstractive Summarization **

Provide a summary of the text below that captures its main idea.

At Microsoft, we have been on a quest to advance AI beyond existing techniques, by taking a more holistic, human-centric approach to learning and understanding. As Chief Technology Officer of Azure AI Cognitive Services, I have been working with a team of amazing scientists and engineers to turn this quest into a reality. In my role, I enjoy a unique perspective in viewing the relationship among three attributes of human cognition: monolingual text (X), audio or visual sensory signals, (Y) and multilingual (Z). At the intersection of all three, there’s magic—what we call XYZ-code as illustrated in Figure 1—a joint representation to create more powerful AI that can speak, hear, see, and understand humans better. We believe XYZ-code will enable us to fulfill our long-term vision: cross-domain transfer learning, spanning modalities and languages. The goal is to have pre-trained models that can jointly learn representations to support a broad range of downstream AI tasks, much in the way humans do today. Over the past five years, we have achieved human performance on benchmarks in conversational speech recognition, machine translation, conversational question answering, machine reading comprehension, and image captioning. These five breakthroughs provided us with strong signals toward our more ambitious aspiration to produce a leap in AI capabilities, achieving multi-sensory and multilingual learning that is closer in line with how humans learn and understand. I believe the joint XYZ-code is a foundational component of this aspiration, if grounded with external knowledge sources in the downstream AI tasks.


**Example- 2: Extractive Summarization**
Below is an extract from the annual financial report of a company. Extract key financial number (if present), key internal risk factors, and key external risk factors.

# Start of Report
Revenue increased $7.5 billion or 16%. Commercial products and cloud services revenue increased $4.0 billion or 13%. O365 Commercial revenue grew 22% driven by seat growth of 17% and higher revenue per user. Office Consumer products and cloud services revenue increased $474 million or 10% driven by Consumer subscription revenue, on a strong prior year comparable that benefited from transactional strength in Japan. Gross margin increased $6.5 billion or 18% driven by the change in estimated useful lives of our server and network equipment. 
Our competitors range in size from diversified global companies with significant research and development resources to small, specialized firms whose narrower product lines may let them be more effective in deploying technical, marketing, and financial resources. Barriers to entry in many of our businesses are low and many of the areas in which we compete evolve rapidly with changing and disruptive technologies, shifting user needs, and frequent introductions of new products and services. Our ability to remain competitive depends on our success in making innovative products, devices, and services that appeal to businesses and consumers.
# End of Report


#### Performance

The correct prompt methodology (Extractive or Abstractive) with clear instructions will lead to a summarized text avoiding high token usage with lower hits to Open AI end point, enhancing customer experience.

#### Strengths

Gen AI models can generate abstractive and extractive summarization with the same model thus avoiding non-relevant, inconsistent, or contradictory outcomes.

#### Limitations

Non optimized model parameters selection will lead to in-consistent output.

---

