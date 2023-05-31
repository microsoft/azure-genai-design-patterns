
# Large Document Summarization

## Use Cases

Document Summarization can be used to summarize Call Center Log Analytics, Customer and Agent Communications, Legal Documents and Contracts, blogs, and social media feeds. Summarizing these documents drive business value in various industries including legal, retail, customer services, call center, entertainment, financial, banking and travel industries. 


## Challenges

Large Documents summarization has below Challenges

**1 -** Input text size and token limitations in GPT models 

**2-** Summarization of Summarization leads to information loss due to multiple chunks summarizations


## Solution Patterns

---
### Pattern 1: (Chunk Based Summaries with Parallel Processing)
---
#### Approach

This approach splits large documents into chunks and summarizes each chunk independently,and later the summarized chunks are summarized to get the final summary. 



 
#### Implementation

This pattern can be implemented using "Map Reduce" pattern in langchain library to summarize large documents, "Map Reduce" pattern has the capability process multiple chunks in Praelle.The Parallel chunk summarization approach works as below

The below flow provides the parallel flow where chunks can be proceesed in parallel with Open AI, this flow is fast and each chunk is processed independetly of other chunks

<img width="677" alt="image" src="https://github.com/microsoft/azure-openai-design-patterns/assets/50298139/d199bba2-5a91-4db3-af1a-4c9d6db73f25">



**Langchain Example**

Please refer to below GitHub repositories for langchain summarization examples 

**1 -** https://github.com/microsoft/OpenAIWorkshop/blob/main/scenarios/powerapp_and_python/python/Langchain_Summarization.ipynb 



#### Performance

This approach cannot process the chunks in parallel , which results in fast parallel processing 
#### Strengths

Fast execution  

#### Limitations

Loss of information may be possible as each chunk is indpendently processed with no relation to other chunks summaries.

---



### Pattern 2: (Chunk Based Summaries with Suquential Processing)
---
#### Approach

The common approach is to split large documents into chunks do summarization on each chunk in sequence, and later, summarize the final chunk to get final summary
 
#### Implementation

This pattern can be implemented using "Refine" pattern in langchain library to summarize large documents, "Refine Pattern" pattern has the capability to keep the context of previous chunk summaries thus preventing the loss of information

The below flow provides the sequential flow where chunks can be proceesed in sequence with Open AI, each chunk is processed with input of previous chunk

<img width="725" alt="image" src="https://github.com/microsoft/azure-openai-design-patterns/assets/50298139/c6b22552-b5f9-44ed-b80e-c561dbd9678f">

**Langchain Example**

Please refer to below GitHub repositories for langchain summarization examples 

**1 -** https://github.com/microsoft/OpenAIWorkshop/blob/main/scenarios/powerapp_and_python/python/Langchain_Summarization.ipynb 



#### Performance

This approach cannot processes the chunks in sequence , which results in slow processing as compared to parallel processing 
#### Strengths

The context is maintained and there is low probability of loss of informatuin during summarization
#### Limitations

Processing will be slow due to sequential nature of the flow

---
