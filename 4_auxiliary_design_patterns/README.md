## Auxiliary Design Patterns

You'll find here a few patterns that are not directly related to Agentic AI Systems, but are important to understand in the context of a Agentic AI Systems design as they may support building specific agent skills or data enrichment pipelines.

### [Pattern 01 - Large Document Summarization](01-large-document-summarization/README.md)

This pattern is used to summarize large documents. The pattern uses the summarization capabilities of the OpenAI LLMs but requires more than one API call due to the size of the input. The pattern is useful for summarizing documents which are much larger than the maximum token limit of the OpenAI models involved in the summarization process.

### [Pattern 02 - Classification with Large Number of Labels](02-classification-with-large-number-of-labels/README.md)

This patterns covers the ability to use GPT models to drive classification scenarios with  high cardinality of labels which would not fit within the limits of a prompt or would drive high cost of API calls due to their size. It covers techniques involving classification hierarchies with sub-classifications and chaining of prompts to drive the final classification efficiently from a speed, cost and maintenance standpoint.

### [Pattern 03 - Minimizing Hallucination](03-minimizing-hallucination/README.md)

This patterns covers techniques to reduce hallucination with scenarios where grounded data is provided, including proper handling of missing information and how to handle the situation as part of larger OpenAI workflows.

### [Pattern 04 - Output Structure Enforcement](04-output-structure-enforcement/README.md)

This pattern focuses on ensuring that the generated output follows a required output structure in which a slight deviation could have a significant impact on the downstream processes and break them.