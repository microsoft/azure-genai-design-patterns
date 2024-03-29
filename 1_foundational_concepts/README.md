# Foundational Design and Implementation Concepts for Agentic AI Systems

## Definition

Agentic AI systems are engineered to independently pursue sophisticated objectives and workflows with minimal human guidance. These entities function as autonomous agents, capable of making decisions and executing tasks on their own. A GenAI-based agent leverages the capabilities of Large Language Models (LLMs) to comprehend instructions, engage in reasoning, and produce actionable outputs

## Motivation

The prowess of LLMs in Natural Language Processing (NLP) tasks and creative content generation is well-documented, traditionally catering to human readers for interpretation.

However, a significant and emerging application lies in automating tasks that have been performed by humans, particularly those involving digital interfaces—ranging from transaction processing and customer support to routine office tasks. Recent breakthroughs have dramatically enhanced the utility of LLMs, including their ability to generate executable code, create well-structured data, and understand complex instructions and contexts. 

Innovations such as extended context length support and the processing of images and videos have made it feasible to develop highly effective agents for a broad spectrum of real-life applications.

These advancements catalyze the creation of GenAI agents, which are poised to automate and streamline a vast array of digital and interactive tasks previously reliant on human intervention.

## Key Scenarios

1. Technical support automation
2. Code assistance for large scale projects like code base modernization
3. Automated audits (call centers, financial transactions, etc.)
4. Complex data analysis for non-technical resources
5. e-Commerce agent driven conversions
6. ...

## Top Challenges

1. Tension between automation (flexiblity) and control (adherence to processes)
2. Agent output exchange and broadcasting
3. Scalability (number of agents): finding the right balance between separation of concerns and overhead of subtasking
4. UX challenges to provide transparency, control and enable human in the loop collaboration
5. Ability to auto-detect unproductive plans and take corrective actions
11. Data access governance
6. Handling memory, insights, notes to enable complex tasks within the constraints of data governance and privacy
7. Learning from experience to alleviate high cost of execution 
8. Monitoring, telemetry, performance, debuggability
9. Cost management / budgeting
10. Testability
11. System safety and security
12. Responsible AI

## Key Functional Building Blocks

1. Assistant / Coordinator / Dispather
2. Agent / Actor (aka Plugins, skills, specialized agents)
3. User Proxy Agent for Human Collaboration
4. Planner / Replanner / Reflection (ReAct + critical thinking)
5. Knowledge (runtime data access)
6. Memory: short-term (working/scratchpad/whiteboard), long-term (consolidated information), associative (capture meaning), semantic (definitions, rules, categories, ...), episodic (person, places, events), procedural (how to best perform a task)


## System Level Architecture and Key Functional Flows

## Practical Implementation Considerations

### Frameworks and Tools

| Category | Framework | Key Features |
| --- | --- | --- |
| Memory | MemGPT ||
|| LangChain Memory ||
|| Semantic Kernel Memory ||
| Coordinator | LangChain ||
|| Semantic Kernel ||
|| Autogen ||
|| XAgent ||
|| CAMEL ||

...


## UX Considerations

### Guidelines for Human-AI Interaction

[![](./assets/Human-AI-Interaction.png)](https://www.microsoft.com/en-us/haxtoolkit/library/)

## Responsible AI Considerations

### Responsible AI Principles

[The Microsoft Responsible AI Principles](https://query.prod.cms.rt.microsoft.com/cms/api/am/binary/RE5cmFl?culture=en-us&country=us) are a set of guidelines that Microsoft uses to ensure that AI systems are designed and implemented in a responsible manner. These principles are:

1. Fairness: AI systems should treat all people fairly.
2. Reliability and safety: AI systems should perform reliably and safely.
3. Privacy and security: AI systems should be secure and respect privacy.
4. Inclusiveness: AI systems should empower everyone and engage people.
5. Transparency: AI systems should be understandable.
6. Accountability: People should be accountable for AI systems.

### Responsible AI Frameworks and Tools

TODO

### Integrating Responsible AI Principles into the Design and Implementation of Agentic AI Systems

TODO