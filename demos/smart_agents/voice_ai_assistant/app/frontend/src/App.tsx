import { useState, useEffect, ChangeEvent } from 'react';  
import ScrollToBottom from 'react-scroll-to-bottom';  
import { ResultReason } from "microsoft-cognitiveservices-speech-sdk";  
import './App.css';  
import { getTokenOrRefresh } from './token_util';  
import config from "./config.json";  
  
const backendAPIHost = config.backendAPIHost + '/chat';  
const tokenAPIHost = config.tokenAPIHost;  

// Type for language codes  
type LanguageCode = 'en-US' | 'ja-JP' | 'zh-CN' | 'vi-VN' | 'ko-KR' | 'es-ES';  
  
// Mapping from language codes to names  
const languageNames: { [key in LanguageCode]: string } = {  
  'en-US': 'English',  
  'ja-JP': 'Japanese',  
  'zh-CN': 'Chinese',  
  'vi-VN': 'Vietnamese',  
  'ko-KR': 'Korean',  
  'es-ES': 'Spanish',  
};  
  
// Utility function to generate a simple session ID  
const generateSessionId = () => {  
  return Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);  
};  
  
function App() {  
  const speechsdk = require("microsoft-cognitiveservices-speech-sdk");  
  
  const [userPrompt, setPrompt] = useState("");  
  const [language, setLanguage] = useState<LanguageCode>('en-US'); // Using LanguageCode type  
  const [voiceName, setVoiceName] = useState('en-US-JennyNeural');  
  const [messageList, setMessage] = useState<{ role: string, content: string }[]>([  
    { role: 'system', content: '' }  
  ]);  
  const [isMicInput, setIsMicInput] = useState(false);  
  const [comesFromMic, setComesFromMic] = useState(false);  
  const [actionData, setActionData] = useState("");  
  const [sessionId, setSessionId] = useState(generateSessionId());  
  
  const maxMessages = 10;  
  
  const clearMessages = () => {  
    setMessage([{ role: 'system', content: '' }]);  
    setPrompt('');  
    setActionData('');  
    setComesFromMic(false);  
    setSessionId(generateSessionId());  
  };  
  useEffect(() => {  
    updateImageSources();  
  }, [actionData]);  
  
  useEffect(() => {  
    callAPI();  
    if (comesFromMic && messageList[messageList.length - 1].role === 'assistant') {  
      speechToText();  
    }  
  }, [messageList, language]);  
  
  useEffect(() => {  
    if (isMicInput) {  
      updateMessages();  
      setIsMicInput(false);  
    }  
  }, [isMicInput]);  
  const updateImageSources = () => {  
    const imageTags = document.querySelectorAll('.action-content img');  
    imageTags.forEach((element) => {  
      const img = element as HTMLImageElement; // Type assertion  
      const productId = img.id.replace('image_', '');  
      fetch(`${config.backendAPIHost}/images/${productId}`)  
        .then(response => response.blob())  // Assuming the server returns binary data  
        .then(blob => {  
          // Create a local URL for the image blob and set it as the src  
          const imageUrl = URL.createObjectURL(blob);  
          img.src = imageUrl;  
        })  
        .catch(error => console.error('Error fetching image:', error));  
    });  
  };  
  
  const handleLanguageChange = (event: ChangeEvent<HTMLSelectElement>) => {  
    const selectedLanguage = event.target.value as LanguageCode;  
    setLanguage(selectedLanguage);  
    setVoiceName(languageOptions[selectedLanguage]);  
  };  
  
  async function callAPI() {  
    const lastMessage = messageList[messageList.length - 1];  
    if (lastMessage.role === 'user') {  
      try {  
        let completion: any = {};  
    
        const options = {  
          method: 'POST',  
          headers: {  
            'Content-Type': 'application/json',  
          },  
          body: JSON.stringify({  
            session_id: sessionId,  
            messages: messageList,  
            language: languageNames[language as LanguageCode] // Type assertion  
          }),  
        };  
    
        const response = await fetch(backendAPIHost, options);  
        if (!response.ok) {  
          throw new Error(`HTTP error! status: ${response.status}`);  
        } else {  
          completion = await response.json();  
        }  
    
        if (completion != undefined) {  
          const { message, role, action } = completion;  
    
          if (messageList.length > maxMessages) {  
            messageList.splice(1, 1);  
          }  
          setMessage(messageList => [...messageList, { role: role, content: message }]);  
          if (action) {  
            setActionData(action);  
          }  
        }  
      } catch (e) {  
        console.error('Error getting data', e);  
        throw e;  
      }  
    }  
  };  
    const updateMessages = () => {  
    if (userPrompt !== "") {  
      if (messageList.length > maxMessages) {  
        messageList.splice(1, 1);  
      }  
      let newMessageList = [...messageList, { role: 'user', content: userPrompt }];  
      setMessage(newMessageList);  
      setPrompt('');  
    }  
  };  
  
  async function getFromMic() {  
    // Adjustments for language and voice selection  
    const tokenObj = await getTokenOrRefresh(tokenAPIHost);  
    const speechConfig = speechsdk.SpeechConfig.fromAuthorizationToken(tokenObj.authToken, tokenObj.region);  
    var autodetectconfig = speechsdk.AutoDetectSourceLanguageConfig.fromLanguages(["en-US", "vi-VN", "zh-CN", "ja-JP"]);  
    const audioConfig = speechsdk.AudioConfig.fromDefaultMicrophoneInput();  
    const speechRecognizer = speechsdk.SpeechRecognizer.FromConfig(speechConfig, autodetectconfig, audioConfig);  
    
    let emoji = document.getElementById("mic");  
    if (emoji) {  
      emoji.innerHTML = "&#128308;"  
    }  
    
    speechRecognizer.recognizeOnceAsync((result: any): void => {  
      if (result.reason === ResultReason.RecognizedSpeech) {  
        const languageDetectionResult = speechsdk.AutoDetectSourceLanguageResult.fromResult(result);  
        const detectedLanguage = languageDetectionResult.language; // Detected language code  
        const audioPrompt = result.text;  
          
        // Set detected language and corresponding voice for synthesis  
        setLanguage(detectedLanguage);  
        setVoiceName(languageOptions[detectedLanguage as LanguageCode]);  
          
        setPrompt(audioPrompt);  
        setIsMicInput(true);  
        setComesFromMic(true);  
      };  
      if (emoji) emoji.innerHTML = '&#127908';  
    });  
  }  
    
  // Adjust `callAPI` and other relevant functions to use the detected `language` state.  
    
  async function speechToText() {  
    const tokenObj = await getTokenOrRefresh(tokenAPIHost);  
    const speechConfig = speechsdk.SpeechConfig.fromAuthorizationToken(tokenObj.authToken, tokenObj.region);  
  
    speechConfig.speechSynthesisLanguage = language;  
    speechConfig.speechSynthesisVoiceName = voiceName;  
  
    const audioConfig = speechsdk.AudioConfig.fromDefaultSpeakerOutput();  
    const speechSynthesizer = new speechsdk.SpeechSynthesizer(speechConfig, audioConfig);  
  
    const lastMessage = messageList[messageList.length - 1];  
    speechSynthesizer.speakTextAsync(lastMessage.content)  
    setComesFromMic(false);  
  }  
  
  const languageOptions: { [key in LanguageCode]: string } = {  
    'en-US': 'en-US-JennyNeural',  
    'ja-JP': 'ja-JP-NanamiNeural',  
    'zh-CN': 'zh-CN-XiaoxiaoNeural',  
    'vi-VN': 'vi-VN-HoaiMyNeural',  
    'ko-KR': 'ko-KR-SunHiNeural',  
    'es-ES': 'es-ES-ElviraNeural'  
  };  
  
  return (  
    <div className="App" style={{ display: 'flex', flexDirection: 'row', height: '100vh' }}>  
      {/* Action Content */}  
      <div className="action-content" style={{ flexGrow: 2, overflowY: 'auto', padding: '20px' }}>  
        {actionData && (  
          <div dangerouslySetInnerHTML={{ __html: actionData }}></div>  
        )}  
      </div>  
  
      {/* Chat Window */}  
      <div className="chat-window" style={{ flexGrow: 1, display: 'flex', flexDirection: 'column' }}>  
        <div className="chat-header">  
          <p>Hello, I am an AI Assistant. Select your language to chat</p>  
        </div>  
        <div className="chat-body">  
          <ScrollToBottom className="message-container">  
            {messageList.map((message, index) => {  
              if (message.role === "system") {  
                return null; // ignore this element  
              }  
              return (  
                <div key={index} className="message" id={message.role === "user" ? "other" : "you"}>  
                  <div className="message-content"><p>{message.content}</p></div>  
                  <div className="message-meta"><p id="author">{message.role}</p></div>  
                </div>  
              );  
            })}  
          </ScrollToBottom>  
        </div>  
        <div className="chat-footer">  
          <input  
            type="text"  
            value={userPrompt}  
            placeholder="Type your prompt here..."  
            onChange={(event) => setPrompt(event.target.value)}  
            onKeyDown={(event) => event.key === "Enter" && updateMessages()}  
          />  
          <button onClick={updateMessages}>&#9658;</button>  
          <button id="mic" onClick={getFromMic}>&#127908;</button>  
          <button onClick={clearMessages}>Reset</button>  
        </div>  
      </div>  
    </div>  
  );  
}  
  
export default App;  
