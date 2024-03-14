# Generative AI Design Patterns for Agentic AI Systems

## Introduction

This repository contains a set of design patterns illustrating how to effectively build Agentic solutions powered by LLMs (Large Language Models) in Azure.

Agentic AI systems are designed to autonomously pursue complex goals and workflows with limited direct human supervision. These systems act as independent agents, making decisions and performing tasks autonomously.

The main capabilities of Agentic AI systems are:
- Autonomy: takes goal-directed actions with minimal human oversight
- Reasoning: engages in contextual decision-making, makes judgment calls and weighing tradeoffs
- Adaptable Planning: dynamically adjusts goals and plans based on changing conditions
- Language Understanding: comprehends and follows natural language instructions
- Workflow Optimization: fluidly moves between subtasks and applications to complete processes efficiently

These capabilities come with great challenges:
- balancing autonomy with predictability and safety
- ensuring the system is transparent, explainable, auditable
- ensuring security and privacy
- ensuring the system is fair and unbiased
- human interaction and collaboration

We intend to provide guidance around the building blocks and approaches to deliver such systems with a clear path to production .

The patterns outlined here are not exhaustive and will evolve over time. We welcome contributions from the community to help us improve the patterns and add new ones.

## Table of Content

This repository is structured in 4 main sections:

### [1. Foundational Design and Implementation Concepts](1_foundational_concepts/README.md)

This section covers all the core design principles supporting Agentic AI Systems. Beyond the concepts, we also review they key frameworks to consider for the implementation of these concepts.

The goal of this section is to help you:
- identify which design elements are crucial to your solution
- select the frameworks appropriate to your architecture design
- understand what you need to deliver to be production ready, including Responsible AI and UX considerations

### [2. Agentic Design Patterns](2_design_patterns/README.md)

This section puts in perspective the foundational elements and provides a set of design patterns that are commonly used in the industry.

Each pattern is designed to address a specific scenario and is backed by a set of best practices and implementation guidelines.

Here you will find the most advanced common scenarios with reference architectures detailing what challenges they address, how to implement them, and the performance and limitations of each pattern.

### [3. Agentic Accelerators](3_accelerators/README.md)

This area provides sample implementations of some of the patterns as ready to go solution, with miminum configurations and code changes. 

This is a good starting point to understand how to implement the patterns in real world scenarios and test them out with your own data.

Please understand that these accelerators do not necessary cover all aspects under consideration to deliver production ready solutions. Please refer to the [Foundational Design and Implementation Concepts](1_foundational_concepts/README.md) section for a detailed understanding of the concepts at play in a more compregensive fashion.

### [4. Auxiliary Design Patterns](4_auxiliary_design_patterns/README.md)

You'll find here a few patterns that are not directly related to Agentic AI Systems, but are important to understand in the context of a Agentic AI Systems design as they may support building specific agent skills or data enrichment pipelines.

The list here could be very exhaustive, but we're selecting the few which present some challenges which are worth covering in details.


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
