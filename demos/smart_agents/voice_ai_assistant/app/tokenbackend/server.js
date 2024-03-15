
const cors = require('cors');
const express = require('express');
const axios = require('axios');
require('dotenv').config();
const app = express();

const port = process.env.TOKEN_BACKEND_TARGET_PORT;

app.use(cors());
app.use(express.json());

app.use((req, res, next) => {
    res.setHeader('Access-Control-Allow-Origin', '*');
    next();
  });


app.post('/openai-api-call',async (req, res) => {
    if (process.env.AZ_OPENAI_API_KEY === undefined || process.env.AZ_OPENAI_ENDPOINT === undefined || process.env.MODEL === undefined) {
        res.status(500).send('Environment variables not set');
    }
    const messageList = req.body;

    const params = {
        max_tokens: 1024,
        temperature : 1,
        messages: messageList
    };

    const response = await fetch(`${process.env.AZ_OPENAI_ENDPOINT}/openai/deployments/${process.env.MODEL}/chat/completions?api-version=2023-03-15-preview`, {
      method: 'POST',
      headers: {
        'api-key': `${process.env.AZ_OPENAI_API_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(params)
    })

    const data = await response.json();
    res.send(data);
});

app.get('/api/get-speech-token', async (req, res, next) => {
    res.setHeader('Content-Type', 'application/json');
    const speechKey = process.env.SPEECH_API_KEY;
    const speechRegion = process.env.SPEECH_REGION;

    if (speechKey === undefined || speechRegion === undefined) {
        res.status(400).send('You forgot to add your speech key or region to the .env file.');
    } else {
        const headers = { 
            headers: {
                'Ocp-Apim-Subscription-Key': speechKey,
                'Content-Type': 'application/x-www-form-urlencoded'
            }
        };

        try {
            const tokenResponse = await axios.post(`https://${speechRegion}.api.cognitive.microsoft.com/sts/v1.0/issueToken`, null, headers);
            res.send({ token: tokenResponse.data, region: speechRegion });
        } catch (err) {
            res.status(401).send('There was an error authorizing your speech key.');
        }
    }
});

app.listen(port, () => {console.log(`Server running on port: ${port}`)});
