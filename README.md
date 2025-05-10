
![Welcome (19)](https://github.com/user-attachments/assets/caed207e-75da-441d-92a8-dbca934b083a)

 # Attena bot -- AttentionLens Chatbot

This repository contains the **AI-powered assistant backend** for the AttentionLens application, a productivity and task management app specifically designed to support individuals with ADHD. The chatbot, named **Attena**, is powered by a fine-tuned GPT-3.5 Turbo model and integrates tightly with Firebase to deliver personalized, intelligent, and structured task support.

---

## 🎯 Chatbot Objectives 

The AttentionLens chatbot helps ADHD users overcome daily productivity challenges by offering:
- 📌 Clear task management assistance (add, edit, delete, view tasks)
- 💡 ADHD-focused guidance (time management, focus strategies, etc.)
- 😊 Emotionally supportive interaction with natural language processing
- 🔐 Secure data handling via Firebase integration

---

## 🚀 How to Run

### 1. Clone the Repository

```bash
git clone https://github.com/wexa0/AttentionsLens-Chatbot.git
cd AttentionsLens-Chatbot/AttentionLens-Chatbot
```
This command will download all project files to your computer and take you into the backend folder. 
### 2. Install Python 3.9+ (if not already installed)
If you don’t have Python installed, download it from:
🔗 https://www.python.org/downloads/

After installation, verify by running:
```bash
python --version
```
You should see something like: Python 3.9.x or higher.

### 3. Install Project Dependencies
Make sure you are using Python 3.9+, then run:

```bash
pip install -r requirements.txt
```
This will install all the necessary libraries.

### 4. Set OpenAI API Key
Inside chatbot_finetuned_gpt3_5.py, replace the placeholder in this line with our key (inside README file):

```bash
client = OpenAI(api_key="Our_Key_for_OpenAI_API")

```

### 4. Run the Bot
```bash
python chatbot_finetuned_gpt3_5.py
```

This will launch the Firestore listener and start responding to messages in real-time.

## 💻 Flutter Frontend
The Flutter frontend of the AttentionLens application can be found at the following repository:

🔗 https://github.com/wexa0/2024-25_GP_19







