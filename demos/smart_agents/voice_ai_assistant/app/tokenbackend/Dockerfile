FROM node:latest

WORKDIR /app
COPY . .
RUN npm install
EXPOSE 8000
CMD ["node", "./server.js", "-port 8000"]
