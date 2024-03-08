import logging
import os
from dotenv import load_dotenv
from pathlib import Path  
from quart_session import Session
from quart import Quart
from quart_cors import cors  # This is for enabling CORS in Quart  
import pickle
env_path = Path('..') / '.env'
load_dotenv(dotenv_path=env_path)

def create_app():
    if os.getenv("RUNNING_IN_PRODUCTION"):
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.DEBUG)

    app = Quart(__name__)
    app = cors(app, allow_origin="*")  # Enable CORS for all routes, customize as needed  
    app.config['SESSION_TYPE'] = 'redis'
    app.config['SESSION_URI'] = os.getenv("REDIS_URI")
    # app.session_interface.serializer = pickle
    Session(app)
    
    from . import chat  # noqa

    app.register_blueprint(chat.bp)

    return app
