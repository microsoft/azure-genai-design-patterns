## Design Patterns

### [Pattern 01 - Retrieval Augmented Generation (RAG)](01-retrieval-augmented-generation/README.md)

This pattern addresses the needs to leverage/convert data retrieved from existing systems to generate a new output (structured or unstructured) to be passed to downstream processes or other parties.

:rocket: [Retrieval Augmented Generation (RAG) Accelerator](../3_design_patterns_accelerators/01-retrieval-augmented-generation/README.md)

### [Pattern 02 - Code Generation Agent](02-code-generation-agent/README.md)

This pattern discusses best practices to generate or translate code from instruction and context.

:rocket: [Code Generation Agent Accelerator](../3_design_patterns_accelerators/02-code-generation-agent/README.md)

### [Pattern 03 - Smart Agent](03-smart-agent/README.md)

This pattern discusses best practices to build smart agents that can understand and execute complex instructions leveraging a multitude of services. This includes multi-agents collaborations and all the scalability and testability challenges that come with it.

:rocket: [Smart Agent Accelerator](../3_design_patterns_accelerators/03-smart-agent/README.md)

### [Pattern 10 - Large Document Summarization](10-large-document-summarization/README.md)

This pattern is used to summarize large documents. The pattern uses the summarization capabilities of the OpenAI LLMs but requires more than one API call due to the size of the input. The pattern is useful for summarizing documents which are much larger than the maximum token limit of the OpenAI models involved in the summarization process.

### [Pattern 11 - Classification with Large Number of Labels](11-classification-with-large-number-of-labels/README.md)

This patterns covers the ability to use GPT models to drive classification scenarios with  high cardinality of labels which would not fit within the limits of a prompt or would drive high cost of API calls due to their size. It covers techniques involving classification hierarchies with sub-classifications and chaining of prompts to drive the final classification efficiently from a speed, cost and maintenance standpoint.

### [Pattern 12 - Minimizing Hallucination](12-minimizing-hallucination/README.md)

This patterns covers techniques to reduce hallucination with scenarios where grounded data is provided, including proper handling of missing information and how to handle the situation as part of larger OpenAI workflows.

### [Pattern 13 - Output Structure Enforcement](13-output-structure-enforcement/README.md)

This pattern focuses on ensuring that the generated output follows a required output structure in which a slight deviation could have a significant impact on the downstream processes and break them.