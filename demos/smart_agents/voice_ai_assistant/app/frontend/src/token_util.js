/**
 * Function and utility taken from Microsoft's Azure-Samples repository.
 * For in-depth explanation of the Speech SDK token authentication, please visit:
 * https://github.com/Azure-Samples/AzureSpeechReactSample
 * 
 */

import axios from 'axios';
import Cookie from 'universal-cookie';

export async function getTokenOrRefresh(serverUri) {  
    const cookie = new Cookie();  
    const speechToken = cookie.get('speech-token');  
  
    if (speechToken === undefined) {  
        try {  
            const res = await axios.get(`${serverUri}/api/get-speech-token`);  
            const token = res.data.token;  
            const region = res.data.region;  
            cookie.set('speech-token', region + ':' + token, {maxAge: 540, path: '/'});  
  
            // Uncomment below line to see the log when token is fetched from the backend  
            // console.log('Token fetched from back-end: ' + token);  
            return { authToken: token, region: region };  
        } catch (err) {  
            console.log(err.response.data);  
            return { authToken: null, error: err.response.data };  
        }  
    } else {  
        // Uncomment below line to see the log when token is fetched from the cookie  
        // console.log('Token fetched from cookie: ' + speechToken);  
        const idx = speechToken.indexOf(':');  
        return { authToken: speechToken.slice(idx + 1), region: speechToken.slice(0, idx) };  
    }  
}  
