# Classification with Large Number of Labels

## Use Cases
With ease of use, Azure Open AI is a good candidate to implement document classification use cases under scenarios such as:
- Automated Document Routing
- Call Routing
- Automated classification in a content management system
In these scenarios, the document/content needed to be assigned with standard label(s) to be able to be routed or indexed at down stream processes. 


## Challenges

A common denominator for these scenarios is that there is a large number of possible labels to categorize the document/content into.The size of the document to be classified can also be large. 
Labels and label structure can change sometime. 
All these create challenges, especially  fine-tuning & model training approaches in traditional NLP.

## Solution Patterns

---
### Use instruction models such as ChatGPT
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


#### Performance

[ discuss / evaluate the performance of the approach (accuracy, speed, etc.) for instance here, use Rouge() or BERTScore()]

#### Strengths

[ discuss the strengths of the approach ]

#### Limitations

[ when does this approach fails or is not recommended ]

---
### Pattern 2: (name it)
---
[ ... ]