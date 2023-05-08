# azure-openai-design-patterns

## Introduction

This repository contains a set of design patterns using the Azure OpenAI service. The intent is to provide guidance around the building blocks and approaches to deliver common scenarios. This should be considered as a foundation to build prototypes and eventually production ready solutions.

This represents learnings from 100s of use cases observed so far. The patterns are not exhaustive and will evolve over time. We welcome contributions from the community to help us improve the patterns and add new ones.

## Patterns

### [Pattern 01 - Large Document Summarization](patterns/01-large-document-summarization/README.md)

This pattern is used to summarize large documents. The pattern uses the summarization capabilities of the OpenAI LLMs but requires more than one API call due to the size of the input. The pattern is useful for summarizing documents which are much larger than the maximum token limit of the OpenAI models involved in the summarization process.

### [Pattern 02 - Large Unstructured Document to Domain Specific Structured Dataset](patterns/02-large-unstructured-document-to-domain-specific-structured-dataset/README.md)

This pattern addresses the needs to convert large unstructured documents into structured datasets.

Typical examples would involve contracts, agreements, policies, etc. The pattern is useful to match the content of these documents against an expected fixed data structure for downstream processes and workflows to execute.

This is typically handled by human review and data entry with a lot of domain specific knowledge. The challenge comes from extracting structured information from large documents which are much larger than the maximum token limit of the OpenAI models involved in the extraction process and with a lot of domain specific knowledge about the format.

### [Pattern 03 - Retrieval Augmented Generation (RAG)](patterns/03-retrieval-augmented-generation/README.md)

This pattern addresses the needs to leverage/convert data retrieved from existing systems to generate a new output (structured or unstructured) to be passed to downstream processes or other parties.

The challenge comes from generating the output in a consistent manner while dealing with prompt size limitations, and variable/unpredictable inputs.

### [Pattern 04 - Controlled Ouput Format](patterns/04-controlled-output-format/README.md)

This pattern focuses on ensuring that the generated output follow a very strict format in chained scenarios where a slight deviation in the output could have a significant impact on the downstream processes and break them.

### [Pattern 05 - Complex Output Logic](patterns/05-complex-output-logic/README.md)

This pattern enables the generation of complex outputs which require very detailed instructions/logic. The complexity could be in the format itself or the volume of corner cases and variations that the output could take to fulfill the requirements.

The challenges lies in the ability to generate the output in a consistent manner while dealing with prompt size limitations and costs. It covers instructions vs few shorts learning vs fine tuning and prompt chaining techniques.

### [Pattern 06 - Classification with Large Number of Labels](patterns/06-classification-with-large-number-of-labels/README.md)

This patterns covers the ability to use GPT models to drive classification scenarios with very high cardinality of labels which would not fit within the limits of a prompt or would drive high cost of API calls due to their size. It covers techniques involving classification hierarchies with sub-classifications and chaining of prompts to drive the final classification efficiently from a speed, cost and maintenance standpoint.

### [Pattern 07 - Minimizing Hallucination in Truth Grounded Scenarios](patterns/07-minimizing-hallucination-in-truth-grounded-scenarios/README.md)

This patterns covers techniques to reduce hallucination with scenarios where grounded data is provided, including proper handling of missing information and how to handle the situation as part of larger OpenAI workflows.

### [Pattern 08 - Batch and Real Time Processing](patterns/08-batch-and-real-time-processing/README.md)

This pattern section outlines options to process data as part of an OpenAI data flow for both batch processing and real time. Top considerations are around parallelization, data throuput optimizations, API throttling, error handling, latency and high availability.

### [Pattern 09 - UX Considerations to Deal with OpenAI Latency](patterns/09-ux-considerations-to-deal-with-openai-latency/README.md)

This pattern showcases how to design UX around the inherent OpenAI latency (seconds) to provide a good user experience. This section covers optimizations to consider to speed things up (actual vs perceived) and some concepts to increase trust and transparency while improving the User Experience.

### [Pattern 10 - Optimizing Costs and Performance](patterns/10-optimizing-costs-and-performance/README.md)

This section focuses on approaches to optimize costs in your OpenAI based applications. The techniques involve picking the right OpenAI models, breaking down tasks into specialized tasks to build the most optimized OpenAI pipelines from a performance and cost standpoint.

### [Pattern 11 - Scenarios Where OpenAI is Not the Best Fit](patterns/11-scenarios-where-openai-is-not-the-best-fit/README.md)

This section covers scenarios where OpenAI is not the best fit and other approaches should be considered. It covers scale considerations where more traditional AI/ML will be more cost effective as well as hybrid scenarios where OpenAI delegation to other AI/ML models is more appropriate.