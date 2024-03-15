from fastapi import FastAPI, Request, Depends  
from fastapi.responses import FileResponse, JSONResponse  
from fastapi.staticfiles import StaticFiles  
from starlette.middleware.sessions import SessionMiddleware  
from starlette.responses import HTMLResponse  
import os  
import re  
from dotenv import load_dotenv  
from pathlib import Path  
from fastapi.middleware.cors import CORSMiddleware  
import pickle
import base64
from utils import AVAILABLE_FUNCTIONS, Smart_Agent, FUNCTIONS_SPEC, PERSONA
import redis
import time
from fastapi.responses import Response  

# Load environment variables  
env_path = Path('../') / '.env'  
load_dotenv(dotenv_path=env_path)  

# Initialize FastAPI app  
app = FastAPI()  
allowed_origins = ["*"]  
# Add CORSMiddleware to the application  
app.add_middleware(  
    CORSMiddleware,  
    allow_origins=allowed_origins,  # Allows specified origins (use ["*"] for all)  
    allow_credentials=True,  
    allow_methods=["*"],  # Allows all methods  
    allow_headers=["*"],  # Allows all headers  
)  

# Static Files  
app.mount("/static", StaticFiles(directory="static"), name="static")  
  
# Middleware  
app.add_middleware(  
    SessionMiddleware,  
    secret_key=os.environ.get("SECRET_KEY", "a_random_secret_key")  
)  
  
r = redis.StrictRedis(host=os.getenv("REDIS_SERVER"),
        port=os.getenv("REDIS_PORT"), db=0, password=os.getenv("REDIS_PASSWORD"), ssl=True)

@app.on_event("startup")  
async def configure_openai():  
    pass  
    # Your startup code here  
  
@app.on_event("shutdown")  
async def shutdown_openai():  
    pass  
    # Your shutdown code here  
    
@app.get("/", response_class=HTMLResponse)  
async def index():  
    return FileResponse("static/index.html")  
@app.get("/images/{product_id}")  
async def get_image(product_id: str):  
    # Retrieve the image data from Redis using the product ID  
    image_data = r.get(product_id)  
    if not image_data:  
        # If the image is not found, you can return a placeholder image or an error response  
        return Response(content="Image not found", status_code=404)  
  
    # If the image data is found, return it as a response with the appropriate media type  
    # Assuming the image is stored as a raw binary PNG, JPEG, etc.  
    return Response(content=image_data, media_type="image/jpeg")  # Adjust the media type accordingly  

@app.post("/chat")  
async def chat_handler(request: Request):  
    data = await request.json() 
    session_id, data, language = data["session_id"], data["messages"], data["language"]
    print("selected language: ", language)
    user_input = data[-1]["content"]  

    # If there's a conversation history in the request, use it, otherwise start a new one  
    conversation_history = r.get(session_id) 
    conversation_history = pickle.loads(base64.b64decode(conversation_history)) if conversation_history else None 
    # Use the agent to process the user's input and generate a response 
    # Initialize the Smart Agent with the defined persona and available functions  
    agent = Smart_Agent(persona=PERSONA, functions_list=AVAILABLE_FUNCTIONS, functions_spec=FUNCTIONS_SPEC,  
                        init_message="Hi, this is Maya. How can I assist you today?", language=language)  
 
    _, updated_history, _ = agent.run(user_input=user_input, conversation=conversation_history)  
    converted_history = [dict(message) for message in updated_history]  
    updated_history = base64.b64encode(pickle.dumps(updated_history)).decode('ascii')
    # Update session history  
    r.set(session_id, updated_history)   

    message1 = converted_history[-1].get("content")  
    role1 = converted_history[-1].get("role")  
    message2 = converted_history[-2].get("content")  
    role2 = converted_history[-2].get("role")  
    name2 = converted_history[-2].get("name")
  
    action = ""  
    message = message1  
    role = role1  
  
    if role2 == "tool" and name2 =="display_product_info":  # agent wanted to show user something  
        action = message2  
        message = message1  
        role = role1  
    
    response_data = {  
        "message": message,  
        "role": role,  
        "action": action  
    }  
  
    return JSONResponse(content=response_data)  
