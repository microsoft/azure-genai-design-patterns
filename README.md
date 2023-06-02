# azure-openai-design-patterns

## Introduction

This repository contains a set of design patterns using the Azure OpenAI service. The intent is to provide guidance around the building blocks and approaches to deliver common scenarios. This should be considered as a foundation to build prototypes and eventually production ready solutions.

This represents learnings from 100s of use cases observed so far. The patterns are not exhaustive and will evolve over time. We welcome contributions from the community to help us improve the patterns and add new ones.

## General Patterns

### [Pattern 01 - Large Document Summarization](patterns/01-large-document-summarization/README.md)

This pattern is used to summarize large documents. The pattern uses the summarization capabilities of the OpenAI LLMs but requires more than one API call due to the size of the input. The pattern is useful for summarizing documents which are much larger than the maximum token limit of the OpenAI models involved in the summarization process.

### [Pattern 02 - Large Unstructured Document to Domain Specific Structured Dataset](patterns/02-large-unstructured-document-to-domain-specific-structured-dataset/README.md)

This pattern addresses the needs to convert large unstructured documents into structured datasets.

Typical examples would involve contracts, agreements, policies, etc. The pattern is useful to match the content of these documents against an expected fixed data structure for downstream processes and workflows to execute.

This is typically handled by human review and data entry with a lot of domain specific knowledge. The challenge comes from extracting structured information from large documents which are much larger than the maximum token limit of the OpenAI models involved in the extraction process and with a lot of domain specific knowledge about the format.

### [Pattern 03 - Retrieval Augmented Generation (RAG)](patterns/03-retrieval-augmented-generation/README.md)

This pattern addresses the needs to leverage/convert data retrieved from existing systems to generate a new output (structured or unstructured) to be passed to downstream processes or other parties.
This pattern is discussed and implemented in detail under the [specialized section](specialized_scenarios/rag/README.md)

### [Pattern 04 - Ouput Structure Enforcement](patterns/04-output-structure-enforcement/README.md)

This pattern focuses on ensuring that the generated output follows a required output structure in which a slight deviation could have a significant impact on the downstream processes and break them.

### [Pattern 05 - Complex Instruction Logic](patterns/05-complex-instruction-logic/README.md)

This pattern covers scenarios where you need the GPT model to follow a complex instruction logic to generate the desired outcome.

### [Pattern 06 - Classification with Large Number of Labels](patterns/06-classification-with-large-number-of-labels/README.md)

This patterns covers the ability to use GPT models to drive classification scenarios with  high cardinality of labels which would not fit within the limits of a prompt or would drive high cost of API calls due to their size. It covers techniques involving classification hierarchies with sub-classifications and chaining of prompts to drive the final classification efficiently from a speed, cost and maintenance standpoint.
### [Pattern 07 - Enabling GPT model to perform actions ](patterns/07-gpt-performing-actions/README.md)

<<<<<<< HEAD
### [Pattern 07 - Minimizing Hallucination](patterns/07-minimizing-hallucination-in-truth-grounded-scenarios/README.md)

This patterns covers techniques to reduce hallucination with scenarios where grounded data is provided, including proper handling of missing information and how to handle the situation as part of larger OpenAI workflows.
=======
This pattern explains how to how to design a wrapper/plug-in service to enable a GPT model to perform actions on their behalf by interacting with other systems and APIs
>>>>>>> main

### [Pattern 08 - Batch and Real Time Processing](patterns/08-batch-and-real-time-processing/README.md)

This pattern section outlines options to process data as part of an OpenAI data flow for both batch processing and real time. Top considerations are around parallelization, data throuput optimizations, API throttling, error handling, latency and high availability.
### [Pattern 09 - Code Generation](patterns/09-code-generation/README.md)

This pattern discusses best practices to generate or translate code from instruction and context.
### [Pattern 10 - UX Considerations to Deal with OpenAI Latency](patterns/10-ux-considerations-to-deal-with-openai-latency/README.md)

This pattern showcases how to design UX around the inherent OpenAI latency (seconds) to provide a good user experience. This section covers optimizations to consider to speed things up (actual vs perceived) and some concepts to increase trust and transparency while improving the User Experience.

### [Pattern 11 - Top use cases for fine tuning](patterns/11-fine-tuning/README.md)

Discuss use cases where fine-tuning have been most successeful and when to use fine-tuning, when not to.

### [Pattern 12 - Optimizing Costs and Performance](patterns/12-optimizing-costs-and-performance/README.md)

This section focuses on approaches to optimize costs in your OpenAI based applications. The techniques involve picking the right OpenAI models, breaking down tasks into specialized tasks to build the most optimized OpenAI pipelines from a performance and cost standpoint.

### [Pattern 20 - Scenarios Where OpenAI is Not the Best Fit](patterns/20-scenarios-where-openai-is-not-the-best-fit/README.md)

This section covers scenarios where OpenAI is not the best fit and other approaches should be considered. It covers scale considerations where more traditional AI/ML will be more cost effective as well as hybrid scenarios where OpenAI delegation to other AI/ML models is more appropriate.
## Specialized Scenarios

### [1. Implementation guide to build robust automated analytical application](specialized_scenarios/automating_analytics/README.md)

### [2. Implementation guide to build robust Retrieval Augment Generation application](specialized_scenarios/rag/README.md)

## Contributing

This project welcomes contributions and suggestions.  Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit https://cla.opensource.microsoft.com.

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft 
trademarks or logos is subject to and must follow 
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
