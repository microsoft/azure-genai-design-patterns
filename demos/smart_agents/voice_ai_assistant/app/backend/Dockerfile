FROM python:3.10


WORKDIR /app/backend

COPY . /app/backend

RUN rm -rf /app/backend/__pycache__
RUN rm Dockerfile

RUN pip install -r requirements.txt
EXPOSE 5000
CMD ["uvicorn", "chat_api:app", "--port=5000", "--host=0.0.0.0", "--reload"]
