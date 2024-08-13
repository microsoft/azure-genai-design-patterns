### Flight & Hotel Copilot Demo Application:  
  
#### 1. Environment Preparation  
  
To set up the environment for running the Flight & Hotel Copilot demo application, follow these steps:  
  
1. **Create a Virtual Python Environment**:  
   - Ensure your Python version is between 3.8 and 3.9.  
   - You can create a virtual environment using the following command:  
     ```bash  
     python -m venv your_virtual_env_name  
     ```  
  
2. **Install Required Dependencies**:  
   - Use the `requirements.txt` file provided in the repository to install all necessary dependencies.  
   - Activate your virtual environment and run:  
     ```bash  
     pip install -r requirements.txt  
     ```  
  
#### 2. Standalone Copilot Scenarios  
  
The demo application includes standalone scenarios for both flight and hotel bookings. Each scenario has its own copilot, which can assist customers with various tasks.  
  
- **Flight Booking Copilot**:  
  - **Capabilities**: The flight copilot can help customers review their current bookings, check for earlier flights, change flights, and answer questions about flight policies.  
  - **Running the Application**: Navigate to the `travel_leisure` directory and run the following command:  
    ```bash  
    streamlit run flight_copilot.py  
    ```  
  
- **Hotel Booking Copilot**:  
  - **Capabilities**: The hotel copilot can assist customers in reviewing their current hotel bookings, check for earlier availability, change hotel bookings, and answer questions about hotel policies.  
  - **Running the Application**: Navigate to the `travel_leisure` directory and run the following command:  
    ```bash  
    streamlit run hotel_copilot.py  
    ```  
  
#### 3. Multi-Copilot Scenarios  
  
The application also supports multi-copilot scenarios, providing a single interface that utilizes multiple specialist copilots. This approach allows for scaling to multiple scenarios and domains without being too resource-intensive.  
  
- **Multi-Copilot Interface**:  
  - **Capabilities**: The multi-copilot interface uses various specialist copilots behind the scenes to handle different tasks, enabling a seamless and scalable user experience.  
  - **Running the Application**: Navigate to the `travel_leisure` directory and run the following command:  
    ```bash  
    streamlit run multi_agent_copilot.py  
    ```  
  
#### 4. Configuration  
  
To enable the multi-copilot scenarios, you need to create a `secrets.env` file in the `travel_leisure` directory with the following details about the services:  
  
```env  
AZURE_OPENAI_EMB_DEPLOYMENT=  
AZURE_OPENAI_EVALUATOR_DEPLOYMENT=  
AZURE_OPENAI_ENDPOINT=  
AZURE_OPENAI_API_KEY=  
AZURE_OPENAI_CHAT_DEPLOYMENT=  
For OpenAI configurations:
```

Use either GPT-4o or GPT-4o-mini for AZURE_OPENAI_CHAT_DEPLOYMENT.
Use GPT-4o-mini for AZURE_OPENAI_EVALUATOR_DEPLOYMENT.

By following these instructions, you will be able to set up and run the Flight & Hotel Copilot demo application, exploring various standalone and multi-copilot scenarios.

