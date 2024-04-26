# Foundational Design and Implementation Concepts for Agentic AI Systems

## Definition

Agentic AI systems are engineered to independently pursue sophisticated objectives and workflows with minimal human guidance. These entities function as autonomous agents, capable of making decisions and executing tasks on their own. A GenAI-based agent leverages the capabilities of Large Language Models (LLMs) to comprehend instructions, engage in reasoning, and produce actionable outputs.

## Motivation

The prowess of LLMs in Natural Language Processing (NLP) tasks and creative content generation is well-documented, traditionally catering to human readers for interpretation.

However, a significant and emerging application lies in automating tasks that have been performed by humans, particularly those involving digital interfaces, ranging from transaction processing and customer support to routine office tasks. Recent breakthroughs have dramatically enhanced the utility of LLMs, including their ability to generate executable code, create well-structured data, and understand complex instructions and contexts. 

Innovations such as extended context length support and the processing of images and videos have made it feasible to develop highly effective agents for a broad spectrum of real-life applications.

These advancements catalyze the creation of GenAI agents, which are poised to automate and streamline a vast array of digital and interactive tasks previously reliant on human intervention.

## Example Scenarios benefiting from Agentic AI Systems

### 1. Technical support automation

#### GenAI Impact

GenAI agents can be deployed to automate technical support tasks, such as troubleshooting software issues, diagnosing hardware problems, and providing guidance on system configurations or application usage. These agents can understand user queries, analyze system logs, and generate solutions to resolve technical issues or guide users through complex processes.

#### Key Capabilities

- information retrieval from knowledge bases and documentation
- log analysis and error diagnosis
- auto-generation of step-by-step troubleshooting guides, knowledge base articles, and FAQs
- integration with ticketing systems for issue tracking and resolution

### 2. Code assistance for large scale projects like code base modernization

#### GenAI Impact

GenAI agents can assist developers in large-scale codebase modernization projects by analyzing legacy code, identifying outdated or deprecated components, and suggesting refactoring strategies.

This includes porting entire applications from one programming language to another, updating code to comply with new standards, best practices and target proprietary APIs.

GenAI capability to understand code and translate it to specifications offers a unique benefit in this context to remap functionality from one language context (with its specific libraries and dependencies) to another.

#### Key Capabilities

- code analysis and comprehension
- large scale knowledge base processing (libraries, frameworks, APIs)
- understanding of programming paradigms and design patterns
- understanding of development processes including specification, documentation, automated-testing, deployment, monitoring, etc.
- human in the loop as a key collaborator to validate and refine the generated code
- learning from the feedback to improve the quality of the generated code and speed up the overall process

### 3. Automated audits (call centers, financial transactions, etc.)

#### GenAI Impact

GenAI offers a unique capability to automate what is typically done on small sample of data to the entire dataset, and to do so in a fraction of the time it would take a human to do so.

These processes are usually very clearly documented as they need to attend to compliance and regulatory requirements, and the GenAI agent can be fed these documents to ensure it follows the same process.

GenAI offers some unique capabilities to standardize, alleviate biases, and provide a consistent and reliable audit trail as well as generate customized training to human agents for instance to speed up skilling up or increase the overall quality of a workforce.

#### Key Capabilities

- structured summarization of large datasets (audio transcripts for instance)
- compliance monitoring of regulatory requirements
- anomaly detection
- automated report generation
- training material generation
- ad-hoc coaching

### 4. Complex data analysis for non-technical resources

#### GenAI Impact

GenAI finally enables non-technical resources to leverage the power of data analysis and data science to make informed decisions, a capability which has been a target for years but never fully realized.

GenAI can interact with business users, refine questions, leverage exposed data sets, perform analysis and critical thinking to provide insights and recommendations to the business users.

#### Key Capabilities

- business semantic repository
- datastore query generation
- code generation for data processing and advanced analytics
- data visualization
- critical thinking with human in the loop
- transparency of thought process
- explainability of decisions
- learning from feedback

### 5. e-Commerce agent driven conversions

#### GenAI Impact

GenAI reasoning capabilities can be leveraged to understand the context of a user request, and provide a personalized response that is tailored to the user's preferences and needs.

GenAI is also very good at building strategies to accomplish goals, and can be used to optimize the conversion rate of an e-commerce website by analyzing user behavior, identifying patterns, and recommending actions to increase sales.

#### Key Capabilities

- reasoning against conversion goals
- storing and retrieving user context to maximize personalization and effectiveness of conversations
- merging e-commerce context with agent conversation to provide a seamless experience
- learning from user feedback to improve the quality of the recommendations
- automating analysis of past interactions, learning from them, and improving the overall conversion rate by storing winning strategies


## Top Challenges

### 1. Tension between automation (flexiblity) and control (adherence to processes)

The purpose of agents is to pursue complex goals with limited supervision to automate tasks, but the challenge is to ensure that the agent is not too autonomous/creative to the point where it deviates from the intended process.

This requires a balance between flexibility and control, where the agent can adapt to different scenarios while adhering to the desired process.

This is typically addressed by providing the agent with a set of rules or guidelines to follow (constrain the action space), or even by having another agent monitoring adherence to the process (accountability for the actions taken.

### 2. Agent to agent communication and broadcasting

Defining and controlling the output of the agent is a key challenge. The agent should be able to communicate its output effectively to other agents or users, and this communication should be standardized and consistent.

Different means of communication and information retrieval are possible, depending on the complexity of the solution. This could range from simple text-based communication to more complex data exchange formats involving standardized memorization and memory retrieval.

In complex multi-agents system, a broadcasting mechanism of all the outputs/findings of agents can be considered to facilitate highly creative and collaborative solutions where feedback from one agent can be leveraged as context immediately by all other agents at their discretion.

### 3. Scalability (number of agents): finding the right balance between separation of concerns and overhead of subtasking

One of the key scalability factor from a compute standpoint (and therefore indirectly cost and latency) is the number of agents involved in solving a complex goal.

The are benefits from a testability and robsutness standpoint in breaking down the duties of each agents into smaller roles, but this can also lead to overhead in terms of communication and coordination between agents.

Striking the right balance could be a challenge, but the more powerful LLMs enable the definition of agents which can follow relatively complex rules and guidelines, reducing the need for a large number of agents.

This is something to take into account at design time and over the entire life of the system as the evolution of tasks, LLM capabilities and performance requirements may lead to reconsider the agents involved and their role definitions.

### 4. Ability to auto-detect unproductive plans and take corrective actions (interruptability)

As semi-autonomous systems, it is crucial to be able to evaluate and detect when an agent is not making progress towards its goal, or when it is following an unproductive path to avoid lengthy and costly execution with no valuable outcome.

Two core concepts here are to be considered, usually in a complementary fashion:
- Agents should be able to evaluate their own comprehension of the task at hand and require human clarification when unsure about the meaning of the task or the context in which it is to be executed. This is a core concept which makes 'conversational' agents more valuable then 'question/answer' bots.
- Some agents could be dedicated to a playing a critical thinking function, reviewing other agents plans and evaluating their validity before their executions. This function can be embedded in each agent themselves at the cost of more complex instructions and less robustness than a dedicated agent approach.

Another aspect of the Agentic AI System is the ability for the human to interrupt the flow of execution at any time, and provide feedback or guidance to the agent. This is a key aspect of the human-in-the-loop approach, where the human can provide context, feedback, or guidance to the agent to ensure that the task is executed correctly and rectify the course of action if needed.

### 5. UX challenges to provide transparency, control and enable human in the loop collaboration

As stated above, great value comes from an active collaboration of the user with the agentic AI system. This requires a user experience that is transparent, provides control to the user, and enables the user to provide feedback and guidance to the system.

The transparency also alleviates one of the key UX challenge of this type of system, which is the relative low latency involved at each step. Not showcasing anything than a spinning icon is a very frustrating experience.

Transparency provides two great benefits:
- The user can understand what the system is doing and why, which can help build trust in the system and increase user satisfaction.
- The user can provide feedback and guidance to the system, which can help improve the system's performance and ensure that the task is executed correctly.

Besides providing transparency, and as stated in the previous section, the system gains from providing control points to the user. An interuptability function provided throughout the flow will enable users to guide the decision making-process when needed.

### 6. Data access & data privacy governance

Most agents will rely on one or more sources of information to inform their reasoning and decision-making. This data can come from a variety of sources, including databases, APIs, and other agents.

It is crucial to consider the security context of the end-user (or system) triggering the original ask, to understand what the scope of data access is by this entity and enforce the established data governance in place on these systems.

In most cases, this responsility is accomplished via propagation of the requester security context down to the underlying data services.

Nevertheless, as data is being generated by these agents (for instance, in the process of memorization for consumption by other agents), special attention needs to be put in place around these memory stores to guarantee the original data governance and data privacy are respected.

### 7. Handling memory, insights, notes to enable complex tasks within the constraints of data governance and privacy

Complex agents system perform best when they can constantly learn from their previous interactions and adapt their behavior accordingly. This requires the ability to store and retrieve information from memory, enabling agents to learn from past interactions and make better decisions in the future.

Nevertheless, as stated in the previous section, this memory store needs to be governed by the same data governance and data privacy rules as the original data sources. Consequently, it is imperative to obfuscate and anonymize the data stored in memory to ensure that the privacy of the data is respected while maximizing the re-usablity of the data for the agents by generating a 'global' cache of insights and learnings.


### 8. Learning from experience to alleviate high cost of execution 

As described earlier, a multi-agents system can involve a significant number of agent interactions (between agent and human, between agents, and between agents and external systems). This can lead to a high cost of execution, especially if the agents are not able to learn from their experience and adapt their behavior accordingly.

One way to alleviate this cost is to enable agents to learn from their experience and improve their performance over time. This can be done by providing agents with the ability to store and retrieve information from memory, enabling them to learn from past interactions and make better decisions in the future.

This is best accomplished by summarizing at the end of an interaction the key learnings and insights that were generated by the system, with special emphasis on the overall path to resolution. The idea is to store which steps lead to a succesful outcome and in which order, plus potential details as relevant to the context. This would enable further requests to look up past request and get one or more 'plans' that outline how to handle such request, and provide this as context to the system Planner to further refine the plan.

One key aspect of that is to preserve data governance and data privacy as part of storing and restoring such plans. In practice it means that data obfuscation and data anonymization should be considered as part of the memory storage process.

### 9. The importance of Telemetry for Monitoring, Auditing & Performance

Agentic AI Systems are very powerful but also very complex in nature with their undeterministic behavior. It is crucial to monitor and audit the system to ensure that it is performing as expected and to identify any issues that may arise.

Telemetry is a key aspect of monitoring and auditing the system, as it provides real-time data on the performance of the system, the interactions between agents, and the outcomes of these interactions.

Metrics should be in place not only to monitor the typical aspects of an application, but to monitor the quality of the agent interactions (quality of generation), as well as the safety of these systems. This is typically done by sampling actual interactions and deploying LLM based evaluations which can verify that the agents are behaving according to specifications and not deviating from the intended process.

### 10. Cost management / budgeting

Since agents and multi-agents system drive a large number of interactions with LLMs, the cost of execution can be significant.

It is important to monitor and manage the cost of execution to ensure that the system remains within budget.

Some key considerations are:
- clear monitoring of the cost of execution of each agent task
- setting up budget limits for an agentic system, which could drive interruption of tasks if the cost of execution exceeds the budget
- optimizing the execution of tasks to minimize the cost of execution at each agent skill level, by optiming the LLM involved and use the most cost-effective LLM for the task at hand
- leveraging caching and memory to reduce the cost of execution by reusing information from previous interactions
- consider using the right LLM for the task, depending on the task complexity (easier to adjust a previous task with a different context than to come up with a plan from scratch)

### 11. Testability

The Agentic AI Systems by nature become very autonomous and can take an infinite number of paths to reach a goal. This makes testing the system a challenge, as it is impossible to test every possible path.

It is crucial to break down these systems in skills which need to be individually tested and released. Robustness is driven by clearly scoping each skill to a specific task and ensuring that the skill can be tested and released in isolation.

This is the only way to really scale the system as you add skills and agents to the system, and ensure that the system remains robust and reliable.

A clear audit trace needs to be in place to log every aspect of the system execution, from the initial request to the final outcome, to ensure that the system can be debugged and improved over time.

### 12. System Risk & Safety Evaluation

The complexity of Agentic AI Systems can lead to unexpected behaviors and outcomes, which can pose risks to the system and its users. For use cases which are regulated, it is crucial to evaluate how each step of the system execution can be audited and validated to ensure that the system is behaving as expected against the regulatory requirements.

On top of that, because of the capabilities of LLMs to generate content, it is crucial to evaluate the safety of the generated content, to ensure that it is not harmful or offensive to the users.

This is typically done by deploying LLM based evaluations which can verify that the agents are behaving according to specifications and not deviating from the intended process.

These are extremely important to put in place as part of the overall testing process during the development of these systems, but also as part of the monitoring and auditing process once the system is in production.

## Key Functional Building Blocks

(coming soon)

1. Assistant / Coordinator / Dispather
2. Agent / Actor (aka Plugins, skills, specialized agents)
3. User Proxy Agent for Human Collaboration
4. Planner / Replanner / Reflection (ReAct + critical thinking)
5. Knowledge (runtime data access)
6. Memory: short-term (working/scratchpad/whiteboard), long-term (consolidated information), associative (capture meaning), semantic (definitions, rules, categories, ...), episodic (person, places, events), procedural (how to best perform a task)
7. Thought-Process (execution of a plan)
8. Explainer (provide context, explain decisions)

## System Level Architecture and Key Functional Flows

(coming soon)

## Practical Implementation Considerations

The framework landscape is evolving rapidly, but a few frameworks are surfacing as promising for building Agentic AI systems with a rich set of the Key Functional Building Blocks being made available. You may have to combine a few to build a full end-to-end solution based on their strenghts.

Here are some of the key frameworks to consider when building an Agentic AI systems:

| Category | Framework | Key Concepts |
| --- | --- | --- |
| Coordinator | [Autogen](https://microsoft.github.io/autogen/) | Agents (Conversable, User Proxy, Retrieval), Code Executors, Tool Executors, Memories (Teachability)|
|| [CrewAI](https://github.com/joaomdmoura/crewai) | Agents, Tasks, Tools, Processes, Crews |
|| [LangChain](https://www.langchain.com/langchain) | Agents, Chains, Retrievers, Memories, Callbacks |
|| [Semantic Kernel](https://learn.microsoft.com/en-us/semantic-kernel/overview/) | Kernels, Planners, Plugins, Memories |
|| [XAgent](https://github.com/OpenBMB/XAgent) (experimental) | Dispatchers, Planners, Actors, Tools |
| Data Access | [Kernel Memory](https://github.com/microsoft/kernel-memory) | Ingestion Pipelines (with OCR), Storage, Data Lineage, Citation, Summarization, Security Filters |
|| [LlamaIndex](https://www.llamaindex.ai/) | Loading, Indexing, Querying, Storing, Evaluating |
| Memory | [AutoGen Teachability](https://microsoft.github.io/autogen/docs/notebooks/agentchat_teachability) | Teachability
|| [LangChain Memory](https://python.langchain.com/docs/modules/memory/) | Conversation Buffer, Conversation Summary, Entity, Conversation Knowledge Graph
|| [Kernel Memory](https://github.com/microsoft/kernel-memory) | Ingestion, Storage, Data Lineage, Citation, Summarization, Security Filters, OCR, Short-Term Memory |
|| [MemGPT](https://memgpt.ai/) | Agents (memory managers), Sources, Tools, Presets, Humans, Personas |
|| [Semantic Kernel Memories](https://learn.microsoft.com/en-us/semantic-kernel/memories/) | Key-Value Memory, Local-Storage Memory, Semantic Memory |

## UX Considerations

### Guidelines for Human-AI Interaction

Please see the [Microsoft Human-AI Interaction Toolkit](https://www.microsoft.com/en-us/haxtoolkit/library/) for a comprehensive set of guidelines for designing and implementing Human-AI Interaction.

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

(coming soon)

### Integrating Responsible AI Principles into the Design and Implementation of Agentic AI Systems

(coming soon)