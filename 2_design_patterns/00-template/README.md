# Design Pattern Title (TEMPLATE)

## Typical Use Cases

Describe one or more use cases and typical industry scenarios which match this pattern. The goal is to understand the context in which the pattern is used. Please outline the key functional aspects that these use cases require to help understand how the pattern relates to the business use cases requirements.

## Challenges

This is a crucial part of the design pattern definition.

Think about these key aspects to write up this section:
- What is typically very challenging about the use case cases? Detail the mechanics of the process and the challenges that are faced.
- Which parts of the final solution are critical to the success of the use case? Privacy, security, accuracy, transparency, variable document types, safety, testing, etc. Detail them and explain why these are important.
- What are the challenges to adoption? Are there any specific technical, business, or regulatory challenges that need to be addressed as well?

## Solution Pattern

NOTE (delete this note in your pattern document)

This section describes the pattern in details. Please use architecture diagrams, pseudo code, and other visual aids to help explain the pattern. The code provided should only be snippets if they help to articulate a concept. The goal is to provide a clear understanding of the pattern and how it can be implemented.

Solution accelerators in a different section will make references to these patterns and provide full code turn key solutions. This document focuses on the how and why.

#### <u>Approach & Architecture Overview</u>

Describe the overall approach:
- components involved
- key data structures
- key data sources and data flow
- key algorithms and techniques

Summarize with an overall architecture diagram (or more than one if needed).

Articulate potential options in the design depending on potential variability of the requirements, or if multiple approaches are viable.

Please provide pros/cons of each alternate approach if you're proposing multiple options.

Include architecture diagrams in 'assets' directory as needed to illustrate the approach.

#### <u>Implementation Details</u>

This should not be a fully working implementation, but should provide enough information to understand the approach. The only purpose of the code is to illustrate concepts.

Consider python notebooks if it helps to illustrate the approach, but blocks of pseudo code are sufficient.

Place the code in 'src' directory and reference it here.

#### <u>Performance & Scalability Considerations</u>

Discuss / evaluate the performance of the approach (accuracy, speed, etc.)
Can this solution scale to large data sets or user bases? How would you acccomplish that? Techniques and considerations. This could point out to part of the architecture and articulate how these pieces can scale.

#### <u>Testability and Evaluation</u>

Does the design approach enable a robust testing strategy?
How would you break the overall solutions into testable components?
What are the key metrics to evaluate the performance of the solution?
What frameworks / approaches would you consider to have in place for a robust test strategy?

#### <u>Observability and Monitoring</u>

How can you monitor the performance of the solution? What are the key metrics to monitor? What are the key logs to monitor? What are the key alerts to set up?

Think about what it takes to run this solution in production. Validate that the architecture takes these parameters into account.

#### <u>Responsible AI & UX Considerations</u>

what aspects of the Responsible AI Principles are relevant to this pattern? How are they addressed?

What UX considerations should be in place to ensure the solution is usable, trusted and useful? Does the underlying architecture enable to deliver on these UX considerations?

#### <u>Known Limitations</u>

When does this approach fails or is not recommended?
Are they some clear limitations to this approach? What are the trade-offs?