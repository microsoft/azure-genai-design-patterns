# Classification with Large Number of Labels

## Use Cases
With ease of use, Azure Open AI is a good candidate to implement document classification use cases under scenarios such as:
- Automated Document Routing
- Call Routing
- Automated classification in a content management system
In these scenarios, the document/content needed to be assigned with standard label(s) to be able to be routed or indexed at down stream processes. 


## Challenges
Sheer number of labels creates a challenge for any ML classifier including LLMs. Some labels may look similiar even though they are meant for different categories. For example: the label "Application Issue" can appear in both Human Resource domain as well as Sales & Marketing domain. Without enough context, AI or even human can easily misclassify. 
For instruction-based approach in LLMs using zero-shot or few-shot instruction, the sheer number of labels and additional context can create a large enough prompt that may exceed token limit and is expensive & slow.

## Solution Patterns

---
### Reorganize labels into a hierarchy and use multi-step prediction using instruction based model
---
#### Approach

This is the easiest approach where you can start right away by using instruction in prompt to instruction model (e.g. ChatGPT) to classify the content into one or multiple standard categories. 
It is important that the category name is self-descriptive and distinctive from others. Considering adding descriptions to category names when this is not the case or when there's additional context which is not there in the name.
To deal a very large number of labels that may exceed prompt token's limit. Structure labels into hierarchy and classify with mutliple passes.

For example consider following labels:
- Recruitment and Hiring
- Employee Benefits
- Performance Management
- Employee Relations
- Workplace Safety
- Payroll Processing
- Tax Compliance
- Employee Benefits
- Garnishments
- Wage and Hour Laws
- Sales Strategy
- Lead Generation
- Customer Relationship Management
- Sales Performance
- Sales Forecasting
- Marketing Strategy
- Brand Management
- Advertising and Promotion
- Social Media Marketing
- Market Research
- IT Infrastructure
- Software Development
- Cybersecurity
- Cloud Computing
- Data Analytics
- Production Planning
- Quality Control
- Inventory Management
- Supply Chain Management
- Lean Manufacturing
.......
With enough number of labels and description in one single classification prompt may not only exceed token limit but also decrease acccuracy as the model has to scan through a large number of labels.
This challenge can be addressed by restructuring the labels into one or several layers. For example this is a two-layers label structure.

Human Resources
- Recruitment and Hiring
- Employee Benefits
- Performance Management
- Employee Relations
- Workplace Safety

Payroll
- Payroll Processing
- Tax Compliance
- Employee Benefits
- Garnishments
- Wage and Hour Laws

Sales:
- Sales Strategy
- Lead Generation
- Customer Relationship Management
- Sales Performance
- Sales Forecasting

Marketing:
- Marketing Strategy
- Brand Management
- Advertising and Promotion
- Social Media Marketing
- Market Research

Technology:
- IT Infrastructure
- Software Development
- Cybersecurity
- Cloud Computing
- Data Analytics

Manufacturing:
•	Production Planning
•	Quality Control
•	Inventory Management
•	Supply Chain Management
•	Lean Manufacturing

Then to we can have two seperate classifiers, first to classify into one of major categories (Human Resources, Payroll, Sales, Marketing, Technology, Manufacturing) then within the predicted major category, have 2nd classifier to classify the into one of the major category's sub categories.
The prompt size for each classifier is smaller which can allow you to put more description to further improve LLM's accuracy. 

#### Implementation

Check out the accompanied notebook on example to classify using instruction prompt 
#### Performance
Compare accuracy between using 1-stage instruction prompt and 2-stage instruction prompt approach 

#### Strengths

- Simplicity: using just instruction without fine-tuning
- Flexibility to add new labels without having to retrain

#### Limitations
- Expensive 
- Slow 

---
### Pattern 2: Using embedding approach 
#### Approach
In this approach, Open AI's embedding model is used to convert target document content and all labels into vector representation format. Then we can use a distance measure to compute the document's vector with all the labels. The shortest measure represent the label with closest semantic meaning to the document.
labels can be re-organized into a hierarchy just like above method or can be in a flat structure. 
Added context & description can be added to highlight semantic distinction between labels.

#### Implementation

Check out the accompanied notebook on example to classify using embedding vector approach 
#### Performance
Compare accuracy between using 1-stage and 2-stage intruction approaches

#### Strengths

- Simplicity: Just need to compute embedding without fine-tuning
- Flexibility to add new labels without having to retrain
- Much faster than instruction based model

#### Limitations
- This may not work for multi-label classification 

### Pattern 3: Fine-tuning on top of embedding
#### Approach
This approach start with creating embedding representation just like pattern 2 but goes further by adding a classifying ML model (classifical ML) on top that is trained on labeled examples.

#### Implementation

Check out the accompanied notebook on example to classify using embedding with classification  approach 
#### Performance
Compare performance of this approach with other approaches
#### Strengths

- Improved accuracy because of the fine-tuning process

#### Limitations
- This requires preperation of labeled examples

### Pattern 4: Fine-tuning LLM
This approach uses a fine-tunable models from Open AI to fine-tune on labeled data.

#### Implementation

Check out the accompanied notebook on example to classify using fine-tuned LLMs
#### Performance
Compare performance of this approach with other approaches
#### Strengths

- Improved accuracy because of the fine-tuning directly on LLMs

#### Limitations
- This requires preperation of labeled examples
- Cost can be expensive because of fine-tuning and LLMs deployment
